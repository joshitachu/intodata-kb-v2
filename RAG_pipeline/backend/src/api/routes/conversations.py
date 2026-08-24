"""Conversation API routes for chat-based RAG."""
from __future__ import annotations

import json
import logging
import asyncio
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
from src.harness.agent_plans import AgentPlanService
from src.harness.registry import base_capability_registry
from src.config import settings
from src.harness.response_synthesis import TypedAgentResponse
from src.harness.agent_chat_runtime import (
    AgentChatIdempotencyConflict,
    AgentRunInProgress,
    build_production_agent_turn_runtime,
    execute_production_agent_chat_turn,
)
from src.harness.agent_runs import AgentRunService, PublicAgentEvent
from src.harness.cohort import live_streaming_selected
from src.harness.approvals import ApprovalConflict, ApprovalDecision, ApprovalDenied, ApprovalService
from src.harness.capability_runtime import CapabilityExecutionContext, CapabilityExecutionRequest
from src.harness.agent_loop import stream_transport
from src.rbac.permissions import get_user_permissions, get_user_project_ids
from src.schemas import (
    ConversationCreateRequest,
    ConversationDetailResponse,
    AgentPlanProjectionResponse,
    ConversationListResponse,
    ConversationMessageRequest,
    ConversationResponse,
    ConversationUpdateRequest,
    DocumentSource,
    MessageResponse,
    MessageHarnessActivity,
    MessageHarnessActionChoice,
    MessageHarnessPendingAction,
    PageScope,
    SlashCommandListResponse,
    SlashCommandResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/conversations", tags=["conversations"])


class ApprovalDecisionRequest(BaseModel):
    expected_version: int
    decision: ApprovalDecision


class ApprovalDecisionResponse(BaseModel):
    proposal_id: str
    status: str
    message: str
    observation: dict | None = None


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
        .where(
            Conversation.user_id == user.id,
            Conversation.deleted_at.is_(None),
            Conversation.visible_in_chat.is_(True),
        )
    )
    total = total_result.scalar_one()

    # Get conversations with message counts
    result = await db.execute(
        select(Conversation)
        .where(
            Conversation.user_id == user.id,
            Conversation.deleted_at.is_(None),
            Conversation.visible_in_chat.is_(True),
        )
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
                origin_surface=c.origin_surface,
                visible_in_chat=c.visible_in_chat,
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
    surface = request.surface if request else "chat"
    conversation = Conversation(
        user_id=user.id,
        title=title,
        origin_surface=surface,
        visible_in_chat=surface == "chat",
    )
    db.add(conversation)
    await db.flush()

    return ConversationResponse(
        id=conversation.id,
        title=conversation.title,
        message_count=0,
        origin_surface=conversation.origin_surface,
        visible_in_chat=conversation.visible_in_chat,
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
    pending_proposal = (
        await ApprovalService(db).pending_for_conversation(
            conversation_id=conversation.id, owner_user_id=user.id
        )
        if settings.agent_runtime_v2_effects_enabled
        else None
    )
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
        if (
            pending_action is None
            and pending_proposal is not None
            and message.role == "assistant"
            and message.in_reply_to_message_id == pending_proposal.message_id
        ):
            pending_action = MessageHarnessPendingAction(
                conversation_id=conversation.id,
                proposal_id=pending_proposal.proposal_id,
                expected_version=pending_proposal.version,
                kind="approval",
                prompt=(
                    "Approve the exact proposed action: "
                    f"{pending_proposal.semantic_name.replace('_', ' ')}?"
                ),
                choices=[
                    MessageHarnessActionChoice(label="Approve", value="approve"),
                    MessageHarnessActionChoice(label="Decline", value="reject"),
                ],
                expires_at=pending_proposal.expires_at,
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
                    in ("agent_response_v1", "agent_response_v2_grounded")
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
                page_scope=(message.page_scope if isinstance(message.page_scope, dict) else None),
                created_at=message.created_at,
            )
        )

    agent_plan = None
    if settings.agent_runtime_v2_planning_enabled:
        agent_plan = await AgentPlanService(db, base_capability_registry()).current(
            conversation_id=conversation.id, owner_user_id=user.id
        )
    return ConversationDetailResponse(
        id=conversation.id,
        title=conversation.title,
        messages=messages,
        origin_surface=conversation.origin_surface,
        visible_in_chat=conversation.visible_in_chat,
        agent_plan=(AgentPlanProjectionResponse(**agent_plan.model_dump()) if agent_plan else None),
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
    )


@router.post(
    "/{conversation_id}/approvals/{proposal_id}/decision",
    response_model=ApprovalDecisionResponse,
    summary="Approve or reject one exact agent effect proposal",
)
async def decide_agent_approval(
    conversation_id: int,
    proposal_id: str,
    payload: ApprovalDecisionRequest,
    request: Request,
    db: DBSession,
    qdrant: QdrantClient,
    user: CurrentUser,
) -> ApprovalDecisionResponse:
    if not settings.agent_runtime_v2_effects_enabled:
        raise api_error(409, "effect_runtime_disabled", "Effect approvals are currently disabled")
    conversation = (
        await db.execute(
            select(Conversation)
            .where(
                Conversation.id == conversation_id,
                Conversation.user_id == user.id,
                Conversation.deleted_at.is_(None),
            )
            .options(selectinload(Conversation.messages))
        )
    ).scalar_one_or_none()
    if conversation is None:
        raise api_error(404, "not_found", "Conversation not found")
    project_scope = await get_user_project_ids(user, db)
    permissions = get_user_permissions(user)
    approvals = ApprovalService(db)
    try:
        await approvals.decide(
            proposal_id=proposal_id,
            owner_user_id=user.id,
            expected_version=payload.expected_version,
            decision=payload.decision,
            project_scope=project_scope,
            permissions=permissions,
        )
        if payload.decision is ApprovalDecision.REJECT:
            await db.commit()
            return ApprovalDecisionResponse(
                proposal_id=proposal_id,
                status="rejected",
                message="The proposed action was declined. No effect was executed.",
            )
        proposal = await approvals.approved_row(
            proposal_id=proposal_id, owner_user_id=user.id
        )
        decision_message = ConversationMessage(
            conversation_id=conversation_id,
            role="user",
            content=f"Approved pending action: {proposal.semantic_name.replace('_', ' ')}.",
            client_turn_id=f"approval-{proposal_id}"[:64],
            client_turn_fingerprint=proposal.arguments_hash,
        )
        db.add(decision_message)
        await db.flush()
        runtime = await build_production_agent_turn_runtime(
            db=db,
            qdrant=qdrant,
            conversation=conversation,
            user_id=user.id,
            user_groups=[role.name for role in user.roles],
            project_scope=project_scope,
            permissions=permissions,
            access_token=request.cookies.get("access_token"),
        )
        state = await runtime.state_repository.load(
            conversation_id=conversation_id, owner_user_id=user.id
        )
        claim = await runtime.state_repository.claim_turn(
            conversation_id=conversation_id,
            owner_user_id=user.id,
            message_id=decision_message.id,
            expected_version=state.version,
            idempotency_key=f"approval-resume:{proposal_id}",
        )
        context = CapabilityExecutionContext(
            user_id=user.id,
            conversation_id=conversation_id,
            project_scope=project_scope,
            permissions=permissions,
            approvals={proposal.capability_id},
            access_token=request.cookies.get("access_token"),
            allow_writes_content=True,
            allow_external_side_effects=True,
            allowed_capability_ids={proposal.capability_id},
            current_message_id=decision_message.id,
            conversation_state_version=state.version,
            conversation_turn_claim_id=claim.id,
            conversation_turn_claim_token=claim.lease_token,
        )
        prepared = await runtime.effect_operation_service.prepare(
            turn_claim_id=claim.id,
            call_id=f"approval:{proposal_id}",
            capability_id=proposal.capability_id,
            arguments_hash=proposal.arguments_hash,
            proposal=proposal,
        )
        observation = prepared.replay or await runtime.executor.execute(
            CapabilityExecutionRequest(
                capability_id=proposal.capability_id,
                arguments=dict(proposal.arguments),
                idempotency_key=prepared.operation_id,
            ),
            context,
        )
        if prepared.replay is None:
            await runtime.effect_operation_service.commit(prepared, observation)
        await runtime.agent_plan_service.record_capability_outcome(
            conversation_id=conversation_id,
            owner_user_id=user.id,
            message_id=decision_message.id,
            capability_id=proposal.capability_id,
            operation_id=prepared.operation_id,
            status=observation.status.value,
            receipt_ref=observation.receipt_ref,
            workflow_ref=observation.workflow_ref,
        )
        if observation.succeeded:
            await approvals.mark_executed(
                proposal_id=proposal_id, owner_user_id=user.id
            )
        await runtime.state_repository.finalize_claim(
            claim_id=claim.id,
            lease_token=claim.lease_token or "",
            action="approval_resume",
            final_result={"observation": observation.model_dump(mode="json")},
        )
        await db.commit()
        return ApprovalDecisionResponse(
            proposal_id=proposal_id,
            status="executed" if observation.succeeded else "failed",
            message=observation.summary or "The approved action was processed.",
            observation=observation.model_dump(mode="json"),
        )
    except ApprovalDenied as exc:
        await db.rollback()
        raise api_error(403, "approval_denied", str(exc)) from exc
    except ApprovalConflict as exc:
        await db.rollback()
        raise api_error(409, "approval_conflict", str(exc)) from exc


def _page_scope_payload(page_scope: PageScope | None) -> dict | None:
    """Serialize a validated page scope for storage.

    `exclude_none` keeps the stored shape identical to the frontend `TopologyLevel`
    union, where `sourceRoot` is an optional key rather than an explicit null. The
    topology tools emit the same union back as navigation targets, so shape drift here
    would surface as chips that navigate nowhere.
    """
    if page_scope is None:
        return None
    return page_scope.model_dump(mode="json", exclude_none=True)


async def _persist_page_scope(
    db: DBSession,
    message: ConversationMessage,
    page_scope: PageScope | None,
) -> None:
    """Record the client's viewing context on the turn's user message.

    Stored verbatim as an untrusted prompt hint. It is deliberately NOT used to derive
    project access, narrow retrieval, or override `request.project_id` — the route's
    existing project authorization above is the only authority, so a page_scope naming
    an inaccessible project grants nothing.

    Written after the turn rather than at message creation because the agent runtime
    owns the user-message row (it is the durable idempotency key for the turn); an
    explicit UPDATE keeps the write correct whether or not the instance is attached.
    """
    payload = _page_scope_payload(page_scope)
    if payload is None:
        return
    await db.execute(
        update(ConversationMessage)
        .where(ConversationMessage.id == message.id)
        .values(page_scope=payload)
    )
    await db.commit()
    message.page_scope = payload


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
        sources=_deserialize_sources(message.sources) if message.sources else None,
        latency_ms=message.latency_ms,
        status=message.status,
        workflow_run_id=message.workflow_run_id,
        structured_answer=message.structured_answer,
        workflow_cards=(list(typed.workflow_cards) if typed is not None else []),
        created_at=message.created_at,
    )


