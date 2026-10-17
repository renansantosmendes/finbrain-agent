"""Persistence of the FinBrain user state in the finbrain schema.

Covers the profile, the workspace (where the user stopped), the saved
simulation scenarios, the conversations with the agent and the trail of
tool calls. Every function takes the user id and filters by it, so one user
can never read or change another user's rows.
"""
from typing import Any, Optional
from uuid import UUID, uuid4

from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from logging_config import logger
from persistence import (
    DEFAULT_CONVERSATION_TITLE,
    SCHEMA_NAME,
    WEB_THREAD_PREFIX,
    open_connection,
)

WORKSPACE_FIELDS = (
    "current_tab",
    "active_symbol",
    "chart_range",
    "show_ma5",
    "show_ma20",
    "show_table",
    "active_scenario_id",
    "active_conversation_id",
)
SCENARIO_FIELDS = (
    "name",
    "aporte_mensal",
    "anos",
    "retorno_anual_pct",
    "volatilidade_anual_pct",
    "patrimonio_inicial",
)
DEFAULT_SCENARIO = {
    "name": "Cenário",
    "aporte_mensal": 500,
    "anos": 10,
    "retorno_anual_pct": 5.7,
    "volatilidade_anual_pct": 14.5,
    "patrimonio_inicial": 0,
}


async def _fetch_one(
    query: sql.Composable,
    params: tuple[Any, ...],
) -> Optional[dict[str, Any]]:
    """Run a query and return its first row as a dict.

    Parameters
    ----------
    query : sql.Composable
        Statement to run.
    params : tuple[Any, ...]
        Values bound to the statement.

    Returns
    -------
    Optional[dict[str, Any]]
        First row, or None when the query returns nothing.
    """
    async with open_connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cursor:
            await cursor.execute(query, params)
            return await cursor.fetchone()


async def _fetch_all(
    query: sql.Composable,
    params: tuple[Any, ...],
) -> list[dict[str, Any]]:
    """Run a query and return every row as a dict.

    Parameters
    ----------
    query : sql.Composable
        Statement to run.
    params : tuple[Any, ...]
        Values bound to the statement.

    Returns
    -------
    list[dict[str, Any]]
        Rows of the result, in the order the query returns them.
    """
    async with open_connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cursor:
            await cursor.execute(query, params)
            return await cursor.fetchall()


def _table(name: str) -> sql.Identifier:
    """Qualify a table name with the finbrain schema.

    Parameters
    ----------
    name : str
        Table name without schema.

    Returns
    -------
    sql.Identifier
        Safely quoted ``finbrain.<name>``.
    """
    return sql.Identifier(SCHEMA_NAME, name)


async def upsert_user(
    user_id: str,
    email: str,
    name: str,
) -> None:
    """Create the profile of a user, or refresh it on each sign-in.

    Parameters
    ----------
    user_id : str
        Id of the account in the auth service.
    email : str
        E-mail of the account.
    name : str
        Display name of the account.

    Returns
    -------
    None
    """
    await _fetch_one(
        sql.SQL(
            """
            INSERT INTO {users} (id, email, name, last_seen_at)
            VALUES (%s, %s, %s, now())
            ON CONFLICT (id) DO UPDATE
            SET email = EXCLUDED.email,
                name = EXCLUDED.name,
                last_seen_at = now()
            RETURNING id
            """
        ).format(users=_table("users")),
        (UUID(user_id), email, name),
    )


async def get_profile(user_id: str) -> Optional[dict[str, Any]]:
    """Read the profile of a user.

    Parameters
    ----------
    user_id : str
        Id of the account in the auth service.

    Returns
    -------
    Optional[dict[str, Any]]
        Profile fields, or None when the user has never signed in.
    """
    return await _fetch_one(
        sql.SQL("SELECT id, email, name, created_at, last_seen_at FROM {users} WHERE id = %s").format(
            users=_table("users")
        ),
        (UUID(user_id),),
    )


