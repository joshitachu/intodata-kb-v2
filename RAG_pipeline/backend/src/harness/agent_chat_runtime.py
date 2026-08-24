"""Production dependency assembly for the one authoritative chat agent."""

from __future__ import annotations

import asyncio
import datetime
import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from collections.abc import Awaitable, Callable
from typing import Any

from qdrant_client import AsyncQdrantClient
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.db.models import HarnessAgentTurnAuditRow, QueryUsage
from src.db.session import async_session_factory
from src.db.models.conversation import Conversation, ConversationMessage
from src.db.models.harness import HarnessConversationTurnClaimRow
from src.api.metrics import (
    agent_chat_cost_usd,
    agent_chat_guard_total,
    agent_chat_latency_seconds,
    agent_chat_tool_calls,
    agent_chat_turns_total,
    agent_runtime_canary_turns_total,
    agent_runtime_canary_latency_seconds,
    agent_runtime_canary_cost_usd,
    agent_runtime_canary_guard_total,
)
from src.harness.agent_loop import (
    AgentBoundaryAction,
    AgentDecisionModel,
    AgentLoopResult,
    AgentLoopLimits,
    AgentStopReason,
    ProviderAgentDecisionModel,
    registered_capability_manifest,
    response_fingerprint,
)
from src.harness.agent_loop_context import (
    build_authoritative_agent_input,
    persist_grounded_agent_response,
    persist_typed_agent_response,
    run_authoritative_agent_turn,
)
from src.harness.capability_runtime import CapabilityExecutionContext
from src.agent_runtime_canary import CanaryStagePolicy
from src.harness.cohort import CohortDecision, CohortSelector, live_streaming_selected
from src.harness.grounding import GroundedAnswer
from src.harness.response_synthesis import render_typed_response
from src.harness.models import (
    DOCUMENT_LIST_CAPABILITY,
    DOCUMENT_READ_CAPABILITY,
    GENERATED_DOCUMENT_READ_CAPABILITY,
    HarnessRiskTier,
)
from src.harness.manifest import ManifestScope
from src.harness.readonly_runtime import (
    READONLY_MODEL_PROFILE,
    READONLY_PROMPT_VERSION,
    READONLY_RUNTIME_RELEASE,
    build_readonly_primary_chat_model,
    build_readonly_primary_runtime,
    grounding_failure_answer,
    public_answer_chunks,
    run_readonly_primary_turn,
)
from src.harness.trusted_answer import AnswerDeltaSink, TrustedAnswerSink
from src.schemas import DocumentSource
from src.harness.planning_runtime import build_planning_primary_runtime
from src.harness.effect_runtime import build_effect_primary_runtime
from src.harness.approvals import ApprovalService
from src.harness.effect_operations import EffectOperationService
from src.harness.connectors import (
    ConnectorRepository,
    ConnectorService,
    default_connector_provider_registry,
)
from src.harness.connectors.capabilities import build_request_capability_registry
from src.harness.conversation_state import (
    ConversationPreparedAction,
    ConversationState,
    ConversationStateRepository,
)
from src.harness.agent_plans import AgentPlanService, agent_plan_capabilities
from src.harness.task_controls import conversation_task_control_capabilities
from src.harness.conversation_state import ConversationStateConflict
from src.harness.conversation_tasks import ConversationTaskService
from src.harness.evidence import build_registered_read_adapters
from src.harness.evidence_summary import EvidenceSummary
from src.harness.executor import HarnessCapabilityExecutor
from src.harness.packs.draft_service import PackDraftService
from src.harness.providers.mcp_docs import McpDocumentationProvider
from src.harness.providers.mcp_projects import McpProjectsProvider
from src.harness.registry import CapabilityRegistry, base_capability_registry
from src.harness.topology_capability import TOPOLOGY_STRUCTURE_CAPABILITY, topology_capabilities
from src.harness.topology_guidance import (
    is_topology_surface,
    topology_capability_enabled,
    topology_turn_guidance,
)
from src.harness.turn_provenance import safe_project_turn_provenance
from src.harness.repository import HarnessWorkflowRepository
from src.harness.workflow_control import WorkflowCancellationController
from src.harness.workflow_projection import AuthoritativeWorkflowCardProjector
from src.harness.agent_runs import AgentRunProjection, AgentRunService, PublicAgentEvent
from src.harness.agent_trace import safe_argument_shape
from src.harness.run_budgets import LayeredBudgetController, LayeredBudgetLimits
from src.turn_timings import TurnTimings, turn_timings_scope


_DOCUMENTATION_WORKFLOW_KEY = "documentation.generate"
CHAT_NON_RAG_DOCUMENT_CAPABILITY_IDS = frozenset(
    {
        DOCUMENT_LIST_CAPABILITY.id,
        DOCUMENT_READ_CAPABILITY.id,
        GENERATED_DOCUMENT_READ_CAPABILITY.id,
    }
)


def _chat_denied_capability_ids(
    query: str, page_scope: dict[str, Any] | None = None
) -> set[str]:
    denied = set(CHAT_NON_RAG_DOCUMENT_CAPABILITY_IDS)
    if not topology_capability_enabled(query, page_scope):
        denied.add(TOPOLOGY_STRUCTURE_CAPABILITY.id)
    return denied
logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ProductionAgentTurnRuntime:
    registry: CapabilityRegistry
    executor: HarnessCapabilityExecutor
    model: AgentDecisionModel
    limits: AgentLoopLimits
    state_repository: ConversationStateRepository
    workflow_card_projector: AuthoritativeWorkflowCardProjector
    approval_service: ApprovalService
    effect_operation_service: EffectOperationService
    agent_plan_service: AgentPlanService
    trusted_answer_sink: TrustedAnswerSink
    answer_delta_sink: AnswerDeltaSink


@dataclass(frozen=True, slots=True)
class AgentChatTurnExecution:
    user_message: ConversationMessage
    assistant_message: ConversationMessage
    result: Any
    latency_ms: int
    run_id: str | None = None
    replayed: bool = False


@dataclass(frozen=True, slots=True)
class PrimaryTurnOutcome:
    """The result of a primary turn plus the V2 grounded answer, if any."""

    result: AgentLoopResult
    grounded: GroundedAnswer | None
    cohort: CohortDecision
    manifest_snapshot_hash: str | None = None
    document_sources: list[DocumentSource] = field(default_factory=list)
    #: Topology navigation targets observed this turn, verbatim, for the canvas.
    topology_targets: list[dict[str, Any]] = field(default_factory=list)
    evidence_summary: EvidenceSummary = field(default_factory=EvidenceSummary)


