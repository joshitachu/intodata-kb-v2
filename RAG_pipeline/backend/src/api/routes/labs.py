"""Labs chat lane: the same public chat contract, a different reasoning loop.

Every request/response schema and every SSE frame here is deliberately identical
to `src/api/routes/conversations.py`. The labs frontend is a clone of the
production chat components, so any divergence in the envelope would show up as a
UI difference and contaminate the only thing this lane exists to measure: whether
the OpenCode loop can answer as well as the production harness over the same
capability executor.

What keeps the lanes apart is the `conversations.lane` column: this route creates
`lane='v2'` and reads only `lane='v2'`. It used to be two hacks instead -- a
`[labs] ` prefix written into the user's own title, and `visible_in_chat=False`
so the production list would skip the row. Both are gone: the prefix broke as
soon as a user typed it, and `visible_in_chat` belongs to the topology surface,
where it means "the user has not promoted this thread", not "other lane".

Old rows still carry the prefix in storage. It is stripped for display at the
API boundary (`schemas.strip_legacy_lane_prefix`) rather than rewritten in the
database, so nobody's title is edited to make a refactor tidy.
"""

from __future__ import annotations

import asyncio
import datetime
import json
import logging
from typing import Any, Final

from fastapi import APIRouter, Query, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import selectinload

