"""Streaming variant of a chat turn, exposed as Server-Sent Events.

`stream_turn` runs the same steps as `app._run_turn` (slash commands,
conversation persistence, Langfuse flush, one reconnect on a stale checkpointer
connection) but yields events while the agent works instead of one final
string. Each event is an SSE frame:

- ``tool``: a tool started or finished (``{"name", "status"}``).
- ``token``: a piece of model text (``{"text"}``). Intermediate text written
  before a tool call is streamed too; the ``done`` event carries the final
  reply, which replaces whatever was streamed.
- ``done``: the turn finished (``{"session_id", "reply"}``).
- ``error``: the turn failed (``{"code", "message"}``). Nothing is logged as
  the assistant reply in that case.
"""
import json
import logging
from typing import Any, AsyncIterator

import psycopg

import commands
import persistence

logger = logging.getLogger(__name__)


def sse(event: str, data: dict[str, Any]) -> str:
    """Format one Server-Sent Events frame.

    Parameters
    ----------
    event : str
        Event name sent in the ``event:`` line.
    data : dict[str, Any]
        Payload, serialized as one JSON line in the ``data:`` line.

    Returns
    -------
    str
        The frame, ending with the blank line that closes it.

    Example
    -------
    >>> sse("token", {"text": "oi"})
    'event: token\\ndata: {"text": "oi"}\\n\\n'
    """
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def text_of_chunk(chunk: Any) -> str:
    """Return the plain text of a model stream chunk.

    Parameters
    ----------
    chunk : Any
        A LangChain message chunk. Its ``content`` is either a string or a
        list of content blocks.

    Returns
    -------
    str
        The text of the chunk, empty when it carries no text (for example a
        tool call delta).

    Example
    -------
    >>> class Chunk:
    ...     content = [{"type": "text", "text": "ol"}, {"type": "text", "text": "á"}]
    >>> text_of_chunk(Chunk())
    'olá'
    """
    content = getattr(chunk, "content", "")
    if isinstance(content, str):
        return content
    return "".join(
        block.get("text", "")
        for block in content
        if isinstance(block, dict) and block.get("type") == "text"
    )


def _events_to_frames(event: dict[str, Any]) -> list[str]:
    """Translate one LangChain event into zero or more SSE frames.

    Parameters
    ----------
    event : dict[str, Any]
        An event produced by ``astream_events(version="v2")``.

    Returns
    -------
    list[str]
        The frames to send to the client for this event.
    """
    kind = event.get("event")
    if kind == "on_chat_model_stream":
        text = text_of_chunk(event.get("data", {}).get("chunk"))
        return [sse("token", {"text": text})] if text else []
    if kind in ("on_tool_start", "on_tool_end"):
        status = "start" if kind == "on_tool_start" else "end"
        return [sse("tool", {"name": event.get("name", ""), "status": status})]
    return []


async def _direct_command_frames(session_id: str, message: str, reply: str) -> AsyncIterator[str]:
    """Answer a slash command without calling the agent and log the exchange.

    Parameters
    ----------
    session_id : str
        Conversation thread the exchange belongs to.
    message : str
        Raw text the user sent, for example ``/help``.
    reply : str
        Text answered directly by the command.

    Yields
    ------
    str
        A single ``done`` frame.
    """
    await persistence.log_message(session_id, "user", message)
    await persistence.log_message(session_id, "assistant", reply)
    yield sse("done", {"session_id": session_id, "reply": reply})


async def stream_turn(
    rt: Any,
    client_session_id: str,
    thread_id: str,
    message: str,
    config: dict[str, Any],
) -> AsyncIterator[str]:
    """Run one agent turn and yield its events as SSE frames.

    Parameters
    ----------
    rt : Any
        The ``AgentRuntime`` holding the compiled agent and Langfuse handles.
    client_session_id : str
        Session id the client sees; echoed in the ``done`` event.
    thread_id : str
        Internal thread id used for the checkpointer and the message log.
    message : str
        Text the user sent.
    config : dict[str, Any]
        LangGraph config for the turn (thread id, callbacks and metadata).

    Yields
    ------
    str
        SSE frames in the order the events happened.

    Example
    -------
    >>> frames = [frame async for frame in stream_turn(rt, "s", "s", "/help", {})]  # doctest: +SKIP
    """
    try:
        resolved = commands.resolve(message)
        if resolved is not None and resolved.direct_reply is not None:
            logger.info("stream: /%s handled directly session_id=%s", resolved.command_name, thread_id)
            async for frame in _direct_command_frames(thread_id, message, resolved.direct_reply):
                yield frame
            return

        agent_input = resolved.prompt_for_agent if resolved is not None else message
        inputs = {"messages": [{"role": "user", "content": agent_input}]}
        await persistence.log_message(thread_id, "user", message)

        attempt = 0
        while True:
            attempt += 1
            emitted = False
            try:
                async for event in rt.agent.astream_events(inputs, config, version="v2"):
                    for frame in _events_to_frames(event):
                        emitted = True
                        yield frame
                break
            except psycopg.OperationalError:
                if emitted or attempt > 1:
                    raise
                logger.warning("stream: checkpointer connection closed, reconnecting session_id=%s", thread_id)
                await rt.reconnect_checkpointer()

        state = await rt.agent.aget_state(config)
        reply = state.values["messages"][-1].text
        await persistence.log_message(thread_id, "assistant", reply)
        logger.info("stream: turn completed session_id=%s reply_len=%d", thread_id, len(reply))
        yield sse("done", {"session_id": client_session_id, "reply": reply})
    except Exception:
        logger.exception("stream: turn failed session_id=%s", thread_id)
        yield sse("error", {"code": "agent_failed", "message": "Não foi possível concluir a resposta agora."})
    finally:
        if rt.langfuse is not None:
            rt.langfuse.flush()
