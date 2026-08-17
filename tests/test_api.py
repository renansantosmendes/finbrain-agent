"""Unit tests for the /chat and /health routes in app.py.

The real agent, MCP tools and Neon connection are never touched here:
- `get_runtime` is overridden with a fake AgentRuntime holding a fake agent.
- `persistence.log_message` is patched out.
- The TestClient is used WITHOUT the `with` context manager, so FastAPI's
  lifespan (which does real network calls on startup) never runs.
"""
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

import app as app_module
from app import AgentRuntime, app, get_runtime


class FakeMessage:
    def __init__(self, text: str):
        self.text = text


class FakeAgent:
    def __init__(self, reply: str = "mocked reply"):
        self.reply = reply
        self.received_calls = []

    async def ainvoke(self, inputs, config):
        self.received_calls.append((inputs, config))
        return {"messages": [FakeMessage(self.reply)]}


class FailingAgent:
    async def ainvoke(self, inputs, config):
        raise RuntimeError("mcp server unreachable")


@pytest.fixture
def fake_runtime():
    rt = AgentRuntime()
    rt.agent = FakeAgent()
    rt.langfuse_handler = None
    return rt


@pytest.fixture
def client(fake_runtime):
    app.dependency_overrides[get_runtime] = lambda: fake_runtime
    with patch.object(app_module.persistence, "log_message", new_callable=AsyncMock) as mock_log:
        yield TestClient(app), mock_log
    app.dependency_overrides.clear()


def test_health_returns_ok():
    resp = TestClient(app).get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_chat_generates_session_id_when_none_given(client):
    test_client, _ = client
    resp = test_client.post("/chat", json={"message": "oi"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["reply"] == "mocked reply"
    uuid.UUID(body["session_id"])  # raises if not a valid uuid


def test_chat_reuses_provided_session_id_as_thread_id(client, fake_runtime):
    test_client, _ = client
    resp = test_client.post("/chat", json={"message": "oi", "session_id": "abc-123"})
    assert resp.status_code == 200
    assert resp.json()["session_id"] == "abc-123"

    _, config = fake_runtime.agent.received_calls[0]
    assert config["configurable"]["thread_id"] == "abc-123"


def test_chat_logs_user_message_then_assistant_reply(client):
    test_client, mock_log = client
    test_client.post("/chat", json={"message": "qual o preço da PETR4?", "session_id": "log-thread"})

    assert mock_log.await_count == 2
    user_call, assistant_call = mock_log.await_args_list
    assert user_call.args == ("log-thread", "user", "qual o preço da PETR4?")
    assert assistant_call.args == ("log-thread", "assistant", "mocked reply")


def test_chat_rejects_empty_message(client):
    test_client, _ = client
    resp = test_client.post("/chat", json={"message": ""})
    assert resp.status_code == 422


def test_chat_rejects_missing_message(client):
    test_client, _ = client
    resp = test_client.post("/chat", json={"session_id": "abc"})
    assert resp.status_code == 422


def test_chat_returns_500_when_agent_invocation_fails(client, fake_runtime):
    fake_runtime.agent = FailingAgent()
    test_client, _ = client
    resp = test_client.post("/chat", json={"message": "oi"})
    assert resp.status_code == 500


def test_chat_returns_503_when_agent_not_ready():
    app.dependency_overrides[get_runtime] = lambda: AgentRuntime()  # agent is None
    try:
        resp = TestClient(app).post("/chat", json={"message": "oi"})
        assert resp.status_code == 503
    finally:
        app.dependency_overrides.clear()