@dataclass(slots=True)
class _StepTimeline:
    """Per-run step-boundary clock, so every recorded step gets a real duration.

    A step's start is the previous recorded boundary in the same run. That makes a
    ``runtime.update`` step's ``latency_ms`` the model decode time that preceded
    it -- the number that was missing while every non-terminal step stored 0. A
    tool step instead starts when its call was proposed, so a slow tool is never
    misattributed to the model, and ``tool.proposed`` itself measures how long the
    model took to choose it.

    Only recorded steps move the boundary. Live progress heartbeats are far more
    frequent than steps and would otherwise collapse every decode measurement.
    """

    _boundary: float = field(default_factory=time.monotonic)
    _call_started: dict[str, float] = field(default_factory=dict)

    def note_call_proposed(self, call_ref: str) -> None:
        self._call_started[call_ref] = time.monotonic()

    def measure(
        self, *, event_type: str, call_ref: str | None
    ) -> tuple[datetime.datetime, datetime.datetime, int]:
        """Close the current step and return ``(started_at, finished_at, ms)``."""

        now = time.monotonic()
        started = self._boundary
        if event_type.startswith("tool.") and event_type != "tool.proposed" and call_ref:
            started = self._call_started.get(call_ref, self._boundary)
        self._boundary = now
        elapsed = max(0.0, now - started)
        finished_at = datetime.datetime.now(datetime.UTC)
        return (
            finished_at - datetime.timedelta(seconds=elapsed),
            finished_at,
            int(round(elapsed * 1_000)),
        )


class AgentChatIdempotencyConflict(RuntimeError):
    """A client turn key was reused for a different canonical request."""


class AgentRunInProgress(RuntimeError):
    """A duplicate transport may follow/replay this run but not execute it."""

    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        super().__init__("The existing Agent Run is still in progress")


def _apply_interaction_grounding_failure(turn: Any) -> None:
    """Replace invalid interaction output with the permitted grounding failure."""

    grounded_failure = grounding_failure_answer()
    fingerprint = response_fingerprint(
        AgentBoundaryAction.ANSWER,
        grounded_failure.content,
        state_version=turn.result.state_version,
        observations=turn.result.observations,
    )
    turn.result = turn.result.model_copy(
        update={
            "action": AgentBoundaryAction.ANSWER,
            "output": grounded_failure.content,
            "stop_reason": AgentStopReason.INVALID_MODEL_OUTPUT,
            "response_fingerprint": fingerprint,
        }
    )
    turn.grounded = grounded_failure


def _authoritative_turn_context(project_scope: list[str] | None) -> str:
    """Project the server-owned scope into model context without credentials."""

    return json.dumps(
        {
            "authorized_project_scope": project_scope,
            "scope_instruction": (
                "When exactly one project is listed, use that exact project_id for "
                "project-scoped tools, including contextual follow-up requests."
            ),
        },
        sort_keys=True,
    )


