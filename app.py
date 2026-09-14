"""FastAPI wrapper around the FinBrain deep agent.

Exposes POST /chat: send a message tied to a session_id, get a reply back.

Persistence model (see persistence.py):
- Conversation state (LangGraph checkpoints) and the readable message log
  both live in Postgres (Neon), keyed by session_id == thread_id. That's
  what survives a serverless cold start -- a new instance reconnects to the
  same database and resumes the thread from where it left off.
- What does NOT survive a cold start is the AgentRuntime below: it caches
  the compiled agent, the MCP tools list and the Langfuse prompt in process
  memory so warm requests (same instance, no cold start) don't pay the cost
  of re-fetching them on every call. That's a performance cache, not the
  source of truth for conversation history.
"""
import asyncio
import os
import sys
import uuid
from contextlib import asynccontextmanager
from typing import Optional

if sys.platform == "win32":
    # psycopg's async driver (used for conversation persistence) can't run
    # under Windows' default ProactorEventLoop.
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from dotenv import load_dotenv
load_dotenv()

import psycopg
from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

from deepagents import create_deep_agent
from deepagents.backends.filesystem import FilesystemBackend
from langchain.agents.middleware import (
    ContextEditingMiddleware,
    ModelCallLimitMiddleware,
    ModelFallbackMiddleware,
    PIIMiddleware,
    ToolCallLimitMiddleware,
    ToolErrorMiddleware,
    ToolRetryMiddleware,
)
from langchain_mcp_adapters.client import MultiServerMCPClient
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langfuse.langchain import CallbackHandler
from langfuse import Langfuse

import commands
import persistence
import telegram
from logging_config import logger

MCP_URL = "https://finbrain-mcp.vercel.app/mcp"
MODEL = "openai:gpt-5-nano"
FALLBACK_MODEL = "openai:gpt-4o-mini"


def _log_agent_activity(session_id: str, messages: list) -> None:
    """Log the tool calls the agent made this turn: which tool, with which
    args, and what it got back. `messages` is the full checkpointed history
    (it accumulates across turns), so this only looks at what comes after the
    last human message -- i.e. what happened in response to it.
    """
    last_human_idx = -1
    for i, m in enumerate(messages):
        if getattr(m, "type", None) == "human":
            last_human_idx = i

    for m in messages[last_human_idx + 1:]:
        msg_type = getattr(m, "type", None)
        if msg_type == "ai":
            for tc in getattr(m, "tool_calls", None) or []:
                logger.info(
                    "agent: session_id=%s calling tool=%s args=%s",
                    session_id, tc.get("name"), tc.get("args"),
                )
        elif msg_type == "tool":
            preview = str(getattr(m, "content", ""))[:300]
            logger.info(
                "agent: session_id=%s tool_result tool=%s preview=%s",
                session_id, getattr(m, "name", "?"), preview,
            )


def _on_tool_error(exc: Exception, request) -> str:
    """Turn a tool-execution exception into a message the model can react to.

    Named after the exception type only (not str(exc)) since the raw message
    can carry internal detail (stack traces, connection strings) we don't
    want reaching the model or, downstream, the user.
    """
    tool_name = request.tool_call["name"]
    logger.warning("tool error: %s raised %s", tool_name, type(exc).__name__)
    return f"A ferramenta `{tool_name}` falhou ({type(exc).__name__}). Tente novamente ou ajuste os parâmetros."


def _build_middleware() -> list:
    """Production-hardening middleware. See README for the rationale per item.

    Order matters: earlier entries are outermost (langchain.agents.middleware
    docs: "first defined = outermost"). ToolErrorMiddleware must wrap
    ToolRetryMiddleware -- retries happen first, and only once they're
    exhausted (on_failure="error" makes the retry middleware re-raise) does
    the error middleware turn the exception into a message instead of a hard
    500.

    Not listed here: summarization. create_deep_agent already inserts its own
    (deepagents' SummarizationMiddleware, a superset of LangChain's -- it also
    offloads evicted history to a backend file and recovers from context
    overflow) into every agent's base stack unconditionally. Adding another
    one collides by middleware name and create_agent rejects the duplicate.
    """
    return [
        ToolErrorMiddleware(_on_tool_error),
        ToolRetryMiddleware(max_retries=3, on_failure="error"),
        ModelFallbackMiddleware(FALLBACK_MODEL),
        ModelCallLimitMiddleware(run_limit=15, exit_behavior="end"),
        ToolCallLimitMiddleware(run_limit=20),
        PIIMiddleware("email", strategy="redact"),
        PIIMiddleware("credit_card", strategy="redact"),
        ContextEditingMiddleware(),
    ]

