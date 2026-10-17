"""Conversation persistence backed by Neon Postgres.

Everything lives in the dedicated `finbrain` schema (see db/schema.sql):
- The LangGraph checkpoint tables (via AsyncPostgresSaver), which give the
  agent real memory across turns for a given thread_id.
- `conversations` and `messages`, the readable transcript of each web
  conversation, linked to its owner through conversations.thread_id.

Only threads created for an authenticated web user (`web:<user_id>:<uuid>`)
are logged in `finbrain.messages`: the transcript needs an owner. Open-mode
and Telegram threads keep working, but their turns are not stored.
"""
import os
from contextlib import asynccontextmanager
from typing import AsyncIterator, Optional
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

import psycopg
from psycopg import AsyncConnection
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from logging_config import logger

SCHEMA_NAME = "finbrain"
WEB_THREAD_PREFIX = "web"
DEFAULT_CONVERSATION_TITLE = "Nova conversa"
TITLE_MAX_LENGTH = 60

# The agent calls the reply "assistant"; the schema stores it as "agent".
ROLE_BY_LOG_NAME = {"user": "user", "assistant": "agent", "system": "system"}

# Reused across requests on a warm instance so log_message doesn't pay a
# fresh TCP+TLS+auth handshake (against Neon's pooler) on every call -- see
# init_pool()/close_pool(), called from AgentRuntime.startup()/shutdown() in
# app.py. None until init_pool() runs; open_connection() falls back to a
# one-off connection if it's ever used before that, so a pool issue degrades
# latency instead of breaking logging outright.
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
    """Connection string that makes the checkpointer use the finbrain schema.

    Returns
    -------
    str
        NEON_DATABASE_URL with the search path set to the finbrain schema.
    """
    base_url = os.environ["NEON_DATABASE_URL"]
    return _with_schema_search_path(base_url)


def owner_user_id_of_thread(thread_id: str) -> Optional[str]:
    """Return the user id encoded in a web thread id, if the thread has one.

    Web threads are built as `web:<user_id>:<conversation_uuid>` in
    web_auth.resolve_session. Any other thread (open mode, Telegram, the
    command line script) has no owner and yields None.

    Parameters
    ----------
    thread_id : str
        Thread id used by the agent.

    Returns
    -------
    Optional[str]
        The user id, or None when the thread does not belong to a web user.

    Example
    -------
    >>> owner_user_id_of_thread("web:ad13:0b7e")
    'ad13'
    """
    parts = thread_id.split(":")
    if len(parts) != 3 or parts[0] != WEB_THREAD_PREFIX or not parts[1]:
        return None
    return parts[1]


def bootstrap_schema() -> None:
    """Make sure the finbrain schema exists before the agent starts.

    The tables are created by db/apply_schema.py from db/schema.sql; this
    only guards against a database where that script was never run. Runs
    with a plain connection and is idempotent.

    Returns
    -------
    None
    """
    base_url = os.environ["NEON_DATABASE_URL"]
    with psycopg.connect(base_url, autocommit=True) as conn:
        conn.execute(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA_NAME}")
    logger.info("persistence: schema '%s' ready", SCHEMA_NAME)


async def init_pool() -> None:
    """Open the connection pool once per warm process.

    Small pool that opens eagerly so the first request doesn't pay connection
    setup on top of everything else a cold start already does.

    Returns
    -------
    None
    """
    global _pool
    base_url = os.environ["NEON_DATABASE_URL"]
    _pool = AsyncConnectionPool(base_url, min_size=1, max_size=5, kwargs={"autocommit": True}, open=False)
    await _pool.open(wait=True)
    logger.info("persistence: connection pool opened")


async def close_pool() -> None:
    """Close the connection pool, if it was opened.

    Returns
    -------
    None
    """
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


@asynccontextmanager
async def open_connection() -> AsyncIterator[AsyncConnection]:
    """Yield an autocommit connection from the pool, or a one-off one.

    Returns
    -------
    AsyncIterator[AsyncConnection]
        Connection that stays open for the duration of the ``async with``.
    """
    if _pool is not None:
        async with _pool.connection() as conn:
            yield conn
        return
    base_url = os.environ["NEON_DATABASE_URL"]
    async with await AsyncConnection.connect(base_url, autocommit=True) as conn:
        yield conn


async def log_message(
    thread_id: str,
    role: str,
    content: str,
    metadata: dict | None = None,
) -> None:
    """Append a message to the transcript of a web conversation.

    Messages of threads without an owner are skipped. The first user message
    also sets the conversation title, cut to TITLE_MAX_LENGTH characters.

    Parameters
    ----------
    thread_id : str
        Thread id used by the agent.
    role : str
        "user" or "assistant" (stored as "agent").
    content : str
        Text of the message.
    metadata : dict | None
        Extra data stored with the message.

    Returns
    -------
    None
    """
    if owner_user_id_of_thread(thread_id) is None:
        logger.debug("persistence: thread %s has no web owner, message not stored", thread_id)
        return
    stored_role = ROLE_BY_LOG_NAME.get(role, role)
    async with open_connection() as conn:
        await conn.execute(
            f"""
            WITH target AS (
                SELECT id FROM {SCHEMA_NAME}.conversations WHERE thread_id = %s
            )
            INSERT INTO {SCHEMA_NAME}.messages (conversation_id, position, role, content, metadata)
            SELECT target.id,
                   COALESCE(
                       (SELECT max(m.position) FROM {SCHEMA_NAME}.messages m
                        WHERE m.conversation_id = target.id),
                       0
                   ) + 1,
                   %s, %s, %s
            FROM target
            """,
            (thread_id, stored_role, content, Jsonb(metadata or {})),
        )
        await conn.execute(
            f"""
            UPDATE {SCHEMA_NAME}.conversations
            SET last_message_at = now(),
                title = CASE
                    WHEN %s = 'user' AND title = %s THEN left(%s, %s)
                    ELSE title
                END
            WHERE thread_id = %s
            """,
            (
                stored_role,
                DEFAULT_CONVERSATION_TITLE,
                content.strip() or DEFAULT_CONVERSATION_TITLE,
                TITLE_MAX_LENGTH,
                thread_id,
            ),
        )
    logger.debug(
        "persistence: stored message thread_id=%s role=%s len=%d",
        thread_id,
        stored_role,
        len(content),
    )