def _turn_prompt_context(
    project_scope: list[str] | None,
    page_scope: dict[str, Any] | None,
) -> str:
    """Compose the authoritative scope projection with this turn's guidance.

    ``project_scope`` is authoritative and comes from the authenticated request.
    ``page_scope`` is client-supplied and untrusted: it is rendered as a prompt
    preference only and deliberately not merged into the scope projection above,
    so it can never widen project access or become a retrieval filter.
    """

    return "\n\n".join(
        (_authoritative_turn_context(project_scope), topology_turn_guidance(page_scope))
    )


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
    # Plan controls are Nexus-owned, not Pack-owned. They must be present in a
    # planning manifest even when a project's compiled Pack omits them. Topology
    # is registered here for the same reason: it reads published artifacts rather
    # than a Pack-governed domain provider, so a project whose persisted Pack
    # stack predates it must still be able to answer structural questions.
    control_registry = base_capability_registry()
    for definition in (
        *agent_plan_capabilities(),
        *conversation_task_control_capabilities(),
        *topology_capabilities(),
    ):
        try:
            registry.get(definition.id)
        except Exception:
            registry.register(definition, control_registry.handler_for(definition.id))

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
    trusted_answer_sink = TrustedAnswerSink()
    # Inert until the harness binds `publish` below: the public event callback does
    # not exist yet at runtime-assembly time, which is the same ordering that made
    # `trusted_answer_sink` a mutable sink.
    answer_delta_sink = AnswerDeltaSink()
    evidence_handlers = build_registered_read_adapters(
        qdrant=qdrant,
        db=db,
        user_groups=user_groups,
        docs_provider=docs_provider,
        conversation_history=history,
        conversation_id=conversation.id,
        trusted_answer_sink=trusted_answer_sink,
        answer_delta_sink=answer_delta_sink,
    )
    agent_plan_service = AgentPlanService(db, registry)
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
        agent_plan_service=agent_plan_service,
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
        approval_service=ApprovalService(db),
        effect_operation_service=EffectOperationService(db),
        agent_plan_service=agent_plan_service,
        trusted_answer_sink=trusted_answer_sink,
        answer_delta_sink=answer_delta_sink,
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
    page_scope: dict[str, Any] | None = None,
    model: AgentDecisionModel | None = None,
    public_event_listener: Callable[[PublicAgentEvent], Awaitable[None]] | None = None,
) -> AgentChatTurnExecution:
    """Claim, execute, and persist one agent turn for either public transport.

    ``page_scope`` is the client's untrusted viewing context. It reaches the prompt
    as a disambiguation preference only: it never widens ``project_scope``, never
    filters retrieval, and never authorizes anything.
    """

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
    cohort = await _claim_runtime_cohort(
        db=db,
        user_message=user_message,
        live_streaming=live_streaming_selected(settings, user_id=user_id),
        candidate=_select_cohort(
            runtime=runtime,
            user_id=user_id,
            transport=transport,
            permissions=permissions,
            project_scope=project_scope,
        ),
    )
    claimed_live_streaming = bool(
        isinstance(user_message.turn_plan, dict)
        and user_message.turn_plan.get("live_streaming") is True
    )
    run_service: AgentRunService | None = None
    run_projection: AgentRunProjection | None = None
    budget_controller: LayeredBudgetController | None = None
    event_callback: Callable[[str, dict[str, Any]], Awaitable[None]] | None = None
    heartbeat_callback: Callable[[str, dict[str, Any]], Awaitable[None]] | None = None
    if claimed_live_streaming and cohort.is_v2_readonly:
        budget_controller = LayeredBudgetController(_layered_budget_limits())
        run_service = AgentRunService(db)
        run_projection, created = await run_service.start_or_load(
            conversation_id=conversation_id,
            owner_user_id=user_id,
            user_message_id=user_message_id,
            runtime_cohort=cohort.cohort.value,
            transport=transport,
            budget=budget_controller.limits.model_dump_public(),
            objective_summary=None,
        )
        if not created:
            if run_projection.status in {"completed", "failed", "interrupted"}:
                if public_event_listener is not None:
                    for event in await run_service.replay(
                        run_id=run_projection.run_id,
                        conversation_id=conversation_id,
                        owner_user_id=user_id,
                        after_sequence=0,
                    ):
                        await public_event_listener(event)
                return await _terminal_run_replay(
                    db=db,
                    user_message=user_message,
                    run=run_projection,
                    started=started,
                )
            raise AgentRunInProgress(run_projection.run_id)

        definitions_by_semantic = {
            definition.semantic_name: definition
            for definition in runtime.registry.list()
            if definition.semantic_name
        }
        call_context: dict[str, dict[str, Any]] = {}
        attempt_counts: dict[str, int] = {}
        last_failed_call: dict[str, str] = {}
        timeline = _StepTimeline()

        async def emit_public(event_type: str, payload: dict[str, Any]) -> None:
            assert run_service is not None and run_projection is not None
            event = await run_service.append_public_event(
                run_id=run_projection.run_id,
                event_type=event_type,
                public_payload=payload,
                # Live progress is true only at the instant it is emitted. A
                # reconnecting client must not be told the run is "Writing the
                # answer... 4s" minutes later, so heartbeats are not replayed.
                replayable=event_type != "assistant.progress",
            )
            try:
                if event_type in {"run.started", "runtime.update"} or event_type.startswith(
                    ("tool.", "workflow.", "plan.")
                ):
                    call_ref = str(payload.get("call_ref") or "") or None
                    semantic_name = str(payload.get("semantic_name") or "") or None
                    step_started_at, step_finished_at, step_latency_ms = timeline.measure(
                        event_type=event_type, call_ref=call_ref
                    )
                    context = call_context.get(call_ref or "", {})
                    if not semantic_name:
                        semantic_name = context.get("semantic_name")
                    definition = definitions_by_semantic.get(semantic_name or "")
                    capability_id = definition.id if definition else context.get("capability_id")
                    task_type = (
                        definition.task_types[0]
                        if definition and definition.task_types else context.get("task_type")
                    )
                    reason_code: str | None = None
                    recovery_from: str | None = None
                    parent_call_ref: str | None = None
                    attempt: int | None = context.get("attempt")
                    argument_shape = context.get("argument_shape", {})
                    arguments_hash = context.get("arguments_hash")
                    if event_type == "tool.proposed" and semantic_name:
                        attempt = attempt_counts.get(semantic_name, 0) + 1
                        attempt_counts[semantic_name] = attempt
                        raw_arguments = payload.get("arguments")
                        argument_shape = safe_argument_shape(raw_arguments or {})
                        arguments_hash = hashlib.sha256(
                            json.dumps(raw_arguments or {}, sort_keys=True, default=str).encode()
                        ).hexdigest()
                        recovery_from = last_failed_call.get(semantic_name)
                        parent_call_ref = recovery_from
                        reason_code = (
                            "correct_validation_failure" if recovery_from
                            else "model_selected_capability"
                        )
                        if call_ref:
                            timeline.note_call_proposed(call_ref)
                            call_context[call_ref] = {
                                "semantic_name": semantic_name,
                                "capability_id": capability_id,
                                "task_type": task_type,
                                "attempt": attempt,
                                "argument_shape": argument_shape,
                                "arguments_hash": arguments_hash,
                                "reason_code": reason_code,
                                "recovery_from": recovery_from,
                                "parent_call_ref": parent_call_ref,
                            }
                    elif event_type.startswith("tool."):
                        reason_code = context.get("reason_code")
                        recovery_from = context.get("recovery_from")
                        parent_call_ref = context.get("parent_call_ref")
                        if payload.get("status") in {"validation_failed", "call_set_rejected"} and call_ref and semantic_name:
                            last_failed_call[semantic_name] = call_ref
                    elif event_type == "run.started":
                        reason_code = "server_selected_runtime_cohort"
                    elif event_type == "runtime.update":
                        reason_code = "runtime_phase_completed"
                    await run_service.record_step(
                        run_id=run_projection.run_id,
                        sequence=event.sequence,
                        kind=(
                            "routing" if event_type == "run.started"
                            else event_type.split(".", 1)[0]
                        ),
                        capability_id=capability_id,
                        semantic_name=semantic_name,
                        call_ref=call_ref,
                        parent_call_ref=parent_call_ref,
                        attempt=attempt,
                        phase=str(payload.get("phase") or "") or None,
                        reason_code=reason_code,
                        task_type=task_type,
                        trigger="model_tool_call" if event_type.startswith("tool.") else "runtime",
                        recovery_from=recovery_from,
                        arguments_hash=arguments_hash,
                        argument_shape=argument_shape,
                        validation_errors=tuple(
                            item for item in payload.get("validation_errors", [])
                            if isinstance(item, dict)
                        ),
                        details={
                            "event_type": event_type,
                            "error_code": str(payload.get("error_code") or "") or None,
                            "tool_label": str(payload.get("tool_label") or "") or None,
                            "summary": str(payload.get("summary") or "") or None,
                            "workflow_run_id": payload.get("workflow_run_id"),
                            "selected_cohort": (
                                str(payload.get("cohort") or "") or None
                                if event_type == "run.started" else None
                            ),
                            "transport": (
                                str(payload.get("transport") or "") or None
                                if event_type == "run.started" else None
                            ),
                        },
                        policy_status=(
                            "selected" if event_type == "run.started"
                            else str(payload.get("status") or "") or None
                        ),
                        observation_status=(
                            "selected" if event_type == "run.started"
                            else "completed" if event_type == "runtime.update"
                            else str(payload.get("status") or "") or None
                        ),
                        workflow_ref=payload.get("workflow_ref") if isinstance(payload.get("workflow_ref"), dict) else None,
                        receipt_ref=payload.get("receipt_ref") if isinstance(payload.get("receipt_ref"), dict) else None,
                        evidence_refs=tuple(
                            str(item.get("evidence_id") or item.get("id") or "")
                            for item in payload.get("evidence_refs", [])
                            if isinstance(item, dict)
                        ),
                        latency_ms=step_latency_ms,
                        started_at=step_started_at,
                        finished_at=step_finished_at,
                    )
            except Exception:
                await db.rollback()
                logger.exception("Agent trace step persistence failed for %s", event_type)
            if public_event_listener is not None:
                await public_event_listener(event)

        async def emit_heartbeat(event_type: str, payload: dict[str, Any]) -> None:
            """Publish one live progress heartbeat on an independent session.

            This runs concurrently with the turn's own work, which may be blocked
            for tens of seconds inside a tool call while holding an open
            transaction on ``db``. Committing on that session from another task
            would flush its pending state at an arbitrary point and can raise a
            concurrent-operation error, so the heartbeat gets its own session. The
            run's sequence allocation is already an atomic
            ``UPDATE ... RETURNING``, which is exactly what makes this safe.
            """

            assert run_projection is not None
            try:
                async with async_session_factory() as heartbeat_db:
                    event = await AgentRunService(heartbeat_db).append_public_event(
                        run_id=run_projection.run_id,
                        event_type=event_type,
                        public_payload=payload,
                        replayable=False,
                    )
            except Exception:
                # Includes the benign race where the run went terminal between the
                # tick and the append. Progress is cosmetic: never fail the turn.
                logger.debug("Progress heartbeat was not published", exc_info=True)
                return
            if public_event_listener is not None:
                await public_event_listener(event)

        async def publish_answer_delta(text: str) -> None:
            """Show one batch of the trusted RAG answer while it is generating.

            Provisional by construction: emitted ``replayable=False``, so a
            reconnect replays only the authoritative deltas written after
            persistence. Streaming changes WHEN text appears, never what is stored
            or replayed. Uses the heartbeat's independent session for the same
            reason the ticker does -- this runs while the turn's own session is
            inside the tool call that is producing the text.
            """

            await emit_heartbeat("assistant.delta", {"content": text})

        # Bound only here, once the transport callback exists. Only
        # `answer_from_documentation` can reach this sink, and only because that
        # answer bypasses the grounding validator by already being deterministically
        # cited -- there is no later transform for the streamed text to diverge from.
        runtime.answer_delta_sink.publish = publish_answer_delta

        event_callback = emit_public
        heartbeat_callback = emit_heartbeat
        await event_callback(
            "run.started",
            {
                "status": "running",
                "cohort": cohort.cohort.value,
                "transport": transport,
                "objective": query,
            },
        )
    # Component latencies (embedding / vector search / model) are produced deep
    # inside the retrieval and model layers. This scope is what lets them be
    # attributed to one turn and persisted below. It covers the failure path too:
    # a turn that dies after 40 seconds of model time is exactly the turn whose
    # component timings matter most.
    with turn_timings_scope() as timings:
        try:
            outcome = await _run_primary_or_replay(
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
                cohort=cohort,
                page_scope=page_scope,
                event_callback=event_callback,
                heartbeat_callback=heartbeat_callback,
                budget_controller=budget_controller,
            )
        except Exception as exc:
            # Provider/runtime failures are transport-independent typed stops. The
            # common persistence path below stores one safe assistant message and
            # Agent Run terminal, so non-stream never leaks a raw HTTP 500 while SSE
            # and reconnect replay the exact same failed result.
            logger.error(
                "Agent turn stopped unexpectedly conversation_id=%s message_id=%s "
                "exception_type=%s",
                conversation_id,
                user_message_id,
                type(exc).__name__,
            )
            safe_output = (
                "This turn stopped unexpectedly before I could finish. No completed "
                "action was hidden; please retry the request."
            )
            safe_stop = AgentStopReason.PLANNER_UNAVAILABLE
            outcome = PrimaryTurnOutcome(
                result=AgentLoopResult(
                    action=AgentBoundaryAction.ANSWER,
                    output=safe_output,
                    stop_reason=safe_stop,
                    state_version="",
                    typed_response=render_typed_response(
                        action=AgentBoundaryAction.ANSWER.value,
                        content=safe_output,
                        plan=None,
                        facts=[],
                        state_version="",
                        stop_reason=safe_stop.value,
                    ),
                ),
                grounded=None,
                cohort=cohort,
            )
    result = outcome.result
    assistant_message = await _get_or_create_assistant_message(
        db=db,
        conversation_id=conversation_id,
        user_message_id=user_message_id,
        outcome=outcome,
        latency_ms=max(0, int((time.perf_counter() - started) * 1_000)),
    )

    latency_ms = assistant_message.latency_ms or max(
        0, int((time.perf_counter() - started) * 1_000)
    )
    if event_callback is not None:
        # Answer text is published only here, after the grounding validator
        # accepted it and after it was persisted. Live progress during generation
        # is carried by `assistant.progress` instead, which never contains model
        # prose -- a partially shown answer that validation then rejects would be
        # a compliance regression, so draft prose is not streamed.
        for chunk in public_answer_chunks(result.output):
            await event_callback("assistant.delta", {"content": chunk})
        await event_callback(
            "assistant.completed",
            {"message_id": assistant_message.id, "status": "completed"},
        )
    recorded = await _persist_usage_and_audit(
        db=db,
        conversation_id=conversation_id,
        user_id=user_id,
        user_message_id=user_message_id,
        assistant_message_id=assistant_message.id,
        result=result,
        cohort=cohort,
        transport=transport,
        latency_ms=latency_ms,
        timings=timings,
    )
    await db.commit()
    if run_service is not None and run_projection is not None:
        before_terminal = (
            await run_service.get_owned(
                run_id=run_projection.run_id,
                conversation_id=conversation_id,
                owner_user_id=user_id,
            )
        ).next_event_sequence - 1
        budget_stops = {
            "soft_deadline_exceeded", "hard_deadline_exceeded", "model_call_limit",
            "tool_call_limit", "cost_budget_exceeded",
        }
        failed_stops = {
            "model_timeout", "tool_timeout", "planner_unavailable",
            "invalid_model_output",
        }
        terminal_status = (
            "interrupted" if result.stop_reason.value in budget_stops
            else "failed" if result.stop_reason.value in failed_stops
            else "completed"
        )
        await run_service.finish(
            run_id=run_projection.run_id,
            status=terminal_status,
            stop_reason=result.stop_reason.value,
            assistant_message_id=assistant_message.id,
            usage=result.usage.model_dump(mode="json"),
            timing={
                "latency_ms": latency_ms,
                "model": settings.rag_default_model,
                "provider": (
                    settings.rag_default_model.split("/", 1)[0]
                    if "/" in settings.rag_default_model else None
                ),
                # The component split on the run itself, so a run can be diagnosed
                # from its own row without joining query_usage.
                **timings.component_ms(),
                "model_calls": timings.model_calls,
                "search_calls": timings.search_calls,
            },
            budget=budget_controller.snapshot() if budget_controller else None,
            manifest_snapshot_hash=outcome.manifest_snapshot_hash,
            terminal_payload={
                "status": terminal_status,
                "stop_reason": result.stop_reason.value,
                "usage": result.usage.model_dump(mode="json"),
                "latency_ms": latency_ms,
            },
        )
        if public_event_listener is not None:
            for event in await run_service.replay(
                run_id=run_projection.run_id,
                conversation_id=conversation_id,
                owner_user_id=user_id,
                after_sequence=before_terminal,
            ):
                await public_event_listener(event)
    await db.refresh(user_message)
    await db.refresh(assistant_message)
    if recorded:
        _observe_metrics(
            result=result,
            cohort=cohort,
            transport=transport,
            latency_ms=latency_ms,
        )
    return AgentChatTurnExecution(
        user_message=user_message,
        assistant_message=assistant_message,
        result=result,
        latency_ms=latency_ms,
        run_id=run_projection.run_id if run_projection else None,
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


def grounded_structured_answer(
    outcome: PrimaryTurnOutcome,
    grounded: GroundedAnswer,
) -> dict[str, Any]:
    """Build the persisted ``agent_response_v2_grounded`` payload for one turn.

    ``topology_targets`` is an additive key on that existing payload, and the only
    transport-independent channel to the client: live SSE also emits the same
    targets per tool call, but the non-streaming transport and a page reload read
    them from here. Targets are copied verbatim — their ids belong to whichever id
    space the canvas is rendering and are never re-derived.
    """

    structured_answer = grounded.to_structured_answer(
        runtime_release=READONLY_RUNTIME_RELEASE,
        manifest_snapshot_hash=outcome.manifest_snapshot_hash or "",
        model_profile=READONLY_MODEL_PROFILE,
        prompt_version=READONLY_PROMPT_VERSION,
    )
    if outcome.topology_targets:
        structured_answer["topology_targets"] = list(outcome.topology_targets)
    structured_answer["evidence_summary"] = outcome.evidence_summary.model_dump(mode="json")
    if outcome.evidence_summary.zero_evidence:
        disclosures = structured_answer.setdefault("disclosures", [])
        if "no_current_turn_evidence" not in disclosures:
            disclosures.append("no_current_turn_evidence")
    return structured_answer


async def _get_or_create_assistant_message(
    *,
    db: AsyncSession,
    conversation_id: int,
    user_message_id: int,
    outcome: PrimaryTurnOutcome,
    latency_ms: int,
) -> ConversationMessage:
    result = outcome.result
    grounded = outcome.grounded
    # Flat provenance for both answer shapes. Derived from what this turn already
    # recorded, so it costs no tool call and cannot alter the answer.
    provenance = safe_project_turn_provenance(
        observations=result.observations,
        document_sources=outcome.document_sources,
        evidence_summary=outcome.evidence_summary,
    )
    if grounded is not None:
        # V2 read-only path: persist the validated grounded answer additively.
        # No fact-ID renderer runs and no workflow card is linked.
        candidate = ConversationMessage(
            conversation_id=conversation_id,
            role="assistant",
            content=grounded.content,
            in_reply_to_message_id=user_message_id,
            workflow_run_id=None,
            latency_ms=latency_ms,
            runtime_tool_summary=provenance.runtime_tool_summary,
            primary_source_layer=provenance.primary_source_layer,
            fallback_used=provenance.fallback_used,
        )
        candidate.sources = (
            [source.model_dump(mode="json") for source in outcome.document_sources]
            or None
        )
        persist_grounded_agent_response(
            candidate,
            content=grounded.content,
            structured_answer=grounded_structured_answer(outcome, grounded),
        )
        return await _insert_or_load_assistant_message(
            db=db, candidate=candidate, user_message_id=user_message_id
        )
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
        runtime_tool_summary=provenance.runtime_tool_summary,
        primary_source_layer=provenance.primary_source_layer,
        fallback_used=provenance.fallback_used,
    )
    persist_typed_agent_response(candidate, result)
    return await _insert_or_load_assistant_message(
        db=db, candidate=candidate, user_message_id=user_message_id
    )


