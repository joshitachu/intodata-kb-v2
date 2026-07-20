"""Production dependency assembly for the one authoritative chat agent."""

from __future__ import annotations

import asyncio
import datetime
import hashlib
import json
import time
from dataclasses import dataclass
from typing import Any

from qdrant_client import AsyncQdrantClient
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.db.models import HarnessAgentTurnAuditRow, QueryUsage
from src.db.models.conversation import Conversation, ConversationMessage
from src.db.models.harness import HarnessConversationTurnClaimRow
from src.api.metrics import (
    agent_chat_cost_usd,
    agent_chat_guard_total,
    agent_chat_latency_seconds,
    agent_chat_tool_calls,
    agent_chat_turns_total,
)
from src.harness.agent_loop import (
    AgentDecisionModel,
    AgentLoopResult,
    AgentLoopLimits,
    AgentStopReason,
    ProviderAgentDecisionModel,
    registered_capability_manifest,
)
from src.harness.agent_loop_context import (
    persist_typed_agent_response,
    run_authoritative_agent_turn,
)
from src.harness.capability_runtime import CapabilityExecutionContext
from src.harness.connectors import (
    ConnectorRepository,
    ConnectorService,
    default_connector_provider_registry,
)
from src.harness.connectors.capabilities import build_request_capability_registry
from src.harness.conversation_state import ConversationStateRepository
from src.harness.conversation_state import ConversationStateConflict
from src.harness.conversation_tasks import ConversationTaskService
from src.harness.evidence import build_registered_read_adapters
from src.harness.executor import HarnessCapabilityExecutor
from src.harness.packs.draft_service import PackDraftService
from src.harness.packs.project_service import ProjectPackService
from src.harness.packs.runtime import compile_governed_pack_registry
from src.harness.providers.mcp_docs import McpDocumentationProvider
from src.harness.providers.mcp_projects import McpProjectsProvider
from src.harness.registry import CapabilityRegistry
from src.harness.repository import HarnessWorkflowRepository
from src.harness.workflow_control import WorkflowCancellationController
from src.harness.workflow_projection import AuthoritativeWorkflowCardProjector


_DOCUMENTATION_WORKFLOW_KEY = "documentation.generate"


@dataclass(frozen=True, slots=True)
class ProductionAgentTurnRuntime:
    registry: CapabilityRegistry
    executor: HarnessCapabilityExecutor
    model: AgentDecisionModel
    limits: AgentLoopLimits
    state_repository: ConversationStateRepository
    workflow_card_projector: AuthoritativeWorkflowCardProjector


@dataclass(frozen=True, slots=True)
class AgentChatTurnExecution:
    user_message: ConversationMessage
    assistant_message: ConversationMessage
    result: Any
    latency_ms: int


class AgentChatIdempotencyConflict(RuntimeError):
    """A client turn key was reused for a different canonical request."""


async def build_production_agent_turn_runtime(
    *,
    db: AsyncSession,
    qdrant: AsyncQdrantClient,
    conversation: Conversation,
    user_id: int,
    user_groups: list[str],
    project_scope: list[str] | None,
    permissions: set[str] | None,
    access_token: str | None,
    model: AgentDecisionModel | None = None,
) -> ProductionAgentTurnRuntime:
    """Build every authenticated dependency; missing bindings fail closed."""

    connector_repository = ConnectorRepository(db)
    registry = await build_request_capability_registry(
        connector_repository,
        project_ids=project_scope,
    )
    if project_scope is not None and len(project_scope) == 1:
        registry = await _compile_exact_project_pack(
            db=db,
            registry=registry,
            project_id=project_scope[0],
        )

    docs_provider = McpDocumentationProvider()
    projects_provider = McpProjectsProvider()
    state_repository = ConversationStateRepository(db)
    pack_draft_service = PackDraftService(db, registry)
    conversation_task_service = ConversationTaskService(
        db=db,
        registry=registry,
        state_repository=state_repository,
        docs_provider=docs_provider,
        projects_provider=projects_provider,
    )
    workflow_repository = HarnessWorkflowRepository(db)
    workflow_card_projector = AuthoritativeWorkflowCardProjector(workflow_repository)
    history = [
        {"message_id": item.id, "role": item.role, "content": item.content}
        for item in list(conversation.messages)[-20:]
        if item.role in {"user", "assistant"} and item.content
    ]
    evidence_handlers = build_registered_read_adapters(
        qdrant=qdrant,
        db=db,
        user_groups=user_groups,
        docs_provider=docs_provider,
        conversation_history=history,
        conversation_id=conversation.id,
    )
    executor = HarnessCapabilityExecutor(
        registry,
        docs_provider=docs_provider,
        projects_provider=projects_provider,
        connector_service=ConnectorService(
            connector_repository,
            default_connector_provider_registry(),
        ),
        workflow_cancellation_controller=WorkflowCancellationController(
            workflow_repository,
            docs_provider,
        ),
        pack_draft_service=pack_draft_service,
        conversation_state_repository=state_repository,
        conversation_task_service=conversation_task_service,
        evidence_handlers=evidence_handlers,
    )
    _validate_agent_manifest(
        registry,
        context=CapabilityExecutionContext(
            user_id=user_id,
            conversation_id=conversation.id,
            project_scope=project_scope,
            permissions=permissions,
            access_token=access_token,
        ),
    )
    return ProductionAgentTurnRuntime(
        registry=registry,
        executor=executor,
        model=model or ProviderAgentDecisionModel(),
        limits=AgentLoopLimits(
            max_iterations=settings.agent_chat_max_iterations,
            max_tool_calls=settings.agent_chat_max_tool_calls,
            deadline_seconds=settings.agent_chat_deadline_seconds,
            max_cost_usd=settings.agent_chat_max_cost_usd,
        ),
        state_repository=state_repository,
        workflow_card_projector=workflow_card_projector,
    )


