"""Unit tests for the /chat and /health routes in app.py.

The real agent, MCP tools and Neon connection are never touched here:
- `get_runtime` is overridden with a fake AgentRuntime holding a fake agent.
- `persistence.log_message` is patched out.
- The TestClient is used WITHOUT the `with` context manager, so FastAPI's
  lifespan (which does real network calls on startup) never runs.
"""
import json
import logging
import uuid
from unittest.mock import AsyncMock, patch

import psycopg
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


class StaleConnectionThenOkAgent:
    """Simulates Neon closing the checkpointer's idle connection: the first
    call raises the same error LangGraph's Postgres checkpointer raises in
    that case, the retry (after a reconnect) succeeds."""

    def __init__(self, reply: str = "mocked reply after reconnect"):
        self.reply = reply
        self.calls = 0

    async def ainvoke(self, inputs, config):
        self.calls += 1
        if self.calls == 1:
            raise psycopg.OperationalError("the connection is closed")
        return {"messages": [FakeMessage(self.reply)]}


class AlwaysStaleConnectionAgent:
    async def ainvoke(self, inputs, config):
        raise psycopg.OperationalError("the connection is closed")


class InvoiceAwareAgent:
    """Simulates the model calling read_credit_card_invoices() during the
    turn, so tests can assert the extracted PDF text actually reached the
    tool for this request."""

    def __init__(self, reply: str = "mocked reply"):
        self.reply = reply
        self.seen_invoices_json = None

    async def ainvoke(self, inputs, config):
        self.seen_invoices_json = app_module.invoices.read_credit_card_invoices.invoke({})
        return {"messages": [FakeMessage(self.reply)]}


def _make_minimal_pdf(text: str = "PETR4 compra R$ 120,00") -> bytes:
    """Hand-writes a minimal valid single-page PDF with `text` drawn on it
    (pypdf has no page-authoring API)."""
    content = f"BT /F1 24 Tf 100 700 Td ({text}) Tj ET".encode()
    objs = [
        b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n",
        b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n",
        b"3 0 obj\n<< /Type /Page /Parent 2 0 R /Resources << /Font << /F1 4 0 R >> >> "
        b"/MediaBox [0 0 612 792] /Contents 5 0 R >>\nendobj\n",
        b"4 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n",
        b"5 0 obj\n<< /Length %d >>\nstream\n%s\nendstream\nendobj\n" % (len(content), content),
    ]
    pdf = b"%PDF-1.4\n"
    offsets = []
    for obj in objs:
        offsets.append(len(pdf))
        pdf += obj
    xref_offset = len(pdf)
    pdf += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    for off in offsets:
        pdf += ("%010d 00000 n \n" % off).encode()
    pdf += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF" % (len(objs) + 1, xref_offset)
    return pdf


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


def test_chat_reconnects_checkpointer_and_retries_on_stale_connection(client, fake_runtime):
    fake_runtime.agent = StaleConnectionThenOkAgent()
    test_client, _ = client

    with patch.object(fake_runtime, "reconnect_checkpointer", new_callable=AsyncMock) as mock_reconnect:
        resp = test_client.post("/chat", json={"message": "oi"})

    assert resp.status_code == 200
    assert resp.json()["reply"] == "mocked reply after reconnect"
    mock_reconnect.assert_awaited_once()
    assert fake_runtime.agent.calls == 2


def test_chat_returns_500_when_retry_after_reconnect_also_fails(client, fake_runtime):
    fake_runtime.agent = AlwaysStaleConnectionAgent()
    test_client, _ = client

    with patch.object(fake_runtime, "reconnect_checkpointer", new_callable=AsyncMock) as mock_reconnect:
        resp = test_client.post("/chat", json={"message": "oi"})

    assert resp.status_code == 500
    mock_reconnect.assert_awaited_once()


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


def test_telegram_webhook_falls_back_to_chat_id_as_session_id_without_sender(client, telegram_send, fake_runtime):
    test_client, _ = client
    update = {"message": {"chat": {"id": 777}, "text": "oi"}}
    test_client.post("/telegram/webhook", json=update)

    _, config = fake_runtime.agent.received_calls[0]
    assert config["configurable"]["thread_id"] == "telegram-777"


def test_telegram_webhook_sessions_are_keyed_by_user_not_chat(client, telegram_send, fake_runtime):
    """Same person (from.id=111), two different chat_ids -- must land on the
    same thread_id so their history isn't split/lost across chats."""
    test_client, _ = client

    update_1 = {"message": {"chat": {"id": 111}, "from": {"id": 111}, "text": "oi"}}
    update_2 = {"message": {"chat": {"id": 999}, "from": {"id": 111}, "text": "de novo"}}
    test_client.post("/telegram/webhook", json=update_1)
    test_client.post("/telegram/webhook", json=update_2)

    thread_ids = [config["configurable"]["thread_id"] for _, config in fake_runtime.agent.received_calls]
    assert thread_ids == ["telegram-111", "telegram-111"]


def test_telegram_webhook_different_users_in_same_chat_get_different_sessions(client, telegram_send, fake_runtime):
    """A group chat (shared chat_id) must not merge two users' history into
    one thread_id."""
    test_client, _ = client

    update_1 = {"message": {"chat": {"id": 999}, "from": {"id": 111}, "text": "oi"}}
    update_2 = {"message": {"chat": {"id": 999}, "from": {"id": 222}, "text": "oi"}}
    test_client.post("/telegram/webhook", json=update_1)
    test_client.post("/telegram/webhook", json=update_2)

    thread_ids = [config["configurable"]["thread_id"] for _, config in fake_runtime.agent.received_calls]
    assert thread_ids == ["telegram-111", "telegram-222"]


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