async def _insert_or_load_assistant_message(
    *,
    db: AsyncSession,
    candidate: ConversationMessage,
    user_message_id: int,
) -> ConversationMessage:
    """Idempotently persist one assistant message keyed on the user message."""

    statement = _insert_for_session(db, ConversationMessage.__table__).values(
        conversation_id=candidate.conversation_id,
        role=candidate.role,
        content=candidate.content,
        in_reply_to_message_id=candidate.in_reply_to_message_id,
        workflow_run_id=candidate.workflow_run_id,
        latency_ms=candidate.latency_ms,
        structured_answer=candidate.structured_answer,
        sources=candidate.sources,
        runtime_tool_summary=candidate.runtime_tool_summary,
        primary_source_layer=candidate.primary_source_layer,
        fallback_used=candidate.fallback_used,
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
    cohort: CohortDecision,
    transport: str,
    latency_ms: int,
    timings: TurnTimings | None = None,
) -> bool:
    component_ms = (timings or TurnTimings()).component_ms()
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
            cohort_source=cohort.cohort_source,
            cohort_bucket=cohort.cohort_bucket,
            transport=transport,
            state_version=result.state_version,
            terminal_action=result.action.value if result.action is not None else None,
            stop_reason=result.stop_reason.value,
            capability_ids=capability_ids,
            observation_statuses=observation_statuses,
            response_fingerprint=result.response_fingerprint,
            flags={
                "canary_stage": settings.agent_runtime_v2_canary_stage,
                "runtime_cohort": cohort.cohort.value,
                "repeat": result.stop_reason.value == "repeated_response",
                "clarification": result.action is not None
                and result.action.value == "clarify",
                "denied": any(value == "denied" for value in observation_statuses),
                "stale_workflow": stale_workflow,
                "state_conflict": result.stop_reason.value == "state_conflict",
                # Cache accounting is auditable without a schema change: the audit
                # row has no cached-token column, so the ratio that produced the
                # discounted `cost_usd` is recorded on this existing JSON column.
                "cached_input_tokens": result.usage.cached_input_tokens,
                "cache_hit_ratio": round(result.usage.cache_hit_ratio, 4),
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
            # The three component columns exist for every interface but were only
            # ever written by the older /query paths. Without them a chat turn's
            # 24s was one opaque number; with them the model/retrieval split is
            # queryable per turn.
            embedding_ms=component_ms["embedding_ms"],
            search_ms=component_ms["search_ms"],
            llm_ms=component_ms["llm_ms"],
        )
    )
    return True