async def execute_production_agent_chat_turn(
    *,
    db: AsyncSession,
    qdrant: AsyncQdrantClient,
    conversation: Conversation,
    query: str,
    images: list[str] | None,
    client_turn_id: str | None,
    requested_project_id: str | None,
    user_id: int,
    user_groups: list[str],
    project_scope: list[str] | None,
    permissions: set[str] | None,
    access_token: str | None,
    transport: str,
    model: AgentDecisionModel | None = None,
) -> AgentChatTurnExecution:
    """Claim, execute, and persist one agent turn for either public transport."""

    started = time.perf_counter()
    conversation_id = conversation.id
    user_message = await _get_or_create_user_message(
        db=db,
        conversation_id=conversation_id,
        query=query,
        images=images,
        client_turn_id=client_turn_id,
        request_fingerprint=_request_fingerprint(
            query=query,
            images=images,
            requested_project_id=requested_project_id,
        ),
    )
    # The client turn is the durable idempotency authority. Persist it before a
    # model or capability can run so concurrent transports converge on one row.
    await db.commit()
    user_message_id = user_message.id
    await db.refresh(conversation, ["messages"])
    runtime = await build_production_agent_turn_runtime(
        db=db,
        qdrant=qdrant,
        conversation=conversation,
        user_id=user_id,
        user_groups=user_groups,
        project_scope=project_scope,
        permissions=permissions,
        access_token=access_token,
        model=model,
    )
    result = await _run_primary_or_replay(
        db=db,
        conversation=conversation,
        query=query,
        user_id=user_id,
        project_scope=project_scope,
        permissions=permissions,
        access_token=access_token,
        runtime=runtime,
        images=images,
        message_id=user_message_id,
    )
    assistant_message = await _get_or_create_assistant_message(
        db=db,
        conversation_id=conversation_id,
        user_message_id=user_message_id,
        result=result,
        latency_ms=max(0, int((time.perf_counter() - started) * 1_000)),
    )

    latency_ms = assistant_message.latency_ms or max(
        0, int((time.perf_counter() - started) * 1_000)
    )
    recorded = await _persist_usage_and_audit(
        db=db,
        conversation_id=conversation_id,
        user_id=user_id,
        user_message_id=user_message_id,
        assistant_message_id=assistant_message.id,
        result=result,
        transport=transport,
        latency_ms=latency_ms,
    )
    await db.commit()
    await db.refresh(user_message)
    await db.refresh(assistant_message)
    if recorded:
        _observe_metrics(
            result=result,
            transport=transport,
            latency_ms=latency_ms,
        )
    return AgentChatTurnExecution(
        user_message=user_message,
        assistant_message=assistant_message,
        result=result,
        latency_ms=latency_ms,
    )


async def _get_or_create_user_message(
    *,
    db: AsyncSession,
    conversation_id: int,
    query: str,
    images: list[str] | None,
    client_turn_id: str | None,
    request_fingerprint: str,
) -> ConversationMessage:
    if client_turn_id:
        statement = _insert_for_session(db, ConversationMessage.__table__).values(
            conversation_id=conversation_id,
            role="user",
            content=query,
            image_data=images,
            client_turn_id=client_turn_id,
            client_turn_fingerprint=request_fingerprint,
        ).on_conflict_do_nothing(
            index_elements=["conversation_id", "client_turn_id"]
        ).returning(ConversationMessage.id)
        inserted_id = (await db.execute(statement)).scalar_one_or_none()
        if inserted_id is not None:
            message = await db.get(ConversationMessage, inserted_id)
            if message is None:
                raise RuntimeError("Inserted client turn could not be reloaded")
            return message
        existing = (
            await db.execute(
                select(ConversationMessage).where(
                    ConversationMessage.conversation_id == conversation_id,
                    ConversationMessage.client_turn_id == client_turn_id,
                    ConversationMessage.role == "user",
                )
            )
        ).scalar_one()
        if existing.client_turn_fingerprint != request_fingerprint:
            raise AgentChatIdempotencyConflict(
                "client_turn_id is already bound to a different request"
            )
        return existing
    message = ConversationMessage(
        conversation_id=conversation_id,
        role="user",
        content=query,
        image_data=images,
        client_turn_id=client_turn_id,
        client_turn_fingerprint=request_fingerprint,
    )
    db.add(message)
    await db.flush()
    return message