# --- /invoices/analyze --------------------------------------------------

def test_analyze_invoices_rejects_when_no_files(client):
    test_client, _ = client
    resp = test_client.post("/invoices/analyze", files={})
    assert resp.status_code == 422


def test_analyze_invoices_rejects_more_than_max_files(client):
    test_client, _ = client
    pdf_bytes = _make_minimal_pdf()
    files = [
        ("files", (f"f{i}.pdf", pdf_bytes, "application/pdf"))
        for i in range(app_module.invoices.MAX_FILES + 1)
    ]
    resp = test_client.post("/invoices/analyze", files=files)
    assert resp.status_code == 422


def test_analyze_invoices_happy_path_reaches_the_tool(client, fake_runtime):
    fake_runtime.agent = InvoiceAwareAgent()
    test_client, _ = client
    pdf_bytes = _make_minimal_pdf("VALE3 venda R$ 50,00")

    resp = test_client.post(
        "/invoices/analyze",
        files=[("files", ("fatura.pdf", pdf_bytes, "application/pdf"))],
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["reply"] == "mocked reply"
    uuid.UUID(body["session_id"])  # raises if not a valid uuid

    seen = json.loads(fake_runtime.agent.seen_invoices_json)
    assert len(seen["invoices"]) == 1
    assert seen["invoices"][0]["filename"] == "fatura.pdf"
    assert "VALE3 venda R$ 50,00" in seen["invoices"][0]["text"]


def test_analyze_invoices_reuses_provided_session_id(client, fake_runtime):
    fake_runtime.agent = InvoiceAwareAgent()
    test_client, _ = client
    pdf_bytes = _make_minimal_pdf()

    resp = test_client.post(
        "/invoices/analyze",
        files=[("files", ("fatura.pdf", pdf_bytes, "application/pdf"))],
        data={"session_id": "renan-faturas"},
    )

    assert resp.status_code == 200
    assert resp.json()["session_id"] == "renan-faturas"


def test_analyze_invoices_returns_503_when_agent_not_ready():
    app.dependency_overrides[get_runtime] = lambda: AgentRuntime()  # agent is None
    try:
        resp = TestClient(app).post(
            "/invoices/analyze",
            files=[("files", ("fatura.pdf", _make_minimal_pdf(), "application/pdf"))],
        )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 503


# --- /telegram/webhook: documents ---------------------------------------

def test_telegram_webhook_analyzes_pdf_document(client, telegram_send, fake_runtime):
    fake_runtime.agent = InvoiceAwareAgent()
    test_client, _ = client
    pdf_bytes = _make_minimal_pdf("Assinatura Streaming R$ 39,90")

    update = {
        "message": {
            "chat": {"id": 999},
            "document": {"file_id": "abc123", "file_name": "fatura.pdf", "mime_type": "application/pdf"},
        }
    }

    with patch.object(app_module.telegram, "download_file", new_callable=AsyncMock) as mock_download:
        mock_download.return_value = pdf_bytes
        resp = test_client.post("/telegram/webhook", json=update)

    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    mock_download.assert_awaited_once_with("abc123")
    telegram_send.assert_awaited_once_with(999, "mocked reply")

    seen = json.loads(fake_runtime.agent.seen_invoices_json)
    assert len(seen["invoices"]) == 1
    assert "Assinatura Streaming R$ 39,90" in seen["invoices"][0]["text"]


def test_telegram_webhook_rejects_non_pdf_document(client, telegram_send, fake_runtime):
    test_client, _ = client
    update = {
        "message": {
            "chat": {"id": 999},
            "document": {"file_id": "abc123", "file_name": "foto.png", "mime_type": "image/png"},
        }
    }

    resp = test_client.post("/telegram/webhook", json=update)

    assert resp.status_code == 200
    telegram_send.assert_awaited_once()
    args, _ = telegram_send.await_args
    assert args[0] == 999
    assert "PDF" in args[1]
    assert fake_runtime.agent.received_calls == []  # never reached the agent


def test_telegram_webhook_handles_document_download_failure(client, telegram_send):
    test_client, _ = client
    update = {
        "message": {
            "chat": {"id": 999},
            "document": {"file_id": "abc123", "file_name": "fatura.pdf", "mime_type": "application/pdf"},
        }
    }

    with patch.object(app_module.telegram, "download_file", new_callable=AsyncMock) as mock_download:
        mock_download.return_value = None
        resp = test_client.post("/telegram/webhook", json=update)

    assert resp.status_code == 200
    telegram_send.assert_awaited_once()
    args, _ = telegram_send.await_args
    assert args[0] == 999
    assert "baixar" in args[1].lower()


def test_telegram_webhook_document_replies_when_agent_not_ready(telegram_send):
    app.dependency_overrides[get_runtime] = lambda: AgentRuntime()  # agent is None
    try:
        update = {
            "message": {
                "chat": {"id": 999},
                "document": {"file_id": "abc123", "file_name": "fatura.pdf", "mime_type": "application/pdf"},
            }
        }
        resp = TestClient(app).post("/telegram/webhook", json=update)
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    telegram_send.assert_awaited_once()