def _select_cohort(
    *,
    runtime: ProductionAgentTurnRuntime,
    user_id: int,
    transport: str,
    permissions: set[str] | None,
    project_scope: list[str] | None,
) -> CohortDecision:
    """Resolve the runtime cohort from server-owned signals only."""

    manifest = registered_capability_manifest(
        runtime.registry,
        permissions=permissions,
        project_ids=project_scope,
    )
    manifest_has_consequential = any(
        tool.risk_tier != HarnessRiskTier.READ_ONLY.value for tool in manifest.tools
    )
    return CohortSelector.from_settings(settings).select(
        user_id=user_id,
        transport=transport,
        manifest_has_consequential=manifest_has_consequential,
    )


async def _claim_runtime_cohort(
    *,
    db: AsyncSession,
    user_message: ConversationMessage,
    candidate: CohortDecision,
    live_streaming: bool,
) -> CohortDecision:
    """Atomically bind one finite route decision to the durable client turn."""

    snapshot = {
        "schema": "runtime_route_v2",
        "canary_stage": settings.agent_runtime_v2_canary_stage,
        "live_streaming": bool(live_streaming),
        "decision": candidate.model_dump(mode="json"),
    }
    claimed = (
        await db.execute(
            update(ConversationMessage)
            .where(
                ConversationMessage.id == user_message.id,
                ConversationMessage.turn_plan.is_(None),
            )
            .values(turn_plan=snapshot)
            .returning(ConversationMessage.id)
        )
    ).scalar_one_or_none()
    if claimed is not None:
        user_message.turn_plan = snapshot
        await db.commit()
        return candidate
    await db.refresh(user_message, ["turn_plan"])
    stored = user_message.turn_plan
    if not isinstance(stored, dict) or stored.get("schema") != "runtime_route_v2":
        raise AgentChatIdempotencyConflict(
            "client turn already has an incompatible runtime route snapshot"
        )
    try:
        return CohortDecision.model_validate(stored.get("decision"))
    except ValueError as exc:
        raise AgentChatIdempotencyConflict(
            "client turn runtime route snapshot is invalid"
        ) from exc