async def _get_or_create_assistant_message(
    *,
    db: AsyncSession,
    conversation_id: int,
    user_message_id: int,
    result: AgentLoopResult,
    latency_ms: int,
) -> ConversationMessage:
    candidate = ConversationMessage(
        conversation_id=conversation_id,
        role="assistant",
        content=result.output,
        in_reply_to_message_id=user_message_id,
        workflow_run_id=(
            result.typed_response.workflow_cards[0].workflow_run_id
            if result.typed_response is not None and result.typed_response.workflow_cards
            else None
        ),
        latency_ms=latency_ms,
    )
    persist_typed_agent_response(candidate, result)
    statement = _insert_for_session(db, ConversationMessage.__table__).values(
        conversation_id=candidate.conversation_id,
        role=candidate.role,
        content=candidate.content,
        in_reply_to_message_id=candidate.in_reply_to_message_id,
        workflow_run_id=candidate.workflow_run_id,
        latency_ms=candidate.latency_ms,
        structured_answer=candidate.structured_answer,
    ).on_conflict_do_nothing(
        index_elements=["in_reply_to_message_id"]
    ).returning(ConversationMessage.id)
    inserted_id = (await db.execute(statement)).scalar_one_or_none()
    if inserted_id is not None:
        message = await db.get(ConversationMessage, inserted_id)
        if message is None:
            raise RuntimeError("Inserted assistant response could not be reloaded")
        return message
    return (
        await db.execute(
            select(ConversationMessage).where(
                ConversationMessage.in_reply_to_message_id == user_message_id
            )
        )
    ).scalar_one()


async def _persist_usage_and_audit(
    *,
    db: AsyncSession,
    conversation_id: int,
    user_id: int,
    user_message_id: int,
    assistant_message_id: int,
    result: Any,
    transport: str,
    latency_ms: int,
) -> bool:
    model_name = settings.rag_default_model
    provider = model_name.split("/", 1)[0] if "/" in model_name else None
    capability_ids = list(
        dict.fromkeys(item.capability_id for item in result.observations)
    )[:12]
    observation_statuses = [item.status.value for item in result.observations[:12]]
    cards = result.typed_response.workflow_cards if result.typed_response else []
    stale_workflow = any(
        card.status in {"succeeded", "failed", "cancelled"} and card.is_active
        for card in cards
    )
    now = datetime.datetime.now(datetime.UTC)
    inserted_audit_id = (
        await db.execute(
            _insert_for_session(db, HarnessAgentTurnAuditRow.__table__).values(
            conversation_id=conversation_id,
            owner_user_id=user_id,
            user_message_id=user_message_id,
            assistant_message_id=assistant_message_id,
            route="agent",
            cohort_source="single_path",
            cohort_bucket=0,
            transport=transport,
            state_version=result.state_version,
            terminal_action=result.action.value if result.action is not None else None,
            stop_reason=result.stop_reason.value,
            capability_ids=capability_ids,
            observation_statuses=observation_statuses,
            response_fingerprint=result.response_fingerprint,
            flags={
                "repeat": result.stop_reason.value == "repeated_response",
                "clarification": result.action is not None
                and result.action.value == "clarify",
                "denied": any(value == "denied" for value in observation_statuses),
                "stale_workflow": stale_workflow,
                "state_conflict": result.stop_reason.value == "state_conflict",
            },
            input_tokens=result.usage.input_tokens,
            output_tokens=result.usage.output_tokens,
            tool_calls=len(result.observations),
            cost_usd=result.usage.total_cost_usd,
            latency_ms=latency_ms,
            shadow_result=None,
                created_at=now,
                updated_at=now,
            ).on_conflict_do_nothing(
                index_elements=["user_message_id"]
            ).returning(HarnessAgentTurnAuditRow.id)
        )
    ).scalar_one_or_none()
    if inserted_audit_id is None:
        return False
    db.add(
        QueryUsage(
            user_id=user_id,
            interface="conversation_agent",
            query_text=None,
            model_name=model_name,
            provider=provider,
            input_tokens=result.usage.input_tokens,
            output_tokens=result.usage.output_tokens,
            cost_usd=result.usage.total_cost_usd,
            latency_ms=latency_ms,
            conversation_id=conversation_id,
        )
    )
    return True


