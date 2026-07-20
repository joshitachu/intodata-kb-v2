"""Conversation API routes for chat-based RAG."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel

from src.api.errors import api_error
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select, update
from sqlalchemy.orm import selectinload

from src.api.dependencies import CurrentUser, DBSession, QdrantClient
from src.db.models import Conversation, ConversationMessage
from src.harness.commands import list_slash_commands
from src.harness import HarnessRunNotFoundError, HarnessWorkflowRepository
from src.harness.workflow_projection import AuthoritativeWorkflowCardProjector
from src.harness.response_synthesis import TypedAgentResponse
from src.harness.agent_chat_runtime import (
    AgentChatIdempotencyConflict,
    execute_production_agent_chat_turn,
)
from src.harness.agent_loop import stream_transport
from src.rbac.permissions import get_user_permissions, get_user_project_ids
from src.schemas import (
    ConversationCreateRequest,
    ConversationDetailResponse,
    ConversationListResponse,
    ConversationMessageRequest,
    ConversationResponse,
    ConversationUpdateRequest,
    DocumentSource,
    MessageResponse,
    MessageHarnessActivity,
    MessageHarnessActionChoice,
    MessageHarnessPendingAction,
    SlashCommandListResponse,
    SlashCommandResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/conversations", tags=["conversations"])


def _deserialize_sources(raw: list[dict]) -> list[DocumentSource]:
    """Deserialize sources from DB, handling both old Source and new DocumentSource formats."""
    result = []
    for i, s in enumerate(raw, 1):
        result.append(DocumentSource(
            document_id=s["document_id"],
            filename=s["filename"],
            score=s["score"],
            text=s["text"],
            reference_number=s.get("reference_number", i),
            chunk_indexes=s.get("chunk_indexes", []),
            document_role=s.get("document_role", "source"),
            source_layer=s.get("source_layer"),
            section=s.get("section"),
            unit=s.get("unit"),
            group=s.get("group"),
            display_name=s.get("display_name"),
            artifact_path=s.get("artifact_path"),
            visibility=s.get("visibility"),
            run_id=s.get("run_id"),
            project_id=s.get("project_id"),
        ))
    return result


@router.get("/commands", response_model=SlashCommandListResponse, summary="List chat slash commands")
async def list_commands(user: CurrentUser) -> SlashCommandListResponse:
    """Expose only user-facing command discovery metadata.

    Commands are presentation and model-context hints only. They never grant a
    capability, select an execution path, or rewrite the submitted message.
    """
    del user
    return SlashCommandListResponse(
        commands=[SlashCommandResponse(**command.public()) for command in list_slash_commands()]
    )


@router.get("", response_model=ConversationListResponse, summary="List conversations")
async def list_conversations(
    db: DBSession,
    user: CurrentUser,
    skip: int = 0,
    limit: int = Query(default=50, ge=1, le=100),
) -> ConversationListResponse:
    """List user's conversations, sorted by most recently updated."""
    # Count total
    total_result = await db.execute(
        select(func.count(Conversation.id))
        .where(Conversation.user_id == user.id, Conversation.deleted_at.is_(None))
    )
    total = total_result.scalar_one()

    # Get conversations with message counts
    result = await db.execute(
        select(Conversation)
        .where(Conversation.user_id == user.id, Conversation.deleted_at.is_(None))
        .order_by(Conversation.updated_at.desc())
        .offset(skip)
        .limit(limit)
        .options(selectinload(Conversation.messages))
    )
    conversations = result.scalars().all()

    return ConversationListResponse(
        conversations=[
            ConversationResponse(
                id=c.id,
                title=c.title,
                message_count=len(c.messages),
                created_at=c.created_at,
                updated_at=c.updated_at,
            )
            for c in conversations
        ],
        total=total,
    )


@router.post("", response_model=ConversationResponse, status_code=201, summary="Create a conversation")
async def create_conversation(
    db: DBSession,
    user: CurrentUser,
    request: ConversationCreateRequest | None = None,
) -> ConversationResponse:
    """Create a new conversation."""
    title = request.title if request else None
    conversation = Conversation(user_id=user.id, title=title)
    db.add(conversation)
    await db.flush()

    return ConversationResponse(
        id=conversation.id,
        title=conversation.title,
        message_count=0,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
    )


