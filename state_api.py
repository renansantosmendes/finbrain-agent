"""HTTP endpoints that load and save the FinBrain state of the signed-in user.

Every route acts on the account in the access token: the user id never
comes from the request. Routes answer 401 without a valid token. Field
limits mirror the CHECK constraints of db/schema.sql.
"""
from datetime import datetime
from typing import Any, Literal, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field

import state_store
import web_auth

router = APIRouter(prefix="/me", tags=["state"])

TabName = Literal["inicio", "dashboards", "homebroker", "brain", "simulacoes", "transparencia"]
MAX_TOOL_CALLS_PER_BATCH = 100


class WorkspacePatch(BaseModel):
    """Fields of the workspace that the client may change; omit to keep."""

    model_config = ConfigDict(extra="forbid")

    current_tab: Optional[TabName] = None
    active_symbol: Optional[str] = Field(default=None, pattern=r"^[A-Z0-9./-]{1,20}$")
    chart_range: Optional[str] = Field(default=None, pattern=r"^[0-9]{1,3}[A-Z]{1,2}$")
    show_ma5: Optional[bool] = None
    show_ma20: Optional[bool] = None
    show_table: Optional[bool] = None
    active_scenario_id: Optional[UUID] = None
    active_conversation_id: Optional[UUID] = None