# Must be the actual project directory (not "." / not /tmp): create_deep_agent
# resolves skills=["skills"] relative to this same root_dir, and skills/ only
# exists in the deployed bundle, never under /tmp. Using an absolute path also
# keeps this correct regardless of Vercel's working directory at runtime.
#
# Tradeoff: Vercel's filesystem is read-only outside of /tmp in production, so
# any tool that tries to *write* a file (e.g. save a generated chart) will
# fail there. Only relevant if/when such a tool is added -- see README's
# "Limitações conhecidas em produção serverless" section.
_project_root = os.path.dirname(os.path.abspath(__file__))
backend = FilesystemBackend(root_dir=_project_root, virtual_mode=False)


class AgentRuntime:
    """Holds everything expensive to build. One instance per warm process."""

    def __init__(self) -> None:
        self.agent = None
        self.langfuse = None
        self.langfuse_handler = None
        self._checkpointer_cm = None
        self._reconnect_lock = asyncio.Lock()
        # Cached so a stale checkpointer connection can be swapped out and the
        # agent rebuilt on it, without re-fetching the prompt/tools -- see
        # reconnect_checkpointer().
        self._tools = None
        self._system_prompt = None

    async def startup(self) -> None:
        logger.info("startup: bootstrapping schema, loading prompt/tools/checkpointer")
        persistence.bootstrap_schema()

        self.langfuse = Langfuse()
        self.langfuse_handler = CallbackHandler()
        self._system_prompt = self.langfuse.get_prompt("FINBRAIN_SYSTEM_PROMPT").compile()

        client = MultiServerMCPClient({
            "finbrain": {"transport": "streamable_http", "url": MCP_URL},
        })
        self._tools = await client.get_tools()
        logger.info("startup: loaded %d MCP tools", len(self._tools))

        checkpointer = await self._open_checkpointer()
        self._build_agent(checkpointer)
        logger.info("startup: agent ready")

    async def _open_checkpointer(self):
        self._checkpointer_cm = AsyncPostgresSaver.from_conn_string(
            persistence.get_checkpointer_conn_string()
        )
        checkpointer = await self._checkpointer_cm.__aenter__()
        await checkpointer.setup()
        return checkpointer

    def _build_agent(self, checkpointer) -> None:
        self.agent = create_deep_agent(
            model=MODEL,
            tools=self._tools,
            skills=["skills"],
            backend=backend,
            system_prompt=self._system_prompt,
            checkpointer=checkpointer,
            middleware=_build_middleware(),
        )

    async def reconnect_checkpointer(self) -> None:
        """Rebuild the checkpointer connection (and the agent bound to it).

        Neon's unpooled endpoint (required here, see persistence.py, since
        the pooler rejects the search_path startup option) can close an idle
        connection out from under a warm serverless instance between
        requests. psycopg doesn't reconnect on its own -- the next query just
        fails with "the connection is closed" -- so _run_turn calls this once
        and retries when it sees that.
        """
        async with self._reconnect_lock:
            logger.warning("runtime: checkpointer connection was closed, reconnecting")
            if self._checkpointer_cm is not None:
                try:
                    await self._checkpointer_cm.__aexit__(None, None, None)
                except Exception:
                    logger.exception("runtime: error closing stale checkpointer (ignoring)")
            checkpointer = await self._open_checkpointer()
            self._build_agent(checkpointer)
            logger.info("runtime: checkpointer reconnected")

    async def shutdown(self) -> None:
        if self.langfuse is not None:
            self.langfuse.flush()
        if self._checkpointer_cm is not None:
            await self._checkpointer_cm.__aexit__(None, None, None)
        logger.info("shutdown: complete")


runtime = AgentRuntime()


def get_runtime() -> AgentRuntime:
    return runtime


@asynccontextmanager
async def lifespan(app: FastAPI):
    await runtime.startup()
    yield
    await runtime.shutdown()


app = FastAPI(title="FinBrain Agent API", lifespan=lifespan)


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1)
    session_id: Optional[str] = None


class ChatResponse(BaseModel):
    session_id: str
    reply: str


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


