"""Unit tests for the /chat and /health routes in app.py.

The real agent, MCP tools and Neon connection are never touched here:
- `get_runtime` is overridden with a fake AgentRuntime holding a fake agent.
- `persistence.log_message` is patched out.
- The TestClient is used WITHOUT the `with` context manager, so FastAPI's
  lifespan (which does real network calls on startup) never runs.
"""
import logging
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

import app as app_module
from app import AgentRuntime, app, get_runtime


class FakeMessage:
    def __init__(self, text: str):
        self.text = text


class FakeHumanMessage:
    type = "human"


class FakeAIMessage:
    type = "ai"

    def __init__(self, tool_calls=None):
        self.tool_calls = tool_calls or []


class FakeToolMessage:
    type = "tool"

    def __init__(self, name: str, content: str):
        self.name = name
        self.content = content


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


def test_chat_expands_a_known_command_before_calling_the_agent(client, fake_runtime):
    test_client, _ = client
    resp = test_client.post("/chat", json={"message": "/price PETR4", "session_id": "cmd-thread"})

    assert resp.status_code == 200
    assert resp.json()["reply"] == "mocked reply"  # came from the agent, not a direct reply

    inputs, _ = fake_runtime.agent.received_calls[0]
    assert "PETR4" in inputs["messages"][0]["content"]
    assert inputs["messages"][0]["content"] != "/price PETR4"  # expanded, not passed through raw


def test_chat_help_command_never_calls_the_agent(client, fake_runtime):
    test_client, _ = client
    resp = test_client.post("/chat", json={"message": "/help"})

    assert resp.status_code == 200
    assert "/price" in resp.json()["reply"]
    assert fake_runtime.agent.received_calls == []


def test_chat_unknown_command_never_calls_the_agent(client, fake_runtime):
    test_client, _ = client
    resp = test_client.post("/chat", json={"message": "/naoexiste"})

    assert resp.status_code == 200
    assert "não reconhecido" in resp.json()["reply"]
    assert fake_runtime.agent.received_calls == []


def test_chat_logs_raw_command_text_not_the_expanded_prompt(client):
    test_client, mock_log = client
    test_client.post("/chat", json={"message": "/price PETR4", "session_id": "cmd-log-thread"})

    user_call = mock_log.await_args_list[0]
    assert user_call.args == ("cmd-log-thread", "user", "/price PETR4")


def test_chat_logs_which_command_was_selected(client, caplog):
    test_client, _ = client
    with caplog.at_level(logging.INFO, logger="finbrain"):
        test_client.post("/chat", json={"message": "/price PETR4"})
    assert "command=/price" in caplog.text


def test_chat_logs_free_form_messages_as_such(client, caplog):
    test_client, _ = client
    with caplog.at_level(logging.INFO, logger="finbrain"):
        test_client.post("/chat", json={"message": "qual o preço da PETR4?"})
    assert "free-form message" in caplog.text


def test_log_agent_activity_logs_tool_calls_and_results(caplog):
    messages = [
        FakeHumanMessage(),
        FakeAIMessage(tool_calls=[{"name": "collect_yfinance_data", "args": {"ticker": "PETR4.SA"}}]),
        FakeToolMessage("collect_yfinance_data", '{"current_price": 42.0}'),
    ]
    with caplog.at_level(logging.INFO, logger="finbrain"):
        app_module._log_agent_activity("thread-x", messages)

    assert "calling tool=collect_yfinance_data" in caplog.text
    assert "tool_result tool=collect_yfinance_data" in caplog.text


def test_log_agent_activity_ignores_earlier_turns(caplog):
    messages = [
        FakeHumanMessage(),
        FakeAIMessage(tool_calls=[{"name": "old_tool", "args": {}}]),
        FakeToolMessage("old_tool", "old result"),
        FakeHumanMessage(),
        FakeAIMessage(tool_calls=[{"name": "new_tool", "args": {}}]),
    ]
    with caplog.at_level(logging.INFO, logger="finbrain"):
        app_module._log_agent_activity("thread-x", messages)

    assert "new_tool" in caplog.text
    assert "old_tool" not in caplog.text


