"""Conversation API routes for chat-based RAG."""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel

from src.api.errors import api_error
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select, update
from sqlalchemy.orm import selectinload

from src.api.dependencies import CurrentUser, DBSession, QdrantClient
from src.db.models import Conversation, ConversationMessage, QueryUsage
from src.config import settings
from src.generation.conversational_rag import (
    run_conversational_rag,
    run_conversational_rag_stream,
)
from src.generation.llm_client import get_turn_usage, reset_turn_usage
from src.harness.chat import (
    plan_model_harness_chat_turn,
    run_harness_chat_turn,
    run_harness_chat_turn_stream,
)
from src.harness.commands import list_slash_commands, resolve_slash_command
from src.harness.packs.builder_workflow import PackBuilderWorkflow, is_pack_builder_request
from src.harness.packs.draft_service import PackDraftService
from src.harness.registry import base_capability_registry
from src.harness import HarnessRunNotFoundError, HarnessWorkflowRepository
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

    A command is a planner seed, not a capability grant, so command discovery
    is available to any authenticated chat user.
    """
    del user
    return SlashCommandListResponse(
        commands=[SlashCommandResponse(**command.public()) for command in list_slash_commands()]
    )


def _planner_query(query: str) -> str:
    resolved = resolve_slash_command(query)
    return resolved.planner_query if resolved is not None else query


async def _run_pack_builder_turn(
    *, conversation: Conversation, query: str, db: DBSession, user_id: int
) -> MessageResponse:
    """Persist a builder exchange without invoking planner, retrieval, or RAG."""
    reset_turn_usage()
    start = time.perf_counter()
    service = PackDraftService(db, base_capability_registry())
    _, answer = await PackBuilderWorkflow().run(
        service=service, conversation_id=conversation.id, owner_user_id=user_id, query=query
    )
    latency_ms = int((time.perf_counter() - start) * 1000)
    user_message = ConversationMessage(conversation_id=conversation.id, role="user", content=query)
    assistant_message = ConversationMessage(
        conversation_id=conversation.id, role="assistant", content=answer,
        turn_plan={"mode": "pack_builder", "rag_allowed": False},
    )
    db.add_all([user_message, assistant_message])
    usage = get_turn_usage()
    if usage is not None:
        db.add(
            QueryUsage(
                user_id=user_id,
                interface="conversation",
                query_text=query[:500],
                model_name=usage.model,
                provider=usage.provider,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cost_usd=usage.cost_usd,
                latency_ms=latency_ms,
                llm_ms=usage.latency_ms,
                conversation_id=conversation.id,
            )
        )
    await db.flush()
    await db.commit()
    return MessageResponse(
        id=assistant_message.id, role="assistant", content=answer, status="completed",
        turn_plan=assistant_message.turn_plan, created_at=assistant_message.created_at,
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
    messages: list[MessageResponse] = []
    for message in conversation.messages:
        activities: list[MessageHarnessActivity] = []
        pending_action: MessageHarnessPendingAction | None = None
        if message.workflow_run_id is not None:
            try:
                run = await workflow_repo.get_run(
                    [], message.workflow_run_id, owner_user_id=user.id
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
                    else None
                ),
                harness_activities=activities,
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


@router.post("/{conversation_id}/messages", response_model=MessageResponse, summary="Send a message and get a RAG response")
async def send_message(
    conversation_id: int,
    request: ConversationMessageRequest,
    http_request: Request,
    db: DBSession,
    user: CurrentUser,
    qdrant: QdrantClient,
) -> MessageResponse:
    """Send a message and get a RAG-powered response."""
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

    planner_query = _planner_query(request.query)

    # Auto-title from first user message if conversation has no title yet
    if not conversation.title and not conversation.messages:
        conversation.title = request.query[:80].strip()

    # This is a hard gate: an active draft (or an explicit builder start) never
    # reaches the general planner or RAG pipeline.
    draft_service = PackDraftService(db, base_capability_registry())
    if (
        await draft_service.active_for_conversation(conversation.id, user.id) is not None
        or is_pack_builder_request(planner_query)
    ):
        return await _run_pack_builder_turn(
            conversation=conversation, query=request.query, db=db, user_id=user.id
        )

    user_permissions = get_user_permissions(user)
    # Fresh turn: reset usage tracking so the planner + pipeline calls below
    # are all attributed to this message and nothing leaks in from prior turns.
    reset_turn_usage()
    planner_decision = await plan_model_harness_chat_turn(
        query=planner_query,
        conversation=conversation,
        db=db,
        project_ids=project_ids,
        user_permissions=user_permissions,
        access_token=http_request.cookies.get("access_token"),
    )
    if planner_decision.action != "use_rag":
        return await run_harness_chat_turn(
            conversation=conversation,
            query=planner_query,
            db=db,
            user_id=user.id,
            project_ids=project_ids,
            user_groups=user_groups,
            user_permissions=user_permissions,
            qdrant=qdrant,
            access_token=http_request.cookies.get("access_token"),
            planner_decision=planner_decision,
        )

    # Run conversational RAG pipeline
    response = await run_conversational_rag(
        conversation=conversation,
        query=planner_query,
        user_groups=user_groups,
        qdrant=qdrant,
        db=db,
        top_k=request.top_k,
        threshold=request.threshold,
        user_id=str(user.id),
        project_ids=project_ids,
        images=request.images,
        turn_plan=(
            planner_decision.turn_plan.model_dump(mode="json")
            if planner_decision.turn_plan is not None
            else None
        ),
        enforce_turn_plan=settings.harness_turn_plan_enforced,
    )

    return response


@router.post("/{conversation_id}/messages/stream", summary="Send a message and stream the RAG response via SSE")
async def send_message_stream(
    conversation_id: int,
    request: ConversationMessageRequest,
    http_request: Request,
    db: DBSession,
    user: CurrentUser,
    qdrant: QdrantClient,
) -> StreamingResponse:
    """Send a message and stream the RAG-powered response via SSE.

    Returns Server-Sent Events with:
    - event: step               → optional read-only agent tool progress
    - event: sources             → JSON array of DocumentSource objects
    - event: runtime_tool_call   → backend runtime tool-use loop requested a tool (EP-004 slice 3)
    - event: runtime_tool_result → sanitized result of that tool call (EP-004 slice 3)
    - event: token               → individual LLM tokens (JSON-encoded string), or the runtime loop's
                                    final answer as a single token
    - event: usage               → token counts + cost
    - event: done                → {message_id, latency_ms}
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

    planner_query = _planner_query(request.query)

    # Auto-title from first user message if conversation has no title yet
    if not conversation.title and not conversation.messages:
        conversation.title = request.query[:80].strip()
        await db.flush()

    # Same hard gate as the non-streaming endpoint.  Keep this before model
    # planning so a builder turn cannot be reclassified as document Q&A.
    draft_service = PackDraftService(db, base_capability_registry())
    if (
        await draft_service.active_for_conversation(conversation.id, user.id) is not None
        or is_pack_builder_request(planner_query)
    ):
        async def builder_event_generator():
            yield f"event: workspace\ndata: {json.dumps({'workspace': 'pack', 'status': 'running'})}\n\n"
            response = await _run_pack_builder_turn(
                conversation=conversation, query=request.query, db=db, user_id=user.id
            )
            yield f"event: token\ndata: {json.dumps(response.content)}\n\n"
            yield f"event: done\ndata: {json.dumps({'message_id': response.id, 'latency_ms': 0})}\n\n"
        return StreamingResponse(builder_event_generator(), media_type="text/event-stream")

    user_permissions = get_user_permissions(user)
    # Fresh turn: reset usage tracking so the planner + pipeline calls below
    # are all attributed to this message and nothing leaks in from prior turns.
    reset_turn_usage()
    planner_decision = await plan_model_harness_chat_turn(
        query=planner_query,
        conversation=conversation,
        db=db,
        project_ids=project_ids,
        user_permissions=user_permissions,
        access_token=http_request.cookies.get("access_token"),
    )
    use_harness = planner_decision.action != "use_rag"

    async def event_generator():
        try:
            if use_harness:
                async for event_type, data in run_harness_chat_turn_stream(
                    conversation=conversation,
                    query=planner_query,
                    db=db,
                    user_id=user.id,
                    project_ids=project_ids,
                    user_groups=user_groups,
                    user_permissions=user_permissions,
                    qdrant=qdrant,
                    access_token=http_request.cookies.get("access_token"),
                    planner_decision=planner_decision,
                ):
                    yield f"event: {event_type}\ndata: {data}\n\n"
            else:
                async for event_type, data in run_conversational_rag_stream(
                    conversation=conversation,
                    query=planner_query,
                    user_groups=user_groups,
                    qdrant=qdrant,
                    db=db,
                    top_k=request.top_k,
                    threshold=request.threshold,
                    user_id=str(user.id),
                    project_ids=project_ids,
                    images=request.images,
                    turn_plan=(
                        planner_decision.turn_plan.model_dump(mode="json")
                        if planner_decision.turn_plan is not None
                        else None
                    ),
                    enforce_turn_plan=settings.harness_turn_plan_enforced,
                ):
                    yield f"event: {event_type}\ndata: {data}\n\n"
        except Exception as e:
            logger.error("Conversation stream failed: %s", e, exc_info=True)
            safe_message = "Chat response failed. Please try again."
            failed_msg = ConversationMessage(
                conversation_id=conversation.id,
                role="assistant",
                content="",
                status="failed",
                error_message=safe_message,
            )
            db.add(failed_msg)
            await db.flush()
            await db.commit()
            yield f"event: error\ndata: {json.dumps(safe_message)}\n\n"
            yield f"event: done\ndata: {json.dumps({'message_id': failed_msg.id, 'latency_ms': 0})}\n\n"

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
