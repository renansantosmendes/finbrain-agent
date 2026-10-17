"""Unit tests for the /me routes (state_api.py) and the chat recording.

The database is never touched: every function of state_store is replaced
with an AsyncMock, and persistence.log_message is patched out. Tokens are
signed with the test key from test_web_auth, as the auth service does.
"""
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

import state_store
import web_auth
from app import AgentRuntime, app, get_runtime
from tests.test_api import FakeAgent
from tests.test_web_auth import JWKS_URL, FakeJwksClient, bearer, make_token


@pytest.fixture
def runtime():
    fake = AgentRuntime()
    fake.agent = FakeAgent()
    return fake


@pytest.fixture
def stored(monkeypatch):
    functions = [
        "upsert_user",
        "get_profile",
        "get_workspace",
        "update_workspace",
        "list_scenarios",
        "create_scenario",
        "update_scenario",
        "create_conversation",
        "ensure_conversation",
        "list_conversations",
        "get_conversation",
        "update_conversation",
        "record_tool_calls",
        "list_tool_calls",
    ]
    mocks = {}
    for name in functions:
        mocks[name] = AsyncMock(return_value={})
        monkeypatch.setattr(state_store, name, mocks[name])
    mocks["list_scenarios"].return_value = []
    mocks["list_conversations"].return_value = []
    mocks["list_tool_calls"].return_value = []
    mocks["record_tool_calls"].return_value = 0
    return mocks


@pytest.fixture
def client(runtime, monkeypatch, stored):
    monkeypatch.setenv("AUTH_JWKS_URL", JWKS_URL)
    monkeypatch.setattr(web_auth, "_jwks_client", lambda url: FakeJwksClient())
    app.dependency_overrides[get_runtime] = lambda: runtime
    with patch("persistence.log_message", new=AsyncMock()):
        yield TestClient(app)
    app.dependency_overrides.clear()


def test_state_requires_a_token(client, stored):
    response = client.get("/me/state")
    assert response.status_code == 401
    stored["get_workspace"].assert_not_called()


def test_state_rejects_an_invalid_token(client, stored):
    response = client.get("/me/state", headers=bearer("token-invalido"))
    assert response.status_code == 401
    stored["get_workspace"].assert_not_called()


def test_state_uses_the_subject_of_the_token(client, stored):
    user_id = str(uuid.uuid4())
    client.get("/me/state", headers=bearer(make_token(sub=user_id)))
    stored["get_workspace"].assert_awaited_once_with(user_id)


def test_signing_in_upserts_the_profile(client, stored):
    user_id = str(uuid.uuid4())
    client.get("/me/state", headers=bearer(make_token(sub=user_id)))
    stored["upsert_user"].assert_awaited_once_with(user_id, "ana@example.com", "Ana Lima")


def test_patch_state_sends_only_the_fields_sent(client, stored):
    user_id = str(uuid.uuid4())
    response = client.patch(
        "/me/state",
        headers=bearer(make_token(sub=user_id)),
        json={"current_tab": "brain", "active_scenario_id": None},
    )
    assert response.status_code == 200
    stored["update_workspace"].assert_awaited_once_with(
        user_id,
        {"current_tab": "brain", "active_scenario_id": None},
    )


@pytest.mark.parametrize(
    "payload",
    [
        {"current_tab": "admin"},
        {"active_symbol": "petr4 ; drop"},
        {"chart_range": "3 meses"},
        {"campo_inexistente": 1},
    ],
)
def test_patch_state_rejects_invalid_fields(client, payload):
    response = client.patch("/me/state", headers=bearer(make_token()), json=payload)
    assert response.status_code == 422


def test_scenario_limits_follow_the_engine(client):
    response = client.post(
        "/me/scenarios",
        headers=bearer(make_token()),
        json={"aporte_mensal": 99999},
    )
    assert response.status_code == 422


def test_scenario_defaults_match_the_engine(client, stored):
    client.post("/me/scenarios", headers=bearer(make_token()), json={})
    values = stored["create_scenario"].await_args.args[1]
    assert values["aporte_mensal"] == 500
    assert values["anos"] == 10
    assert values["retorno_anual_pct"] == 5.7
    assert values["volatilidade_anual_pct"] == 14.5


def test_scenario_of_another_user_is_not_found(client, stored):
    stored["update_scenario"].return_value = None
    response = client.patch(
        f"/me/scenarios/{uuid.uuid4()}",
        headers=bearer(make_token()),
        json={"anos": 5},
    )
    assert response.status_code == 404


def test_conversation_of_another_user_is_not_found(client, stored):
    stored["get_conversation"].return_value = None
    response = client.get(
        f"/me/conversations/{uuid.uuid4()}",
        headers=bearer(make_token()),
    )
    assert response.status_code == 404


def test_conversation_id_must_be_a_uuid(client):
    response = client.get("/me/conversations/nao-e-uuid", headers=bearer(make_token()))
    assert response.status_code == 422


def test_tool_call_batch_is_limited(client):
    calls = [{"tool": "get_price", "source": "dashboards"}] * 101
    response = client.post("/me/tool-calls", headers=bearer(make_token()), json={"calls": calls})
    assert response.status_code == 422


def test_tool_calls_are_stored_for_the_token_subject(client, stored):
    user_id = str(uuid.uuid4())
    stored["record_tool_calls"].return_value = 1
    response = client.post(
        "/me/tool-calls",
        headers=bearer(make_token(sub=user_id)),
        json={"calls": [{"tool": "get_price", "source": "dashboards", "params": {"sym": "PETR4"}}]},
    )
    assert response.status_code == 201
    assert response.json() == {"stored": 1}
    user_arg, calls = stored["record_tool_calls"].await_args.args
    assert user_arg == user_id
    assert calls == [{"tool": "get_price", "source": "dashboards", "params": {"sym": "PETR4"}}]


def test_chat_records_the_conversation_of_a_signed_in_user(client, runtime, stored):
    user_id = str(uuid.uuid4())
    response = client.post(
        "/chat",
        headers=bearer(make_token(sub=user_id)),
        json={"message": "Quanto está a PETR4?"},
    )
    assert response.status_code == 200
    thread_id = response.json()["session_id"]
    stored["upsert_user"].assert_awaited_once()
    stored["ensure_conversation"].assert_awaited_once()
    recorded_user, recorded_thread = stored["ensure_conversation"].await_args.args
    assert recorded_user == user_id
    assert recorded_thread == f"web:{user_id}:{thread_id}"


def test_open_mode_chat_does_not_record_anything(runtime, monkeypatch, stored):
    monkeypatch.delenv("AUTH_JWKS_URL", raising=False)
    app.dependency_overrides[get_runtime] = lambda: runtime
    try:
        with patch("persistence.log_message", new=AsyncMock()):
            response = TestClient(app).post("/chat", json={"message": "oi"})
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 200
    stored["upsert_user"].assert_not_called()
    stored["ensure_conversation"].assert_not_called()