@router.get("/{conversation_id}", response_model=ConversationDetailResponse, summary="Get a conversation with its messages")
async def get_conversation(
    conversation_id: int,
    db: DBSession,
    user: CurrentUser,
    http_request: Request = None,
) -> ConversationDetailResponse:
    """Get a conversation with all messages."""
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

    workflow_repo = HarnessWorkflowRepository(db)
    project_scope = await get_user_project_ids(user, db)
    message_run_ids: dict[int, list[int]] = {}
    for message in conversation.messages:
        run_ids: list[int] = []
        if message.workflow_run_id is not None:
            run_ids.append(message.workflow_run_id)
        if (
            isinstance(message.structured_answer, dict)
            and message.structured_answer.get("response_type") == "agent_response_v1"
        ):
            try:
                stored_response = TypedAgentResponse.model_validate(
                    message.structured_answer
                )
            except ValueError:
                stored_response = None
            if stored_response is not None:
                run_ids.extend(
                    card.workflow_run_id for card in stored_response.workflow_cards
                )
        message_run_ids[message.id] = list(dict.fromkeys(run_ids))[:20]
    linked_run_ids = list(
        dict.fromkeys(
            run_id
            for message in reversed(conversation.messages)
            for run_id in message_run_ids[message.id]
        )
    )[:20]
    cards = await AuthoritativeWorkflowCardProjector(workflow_repo).project_many(
        linked_run_ids,
        owner_user_id=user.id,
        conversation_id=conversation.id,
        project_scope=project_scope,
        access_token=(
            http_request.cookies.get("access_token") if http_request is not None else None
        ),
        reconcile=True,
    )
    card_by_run_id = {card.workflow_run_id: card for card in cards}
    if linked_run_ids:
        await db.commit()
    messages: list[MessageResponse] = []
    for message in conversation.messages:
        activities: list[MessageHarnessActivity] = []
        pending_action: MessageHarnessPendingAction | None = None
        if message.workflow_run_id is not None:
            try:
                run = await workflow_repo.get_run(
                    project_scope, message.workflow_run_id, owner_user_id=user.id
                )
                run_activities = await workflow_repo.list_activity_events(
                    None if run.project_id is None else [run.project_id], run.id
                )
                activities = [
                    MessageHarnessActivity(
                        workflow_run_id=str(run.id),
                        event_type=item.event_type,
                        label=item.label,
                        status=item.status.value,
                        summary=item.summary,
                        safe_details=_safe_activity_details(item.safe_details),
                        timestamp=item.occurred_at,
                        workflow_status=run.status.value,
                        workflow_started_at=run.started_at,
                        workflow_finished_at=run.finished_at,
                    )
                    for item in run_activities
                ]
                interrupts = await workflow_repo.list_pending_interrupts_for_owner(
                    run.id, user.id
                )
                if interrupts:
                    interrupt = interrupts[0]
                    interrupt_metadata = run.runtime_metadata.get("interrupt")
                    missing_slots = (
                        interrupt_metadata.get("missing_slots", [])
                        if isinstance(interrupt_metadata, dict)
                        else []
                    )
                    run_arguments = run.runtime_metadata.get("arguments")
                    safe_arguments = run_arguments if isinstance(run_arguments, dict) else {}
                    pending_action = MessageHarnessPendingAction(
                        workflow_run_id=run.id,
                        interrupt_id=interrupt.id,
                        expected_version=run.version,
                        kind=interrupt.interrupt_type.value,
                        prompt=interrupt.prompt,
                        choices=[
                            MessageHarnessActionChoice(
                                label=str(option.get("label") or option.get("value") or "Choice"),
                                value=str(option.get("value") or ""),
                            )
                            for option in interrupt.options
                            if isinstance(option, dict) and option.get("value")
                        ],
                        required_fields=[
                            str(field)
                            for field in missing_slots
                            if isinstance(field, str)
                        ],
                        project_id=(
                            str(safe_arguments["project_id"])
                            if safe_arguments.get("project_id")
                            else run.project_id
                        ),
                        platform=(
                            str(safe_arguments["platform"])
                            if safe_arguments.get("platform")
                            else None
                        ),
                        expires_at=interrupt.expires_at,
                    )
            except HarnessRunNotFoundError:
                logger.warning(
                    "Ignoring inaccessible workflow %s linked to message %s",
                    message.workflow_run_id,
                    message.id,
                )
        messages.append(
            MessageResponse(
                id=message.id,
                role=message.role,
                content=message.content,
                sources=_deserialize_sources(message.sources) if message.sources else None,
                image_data=message.image_data,
                latency_ms=message.latency_ms,
                status=message.status,
                error_message=message.error_message,
                primary_source_layer=message.primary_source_layer,
                fallback_used=message.fallback_used,
                doc_gap=message.doc_gap,
                workflow_run_id=message.workflow_run_id,
                turn_plan=(message.turn_plan if isinstance(message.turn_plan, dict) else None),
                structured_answer=(
                    message.structured_answer
                    if isinstance(message.structured_answer, dict)
                    and message.structured_answer.get("response_type")
                    == "agent_response_v1"
                    else None
                ),
                harness_activities=activities,
                workflow_cards=(
                    [
                        card_by_run_id[run_id]
                        for run_id in message_run_ids[message.id]
                        if run_id in card_by_run_id
                    ]
                ),
                pending_action=pending_action,
                created_at=message.created_at,
            )
        )

    return ConversationDetailResponse(
        id=conversation.id,
        title=conversation.title,
        messages=messages,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
    )