from src.api.dependencies import CurrentUser, DBSession
from src.api.errors import api_error
from src.db.models import Conversation, ConversationMessage
from src.db.models.conversation import ConversationLane
from src.harness.agent_runs import PublicAgentEvent
from src.labs.opencode_client import OpenCodeClient, OpenCodeError
from src.labs.runtime import (
    LABS_AGENT,
    LABS_MODEL_ID,
    LABS_PLAN_AGENT,
    LabsRuntimeUnavailable,
    execute_labs_turn,
    labs_turn_claim,
    new_labs_run_id,
)
from src.rbac.permissions import get_user_permissions, get_user_project_ids
from src.schemas import (
    ConversationCreateRequest,
    ConversationDetailResponse,
    ConversationListResponse,
    ConversationMessageRequest,
    ConversationResponse,
    MessageResponse,
    strip_legacy_lane_prefix,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/labs", tags=["labs"])

LABS_COHORT = "labs_opencode"

#: Opt-in prefix that runs one turn on the read-only planning agent.
#:
#: A slash command rather than a request field because
#: :class:`ConversationMessageRequest` is shared with production `/chat`: a field
#: added for this lane would become part of the public chat contract, and the
#: production harness has no planning agent to honour it with. The idiom is the
#: product's own -- `src/harness/commands.py` publishes the discoverable command
#: list, and those commands are likewise carried in the message text rather than
#: in a field.
#:
#: `ConversationMessageRequest.mode` is the deliberate exception to that, and the
#: difference is who has to see it. Plan mode is a thing the user TYPES, so the
#: message text is the honest record of it. Deep dive is a toggle the client
#: holds outside the message, per turn, and it has to survive a retry of the same
#: text -- so it is a field. Production still ignores it; the schema documents
#: that.
LABS_PLAN_COMMAND: Final = "/plan"


# --- Lane-local schemas ----------------------------------------------------


class LabsHealthResponse(BaseModel):
    """Whether the lane's reasoning loop is reachable at all.

    Every field here is read by the labs banner, and the banner's schema mirrors
    this model field for field. That is not a style preference: the two drifted
    once -- the banner declared `status`, `healthy` and `message`, none of which
    this model has ever sent, and typed `detail` as a string while `_probe_opencode`
    filled it with OpenCode's health object. The result was inverted: a reachable
    lane failed the client's validation and rendered red, while an unreachable one
    parsed cleanly and rendered "Lane responding" with the `error` dropped on the
    floor. Adding a field here means adding it there.
    """

    lane: str = LABS_COHORT
    opencode_available: bool
    #: OpenCode's own `/api/health` body, verbatim (`{"healthy": true}` today).
    #: An object, not a sentence -- it is a probe result, not a status line.
    detail: dict[str, Any] | None = None
    error: str | None = None
    #: The model the lane answers with. The whole point of the lane is a
    #: side-by-side against production, so which model is loaded is the single
    #: most useful thing the banner can state, and it is otherwise invisible from
    #: the browser -- it comes from `LABS_MODEL_ID` in the backend environment.
    model: str = LABS_MODEL_ID


# --- Helpers ---------------------------------------------------------------


def _labs_title(raw: str | None) -> str:
    """Store the title the user gave, bounded by the column width.

    No lane marker is added any more -- `Conversation.lane` carries that -- so a
    V2 title is now exactly what a V1 title is: the user's text, truncated.
    """
    return (raw or "").strip()[:200]


def _display_title(conversation: Conversation) -> str:
    """The title as the user sees it, for the lane's own title decisions.

    A thread created before the `lane` column exists is stored with the retired
    `[labs] ` prefix, so an untitled one reads as the bare prefix rather than as
    empty. Auto-titling has to judge emptiness the way the browser displays it,
    or one of those threads keeps a title of nothing forever.
    """
    return (strip_legacy_lane_prefix(conversation.title) or "").strip()


def _select_labs_agent(query: str) -> tuple[str, str]:
    """Split an explicit `/plan ` opt-in off the user's text.

    Returns `(agent, prompt)`. The prefix is the *only* way to leave the default
    agent -- nothing is inferred from the wording of a question, because a mode
    the user did not ask for and cannot see is worse than no mode at all.

    A bare `/plan` with nothing after it stays on the default agent and keeps its
    text verbatim. Stripping it would send an empty prompt, and guessing that the
    user meant "plan something" is exactly the inference the paragraph above
    rules out. Case-insensitive, because the client echoes what was typed.
    """

    # `split(maxsplit=1)` rather than a `startswith` on `"/plan "`: it treats a
    # tab or a newline after the command as the separator it plainly is, and it
    # cannot match a longer command that merely starts with these characters
    # (`/planning`), which a prefix test would.
    parts = query.lstrip().split(maxsplit=1)
    if len(parts) == 2 and parts[0].lower() == LABS_PLAN_COMMAND:
        prompt = parts[1].strip()
        if prompt:
            return LABS_PLAN_AGENT, prompt
    return LABS_AGENT, query


def _conversation_response(conversation: Conversation, message_count: int) -> ConversationResponse:
    return ConversationResponse(
        id=conversation.id,
        title=conversation.title,
        message_count=message_count,
        origin_surface=conversation.origin_surface,
        visible_in_chat=conversation.visible_in_chat,
        lane=conversation.lane,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
    )


def _message_response(message: ConversationMessage) -> MessageResponse:
    return MessageResponse(
        id=message.id,
        role=message.role,
        content=message.content,
        sources=[],
        latency_ms=message.latency_ms,
        status=message.status,
        error_message=message.error_message,
        structured_answer=(
            message.structured_answer
            if isinstance(message.structured_answer, dict)
            else None
        ),
        created_at=message.created_at,
    )


async def _load_labs_conversation(
    db: DBSession, user: CurrentUser, conversation_id: int
) -> Conversation:
    result = await db.execute(
        select(Conversation)
        .where(Conversation.id == conversation_id, Conversation.deleted_at.is_(None))
        .options(selectinload(Conversation.messages))
    )
    conversation = result.scalar_one_or_none()

    if conversation is None:
        raise api_error(404, "not_found", "Conversation not found")
    if conversation.user_id != user.id:
        raise api_error(403, "forbidden", "Not authorized")
    if conversation.lane != ConversationLane.V2:
        # A production conversation reached through the labs path. Reported as
        # missing rather than forbidden: it does not exist in this lane.
        raise api_error(404, "not_found", "Conversation not found")
    return conversation


# Mirrors `conversations._format_public_sse`. Duplicated rather than imported so
# the labs lane cannot silently change the production framing, and vice versa.
def _format_public_sse(event: PublicAgentEvent) -> str:
    return (
        f"id: {event.event_id}\n"
        f"event: {event.type}\n"
        f"data: {event.model_dump_json()}\n\n"
    )


def _public_event(
    *, run_id: str, sequence: int, event_type: str, payload: dict[str, Any]
) -> PublicAgentEvent:
    return PublicAgentEvent(
        event_id=f"{run_id}:evt:{sequence:06d}",
        run_id=run_id,
        sequence=sequence,
        type=event_type,
        created_at=datetime.datetime.now(datetime.UTC),
        public_payload=payload,
        # Labs streams live prose once and never replays it, which is what puts
        # the client's delta buffer in "live" mode -- the same mode production
        # uses for provisional text.
        replayable=False,
    )


# No `structured_answer` is persisted for a labs turn.
#
# The production harness constructs a grounded envelope -- answer status,
# citations, evidence refs, disclosures -- and validates it. This lane
# deliberately does not: it is an agent that answers, like a coding assistant,
# and the citation contract was harness machinery carried over rather than
# something the lane needs. The message content and `runtime_tool_summary` (what
# it actually called) are the record.
#
# `structured_answer` is optional on the API and nullable in the frontend schema,
# so leaving it unset renders the answer as plain prose.


async def _probe_opencode() -> LabsHealthResponse:
    """One reachability probe, shared by /labs/health and the stream preflight."""

    try:
        async with OpenCodeClient() as client:
            detail = await client.health()
        return LabsHealthResponse(
            opencode_available=True,
            detail=detail if isinstance(detail, dict) else {"status": str(detail)},
        )
    except (OpenCodeError, OSError, TimeoutError) as exc:
        logger.error("Labs OpenCode health probe failed: %s", exc, exc_info=True)
        return LabsHealthResponse(opencode_available=False, error=str(exc)[:300])


# --- Endpoints -------------------------------------------------------------


@router.get(
    "/health",
    response_model=LabsHealthResponse,
    summary="Report whether the labs OpenCode lane is reachable",
)
async def labs_health(user: CurrentUser) -> LabsHealthResponse:
    del user
    return await _probe_opencode()


@router.get(
    "/conversations",
    response_model=ConversationListResponse,
    summary="List labs conversations",
)
async def list_labs_conversations(
    db: DBSession,
    user: CurrentUser,
    skip: int = 0,
    limit: int = Query(default=50, ge=1, le=100),
) -> ConversationListResponse:
    """List the caller's labs conversations, most recently updated first."""
    labs_filter = (
        Conversation.user_id == user.id,
        Conversation.deleted_at.is_(None),
        Conversation.lane == ConversationLane.V2,
    )
    total_result = await db.execute(select(func.count(Conversation.id)).where(*labs_filter))
    total = total_result.scalar_one()

    result = await db.execute(
        select(Conversation)
        .where(*labs_filter)
        .order_by(Conversation.updated_at.desc())
        .offset(skip)
        .limit(limit)
        .options(selectinload(Conversation.messages))
    )
    conversations = result.scalars().all()

    return ConversationListResponse(
        conversations=[
            _conversation_response(conversation, len(conversation.messages))
            for conversation in conversations
        ],
        total=total,
    )


@router.post(
    "/conversations",
    response_model=ConversationResponse,
    status_code=201,
    summary="Create a labs conversation",
)
async def create_labs_conversation(
    db: DBSession,
    user: CurrentUser,
    request: ConversationCreateRequest | None = None,
) -> ConversationResponse:
    """Create a labs conversation.

    `lane='v2'` is the whole marker, and it is set here and never again: this
    thread will own an OpenCode session carrying its own history, so moving it to
    the V1 lane later would hand LangGraph a conversation it has never seen.

    `origin_surface='chat'` and `visible_in_chat=True` because that is the truth
    about this thread -- it was created on the chat surface and it belongs in the
    one conversation list, badged with its lane. Those two fields describe where
    a thread lives in the UI; the lane describes who answers it.

    `request.lane` is ignored rather than validated: the route IS the lane, so
    there is nothing for a client to choose here.
    """
    conversation = Conversation(
        user_id=user.id,
        title=_labs_title(request.title if request else None),
        origin_surface="chat",
        visible_in_chat=True,
        lane=ConversationLane.V2,
    )
    db.add(conversation)
    await db.flush()

    return _conversation_response(conversation, 0)


@router.get(
    "/conversations/{conversation_id}",
    response_model=ConversationDetailResponse,
    summary="Get a labs conversation with its messages",
)
async def get_labs_conversation(
    conversation_id: int,
    db: DBSession,
    user: CurrentUser,
) -> ConversationDetailResponse:
    conversation = await _load_labs_conversation(db, user, conversation_id)
    return ConversationDetailResponse(
        id=conversation.id,
        title=conversation.title,
        messages=[_message_response(message) for message in conversation.messages],
        origin_surface=conversation.origin_surface,
        visible_in_chat=conversation.visible_in_chat,
        lane=conversation.lane,
        agent_plan=None,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
    )


@router.delete(
    "/conversations/{conversation_id}",
    status_code=204,
    summary="Delete a labs conversation and its messages",
)
async def delete_labs_conversation(
    conversation_id: int,
    db: DBSession,
    user: CurrentUser,
) -> Response:
    """Delete a labs conversation.

    `_load_labs_conversation` is the ownership and lane gate: it 404s on a
    conversation this user does not own or that is not in the labs lane, so this
    route can never reach a production conversation.
    """

    conversation = await _load_labs_conversation(db, user, conversation_id)
    await db.delete(conversation)
    await db.commit()
    # NOTE: the runtime's in-process conversation->OpenCode-session binding is
    # left behind. Conversation ids are a Postgres sequence and are never
    # reused, so the entry is unreachable rather than stale; it costs one dict
    # entry until the process restarts.
    return Response(status_code=204)


@router.post(
    "/conversations/{conversation_id}/messages/stream",
    summary="Send a message to the labs lane and stream the response via SSE",
)
async def send_labs_message_stream(
    conversation_id: int,
    request: ConversationMessageRequest,
    http_request: Request,
    db: DBSession,
    user: CurrentUser,
) -> StreamingResponse:
    """Run one OpenCode turn and project it as the production SSE envelope.

    Frames are `PublicAgentEvent` exactly as `/chat` emits them:
    - event: run.started          -> lane and objective
    - event: runtime.update       -> a non-default agent mode, or a degraded turn
    - event: assistant.delta      -> answer prose, provisional (replayable=false)
    - event: assistant.reasoning  -> the model's chain of thought, lane-only
    - event: plan.updated         -> the agent's step list, lane-only `todos`
    - event: tool.running         -> an MCP capability call started
    - event: tool.completed       -> an MCP capability call finished
    - event: assistant.completed  -> the persisted assistant message id
    - event: run.completed        -> terminal metadata incl. the grounding verdict
    - event: done                 -> {message_id, latency_ms, run_id, replayed}

    `assistant.reasoning` is the one frame with no production counterpart, and
    `plan.updated` carries one field production does not define (`todos`). Both
    reach the client because this route frames whatever the runtime emits:
    `_public_event` builds a `PublicAgentEvent` directly, whose `type` is a plain
    `str`, and the payload never passes through `sanitize_public_payload`. The
    production path is the opposite -- `AgentRunService.append_public_event`
    rejects any type absent from `_PUBLIC_EVENT_FIELDS`, and its `plan.updated`
    entry admits only `{plan_id, version, status, summary}` -- so both additions
    are expressible here and only here. Every *other* frame nonetheless keeps to
    the production vocabulary on purpose; these two are additions, not a licence
    to diverge.

    Prefixing a message with `/plan ` runs the turn on the read-only planning
    agent (:data:`LABS_PLAN_COMMAND`).
    """
    conversation = await _load_labs_conversation(db, user, conversation_id)

    # The mode is decided from the message text, before anything is persisted or
    # streamed, so the title, the prompt and the turn all agree on one reading of
    # what the user asked for.
    agent, prompt = _select_labs_agent(request.query)

    project_ids = await get_user_project_ids(user, db)
    if request.project_id:
        if project_ids is not None and request.project_id not in project_ids:
            raise api_error(403, "forbidden", "No access to this project")
        project_ids = [request.project_id]

    # Reachability is checked before the response starts streaming: once the
    # first byte is out, 503 is no longer expressible.
    probe = await _probe_opencode()
    if not probe.opencode_available:
        raise api_error(
            503,
            "opencode_unavailable",
            probe.error or "The labs OpenCode server is not reachable.",
        )

    # Auto-title from the first user message, same as `/chat`. Only an untitled
    # conversation is renamed -- see `_display_title` for why emptiness is judged
    # on the displayed title. Titled from the stripped prompt: a title is derived
    # text, and "/plan how does..." names the command rather than the subject.
    if not conversation.messages and not _display_title(conversation):
        conversation.title = _labs_title(prompt[:80])

    # Persisted verbatim, prefix included. This row is the record of what the
    # user typed, and rewriting it would show them a message they did not send
    # and erase the only evidence of why the answer came back as a plan.
    user_message = ConversationMessage(
        conversation_id=conversation_id,
        role="user",
        content=request.query,
    )
    db.add(user_message)
    await db.flush()
    await db.commit()

    run_id = new_labs_run_id()
    user_groups = [role.name for role in user.roles]
    permissions = get_user_permissions(user)
    access_token = http_request.cookies.get("access_token")

    async def event_generator():
        # The claim is taken here, not before the response begins: it is the
        # Conversation State lock for this turn, so it must be acquired after
        # the user message is durable (it pins that message id) and released on
        # every exit path, which only a `finally` around the whole turn gives.
        # A contended claim is not an error -- the turn runs read-only, exactly
        # as the lane behaved before claims existed.
        async with labs_turn_claim(
            db=db,
            conversation_id=conversation_id,
            user_id=user.id,
            message_id=user_message.id,
            # The turn boundary is also where the evidence mode is published for
            # the MCP bridge to read. The bridge answers `tools/list` in its own
            # request and can only see per-conversation state, so a mode passed
            # as an argument to the turn would never reach the tool menu.
            mode=request.mode,
        ) as claim_guard:
            queue: asyncio.Queue[tuple[str, dict[str, Any]]] = asyncio.Queue()

            async def emit(event_type: str, payload: dict[str, Any]) -> None:
                await queue.put((event_type, payload))

            task = asyncio.create_task(
                execute_labs_turn(
                    db=db,
                    conversation=conversation,
                    user_id=user.id,
                    user_groups=user_groups,
                    project_scope=project_ids,
                    permissions=permissions,
                    access_token=access_token,
                    # The stripped text: the model is asked the question, not the
                    # command that chose which agent answers it.
                    message=prompt,
                    emit=emit,
                    # Already on the request schema and already sent by the
                    # production ChatInput the labs page reuses; the lane simply
                    # never forwarded them.
                    images=tuple(request.images or ()),
                    agent=agent,
                    # Per turn, and deliberately not read back off the
                    # conversation: the same thread asks an ordinary question
                    # and then a deep one, so the toggle the user held for THIS
                    # message is the only correct source.
                    mode=request.mode,
                )
            )
            sequence = 0
            try:
                while not task.done() or not queue.empty():
                    try:
                        event_type, payload = await asyncio.wait_for(
                            queue.get(), timeout=0.1
                        )
                    except TimeoutError:
                        continue
                    sequence += 1
                    yield _format_public_sse(
                        _public_event(
                            run_id=run_id,
                            sequence=sequence,
                            event_type=event_type,
                            payload=payload,
                        )
                    )

                try:
                    result = await task
                except (LabsRuntimeUnavailable, OSError, RuntimeError) as exc:
                    # A turn that dies must still terminate the stream. Without a
                    # terminal frame the client sits in "streaming" until the socket
                    # closes and reports nothing about why.
                    stop_reason = (
                        "opencode_unavailable"
                        if isinstance(exc, LabsRuntimeUnavailable)
                        else "runtime_failed"
                    )
                    claim_guard.mark_failed(stop_reason)
                    # The turn is over, but it may already have created the
                    # conversation's OpenCode session and staged its id on the
                    # ORM object. This path returns before the commit below, so
                    # without this the id is discarded and the next turn forks a
                    # SECOND top-level session for the same conversation --
                    # splitting the conversation's history on the OpenCode side,
                    # which is exactly what `opencode_session_id` exists to stop.
                    # Only the first turn of a conversation can create a session,
                    # so this is the only path where it can be lost.
                    try:
                        await db.commit()
                    except SQLAlchemyError:
                        logger.error(
                            "Could not persist the labs session binding for "
                            "conversation %s after a failed turn",
                            conversation_id,
                            exc_info=True,
                        )
                        await db.rollback()
                    logger.error(
                        "Labs turn failed (%s): %s", stop_reason, exc, exc_info=True
                    )
                    sequence += 1
                    yield _format_public_sse(
                        _public_event(
                            run_id=run_id,
                            sequence=sequence,
                            event_type="run.failed",
                            payload={
                                "status": "failed",
                                "stop_reason": stop_reason,
                                "summary": str(exc)[:200],
                                "latency_ms": 0,
                            },
                        )
                    )
                    yield f"event: error\ndata: {json.dumps(str(exc))}\n\n"
                    return

                if not result.answer_text:
                    # No prose at all: the turn produced nothing to attribute to
                    # Conversation State, so the claim is released as failed
                    # unless a capability already committed an effect.
                    claim_guard.mark_failed("runtime_failed")

                assistant_message = ConversationMessage(
                    conversation_id=conversation_id,
                    role="assistant",
                    content=result.answer_text,
                    latency_ms=result.latency_ms,
                    status="completed" if result.answer_text else "failed",
                    runtime_tool_summary=[
                        {
                            "capability_id": record.capability_id,
                            "status": "succeeded" if record.succeeded else "failed",
                            "evidence_count": len(record.evidence_refs),
                            "error_code": record.error_code,
                        }
                        for record in result.tool_calls
                    ],
                )
                db.add(assistant_message)
                await db.flush()
                await db.commit()

                sequence += 1
                yield _format_public_sse(
                    _public_event(
                        run_id=run_id,
                        sequence=sequence,
                        event_type="assistant.completed",
                        payload={
                            "message_id": assistant_message.id,
                            "status": assistant_message.status,
                        },
                    )
                )
                sequence += 1
                yield _format_public_sse(
                    _public_event(
                        run_id=run_id,
                        sequence=sequence,
                        event_type="run.completed",
                        payload={
                            "status": "completed",
                            "stop_reason": "answered",
                            # `capability_calls`, not `tool_calls`: these records
                            # are Nexus capability invocations only, while the
                            # client's own tool count comes off the `tool.*`
                            # frames and covers every OpenCode tool the turn used
                            # (`read`, `todowrite`, `task`, ...). Two different
                            # true numbers. Under the old key the turn header
                            # showed both unlabelled, disagreeing, and neither
                            # explained.
                            "usage": {"capability_calls": len(result.tool_calls)},
                            "latency_ms": result.latency_ms,
                        },
                    )
                )
                yield (
                    "event: done\ndata: "
                    + json.dumps(
                        {
                            "message_id": assistant_message.id,
                            "latency_ms": result.latency_ms,
                            "run_id": run_id,
                            "replayed": False,
                        }
                    )
                    + "\n\n"
                )
            except asyncio.CancelledError:
                # Same contract as the production stream: the turn owns the OpenCode
                # session, so it must settle even if the client walked away.
                claim_guard.mark_failed("client_disconnected")
                await asyncio.shield(task)
                raise

    return StreamingResponse(event_generator(), media_type="text/event-stream")