async def _run_readonly_primary_turn(
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
    cohort: CohortDecision,
    page_scope: dict[str, Any] | None = None,
    event_callback: Callable[[str, dict[str, Any]], Awaitable[None]] | None = None,
    heartbeat_callback: Callable[[str, dict[str, Any]], Awaitable[None]] | None = None,
    budget_controller: LayeredBudgetController | None = None,
) -> PrimaryTurnOutcome:
    """Execute one V2 read-only turn: real reads, grounded answer, no effects."""

    turn_input = await build_authoritative_agent_input(
        conversation=conversation,
        query=query,
        db=db,
        user_id=user_id,
        project_scope=project_scope,
        current_message_id=message_id,
        images=images,
        state_repository=runtime.state_repository,
    )
    context = CapabilityExecutionContext(
        user_id=user_id,
        conversation_id=conversation.id,
        project_scope=project_scope,
        permissions=permissions,
        access_token=access_token,
        current_message_id=message_id,
        denied_capability_ids=_chat_denied_capability_ids(query, page_scope),
    )
    readonly_runtime = build_readonly_primary_runtime(
        model=build_readonly_primary_chat_model(settings),
        registry=runtime.registry,
        executor=runtime.executor,
        context=context,
        scope=(
            ManifestScope(
                permissions=permissions,
                project_ids=project_scope,
                include_bounded_loop_only=True,
                allowed_capability_ids=set(),
            )
            if cohort.is_v2_direct_primary
            else None
        ),
        system_prompt_suffix=_turn_prompt_context(project_scope, page_scope),
        budget_controller=budget_controller,
    )
    turn = await run_readonly_primary_turn(
        graph=readonly_runtime.graph,
        ledger=readonly_runtime.ledger,
        snapshot=readonly_runtime.snapshot,
        user_message=query,
        conversation_seed=turn_input.conversation_history,
        state_version=turn_input.state_version or "",
        prior_response_fingerprints=set(turn_input.prior_response_fingerprints),
        max_corrections=settings.agent_runtime_v2_max_grounding_corrections,
        suppress_structural_citations=is_topology_surface(page_scope),
        event_callback=event_callback,
        heartbeat_callback=heartbeat_callback,
        # Intentionally False on all three cohorts: this caller replays the answer
        # only after it is validated and persisted. Live feedback comes from the
        # `assistant.progress` events the runtime emits during generation.
        emit_answer_deltas=False,
        budget_controller=budget_controller,
        trusted_answer_sink=runtime.trusted_answer_sink,
    )
    return PrimaryTurnOutcome(
        result=turn.result,
        grounded=turn.grounded,
        cohort=cohort,
        manifest_snapshot_hash=readonly_runtime.snapshot.snapshot_hash,
        document_sources=turn.document_sources,
        topology_targets=turn.topology_targets,
        evidence_summary=turn.evidence_summary,
    )


async def _run_planning_primary_turn(
    *, db: AsyncSession, conversation: Conversation, query: str, user_id: int,
    project_scope: list[str] | None, permissions: set[str] | None,
    access_token: str | None, runtime: ProductionAgentTurnRuntime,
    images: list[str] | None, message_id: int, cohort: CohortDecision,
    page_scope: dict[str, Any] | None = None,
    event_callback: Callable[[str, dict[str, Any]], Awaitable[None]] | None = None,
    heartbeat_callback: Callable[[str, dict[str, Any]], Awaitable[None]] | None = None,
    budget_controller: LayeredBudgetController | None = None,
) -> PrimaryTurnOutcome:
    turn_input = await build_authoritative_agent_input(conversation=conversation, query=query, db=db, user_id=user_id, project_scope=project_scope, current_message_id=message_id, images=images, state_repository=runtime.state_repository)
    state = await runtime.state_repository.load(conversation_id=conversation.id, owner_user_id=user_id)
    claim = await runtime.state_repository.claim_turn(conversation_id=conversation.id, owner_user_id=user_id, message_id=message_id, expected_version=state.version, idempotency_key=f"plan-turn:{conversation.id}:{message_id}")
    if claim.replayed and isinstance(claim.final_result, dict):
        return PrimaryTurnOutcome(result=AgentLoopResult.model_validate(claim.final_result), grounded=None, cohort=cohort)
    await db.commit()
    plan_context = await AgentPlanService(db, runtime.registry).current(conversation_id=conversation.id, owner_user_id=user_id)
    context = CapabilityExecutionContext(user_id=user_id, conversation_id=conversation.id, project_scope=project_scope, permissions=permissions, access_token=access_token, current_message_id=message_id, conversation_state_version=state.version, conversation_turn_claim_id=claim.id, conversation_turn_claim_token=claim.lease_token, denied_capability_ids=_chat_denied_capability_ids(query, page_scope))
    plan_runtime = build_planning_primary_runtime(model=build_readonly_primary_chat_model(settings), registry=runtime.registry, executor=runtime.executor, context=context, plan_context=(plan_context.model_projection() if plan_context else None), turn_context=_turn_prompt_context(project_scope, page_scope), budget_controller=budget_controller)
    try:
        turn = await run_readonly_primary_turn(graph=plan_runtime.graph, ledger=plan_runtime.ledger, snapshot=plan_runtime.snapshot, user_message=query, conversation_seed=turn_input.conversation_history, state_version=turn_input.state_version or "", prior_response_fingerprints=set(turn_input.prior_response_fingerprints), max_corrections=settings.agent_runtime_v2_max_grounding_corrections, suppress_structural_citations=is_topology_surface(page_scope), event_callback=event_callback, heartbeat_callback=heartbeat_callback, emit_answer_deltas=False, budget_controller=budget_controller, trusted_answer_sink=runtime.trusted_answer_sink)
    except Exception:
        await db.rollback()
        await runtime.state_repository.fail_claim(
            claim_id=claim.id,
            lease_token=claim.lease_token or "",
            final_result={"stop_reason": "runtime_failure"},
        )
        await db.commit()
        raise
    projected = any(
        event.kind == "gateway.executor_completed"
        and event.canonical_id == "nexus.agent_plan.project_objective"
        and event.observation_status == "succeeded"
        for event in plan_runtime.ledger.events
    )
    budget_stopped = turn.result.stop_reason in {
        AgentStopReason.MODEL_TIMEOUT, AgentStopReason.TOOL_TIMEOUT,
        AgentStopReason.SOFT_DEADLINE_EXCEEDED, AgentStopReason.HARD_DEADLINE_EXCEEDED,
        AgentStopReason.MODEL_CALL_LIMIT, AgentStopReason.TOOL_CALL_LIMIT,
        AgentStopReason.COST_BUDGET_EXCEEDED,
    }
    clarification_stopped = (
        turn.result.stop_reason is AgentStopReason.CLARIFICATION_REQUIRED
    )
    trusted_document_answer = runtime.trusted_answer_sink.answer is not None
    if (
        not projected
        and not budget_stopped
        and not clarification_stopped
        and not trusted_document_answer
    ):
        turn.result = turn.result.model_copy(
            update={"stop_reason": AgentStopReason.INVALID_MODEL_OUTPUT}
        )
    required = any(event.plan_required is True for event in plan_runtime.ledger.events)
    changed = any(
        event.kind == "gateway.executor_completed"
        and event.canonical_id in {"nexus.agent_plan.create_agent_plan", "nexus.agent_plan.revise_agent_plan"}
        and event.observation_status == "succeeded"
        for event in plan_runtime.ledger.events
    )
    if (
        required
        and not changed
        and not budget_stopped
        and not clarification_stopped
        and not trusted_document_answer
    ):
        turn.result = turn.result.model_copy(
            update={"stop_reason": AgentStopReason.INVALID_MODEL_OUTPUT}
        )
    await runtime.state_repository.finalize_claim(claim_id=claim.id, lease_token=claim.lease_token or "", action="agent_plan_turn", final_result=turn.result.model_dump(mode="json"), response_fingerprint=turn.result.response_fingerprint)
    await db.commit()
    return PrimaryTurnOutcome(
        result=turn.result,
        grounded=turn.grounded,
        cohort=cohort,
        manifest_snapshot_hash=plan_runtime.snapshot.snapshot_hash,
        document_sources=turn.document_sources,
        topology_targets=turn.topology_targets,
        evidence_summary=turn.evidence_summary,
    )