def _safe_activity_details(raw: dict) -> dict:
    safe: dict = {}
    for key, value in raw.items():
        if isinstance(value, (str, int, float, bool)) or value is None:
            safe[str(key)[:100]] = value
        elif isinstance(value, list) and all(
            isinstance(item, (str, int, float, bool)) or item is None for item in value
        ):
            safe[str(key)[:100]] = value[:20]
    return safe


def _agent_message_response(execution) -> MessageResponse:  # noqa: ANN001
    message = execution.assistant_message
    typed = execution.result.typed_response
    return MessageResponse(
        id=message.id,
        role=message.role,
        content=message.content,
        latency_ms=message.latency_ms,
        status=message.status,
        workflow_run_id=message.workflow_run_id,
        structured_answer=message.structured_answer,
        workflow_cards=(list(typed.workflow_cards) if typed is not None else []),
        created_at=message.created_at,
    )


@router.patch("/{conversation_id}", response_model=ConversationResponse, summary="Rename a conversation")
async def update_conversation(
    conversation_id: int,
    request: ConversationUpdateRequest,
    db: DBSession,
    user: CurrentUser,
) -> ConversationResponse:
    """Update conversation title."""
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

    conversation.title = request.title

    return ConversationResponse(
        id=conversation.id,
        title=conversation.title,
        message_count=len(conversation.messages),
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
    )


@router.delete("/{conversation_id}", status_code=204, summary="Delete a conversation and its messages")
async def delete_conversation(
    conversation_id: int,
    db: DBSession,
    user: CurrentUser,
) -> None:
    """Soft-delete a conversation and all its messages."""
    result = await db.execute(
        select(Conversation)
        .where(Conversation.id == conversation_id, Conversation.deleted_at.is_(None))
    )
    conversation = result.scalar_one_or_none()

    if conversation is None:
        raise api_error(404, "not_found", "Conversation not found")

    if conversation.user_id != user.id:
        raise api_error(403, "forbidden", "Not authorized")

    now = datetime.now(timezone.utc)
    conversation.deleted_at = now
    # Cascade soft-delete to all messages
    await db.execute(
        update(ConversationMessage)
        .where(
            ConversationMessage.conversation_id == conversation_id,
            ConversationMessage.deleted_at.is_(None),
        )
        .values(deleted_at=now)
    )