@router.patch("/{conversation_id}", response_model=ConversationResponse, summary="Update a conversation")
async def update_conversation(
    conversation_id: int,
    request: ConversationUpdateRequest,
    db: DBSession,
    user: CurrentUser,
) -> ConversationResponse:
    """Update user-controlled conversation presentation state."""
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

    if request.title is not None:
        conversation.title = request.title
    if request.visible_in_chat is not None:
        conversation.visible_in_chat = request.visible_in_chat

    return ConversationResponse(
        id=conversation.id,
        title=conversation.title,
        message_count=len(conversation.messages),
        origin_surface=conversation.origin_surface,
        visible_in_chat=conversation.visible_in_chat,
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
            page_scope=_page_scope_payload(request.page_scope),
            transport="nonstream",
        )
    except AgentChatIdempotencyConflict as exc:
        raise api_error(409, "idempotency_conflict", str(exc)) from exc
    except AgentRunInProgress as exc:
        raise api_error(
            409,
            "agent_run_in_progress",
            f"Agent run {exc.run_id} is still processing; reconnect to its stream.",
        ) from exc
    await _persist_page_scope(db, execution.user_message, request.page_scope)
    return _agent_message_response(execution)


@router.post("/{conversation_id}/messages/stream", summary="Send a message and stream the chat agent response via SSE")
async def send_message_stream(
    conversation_id: int,
    request: ConversationMessageRequest,
    http_request: Request,
    db: DBSession,
    user: CurrentUser,
    qdrant: QdrantClient,
    after_sequence: int = Query(default=0, ge=0),
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
    live_projection = live_streaming_selected(settings, user_id=user.id)
    if live_projection:
        cursor = max(after_sequence, _last_event_sequence(http_request.headers.get("last-event-id")))

        async def live_event_generator():
            queue: asyncio.Queue[PublicAgentEvent] = asyncio.Queue()

            async def listener(event: PublicAgentEvent) -> None:
                if event.sequence > cursor:
                    await queue.put(event)

            task = asyncio.create_task(
                execute_production_agent_chat_turn(
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
                    page_scope=_page_scope_payload(request.page_scope),
                    transport="stream",
                    public_event_listener=listener,
                )
            )
            try:
                while not task.done() or not queue.empty():
                    try:
                        event = await asyncio.wait_for(queue.get(), timeout=0.1)
                    except TimeoutError:
                        continue
                    yield _format_public_sse(event)
                try:
                    execution = await task
                except AgentRunInProgress as exc:
                    async for data in _follow_existing_run(
                        db=db,
                        run_id=exc.run_id,
                        conversation_id=conversation_id,
                        owner_user_id=user.id,
                        after_sequence=cursor,
                    ):
                        yield data
                    return
                except AgentChatIdempotencyConflict as exc:
                    yield f"event: error\ndata: {json.dumps(str(exc))}\n\n"
                    return
                await _persist_page_scope(db, execution.user_message, request.page_scope)
                if execution.run_id is None:
                    async for event_type, data in stream_transport(execution.result):
                        yield f"event: {event_type}\ndata: {data}\n\n"
                yield (
                    "event: done\ndata: "
                    + json.dumps(
                        {
                            "message_id": execution.assistant_message.id,
                            "latency_ms": execution.latency_ms,
                            "run_id": execution.run_id,
                            "replayed": execution.replayed,
                        }
                    )
                    + "\n\n"
                )
            except asyncio.CancelledError:
                # Preserve the single execution authority after client disconnect.
                # Dependency cleanup waits for the durable run to settle.
                await asyncio.shield(task)
                raise

        return StreamingResponse(live_event_generator(), media_type="text/event-stream")

    try:
        execution = await execute_production_agent_chat_turn(
            db=db, qdrant=qdrant, conversation=conversation, query=request.query,
            images=request.images, client_turn_id=request.client_turn_id,
            requested_project_id=request.project_id, user_id=user.id,
            user_groups=user_groups, project_scope=project_ids,
            permissions=get_user_permissions(user),
            access_token=http_request.cookies.get("access_token"),
            page_scope=_page_scope_payload(request.page_scope),
            transport="stream",
        )
    except AgentChatIdempotencyConflict as exc:
        raise api_error(409, "idempotency_conflict", str(exc)) from exc
    await _persist_page_scope(db, execution.user_message, request.page_scope)

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


def _last_event_sequence(last_event_id: str | None) -> int:
    if not last_event_id or ":evt:" not in last_event_id:
        return 0
    try:
        return max(0, int(last_event_id.rsplit(":evt:", 1)[1]))
    except ValueError:
        return 0


def _format_public_sse(event: PublicAgentEvent) -> str:
    return (
        f"id: {event.event_id}\n"
        f"event: {event.type}\n"
        f"data: {event.model_dump_json()}\n\n"
    )


async def _follow_existing_run(
    *,
    db,
    run_id: str,
    conversation_id: int,
    owner_user_id: int,
    after_sequence: int,
):  # noqa: ANN001, ANN202
    service = AgentRunService(db)
    cursor = after_sequence
    deadline = asyncio.get_running_loop().time() + settings.agent_runtime_v2_hard_deadline_seconds
    while asyncio.get_running_loop().time() < deadline:
        events = await service.replay(
            run_id=run_id,
            conversation_id=conversation_id,
            owner_user_id=owner_user_id,
            after_sequence=cursor,
        )
        for event in events:
            cursor = event.sequence
            yield _format_public_sse(event)
        run = await service.get_owned(
            run_id=run_id,
            conversation_id=conversation_id,
            owner_user_id=owner_user_id,
        )
        if run is None:
            yield "event: error\ndata: \"Agent run not found\"\n\n"
            return
        if run.status in {"completed", "failed", "interrupted"}:
            yield (
                "event: done\ndata: "
                + json.dumps(
                    {
                        "message_id": run.assistant_message_id or 0,
                        "latency_ms": 0,
                        "run_id": run.run_id,
                        "replayed": True,
                    }
                )
                + "\n\n"
            )
            return
        await asyncio.sleep(0.1)
    yield "event: error\ndata: \"Agent run is still processing\"\n\n"


class SaveExchangeRequest(BaseModel):
    user_message: str
    assistant_message: str
    latency_ms: int | None = None
    # Same untrusted prompt-hint contract as ConversationMessageRequest.page_scope:
    # persisted with the turn, never an authorization or retrieval input.
    page_scope: PageScope | None = None


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
        page_scope=_page_scope_payload(request.page_scope),
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
        id=assistant_msg.id,
        role=assistant_msg.role,
        content=request.assistant_message,
        sources=[],
        latency_ms=request.latency_ms or 0,
        created_at=assistant_msg.created_at,
    )