async def get_workspace(user_id: str) -> dict[str, Any]:
    """Read the workspace of a user, creating it with defaults if missing.

    Parameters
    ----------
    user_id : str
        Id of the account in the auth service.

    Returns
    -------
    dict[str, Any]
        Workspace row: current tab, symbol, range, indicators and the active
        scenario and conversation.
    """
    await _fetch_one(
        sql.SQL("INSERT INTO {workspaces} (user_id) VALUES (%s) ON CONFLICT (user_id) DO NOTHING RETURNING user_id").format(
            workspaces=_table("workspaces")
        ),
        (UUID(user_id),),
    )
    return await _fetch_one(
        sql.SQL("SELECT * FROM {workspaces} WHERE user_id = %s").format(workspaces=_table("workspaces")),
        (UUID(user_id),),
    )


async def update_workspace(
    user_id: str,
    changes: dict[str, Any],
) -> dict[str, Any]:
    """Change some fields of the workspace and return the updated row.

    Parameters
    ----------
    user_id : str
        Id of the account in the auth service.
    changes : dict[str, Any]
        Fields to change; keys outside WORKSPACE_FIELDS are ignored.

    Returns
    -------
    dict[str, Any]
        Workspace row after the change.
    """
    await get_workspace(user_id)
    fields = {key: value for key, value in changes.items() if key in WORKSPACE_FIELDS}
    if not fields:
        return await get_workspace(user_id)
    assignments = sql.SQL(", ").join(
        sql.SQL("{} = %s").format(sql.Identifier(key)) for key in fields
    )
    return await _fetch_one(
        sql.SQL("UPDATE {workspaces} SET {assignments} WHERE user_id = %s RETURNING *").format(
            workspaces=_table("workspaces"),
            assignments=assignments,
        ),
        (*fields.values(), UUID(user_id)),
    )


async def list_scenarios(user_id: str) -> list[dict[str, Any]]:
    """List the simulation scenarios saved by a user, most recent first.

    Parameters
    ----------
    user_id : str
        Id of the account in the auth service.

    Returns
    -------
    list[dict[str, Any]]
        Scenario rows.
    """
    return await _fetch_all(
        sql.SQL("SELECT * FROM {scenarios} WHERE user_id = %s ORDER BY updated_at DESC").format(
            scenarios=_table("simulation_scenarios")
        ),
        (UUID(user_id),),
    )


async def create_scenario(
    user_id: str,
    values: dict[str, Any],
) -> dict[str, Any]:
    """Save a new simulation scenario for a user.

    Parameters
    ----------
    user_id : str
        Id of the account in the auth service.
    values : dict[str, Any]
        Scenario fields; missing ones take the engine defaults.

    Returns
    -------
    dict[str, Any]
        Scenario row that was created.
    """
    fields = {**DEFAULT_SCENARIO, **{k: v for k, v in values.items() if k in SCENARIO_FIELDS}}
    names = sql.SQL(", ").join(sql.Identifier(key) for key in fields)
    placeholders = sql.SQL(", ").join(sql.Placeholder() for _ in fields)
    return await _fetch_one(
        sql.SQL("INSERT INTO {scenarios} (user_id, {names}) VALUES ({user}, {placeholders}) RETURNING *").format(
            scenarios=_table("simulation_scenarios"),
            names=names,
            user=sql.Placeholder(),
            placeholders=placeholders,
        ),
        (UUID(user_id), *fields.values()),
    )