class ScenarioCreate(BaseModel):
    """A simulation scenario; limits follow simulation-engine.js."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(default="Cenário", min_length=1, max_length=80)
    aporte_mensal: float = Field(default=500, ge=100, le=5000)
    anos: int = Field(default=10, ge=1, le=30)
    retorno_anual_pct: float = Field(default=5.7, ge=-2, le=12)
    volatilidade_anual_pct: float = Field(default=14.5, ge=5, le=30)
    patrimonio_inicial: float = Field(default=0, ge=0, le=100000)


class ScenarioPatch(BaseModel):
    """Fields of a scenario to change; omit to keep."""

    model_config = ConfigDict(extra="forbid")

    name: Optional[str] = Field(default=None, min_length=1, max_length=80)
    aporte_mensal: Optional[float] = Field(default=None, ge=100, le=5000)
    anos: Optional[int] = Field(default=None, ge=1, le=30)
    retorno_anual_pct: Optional[float] = Field(default=None, ge=-2, le=12)
    volatilidade_anual_pct: Optional[float] = Field(default=None, ge=5, le=30)
    patrimonio_inicial: Optional[float] = Field(default=None, ge=0, le=100000)


class ConversationPatch(BaseModel):
    """Title or archive flag of a conversation; omit to keep."""

    model_config = ConfigDict(extra="forbid")

    title: Optional[str] = Field(default=None, max_length=120)
    archived: Optional[bool] = None


class ToolCallIn(BaseModel):
    """One tool call of the trail, as the client observed it."""

    model_config = ConfigDict(extra="forbid")

    tool: str = Field(min_length=1, max_length=80)
    source: str = Field(min_length=1, max_length=80)
    params: dict[str, Any] = Field(default_factory=dict)
    result_hash: Optional[str] = Field(default=None, max_length=128)
    called_at: Optional[datetime] = None
    conversation_id: Optional[UUID] = None


class ToolCallBatch(BaseModel):
    """A batch of tool calls to append to the trail."""

    model_config = ConfigDict(extra="forbid")

    calls: list[ToolCallIn] = Field(min_length=1, max_length=MAX_TOOL_CALLS_PER_BATCH)


class ToolCallsStored(BaseModel):
    """Result of appending calls to the trail."""

    stored: int


async def require_web_user(
    user: Optional[web_auth.WebUser] = Depends(web_auth.optional_web_user),
) -> web_auth.WebUser:
    """Require a verified user and keep their profile up to date.

    Parameters
    ----------
    user : Optional[web_auth.WebUser]
        User behind the bearer token, or None when none was sent.

    Returns
    -------
    web_auth.WebUser
        The verified user.
    """
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Sign in is required.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not user.email:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="The access token has no e-mail.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    display_name = user.name or user.email.split("@", 1)[0]
    await state_store.upsert_user(user.user_id, user.email, display_name)
    return user


def _not_found(detail: str) -> HTTPException:
    """Build the 404 used when a record is missing or belongs to someone else.

    Parameters
    ----------
    detail : str
        Message shown to the client.

    Returns
    -------
    HTTPException
        Exception to raise.
    """
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=detail)


@router.get("/profile", summary="Profile of the signed-in user")
async def read_profile(user: web_auth.WebUser = Depends(require_web_user)) -> dict[str, Any]:
    """Return the profile stored for the signed-in user.

    Parameters
    ----------
    user : web_auth.WebUser
        Verified user.

    Returns
    -------
    dict[str, Any]
        Profile fields.
    """
    return await state_store.get_profile(user.user_id)


@router.get("/state", summary="Workspace where the user stopped")
async def read_workspace(user: web_auth.WebUser = Depends(require_web_user)) -> dict[str, Any]:
    """Return the workspace of the user, with defaults on first use.

    Parameters
    ----------
    user : web_auth.WebUser
        Verified user.

    Returns
    -------
    dict[str, Any]
        Current tab, symbol, range, indicators and active scenario and
        conversation.
    """
    return await state_store.get_workspace(user.user_id)


@router.patch("/state", summary="Save the workspace of the user")
async def update_workspace(
    payload: WorkspacePatch,
    user: web_auth.WebUser = Depends(require_web_user),
) -> dict[str, Any]:
    """Change the fields sent; fields left out keep their stored value.

    Parameters
    ----------
    payload : WorkspacePatch
        Fields to change.
    user : web_auth.WebUser
        Verified user.

    Returns
    -------
    dict[str, Any]
        Workspace after the change.
    """
    changes = payload.model_dump(exclude_unset=True)
    return await state_store.update_workspace(user.user_id, changes)


@router.get("/scenarios", summary="Simulation scenarios of the user")
async def list_scenarios(user: web_auth.WebUser = Depends(require_web_user)) -> list[dict[str, Any]]:
    """List the saved scenarios, most recent first.

    Parameters
    ----------
    user : web_auth.WebUser
        Verified user.

    Returns
    -------
    list[dict[str, Any]]
        Scenario rows.
    """
    return await state_store.list_scenarios(user.user_id)


@router.post("/scenarios", status_code=status.HTTP_201_CREATED, summary="Save a simulation scenario")
async def create_scenario(
    payload: ScenarioCreate,
    user: web_auth.WebUser = Depends(require_web_user),
) -> dict[str, Any]:
    """Create a scenario for the user.

    Parameters
    ----------
    payload : ScenarioCreate
        Parameters of the scenario.
    user : web_auth.WebUser
        Verified user.

    Returns
    -------
    dict[str, Any]
        Scenario created.
    """
    return await state_store.create_scenario(user.user_id, payload.model_dump())


@router.patch("/scenarios/{scenario_id}", summary="Change a simulation scenario")
async def update_scenario(
    scenario_id: UUID,
    payload: ScenarioPatch,
    user: web_auth.WebUser = Depends(require_web_user),
) -> dict[str, Any]:
    """Change fields of a scenario of the user.

    Parameters
    ----------
    scenario_id : UUID
        Scenario to change.
    payload : ScenarioPatch
        Fields to change.
    user : web_auth.WebUser
        Verified user.

    Returns
    -------
    dict[str, Any]
        Scenario after the change.
    """
    row = await state_store.update_scenario(
        user.user_id,
        scenario_id,
        payload.model_dump(exclude_unset=True),
    )
    if row is None:
        raise _not_found("Scenario not found.")
    return row


@router.get("/conversations", summary="Conversations with the FinAgent")
async def list_conversations(
    include_archived: bool = Query(default=False),
    user: web_auth.WebUser = Depends(require_web_user),
) -> list[dict[str, Any]]:
    """List the conversations of the user, most recent first.

    Parameters
    ----------
    include_archived : bool
        Whether archived conversations are listed too.
    user : web_auth.WebUser
        Verified user.

    Returns
    -------
    list[dict[str, Any]]
        Conversation rows (without messages).
    """
    return await state_store.list_conversations(user.user_id, include_archived)


@router.post(
    "/conversations",
    status_code=status.HTTP_201_CREATED,
    summary="Start a new conversation with the FinAgent",
)
async def create_conversation(user: web_auth.WebUser = Depends(require_web_user)) -> dict[str, Any]:
    """Open an empty conversation; send ``session_id`` to /chat to use it.

    Parameters
    ----------
    user : web_auth.WebUser
        Verified user.

    Returns
    -------
    dict[str, Any]
        Conversation row, with its ``session_id``.
    """
    return await state_store.create_conversation(user.user_id)


@router.get("/conversations/{conversation_id}", summary="Read a conversation with its messages")
async def read_conversation(
    conversation_id: UUID,
    user: web_auth.WebUser = Depends(require_web_user),
) -> dict[str, Any]:
    """Return the conversation and its messages, in the order they happened.

    Parameters
    ----------
    conversation_id : UUID
        Conversation to read.
    user : web_auth.WebUser
        Verified user.

    Returns
    -------
    dict[str, Any]
        Conversation row with a ``messages`` list.
    """
    row = await state_store.get_conversation(user.user_id, conversation_id)
    if row is None:
        raise _not_found("Conversation not found.")
    return row


@router.patch("/conversations/{conversation_id}", summary="Rename or archive a conversation")
async def update_conversation(
    conversation_id: UUID,
    payload: ConversationPatch,
    user: web_auth.WebUser = Depends(require_web_user),
) -> dict[str, Any]:
    """Change the title or the archive flag of a conversation.

    Parameters
    ----------
    conversation_id : UUID
        Conversation to change.
    payload : ConversationPatch
        Fields to change.
    user : web_auth.WebUser
        Verified user.

    Returns
    -------
    dict[str, Any]
        Conversation after the change.
    """
    row = await state_store.update_conversation(
        user.user_id,
        conversation_id,
        payload.model_dump(exclude_unset=True),
    )
    if row is None:
        raise _not_found("Conversation not found.")
    return row


@router.post(
    "/tool-calls",
    status_code=status.HTTP_201_CREATED,
    response_model=ToolCallsStored,
    summary="Append tool calls to the trail",
)
async def record_tool_calls(
    payload: ToolCallBatch,
    user: web_auth.WebUser = Depends(require_web_user),
) -> ToolCallsStored:
    """Store a batch of tool calls observed by the client.

    Parameters
    ----------
    payload : ToolCallBatch
        Calls to store.
    user : web_auth.WebUser
        Verified user.

    Returns
    -------
    ToolCallsStored
        Number of calls stored.
    """
    calls = [call.model_dump(exclude_none=True) for call in payload.calls]
    stored = await state_store.record_tool_calls(user.user_id, calls)
    return ToolCallsStored(stored=stored)


@router.get("/tool-calls", summary="Recent tool calls of the user")
async def list_tool_calls(
    limit: int = Query(default=50, ge=1, le=200),
    user: web_auth.WebUser = Depends(require_web_user),
) -> list[dict[str, Any]]:
    """List the most recent tool calls of the user.

    Parameters
    ----------
    limit : int
        Maximum number of calls returned.
    user : web_auth.WebUser
        Verified user.

    Returns
    -------
    list[dict[str, Any]]
        Tool call rows, most recent first.
    """
    return await state_store.list_tool_calls(user.user_id, limit)
