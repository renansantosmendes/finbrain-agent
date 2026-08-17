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

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field

from deepagents import create_deep_agent
from deepagents.backends.filesystem import FilesystemBackend
from langchain_mcp_adapters.client import MultiServerMCPClient
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langfuse.langchain import CallbackHandler
from langfuse import Langfuse

import persistence
from logging_config import logger

MCP_URL = "https://finbrain-mcp.vercel.app/mcp"

# Vercel's filesystem is read-only outside of /tmp in production. Locally
# (no VERCEL env var) keep writing to the project dir like main_mcp.py does.
_backend_root = "/tmp" if os.environ.get("VERCEL") else "."
backend = FilesystemBackend(root_dir=_backend_root, virtual_mode=False)


class AgentRuntime:
    """Holds everything expensive to build. One instance per warm process."""

    def __init__(self) -> None:
        self.agent = None
        self.langfuse_handler = None
        self._checkpointer_cm = None

    async def startup(self) -> None:
        logger.info("startup: bootstrapping schema, loading prompt/tools/checkpointer")
        persistence.bootstrap_schema()

        langfuse = Langfuse()
        self.langfuse_handler = CallbackHandler()
        system_prompt = langfuse.get_prompt("FINBRAIN_SYSTEM_PROMPT").compile()

        client = MultiServerMCPClient({
            "finbrain": {"transport": "streamable_http", "url": MCP_URL},
        })
        tools = await client.get_tools()
        logger.info("startup: loaded %d MCP tools", len(tools))

        self._checkpointer_cm = AsyncPostgresSaver.from_conn_string(
            persistence.get_checkpointer_conn_string()
        )
        checkpointer = await self._checkpointer_cm.__aenter__()
        await checkpointer.setup()

        self.agent = create_deep_agent(
            model="openai:gpt-5-nano",
            tools=tools,
            skills=["skills"],
            backend=backend,
            system_prompt=system_prompt,
            checkpointer=checkpointer,
        )
        logger.info("startup: agent ready")

    async def shutdown(self) -> None:
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


@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest, rt: AgentRuntime = Depends(get_runtime)) -> ChatResponse:
    if rt.agent is None:
        logger.error("chat: rejected, agent not ready")
        raise HTTPException(status_code=503, detail="Agent not ready")

    session_id = request.session_id or str(uuid.uuid4())
    logger.info("chat: request received session_id=%s message_len=%d", session_id, len(request.message))

    await persistence.log_message(session_id, "user", request.message)

    config = {
        "configurable": {"thread_id": session_id},
        "callbacks": [rt.langfuse_handler] if rt.langfuse_handler else [],
        "metadata": {
            "langfuse_session_id": session_id,
            "langfuse_tags": ["api", "financial-agent"],
        },
    }

    try:
        result = await rt.agent.ainvoke(
            {"messages": [{"role": "user", "content": request.message}]},
            config,
        )
    except Exception:
        logger.exception("chat: agent invocation failed session_id=%s", session_id)
        raise HTTPException(status_code=500, detail="Agent invocation failed")

    reply = result["messages"][-1].text

    await persistence.log_message(session_id, "assistant", reply)
    logger.info("chat: request completed session_id=%s reply_len=%d", session_id, len(reply))

    return ChatResponse(session_id=session_id, reply=reply)


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
