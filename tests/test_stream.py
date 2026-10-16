"""Tests for POST /chat/stream (Server-Sent Events).

Same isolation as tests/test_api.py: the agent is a fake that yields the
events astream_events produces, persistence is patched, and the TestClient
is used without the lifespan context manager.
"""
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import psycopg
import pytest
from fastapi.testclient import TestClient

import app as app_module
from app import AgentRuntime, app, get_runtime


class FakeMessage:
    def __init__(self, text: str):
        self.text = text


class FakeChunk:
    def __init__(self, content):
        self.content = content


class StreamingFakeAgent:
    """Emits tool start/end and model token events, then reports the final
    state the way LangGraph's aget_state does."""

    def __init__(self, tokens=(), tools=(), reply="", fail_before_output=False, stale_first_call=False):
        self.tokens = list(tokens)
        self.tools = list(tools)
        self.reply = reply
        self.fail_before_output = fail_before_output
        self.stale_first_call = stale_first_call
        self.calls = []

    async def astream_events(self, inputs, config, version):
        self.calls.append((inputs, config, version))
        if self.stale_first_call and len(self.calls) == 1:
            raise psycopg.OperationalError("the connection is closed")
        if self.fail_before_output:
            raise RuntimeError("mcp server unreachable")
        for name in self.tools:
            yield {"event": "on_tool_start", "name": name, "data": {}}
            yield {"event": "on_tool_end", "name": name, "data": {}}
        for token in self.tokens:
            yield {"event": "on_chat_model_stream", "data": {"chunk": FakeChunk(token)}}

    async def aget_state(self, config):
        return SimpleNamespace(values={"messages": [FakeMessage(self.reply)]})


def parse_frames(body: str) -> list[tuple[str, dict]]:
    frames = []
    for block in body.split("\n\n"):
        if not block.strip():
            continue
        lines = dict(line.split(": ", 1) for line in block.splitlines())
        frames.append((lines["event"], json.loads(lines["data"])))
    return frames


@pytest.fixture
def make_client():
    def build(agent):
        rt = AgentRuntime()
        rt.agent = agent
        rt.langfuse_handler = None
        app.dependency_overrides[get_runtime] = lambda: rt
        patcher = patch.object(app_module.persistence, "log_message", new_callable=AsyncMock)
        log = patcher.start()
        reconnect = patch.object(rt, "reconnect_checkpointer", new_callable=AsyncMock)
        reconnect_mock = reconnect.start()
        return TestClient(app), log, reconnect_mock, (patcher, reconnect)

    yield build
    app.dependency_overrides.clear()
    patch.stopall()


def test_stream_sends_tokens_then_done_with_the_final_reply(make_client):
    client, _, _, _ = make_client(StreamingFakeAgent(tokens=["Olá", ", PETR4"], reply="Olá, PETR4"))
    resp = client.post("/chat/stream", json={"message": "oi", "session_id": "abc-123"})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    frames = parse_frames(resp.text)
    assert [name for name, _ in frames] == ["token", "token", "done"]
    assert "".join(data["text"] for name, data in frames if name == "token") == "Olá, PETR4"
    assert frames[-1][1] == {"session_id": "abc-123", "reply": "Olá, PETR4"}


def test_stream_reports_tools_while_they_run(make_client):
    client, _, _, _ = make_client(StreamingFakeAgent(tools=["get_quote"], tokens=["ok"], reply="ok"))
    frames = parse_frames(client.post("/chat/stream", json={"message": "cotação"}).text)
    assert frames[0] == ("tool", {"name": "get_quote", "status": "start"})
    assert frames[1] == ("tool", {"name": "get_quote", "status": "end"})


def test_stream_logs_the_user_message_and_the_final_reply(make_client):
    client, log, _, _ = make_client(StreamingFakeAgent(tokens=["x"], reply="resposta final"))
    client.post("/chat/stream", json={"message": "pergunta", "session_id": "log-thread"})
    assert log.await_args_list[0].args == ("log-thread", "user", "pergunta")
    assert log.await_args_list[-1].args == ("log-thread", "assistant", "resposta final")


def test_stream_answers_slash_commands_without_calling_the_agent(make_client):
    agent = StreamingFakeAgent(reply="nunca usado")
    client, log, _, _ = make_client(agent)
    frames = parse_frames(client.post("/chat/stream", json={"message": "/help"}).text)
    assert [name for name, _ in frames] == ["done"]
    assert frames[0][1]["reply"]
    assert agent.calls == []
    assert log.await_count == 2


def test_stream_error_before_any_output_sends_error_and_no_assistant_log(make_client):
    client, log, _, _ = make_client(StreamingFakeAgent(fail_before_output=True))
    frames = parse_frames(client.post("/chat/stream", json={"message": "oi"}).text)
    assert [name for name, _ in frames] == ["error"]
    assert frames[0][1]["code"] == "agent_failed"
    roles = [call.args[1] for call in log.await_args_list]
    assert roles == ["user"]


def test_stream_reconnects_once_when_the_checkpointer_was_stale(make_client):
    agent = StreamingFakeAgent(tokens=["ok"], reply="ok", stale_first_call=True)
    client, _, reconnect, _ = make_client(agent)
    frames = parse_frames(client.post("/chat/stream", json={"message": "oi"}).text)
    assert frames[-1][0] == "done"
    assert reconnect.await_count == 1
    assert len(agent.calls) == 2


def test_stream_returns_503_when_agent_not_ready(make_client):
    rt = AgentRuntime()
    rt.agent = None
    app.dependency_overrides[get_runtime] = lambda: rt
    try:
        resp = TestClient(app).post("/chat/stream", json={"message": "oi"})
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 503


def test_stream_uses_the_stream_tag_and_the_thread_id(make_client):
    agent = StreamingFakeAgent(tokens=["ok"], reply="ok")
    client, _, _, _ = make_client(agent)
    client.post("/chat/stream", json={"message": "oi", "session_id": "thread-1"})
    _, config, version = agent.calls[0]
    assert version == "v2"
    assert config["configurable"]["thread_id"] == "thread-1"
    assert "stream" in config["metadata"]["langfuse_tags"]