async def _run_turn(rt: AgentRuntime, session_id: str, message: str, tags: list[str]) -> str:
    """Resolve a slash command or invoke the agent, logging the turn either
    way. Shared by /chat and the Telegram webhook so both channels share one
    conversation history, one command set, and one error/logging behavior.
    """
    # Log the raw text the user sent (e.g. "/preco PETR4"), not the expanded
    # prompt below -- that's what an audit trail of "what did they type"
    # should preserve.
    await persistence.log_message(session_id, "user", message)

    resolved = commands.resolve(message)
    if resolved is not None:
        logger.info("chat: command=/%s session_id=%s", resolved.command_name, session_id)
        if resolved.direct_reply is not None:
            # Known command with bad/missing args, an unknown command, or
            # /help: answered here directly, no agent/LLM call spent on it.
            logger.info("chat: /%s handled directly, no agent call session_id=%s", resolved.command_name, session_id)
            await persistence.log_message(session_id, "assistant", resolved.direct_reply)
            return resolved.direct_reply
    else:
        logger.info("chat: free-form message (no command) session_id=%s", session_id)

    agent_input = resolved.prompt_for_agent if resolved is not None else message

    config = {
        "configurable": {"thread_id": session_id},
        "callbacks": [rt.langfuse_handler] if rt.langfuse_handler else [],
        "metadata": {
            "langfuse_session_id": session_id,
            "langfuse_tags": tags,
        },
    }

    async def _invoke():
        return await rt.agent.ainvoke(
            {"messages": [{"role": "user", "content": agent_input}]},
            config,
        )

    try:
        try:
            result = await _invoke()
        except psycopg.OperationalError:
            # Neon closed the checkpointer's idle connection out from under
            # this warm instance -- reconnect once and retry the same turn
            # instead of failing every request until the next cold start.
            logger.warning("chat: checkpointer connection closed, reconnecting and retrying session_id=%s", session_id)
            await rt.reconnect_checkpointer()
            result = await _invoke()
    except Exception:
        logger.exception("chat: agent invocation failed session_id=%s", session_id)
        raise
    finally:
        # Langfuse batches spans/usage/cost and ships them on a background
        # thread; without an explicit flush, a serverless instance can freeze
        # right after the response is sent and that data never leaves the
        # process. Off the event loop since flush() blocks on network I/O.
        if rt.langfuse is not None:
            await asyncio.to_thread(rt.langfuse.flush)

    _log_agent_activity(session_id, result["messages"])

    reply = result["messages"][-1].text

    await persistence.log_message(session_id, "assistant", reply)
    logger.info("chat: request completed session_id=%s reply_len=%d", session_id, len(reply))

    return reply


@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest, rt: AgentRuntime = Depends(get_runtime)) -> ChatResponse:
    if rt.agent is None:
        logger.error("chat: rejected, agent not ready")
        raise HTTPException(status_code=503, detail="Agent not ready")

    session_id = request.session_id or str(uuid.uuid4())
    logger.info("chat: request received session_id=%s message_len=%d", session_id, len(request.message))

    try:
        reply = await _run_turn(rt, session_id, request.message, tags=["api", "financial-agent"])
    except Exception:
        raise HTTPException(status_code=500, detail="Agent invocation failed")

    return ChatResponse(session_id=session_id, reply=reply)


@app.post("/telegram/webhook")
async def telegram_webhook(request: Request, rt: AgentRuntime = Depends(get_runtime)) -> dict:
    """Receives Telegram updates and replies via the Bot API (see telegram.py).

    Always returns 200 -- Telegram retries the webhook on non-2xx responses,
    and every failure mode here (bad secret, agent not ready, malformed
    update, agent error) is either unrecoverable by a retry or already
    reported to the user via a chat message, so a retry storm would only add
    noise.
    """
    if telegram.TELEGRAM_WEBHOOK_SECRET:
        secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
        if secret != telegram.TELEGRAM_WEBHOOK_SECRET:
            logger.warning("telegram: rejected webhook call with bad/missing secret token")
            return {"ok": False}

    try:
        update = await request.json()
    except Exception:
        logger.warning("telegram: rejected webhook call with malformed/empty JSON body")
        return {"ok": False}

    incoming = telegram.extract_incoming_text(update)
    if incoming is None:
        logger.info("telegram: ignoring update with no text message")
        return {"ok": True}

    chat_id, text = incoming
    session_id = f"telegram-{chat_id}"
    logger.info("telegram: update received chat_id=%s text_len=%d", chat_id, len(text))

    if rt.agent is None:
        logger.error("telegram: rejected, agent not ready chat_id=%s", chat_id)
        await telegram.send_message(chat_id, "O agente ainda está iniciando, tente novamente em instantes.")
        return {"ok": True}

    try:
        reply = await _run_turn(rt, session_id, text, tags=["telegram", "financial-agent"])
    except Exception:
        reply = "Desculpe, ocorreu um erro ao processar sua mensagem. Tente novamente."

    await telegram.send_message(chat_id, reply)
    return {"ok": True}


if __name__ == "__main__":
    # Local dev entrypoint: `python app.py`. Not used in production -- Vercel
    # (Linux) imports `app` directly as an ASGI callable, where the Windows
    # event-loop workaround below doesn't apply and isn't needed.
    #
    # Deliberately NOT `uvicorn app:app` from the CLI: uvicorn's own loop
    # factory hardcodes ProactorEventLoop on win32 and ignores the policy we
    # set above, which breaks psycopg's async driver again. Running uvicorn
    # inside our own asyncio.run() keeps our policy in effect.
    import uvicorn

    config = uvicorn.Config("app:app", host="0.0.0.0", port=8000, reload=False)
    server = uvicorn.Server(config)
    asyncio.run(server.serve())