async def _run_primary_or_replay(
    *,
    db: AsyncSession,
    conversation: Conversation,
    query: str,
    user_id: int,
    project_scope: list[str] | None,
    permissions: set[str] | None,
    access_token: str | None,
    runtime: ProductionAgentTurnRuntime,
    images: list[str] | None,
    message_id: int,
) -> AgentLoopResult:
    try:
        return await run_authoritative_agent_turn(
            conversation=conversation,
            query=query,
            db=db,
            user_id=user_id,
            project_scope=project_scope,
            permissions=permissions,
            access_token=access_token,
            registry=runtime.registry,
            executor=runtime.executor,
            model=runtime.model,
            limits=runtime.limits,
            images=images,
            message_id=message_id,
            state_repository=runtime.state_repository,
            workflow_card_projector=runtime.workflow_card_projector,
        )
    except ConversationStateConflict:
        await db.rollback()
    deadline = time.monotonic() + runtime.limits.deadline_seconds
    while time.monotonic() < deadline:
        claim = (
            await db.execute(
                select(HarnessConversationTurnClaimRow).where(
                    HarnessConversationTurnClaimRow.message_id == message_id
                )
            )
        ).scalar_one_or_none()
        if claim is None:
            break
        if claim.status in {"applied", "failed"} and isinstance(claim.final_result, dict):
            try:
                return AgentLoopResult.model_validate(claim.final_result)
            except ValueError:
                break
        await db.rollback()
        await asyncio.sleep(0.05)
    return AgentLoopResult(
        output="This message is already being processed. Retry shortly to load its result.",
        stop_reason=AgentStopReason.STATE_CONFLICT,
        state_version="",
    )


def _insert_for_session(db: AsyncSession, table):  # noqa: ANN001, ANN202
    dialect = db.get_bind().dialect.name
    if dialect == "postgresql":
        return postgresql_insert(table)
    if dialect == "sqlite":
        return sqlite_insert(table)
    raise RuntimeError(f"Unsupported idempotent chat database dialect: {dialect}")


def _request_fingerprint(
    *,
    query: str,
    images: list[str] | None,
    requested_project_id: str | None,
) -> str:
    payload = json.dumps(
        {
            "query": query,
            "images": images or [],
            "project_id": requested_project_id,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _observe_metrics(
    *,
    result: Any,
    transport: str,
    latency_ms: int,
) -> None:
    outcome = (
        "clarified"
        if result.action is not None and result.action.value == "clarify"
        else "answered"
        if result.action is not None and result.action.value == "answer"
        else "stopped"
    )
    agent_chat_turns_total.labels(transport, outcome).inc()
    agent_chat_latency_seconds.labels(transport).observe(latency_ms / 1_000)
    agent_chat_cost_usd.observe(result.usage.total_cost_usd)
    agent_chat_tool_calls.observe(len(result.observations))
    for guard, triggered in {
        "repeat": result.stop_reason.value == "repeated_response",
        "denial": any(item.status.value == "denied" for item in result.observations),
        "stale_workflow": any(
            card.status in {"succeeded", "failed", "cancelled"} and card.is_active
            for card in (result.typed_response.workflow_cards if result.typed_response else [])
        ),
    }.items():
        agent_chat_guard_total.labels(guard, "triggered" if triggered else "clear").inc()


async def _compile_exact_project_pack(
    *,
    db: AsyncSession,
    registry: CapabilityRegistry,
    project_id: str,
) -> CapabilityRegistry:
    service = ProjectPackService(db, registry)
    compiled = await service.resolve_governed_for_execution(
        project_id,
        _DOCUMENTATION_WORKFLOW_KEY,
    )
    if compiled is None:
        return registry
    private_packs = await service.resolve_private_analysis_for_execution(
        project_id,
        _DOCUMENTATION_WORKFLOW_KEY,
    )
    return compile_governed_pack_registry(
        registry,
        compiled,
        tuple(item.provider_payload() for item in private_packs),
    )


def _validate_agent_manifest(
    registry: CapabilityRegistry,
    *,
    context: CapabilityExecutionContext,
) -> None:
    manifest = registered_capability_manifest(
        registry,
        permissions=context.permissions,
        project_ids=context.project_scope,
    )
    for tool in manifest.tools:
        registry.handler_for(tool.id)


__all__ = [
    "AgentChatTurnExecution",
    "AgentChatIdempotencyConflict",
    "ProductionAgentTurnRuntime",
    "build_production_agent_turn_runtime",
    "execute_production_agent_chat_turn",
]