async def update_scenario(
    user_id: str,
    scenario_id: UUID,
    changes: dict[str, Any],
) -> Optional[dict[str, Any]]:
    """Change a scenario of the user; None when it does not belong to them.

    Parameters
    ----------
    user_id : str
        Id of the account in the auth service.
    scenario_id : UUID
        Scenario to change.
    changes : dict[str, Any]
        Fields to change; keys outside SCENARIO_FIELDS are ignored.

    Returns
    -------
    Optional[dict[str, Any]]
        Scenario row after the change, or None when it was not found.
    """
    fields = {key: value for key, value in changes.items() if key in SCENARIO_FIELDS}
    if not fields:
        return await _fetch_one(
            sql.SQL("SELECT * FROM {scenarios} WHERE id = %s AND user_id = %s").format(
                scenarios=_table("simulation_scenarios")
            ),
            (scenario_id, UUID(user_id)),
        )
    assignments = sql.SQL(", ").join(
        sql.SQL("{} = %s").format(sql.Identifier(key)) for key in fields
    )
    return await _fetch_one(
        sql.SQL("UPDATE {scenarios} SET {assignments} WHERE id = %s AND user_id = %s RETURNING *").format(
            scenarios=_table("simulation_scenarios"),
            assignments=assignments,
        ),
        (*fields.values(), scenario_id, UUID(user_id)),
    )


async def create_conversation(user_id: str) -> dict[str, Any]:
    """Open an empty conversation for a user, with a new thread.

    Parameters
    ----------
    user_id : str
        Id of the account in the auth service.

    Returns
    -------
    dict[str, Any]
        Conversation row, plus ``session_id``: the value the client sends to
        /chat to continue this conversation.
    """
    session_id = uuid4()
    thread_id = f"{WEB_THREAD_PREFIX}:{user_id}:{session_id}"
    row = await _fetch_one(
        sql.SQL(
            """
            INSERT INTO {conversations} (user_id, thread_id)
            VALUES (%s, %s)
            RETURNING *
            """
        ).format(conversations=_table("conversations")),
        (UUID(user_id), thread_id),
    )
    return {**row, "session_id": str(session_id)}


async def ensure_conversation(
    user_id: str,
    thread_id: str,
) -> None:
    """Create the conversation of a web thread if it does not exist yet.

    Parameters
    ----------
    user_id : str
        Id of the account in the auth service.
    thread_id : str
        Thread id used by the agent (``web:<user_id>:<uuid>``).

    Returns
    -------
    None
    """
    await _fetch_one(
        sql.SQL(
            """
            INSERT INTO {conversations} (user_id, thread_id)
            VALUES (%s, %s)
            ON CONFLICT (thread_id) DO NOTHING
            RETURNING id
            """
        ).format(conversations=_table("conversations")),
        (UUID(user_id), thread_id),
    )


async def list_conversations(
    user_id: str,
    include_archived: bool,
) -> list[dict[str, Any]]:
    """List the conversations of a user, most recent first.

    Parameters
    ----------
    user_id : str
        Id of the account in the auth service.
    include_archived : bool
        Whether archived conversations are listed too.

    Returns
    -------
    list[dict[str, Any]]
        Conversation rows.
    """
    archived_filter = sql.SQL("") if include_archived else sql.SQL("AND archived_at IS NULL")
    return await _fetch_all(
        sql.SQL(
            "SELECT * FROM {conversations} WHERE user_id = %s {archived} ORDER BY updated_at DESC"
        ).format(conversations=_table("conversations"), archived=archived_filter),
        (UUID(user_id),),
    )


async def get_conversation(
    user_id: str,
    conversation_id: UUID,
) -> Optional[dict[str, Any]]:
    """Read a conversation of the user with its messages in order.

    Parameters
    ----------
    user_id : str
        Id of the account in the auth service.
    conversation_id : UUID
        Conversation to read.

    Returns
    -------
    Optional[dict[str, Any]]
        Conversation row with a ``messages`` list, or None when it does not
        belong to the user.
    """
    conversation = await _fetch_one(
        sql.SQL("SELECT * FROM {conversations} WHERE id = %s AND user_id = %s").format(
            conversations=_table("conversations")
        ),
        (conversation_id, UUID(user_id)),
    )
    if conversation is None:
        return None
    messages = await _fetch_all(
        sql.SQL(
            "SELECT id, position, role, content, command, metadata, created_at "
            "FROM {messages} WHERE conversation_id = %s ORDER BY position"
        ).format(messages=_table("messages")),
        (conversation_id,),
    )
    return {**conversation, "messages": messages}