# --- /telegram/webhook -------------------------------------------------

@pytest.fixture
def telegram_send(client):
    """Patches telegram.send_message so no real HTTP call is made, and makes
    it available to assert on what would have been sent to the chat.

    Also pins TELEGRAM_WEBHOOK_SECRET to None so these tests don't depend on
    whatever value a local .env happens to define -- tests that specifically
    exercise the secret-token check override it themselves.
    """
    with patch.object(app_module.telegram, "send_message", new_callable=AsyncMock) as mock_send, \
         patch.object(app_module.telegram, "TELEGRAM_WEBHOOK_SECRET", None):
        yield mock_send


def test_telegram_webhook_replies_to_text_message(client, telegram_send):
    test_client, _ = client
    update = {"message": {"chat": {"id": 555}, "text": "qual o preço da PETR4?"}}
    resp = test_client.post("/telegram/webhook", json=update)

    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    telegram_send.assert_awaited_once_with(555, "mocked reply")


def test_telegram_webhook_uses_chat_id_as_session_id(client, telegram_send, fake_runtime):
    test_client, _ = client
    update = {"message": {"chat": {"id": 777}, "text": "oi"}}
    test_client.post("/telegram/webhook", json=update)

    _, config = fake_runtime.agent.received_calls[0]
    assert config["configurable"]["thread_id"] == "telegram-777"


def test_telegram_webhook_ignores_updates_without_text(client, telegram_send):
    test_client, _ = client
    update = {"message": {"chat": {"id": 555}, "sticker": {"file_id": "abc"}}}
    resp = test_client.post("/telegram/webhook", json=update)

    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    telegram_send.assert_not_awaited()


def test_telegram_webhook_rejects_malformed_body_instead_of_500(client, telegram_send):
    """Regression test: an empty/non-JSON body (e.g. a manual curl without
    -H 'Content-Type: application/json') used to crash `request.json()` and
    return a 500. A public webhook endpoint must not do that."""
    test_client, _ = client
    resp = test_client.post(
        "/telegram/webhook",
        content=b"",
        headers={"Content-Type": "application/json"},
    )

    assert resp.status_code == 200
    assert resp.json() == {"ok": False}
    telegram_send.assert_not_awaited()


def test_telegram_webhook_rejects_bad_secret_token(client, telegram_send):
    test_client, _ = client
    with patch.object(app_module.telegram, "TELEGRAM_WEBHOOK_SECRET", "expected-secret"):
        update = {"message": {"chat": {"id": 555}, "text": "oi"}}
        resp = test_client.post(
            "/telegram/webhook",
            json=update,
            headers={"X-Telegram-Bot-Api-Secret-Token": "wrong-secret"},
        )

    assert resp.status_code == 200
    assert resp.json() == {"ok": False}
    telegram_send.assert_not_awaited()


def test_telegram_webhook_accepts_correct_secret_token(client, telegram_send):
    test_client, _ = client
    with patch.object(app_module.telegram, "TELEGRAM_WEBHOOK_SECRET", "expected-secret"):
        update = {"message": {"chat": {"id": 555}, "text": "oi"}}
        resp = test_client.post(
            "/telegram/webhook",
            json=update,
            headers={"X-Telegram-Bot-Api-Secret-Token": "expected-secret"},
        )

    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    telegram_send.assert_awaited_once()


def test_telegram_webhook_sends_error_message_when_agent_fails(client, telegram_send, fake_runtime):
    fake_runtime.agent = FailingAgent()
    test_client, _ = client
    update = {"message": {"chat": {"id": 555}, "text": "oi"}}
    resp = test_client.post("/telegram/webhook", json=update)

    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    telegram_send.assert_awaited_once()
    args, _ = telegram_send.await_args
    assert args[0] == 555
    assert "erro" in args[1].lower()


def test_telegram_webhook_replies_when_agent_not_ready(telegram_send):
    app.dependency_overrides[get_runtime] = lambda: AgentRuntime()  # agent is None
    try:
        update = {"message": {"chat": {"id": 555}, "text": "oi"}}
        resp = TestClient(app).post("/telegram/webhook", json=update)
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    telegram_send.assert_awaited_once()
