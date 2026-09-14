"""Conversation persistence backed by Neon Postgres.

Two things live in the dedicated `agent_conversations` schema:
- The LangGraph checkpoint tables (via AsyncPostgresSaver), which give the
  agent real memory across turns for a given thread_id.
- A plain `messages` table, a human-readable log of what users sent and what
  the agent replied, for auditing/inspection outside of LangGraph's internal
  checkpoint format.
"""
import os
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

import psycopg
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from logging_config import logger

SCHEMA_NAME = "agent_conversations"

# Reused across requests on a warm instance so log_message doesn't pay a
# fresh TCP+TLS+auth handshake (against Neon's pooler) on every call -- see
# init_pool()/close_pool(), called from AgentRuntime.startup()/shutdown() in
# app.py. None until init_pool() runs; log_message falls back to a one-off
# connection if it's ever called before that (e.g. bootstrap ordering bugs),
# so a pool issue degrades latency instead of breaking logging outright.
_pool: AsyncConnectionPool | None = None


def _with_schema_search_path(base_url: str, schema: str = SCHEMA_NAME) -> str:
    """Append a `-c search_path=<schema>,public` connection option to the URL.

    This makes every table LangGraph's checkpointer creates (checkpoints,
    checkpoint_blobs, checkpoint_writes, ...) land in our schema instead of
    `public`, without needing a schema-aware LangGraph API (it doesn't expose
    one). Neon's PgBouncer pooler rejects the `options` startup parameter
    (https://neon.tech/docs/connect/connection-errors#unsupported-startup-parameter),
    so this also switches to the unpooled endpoint by dropping `-pooler` from
    the host, which is what Neon's own docs recommend for this case.
    """
    parts = urlsplit(base_url)
    unpooled_host = parts.hostname.replace("-pooler", "", 1) if parts.hostname else parts.hostname
    netloc = unpooled_host
    if parts.port:
        netloc += f":{parts.port}"
    if parts.username:
        userinfo = parts.username
        if parts.password:
            userinfo += f":{parts.password}"
        netloc = f"{userinfo}@{netloc}"
    query = dict(parse_qsl(parts.query))
    query["options"] = f"-csearch_path={schema},public"
    return urlunsplit(parts._replace(netloc=netloc, query=urlencode(query)))


def get_checkpointer_conn_string() -> str:
    base_url = os.environ["NEON_DATABASE_URL"]
    return _with_schema_search_path(base_url)


def bootstrap_schema() -> None:
    """Create the schema and the messages log table if they don't exist yet.

    Runs with a plain (schema-less) connection since the schema itself may
    not exist yet. Idempotent, safe to call on every process start.
    """
    base_url = os.environ["NEON_DATABASE_URL"]
    with psycopg.connect(base_url, autocommit=True) as conn:
        conn.execute(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA_NAME}")
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS {SCHEMA_NAME}.messages (
                id BIGSERIAL PRIMARY KEY,
                thread_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                metadata JSONB,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)
        conn.execute(f"""
            CREATE INDEX IF NOT EXISTS messages_thread_id_idx
            ON {SCHEMA_NAME}.messages (thread_id, created_at)
        """)
    logger.info("persistence: schema '%s' and messages table ready", SCHEMA_NAME)


async def init_pool() -> None:
    """Open the log-write connection pool once per warm process. Small pool
    (this table only ever sees single-row inserts, never a burst) that opens
    eagerly so the first request doesn't pay connection setup on top of
    everything else a cold start already does.
    """
    global _pool
    base_url = os.environ["NEON_DATABASE_URL"]
    _pool = AsyncConnectionPool(base_url, min_size=1, max_size=5, kwargs={"autocommit": True}, open=False)
    await _pool.open(wait=True)
    logger.info("persistence: log-write connection pool opened")


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


async def log_message(thread_id: str, role: str, content: str, metadata: dict | None = None) -> None:
    args = (thread_id, role, content, Jsonb(metadata) if metadata is not None else None)
    query = f"""
        INSERT INTO {SCHEMA_NAME}.messages (thread_id, role, content, metadata)
        VALUES (%s, %s, %s, %s)
    """
    if _pool is not None:
        async with _pool.connection() as conn:
            await conn.execute(query, args)
    else:
        # Pool not initialized (see module docstring above) -- fall back to a
        # one-off connection rather than fail the log write outright.
        base_url = os.environ["NEON_DATABASE_URL"]
        async with await psycopg.AsyncConnection.connect(base_url, autocommit=True) as conn:
            await conn.execute(query, args)
    logger.debug("persistence: logged message thread_id=%s role=%s len=%d", thread_id, role, len(content))