async def update_conversation(
    user_id: str,
    conversation_id: UUID,
    changes: dict[str, Any],
) -> Optional[dict[str, Any]]:
    """Rename or archive a conversation of the user.

    Parameters
    ----------
    user_id : str
        Id of the account in the auth service.
    conversation_id : UUID
        Conversation to change.
    changes : dict[str, Any]
        ``title`` (text) and/or ``archived`` (bool).

    Returns
    -------
    Optional[dict[str, Any]]
        Conversation row after the change, or None when it was not found.
    """
    assignments = []
    values: list[Any] = []
    if "title" in changes:
        assignments.append(sql.SQL("title = %s"))
        values.append(changes["title"] or DEFAULT_CONVERSATION_TITLE)
    if "archived" in changes:
        assignments.append(sql.SQL("archived_at = CASE WHEN %s THEN now() ELSE NULL END"))
        values.append(bool(changes["archived"]))
    if not assignments:
        return await get_conversation(user_id, conversation_id)
    return await _fetch_one(
        sql.SQL("UPDATE {conversations} SET {assignments} WHERE id = %s AND user_id = %s RETURNING *").format(
            conversations=_table("conversations"),
            assignments=sql.SQL(", ").join(assignments),
        ),
        (*values, conversation_id, UUID(user_id)),
    )


async def record_tool_calls(
    user_id: str,
    calls: list[dict[str, Any]],
) -> int:
    """Store tool calls in the trail of a user.

    A conversation id is kept only when it belongs to the user, so a call
    can never be linked to someone else's conversation.

    Parameters
    ----------
    user_id : str
        Id of the account in the auth service.
    calls : list[dict[str, Any]]
        Calls with ``tool``, ``source``, ``params``, and optionally
        ``conversation_id``, ``result_hash`` and ``called_at``.

    Returns
    -------
    int
        Number of calls stored.
    """
    async with open_connection() as conn:
        async with conn.cursor() as cursor:
            for call in calls:
                await cursor.execute(
                    sql.SQL(
                        """
                        INSERT INTO {tool_calls}
                            (user_id, conversation_id, tool, source, params, result_hash, called_at)
                        VALUES (
                            %s,
                            (SELECT id FROM {conversations} WHERE id = %s AND user_id = %s),
                            %s, %s, %s, %s,
                            COALESCE(%s, now())
                        )
                        """
                    ).format(
                        tool_calls=_table("tool_calls"),
                        conversations=_table("conversations"),
                    ),
                    (
                        UUID(user_id),
                        call.get("conversation_id"),
                        UUID(user_id),
                        call["tool"],
                        call["source"],
                        Jsonb(call.get("params") or {}),
                        call.get("result_hash"),
                        call.get("called_at"),
                    ),
                )
    logger.info("state_store: stored %d tool call(s) for user %s", len(calls), user_id)
    return len(calls)


async def list_tool_calls(
    user_id: str,
    limit: int,
) -> list[dict[str, Any]]:
    """List the most recent tool calls of a user.

    Parameters
    ----------
    user_id : str
        Id of the account in the auth service.
    limit : int
        Maximum number of calls returned.

    Returns
    -------
    list[dict[str, Any]]
        Tool call rows, most recent first.
    """
    return await _fetch_all(
        sql.SQL(
            "SELECT id, conversation_id, tool, source, params, result_hash, called_at "
            "FROM {tool_calls} WHERE user_id = %s ORDER BY called_at DESC LIMIT %s"
        ).format(tool_calls=_table("tool_calls")),
        (UUID(user_id), limit),
    )