@router.post("/{conversation_id}/messages", response_model=MessageResponse, summary="Send a message to the chat agent")
async def send_message(
    conversation_id: int,
    request: ConversationMessageRequest,
    http_request: Request,
    db: DBSession,
    user: CurrentUser,
    qdrant: QdrantClient,
) -> MessageResponse:
    """Send a message through the authoritative bounded agent loop."""
    # Load conversation with messages
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

    # Get user's groups and project access for RBAC filtering
    user_groups = [role.name for role in user.roles]
    project_ids = await get_user_project_ids(user, db)
    if request.project_id:
        if project_ids is not None and request.project_id not in project_ids:
            raise api_error(403, "forbidden", "No access to this project")
        project_ids = [request.project_id]

    # Auto-title from the first user message. The agent runtime persists the turn.
    if not conversation.title and not conversation.messages:
        conversation.title = request.query[:80].strip()
    try:
        execution = await execute_production_agent_chat_turn(
            db=db,
            qdrant=qdrant,
            conversation=conversation,
            query=request.query,
            images=request.images,
            client_turn_id=request.client_turn_id,
            requested_project_id=request.project_id,
            user_id=user.id,
            user_groups=user_groups,
            project_scope=project_ids,
            permissions=get_user_permissions(user),
            access_token=http_request.cookies.get("access_token"),
            transport="nonstream",
        )
    except AgentChatIdempotencyConflict as exc:
        raise api_error(409, "idempotency_conflict", str(exc)) from exc
    return _agent_message_response(execution)


@router.post("/{conversation_id}/messages/stream", summary="Send a message and stream the chat agent response via SSE")
async def send_message_stream(
    conversation_id: int,
    request: ConversationMessageRequest,
    http_request: Request,
    db: DBSession,
    user: CurrentUser,
    qdrant: QdrantClient,
) -> StreamingResponse:
    """Send a message through the authoritative agent and project it as SSE.

    Returns Server-Sent Events with:
    - event: agent_trace       → bounded reasoning-loop progress metadata
    - event: agent_observation → typed capability observations
    - event: agent_response    → the one visible typed answer
    - event: agent_final       → terminal transport metadata
    - event: done              → persisted message metadata
    """
    # Load conversation with messages
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

    # Get user's groups and project access for RBAC filtering
    user_groups = [role.name for role in user.roles]
    project_ids = await get_user_project_ids(user, db)
    if request.project_id:
        if project_ids is not None and request.project_id not in project_ids:
            raise api_error(403, "forbidden", "No access to this project")
        project_ids = [request.project_id]

    # Auto-title from the first user message. Both public transports execute
    # the same turn and differ only in their response projection.
    if not conversation.title and not conversation.messages:
        conversation.title = request.query[:80].strip()
    try:
        execution = await execute_production_agent_chat_turn(
            db=db,
            qdrant=qdrant,
            conversation=conversation,
            query=request.query,
            images=request.images,
            client_turn_id=request.client_turn_id,
            requested_project_id=request.project_id,
            user_id=user.id,
            user_groups=user_groups,
            project_scope=project_ids,
            permissions=get_user_permissions(user),
            access_token=http_request.cookies.get("access_token"),
            transport="stream",
        )
    except AgentChatIdempotencyConflict as exc:
        raise api_error(409, "idempotency_conflict", str(exc)) from exc

    async def event_generator():
        async for event_type, data in stream_transport(execution.result):
            yield f"event: {event_type}\ndata: {data}\n\n"
        yield (
            "event: done\ndata: "
            + json.dumps(
                {
                    "message_id": execution.assistant_message.id,
                    "latency_ms": execution.latency_ms,
                }
            )
            + "\n\n"
        )

    return StreamingResponse(event_generator(), media_type="text/event-stream")


class SaveExchangeRequest(BaseModel):
    user_message: str
    assistant_message: str
    latency_ms: int | None = None


@router.post("/{conversation_id}/messages/save", response_model=MessageResponse, summary="Save a pre-generated exchange")
async def save_exchange(
    conversation_id: int,
    request: SaveExchangeRequest,
    db: DBSession,
    user: CurrentUser,
) -> MessageResponse:
    """Save a pre-generated Q&A exchange (e.g. Deep Dive) without running RAG."""
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

    if not conversation.title and not conversation.messages:
        conversation.title = request.user_message[:80].strip()

    user_msg = ConversationMessage(
        conversation_id=conversation_id,
        role="user",
        content=request.user_message,
    )
    db.add(user_msg)
    await db.flush()

    assistant_msg = ConversationMessage(
        conversation_id=conversation_id,
        role="assistant",
        content=request.assistant_message,
        latency_ms=request.latency_ms,
    )
    db.add(assistant_msg)
    await db.flush()
    await db.commit()

    return MessageResponse(
        message_id=assistant_msg.id,
        content=request.assistant_message,
        sources=[],
        latency_ms=request.latency_ms or 0,
    )