async def _run_effect_primary_turn(
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
    cohort: CohortDecision,
    page_scope: dict[str, Any] | None = None,
    event_callback: Callable[[str, dict[str, Any]], Awaitable[None]] | None = None,
    heartbeat_callback: Callable[[str, dict[str, Any]], Awaitable[None]] | None = None,
    budget_controller: LayeredBudgetController | None = None,
) -> PrimaryTurnOutcome:
    turn_input = await build_authoritative_agent_input(
        conversation=conversation,
        query=query,
        db=db,
        user_id=user_id,
        project_scope=project_scope,
        current_message_id=message_id,
        images=images,
        state_repository=runtime.state_repository,
    )
    state = await runtime.state_repository.load(
        conversation_id=conversation.id, owner_user_id=user_id
    )
    claim = await runtime.state_repository.claim_turn(
        conversation_id=conversation.id,
        owner_user_id=user_id,
        message_id=message_id,
        expected_version=state.version,
        idempotency_key=f"effect-turn:{conversation.id}:{message_id}",
    )
    if claim.replayed and isinstance(claim.final_result, dict):
        return PrimaryTurnOutcome(
            result=AgentLoopResult.model_validate(claim.final_result),
            grounded=None,
            cohort=cohort,
        )
    await db.commit()
    plan = await runtime.agent_plan_service.reconcile_workflow_outcomes(
        conversation_id=conversation.id,
        owner_user_id=user_id,
        message_id=message_id,
    )
    stage_policy = CanaryStagePolicy(stage=settings.agent_runtime_v2_canary_stage)
    context = CapabilityExecutionContext(
        user_id=user_id,
        conversation_id=conversation.id,
        project_scope=project_scope,
        permissions=permissions,
        access_token=access_token,
        current_message_id=message_id,
        conversation_state_version=state.version,
        conversation_turn_claim_id=claim.id,
        conversation_turn_claim_token=claim.lease_token,
        allowed_capability_ids=stage_policy.allowed_effect_capability_ids(
            runtime.registry
        ),
        denied_capability_ids=_chat_denied_capability_ids(query, page_scope),
    )
    effect_runtime = build_effect_primary_runtime(
        model=build_readonly_primary_chat_model(settings),
        registry=runtime.registry,
        executor=runtime.executor,
        context=context,
        approval_service=runtime.approval_service,
        effect_operation_service=runtime.effect_operation_service,
        agent_plan_service=runtime.agent_plan_service,
        background_context={
            "agent_plan": plan.model_projection() if plan else None,
            "conversation_tasks": [item.model_dump(mode="json") for item in state.active_tasks],
            "prepared_action": (
                state.prepared_action.model_dump(mode="json")
                if state.prepared_action is not None
                else None
            ),
            "turn_scope": json.loads(_authoritative_turn_context(project_scope)),
        },
        turn_guidance=topology_turn_guidance(page_scope),
        budget_controller=budget_controller,
    )
    try:
        turn = await run_readonly_primary_turn(
            graph=effect_runtime.graph,
            ledger=effect_runtime.ledger,
            snapshot=effect_runtime.snapshot,
            user_message=query,
            conversation_seed=turn_input.conversation_history,
            state_version=turn_input.state_version or "",
            prior_response_fingerprints=set(turn_input.prior_response_fingerprints),
            max_corrections=settings.agent_runtime_v2_max_grounding_corrections,
            suppress_structural_citations=is_topology_surface(page_scope),
            event_callback=event_callback,
            heartbeat_callback=heartbeat_callback,
            emit_answer_deltas=False,
            budget_controller=budget_controller,
            trusted_answer_sink=runtime.trusted_answer_sink,
        )
    except Exception:
        await db.rollback()
        await runtime.state_repository.fail_claim(
            claim_id=claim.id,
            lease_token=claim.lease_token or "",
            final_result={"stop_reason": "runtime_failure"},
        )
        await db.commit()
        raise
    # A trusted deterministic answer is never overridden by a turn-assessment
    # problem. Both branches below key on "no consequential tool completed", which
    # is ALWAYS true for `answer_from_documentation` because it is a read
    # capability -- so a malformed assessment call could replace a real, cited
    # answer with canned failure text or a clarifying question. Now that the answer
    # streams as it is produced, that would retract text the user has already read.
    # The planning cohort already guards its equivalent overrides this way.
    trusted_document_answer = runtime.trusted_answer_sink.answer is not None
    if (
        effect_runtime.middleware.interaction_grounding_failed
        and effect_runtime.middleware.completed_consequential_semantic_name is None
        and not trusted_document_answer
    ):
        _apply_interaction_grounding_failure(turn)
    elif (
        effect_runtime.middleware.grounded_clarification is not None
        and effect_runtime.middleware.completed_consequential_semantic_name is None
        and not trusted_document_answer
    ):
        question = effect_runtime.middleware.grounded_clarification
        fingerprint = response_fingerprint(
            AgentBoundaryAction.CLARIFY,
            question,
            state_version=turn.result.state_version,
            observations=turn.result.observations,
        )
        turn.result = turn.result.model_copy(
            update={
                "action": AgentBoundaryAction.CLARIFY,
                "output": question,
                "stop_reason": AgentStopReason.CLARIFICATION_REQUIRED,
                "response_fingerprint": fingerprint,
            }
        )
        if turn.grounded is not None:
            turn.grounded = turn.grounded.model_copy(update={"content": question})
    prepared_action, clear_prepared_action = _prepared_action_finalization(
        state=state,
        candidate=effect_runtime.middleware.prepared_action_candidate,
        completed_semantic_name=(
            effect_runtime.middleware.completed_consequential_semantic_name
        ),
        message_id=message_id,
    )
    await runtime.state_repository.finalize_claim(
        claim_id=claim.id,
        lease_token=claim.lease_token or "",
        action="agent_effect_turn",
        final_result=turn.result.model_dump(mode="json"),
        response_fingerprint=turn.result.response_fingerprint,
        prepared_action=prepared_action,
        clear_prepared_action=clear_prepared_action,
    )
    await db.commit()
    return PrimaryTurnOutcome(
        result=turn.result,
        grounded=turn.grounded,
        cohort=cohort,
        manifest_snapshot_hash=effect_runtime.snapshot.snapshot_hash,
        document_sources=turn.document_sources,
        topology_targets=turn.topology_targets,
        evidence_summary=turn.evidence_summary,
    )


def _prepared_action_finalization(
    *,
    state: ConversationState,
    candidate: ConversationPreparedAction | None,
    completed_semantic_name: str | None,
    message_id: int,
) -> tuple[ConversationPreparedAction | None, bool]:
    active = candidate or state.prepared_action
    if (
        completed_semantic_name is not None
        and active is not None
        and active.semantic_name == completed_semantic_name
    ):
        return None, True
    if candidate is None:
        return None, False
    previous = state.prepared_action
    same_action = bool(
        previous is not None and previous.capability_id == candidate.capability_id
    )
    source_ids = list(previous.source_message_ids if same_action and previous else [])
    if message_id not in source_ids:
        source_ids.append(message_id)
    return (
        candidate.model_copy(
            update={
                "source_message_ids": source_ids[-12:],
                "revision": previous.revision + 1 if same_action and previous else 1,
            }
        ),
        False,
    )


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
    cohort: CohortDecision,
    page_scope: dict[str, Any] | None = None,
    event_callback: Callable[[str, dict[str, Any]], Awaitable[None]] | None = None,
    heartbeat_callback: Callable[[str, dict[str, Any]], Awaitable[None]] | None = None,
    budget_controller: LayeredBudgetController | None = None,
) -> PrimaryTurnOutcome:
    if cohort.is_v2_effect_primary:
        return await _run_effect_primary_turn(
            db=db,
            conversation=conversation,
            query=query,
            user_id=user_id,
            project_scope=project_scope,
            permissions=permissions,
            access_token=access_token,
            runtime=runtime,
            images=images,
            message_id=message_id,
            cohort=cohort,
            page_scope=page_scope,
            event_callback=event_callback,
            heartbeat_callback=heartbeat_callback,
            budget_controller=budget_controller,
        )
    if cohort.is_v2_plan_primary:
        return await _run_planning_primary_turn(
            db=db, conversation=conversation, query=query, user_id=user_id,
            project_scope=project_scope, permissions=permissions, access_token=access_token,
            runtime=runtime, images=images, message_id=message_id, cohort=cohort,
            page_scope=page_scope, event_callback=event_callback,
            heartbeat_callback=heartbeat_callback,
            budget_controller=budget_controller,
        )
    if cohort.is_v2_readonly:
        return await _run_readonly_primary_turn(
            db=db,
            conversation=conversation,
            query=query,
            user_id=user_id,
            project_scope=project_scope,
            permissions=permissions,
            access_token=access_token,
            runtime=runtime,
            images=images,
            message_id=message_id,
            cohort=cohort,
            page_scope=page_scope,
            event_callback=event_callback,
            heartbeat_callback=heartbeat_callback,
            budget_controller=budget_controller,
        )
    result = await _run_legacy_primary_or_replay(
        db=db,
        conversation=conversation,
        query=query,
        user_id=user_id,
        project_scope=project_scope,
        permissions=permissions,
        access_token=access_token,
        runtime=runtime,
        images=images,
        message_id=message_id,
        page_scope=page_scope,
    )
    return PrimaryTurnOutcome(result=result, grounded=None, cohort=cohort)


async def _run_legacy_primary_or_replay(
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
    page_scope: dict[str, Any] | None = None,
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
            denied_capability_ids=_chat_denied_capability_ids(query, page_scope),
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


def _layered_budget_limits() -> LayeredBudgetLimits:
    return LayeredBudgetLimits(
        model_timeout_seconds=settings.agent_runtime_v2_model_timeout_seconds,
        read_tool_timeout_seconds=settings.agent_runtime_v2_read_tool_timeout_seconds,
        write_tool_timeout_seconds=settings.agent_runtime_v2_write_tool_timeout_seconds,
        workflow_handoff_timeout_seconds=settings.agent_runtime_v2_workflow_handoff_timeout_seconds,
        soft_deadline_seconds=settings.agent_runtime_v2_soft_deadline_seconds,
        hard_deadline_seconds=settings.agent_runtime_v2_hard_deadline_seconds,
        max_model_calls=settings.agent_runtime_v2_max_model_calls,
        synthesis_reserve_calls=settings.agent_runtime_v2_synthesis_reserve_calls,
        max_tool_calls=settings.agent_chat_max_tool_calls,
        max_cost_usd=settings.agent_chat_max_cost_usd,
    )


async def _terminal_run_replay(
    *,
    db: AsyncSession,
    user_message: ConversationMessage,
    run: AgentRunProjection,
    started: float,
) -> AgentChatTurnExecution:
    assistant = (
        await db.execute(
            select(ConversationMessage).where(
                ConversationMessage.in_reply_to_message_id == user_message.id
            )
        )
    ).scalar_one_or_none()
    if assistant is None:
        raise AgentRunInProgress(run.run_id)
    try:
        stop_reason = AgentStopReason(run.stop_reason or "answered")
    except ValueError:
        stop_reason = AgentStopReason.INVALID_MODEL_OUTPUT
    result = AgentLoopResult(
        action=AgentBoundaryAction.ANSWER,
        output=assistant.content,
        stop_reason=stop_reason,
        state_version="",
    )
    return AgentChatTurnExecution(
        user_message=user_message,
        assistant_message=assistant,
        result=result,
        latency_ms=assistant.latency_ms or max(0, int((time.perf_counter() - started) * 1_000)),
        run_id=run.run_id,
        replayed=True,
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
    cohort: CohortDecision,
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
    agent_runtime_canary_turns_total.labels(
        settings.agent_runtime_v2_canary_stage,
        cohort.cohort.value,
        transport,
        outcome,
    ).inc()
    agent_runtime_canary_latency_seconds.labels(
        settings.agent_runtime_v2_canary_stage,
        cohort.cohort.value,
        transport,
    ).observe(latency_ms / 1_000)
    agent_runtime_canary_cost_usd.labels(
        settings.agent_runtime_v2_canary_stage,
        cohort.cohort.value,
    ).observe(result.usage.total_cost_usd)
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
        agent_runtime_canary_guard_total.labels(
            settings.agent_runtime_v2_canary_stage,
            cohort.cohort.value,
            guard,
            "triggered" if triggered else "clear",
        ).inc()


async def _compile_exact_project_pack(
    *,
    db: AsyncSession,
    registry: CapabilityRegistry,
    project_id: str,
) -> CapabilityRegistry:
    # Project-specific stack composition was retired in favour of the MCP
    # plugin-pack binding. Keep the registry passed by the caller intact: it
    # remains the fixture-backed chat capability allowlist, not a creatable
    # documentation pack.
    _ = (db, project_id)
    return registry


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
    "AgentRunInProgress",
    "ProductionAgentTurnRuntime",
    "build_production_agent_turn_runtime",
    "execute_production_agent_chat_turn",
    "grounded_structured_answer",
]
