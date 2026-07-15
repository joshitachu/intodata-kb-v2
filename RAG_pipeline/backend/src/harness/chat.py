"""Chat-facing orchestration for Nexus harness capabilities.

This module keeps harness tool execution outside the normal document-QA path.
The first executable capability triggers MCP documentation generation; later
capabilities can reuse the same workflow/activity stream contract.
"""

from __future__ import annotations

import json
import logging
import re
import time
import datetime
import uuid
from collections.abc import AsyncGenerator
from dataclasses import dataclass, replace
from typing import Any

from qdrant_client import AsyncQdrantClient
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import Conversation, ConversationMessage, HarnessConversationContinuationRow
from src.harness import HarnessWorkflowRepository, HarnessWorkflowService, default_capability_registry
from src.harness.evidence import (
    evidence_refs,
    latest_evidence_timestamp,
    list_generated_doc_evidence,
    query_runtime_evidence,
    read_connector_evidence,
    search_document_evidence,
)
from src.harness.evidence_store import (
    EvidenceAnswerSynthesizer,
    EvidenceAnswerStatus,
    EvidenceRankingContext,
    EvidenceSnapshot,
    EvidenceSourceClass,
    HarnessEvidenceRepository,
    InvestigationAnswer,
    OperationalAnswerComposer,
    group_conflicts,
    rank_evidence,
)
from src.harness.executor import (
    CapabilityExecutionContext,
    CapabilityExecutionRequest,
    HarnessCapabilityExecutor,
)
from src.harness.connectors import ConnectorRepository, ConnectorService, default_connector_provider_registry
from src.harness.connectors.capabilities import build_request_capability_registry
from src.harness.manifest import (
    ManifestScope,
    build_runtime_capability_manifest,
    user_facing_capability_lines,
)
from src.harness.models import (
    ActivityEvent,
    ActivityEventStatus,
    DOCUMENTATION_GENERATION_CAPABILITY,
    DOCUMENTATION_GENERATION_STATUS_CAPABILITY,
    DOCUMENTATION_PACK_CREATE_CAPABILITY,
    DOCUMENT_LIST_CAPABILITY,
    DOCUMENT_SEARCH_CAPABILITY,
    DOCUMENT_UPDATE_FROM_EVIDENCE_CAPABILITY,
    EXTERNAL_CONNECTOR_READ_CAPABILITY,
    EvidenceItem,
    OPERATIONAL_INVESTIGATION_CAPABILITY,
    PROJECT_ONBOARDING_CAPABILITY,
    RUNTIME_EVIDENCE_CAPABILITY,
    WorkflowRun,
    WorkflowRunStatus,
    WorkflowRunUpdate,
)
from src.api.metrics import (
    harness_planner_decisions_total,
    harness_planner_fallback_total,
)
from src.harness.planner import (
    EvidencePolicy,
    ContinuationEnvelope,
    ContinuationSlot,
    PlannerAction,
    RuntimeScope,
    RuntimeScopeKind,
    TurnExecutionMode,
    TurnPlan,
    TurnTaskType,
    plan_harness_turn_detailed,
)
from src.harness.providers.mcp_docs import (
    McpDocumentationProvider,
    McpDocumentationProviderError,
    McpEvidenceDocUpdateResult,
    McpDocumentationGenerationResult,
    McpProjectInfo,
)
from src.harness.providers.mcp_projects import McpProjectsProvider
from src.harness.packs.models import PackActivation
from src.harness.packs.project_service import ProjectPackService
from src.harness.packs.runtime import compile_resolved_pack_registry
from src.harness.registry import base_capability_registry
from src.harness.resolution import (
    ConversationalFocus,
    EntityCandidate,
    EntityReference,
    EntityType,
    ResolutionStatus,
    WorkflowKind,
    merge_conversational_focus,
    resolve_entity,
    resolve_time_window,
)
from src.harness.runtime import RuntimeTurnRequest, default_langgraph_runtime_provider
from src.harness.workflows import (
    InvestigationCall,
    OnboardingRequest,
    DocumentationUpdateRequest,
    OperationalInvestigationRequest,
    default_operational_investigation_workflow,
    default_documentation_update_workflow,
    default_project_onboarding_workflow,
    normalize_project_platform,
)
from src.schemas import MessageResponse

logger = logging.getLogger(__name__)

_DOC_GENERATION_RE = re.compile(
    r"\b("
    r"generate|regenerate|create|build|make|publish|run|start|trigger|update"
    r")\b.{0,80}\b("
    r"docs?|documentation|runbook|website|doc generator"
    r")\b|"
    r"\b("
    r"docs?|documentation|runbook|website|doc generator"
    r")\b.{0,80}\b("
    r"generate|regenerate|create|build|make|publish|run|start|trigger|update"
    r")\b",
    re.IGNORECASE,
)

_DOCUMENTATION_PACK_CREATE_RE = re.compile(
    r"\b(?:create|build|define|design|manage)\b.{0,80}\b(?:documentation\s+)?pack\b"
    r"|\b(?:documentation\s+)?pack\b.{0,40}\bcreate\b",
    re.IGNORECASE,
)

_CAPABILITY_QUESTION_RE = re.compile(
    r"\b("
    r"what can you do|what tools|which tools|list tools|show tools|"
    r"capabilities|are you agentic|can you|could you|are you able"
    r")\b.{0,160}\b("
    r"docs?|documentation|generate|make|create|update|logs?|runtime|"
    r"connectors?|sources?|tools?|capabilities|do"
    r")\b",
    re.IGNORECASE,
)

_DOC_QA_RE = re.compile(
    r"\b("
    r"what|where|who|why|how|summari[sz]e|explain|show|find|search|read|list"
    r")\b.{0,80}\b(docs?|documentation|runbook|website)\b",
    re.IGNORECASE,
)

_DOC_UPDATE_FROM_EVIDENCE_RE = re.compile(
    r"\b(update|patch|edit|change)\b.{0,80}\b(docs?|documentation|page|runbook)\b"
    r".{0,120}\b(file|upload|uploaded|attached|attachment|evidence|csv|pdf|docx|spreadsheet)\b|"
    r"\b(file|upload|uploaded|attached|attachment|evidence|csv|pdf|docx|spreadsheet)\b"
    r".{0,120}\b(update|patch|edit|change)\b.{0,80}\b(docs?|documentation|page|runbook)\b|"
    r"\b(update|patch|edit|change)\b.{0,120}\b[A-Za-z0-9][A-Za-z0-9_.-]{0,140}\.md\b"
    r".{0,120}\b(using|with|from|evidence)\b",
    re.IGNORECASE,
)
_DOC_FILENAME_RE = re.compile(r"\b([A-Za-z0-9][A-Za-z0-9_.-]{0,140}\.md)\b")
_DOC_SECTION_RE = re.compile(
    r"\bsection\s+(?:#{1,6}\s*)?[`\"']?([A-Za-z0-9][A-Za-z0-9 _.-]{0,100}?)[`\"']?"
    r"(?=\s+(?:using|with|from|evidence)\b|[,;.]|$)",
    re.IGNORECASE,
)
_EVIDENCE_PREFIX_RE = re.compile(
    r"\b(?:using|with|from|evidence\s*:?)\b\s*(.+)$",
    re.IGNORECASE | re.DOTALL,
)

_CAPABILITY_ID = DOCUMENTATION_GENERATION_CAPABILITY.id
_DOC_STATUS_CAPABILITY_ID = DOCUMENTATION_GENERATION_STATUS_CAPABILITY.id
_DOC_UPDATE_CAPABILITY_ID = DOCUMENT_UPDATE_FROM_EVIDENCE_CAPABILITY.id
_INVESTIGATION_CAPABILITY_ID = OPERATIONAL_INVESTIGATION_CAPABILITY.id
_CONNECTOR_CAPABILITY_ID = EXTERNAL_CONNECTOR_READ_CAPABILITY.id
_PENDING_PROJECT_SCOPE_ID = "__pending_project_scope__"

_INVESTIGATION_RE = re.compile(
    r"\b(investigate|diagnose|root cause|why)\b.{0,120}\b("
    r"fail|failed|failure|error|incident|broken|unhealthy|integration"
    r")\b|"
    r"\b(fail|failed|failure|error|incident|broken|unhealthy)\b.{0,120}\b("
    r"investigate|diagnose|root cause"
    r")\b",
    re.IGNORECASE,
)

_CONNECTOR_INVENTORY_RE = re.compile(
    r"\b(show|list|what|which|check|read)\b.{0,80}\b("
    r"connectors?|sources?|runtime sources?|external systems?"
    r")\b|"
    r"\b(connectors?|sources?|runtime sources?)\b.{0,80}\b("
    r"configured|connected|available|status|health"
    r")\b",
    re.IGNORECASE,
)
_PROJECT_ONBOARDING_RE = re.compile(
    r"\b(?:create|add|start|onboard)\b.{0,40}\bproject\b|"
    r"\bproject\b.{0,40}\b(?:create|add|start|onboard)\b|"
    r"\b(?:create|add|start|onboard)\s+[A-Za-z0-9][A-Za-z0-9_-]{0,99}"
    r"\s+(?:from|using|with)\s+(?:a\s+)?(?:zip|git|repository)\b",
    re.IGNORECASE,
)

_DOC_GENERATION_STATUS_RE = re.compile(
    r"\b(?:docs?|documentation|document generation|generation)\b.{0,100}"
    r"\b(?:done|finished|complete|completed|status|still running|failed)\b|"
    r"\b(?:done|finished|complete|completed|status|still running|failed)\b.{0,100}"
    r"\b(?:docs?|documentation|document generation|generation)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class HarnessChatIntent:
    """Executable harness intent selected from a chat turn."""

    mode: str
    capability_id: str


@dataclass(frozen=True)
class ProjectOption:
    """Safe project option shown during scope clarification."""

    id: str
    display_name: str
    status: str | None = None
    platform: str | None = None


@dataclass(frozen=True)
class HarnessPlannerDecision:
    """Structured planner decision for chat harness routing.

    ``decision_source``/``fallback_reason``/``matched_rule`` record WHY this
    route was chosen so every turn is reconstructable from logs and activity:
    ``model`` (model planned it), ``regex_fallback`` (model could not plan,
    deterministic rule routed it), or ``regex`` (deterministic planner called
    directly, e.g. legacy call sites and tests).
    """

    action: str
    mode: str
    capability_id: str | None = None
    arguments: dict[str, Any] | None = None
    missing_slots: tuple[str, ...] = ()
    confidence: float = 1.0
    user_visible_reason: str = ""
    direct_answer: str | None = None
    tool_calls: tuple[dict[str, Any], ...] = ()
    decision_source: str = "regex"
    fallback_reason: str | None = None
    matched_rule: str | None = None
    planner_note: str | None = None
    planner_latency_ms: int = 0
    turn_plan: TurnPlan | None = None


_PLANNER_ANSWER_DIRECT = "answer_direct"
_PLANNER_ASK_CLARIFICATION = "ask_clarification"
_PLANNER_CALL_CAPABILITY = "call_capability"
_PLANNER_USE_RAG = "use_rag"


def detect_harness_chat_intent(query: str) -> HarnessChatIntent | None:
    """Return the harness intent for a chat query, if it is executable.

    This is now a compatibility wrapper around the planner. Conversation routes
    use it only to decide whether the harness should handle the turn; execution
    re-runs the structured planner with project context.
    """
    decision = plan_harness_chat_turn(query)
    if decision.action == _PLANNER_USE_RAG:
        return None
    return HarnessChatIntent(
        mode=decision.mode,
        capability_id=decision.capability_id or "nexus.capability.answer",
    )


def plan_harness_chat_turn(query: str) -> HarnessPlannerDecision:
    """Plan whether the harness should answer, clarify, call a tool, or use RAG.

    This is a deterministic planner boundary for the first capability-aware
    slice. It produces the same strict shape expected from a future model-backed
    planner, while keeping backend validation in control.
    """
    stripped = query.strip()
    if not stripped:
        return HarnessPlannerDecision(action=_PLANNER_USE_RAG, mode="rag", matched_rule="empty_query")
    if _is_capability_question(stripped):
        return HarnessPlannerDecision(
            action=_PLANNER_ANSWER_DIRECT,
            mode="capability_summary",
            confidence=0.92,
            user_visible_reason="The user asked what Nexus can do.",
            matched_rule="capability_question",
        )
    if _DOC_UPDATE_FROM_EVIDENCE_RE.search(stripped):
        return HarnessPlannerDecision(
            action=_PLANNER_CALL_CAPABILITY,
            mode="update_docs_from_evidence",
            capability_id=_DOC_UPDATE_CAPABILITY_ID,
            arguments={},
            confidence=0.86,
            user_visible_reason="The user asked to update documentation from evidence.",
            matched_rule="doc_update_from_evidence",
        )
    if _DOC_GENERATION_STATUS_RE.search(stripped):
        return HarnessPlannerDecision(
            action=_PLANNER_CALL_CAPABILITY,
            mode="documentation_generation_status",
            capability_id=_DOC_STATUS_CAPABILITY_ID,
            arguments={},
            confidence=0.84,
            user_visible_reason="The user asked for documentation generation status.",
            matched_rule="doc_generation_status",
        )
    if _PROJECT_ONBOARDING_RE.search(stripped):
        project_match = re.search(
            r"\b(?:project(?:\s+(?:called|named))?|called|named)\s+([A-Za-z0-9][A-Za-z0-9_-]{0,99})\b",
            stripped,
            re.IGNORECASE,
        )
        if project_match is None:
            project_match = re.search(
                r"\b(?:create|add|start|onboard)\s+([A-Za-z0-9][A-Za-z0-9_-]{0,99})"
                r"\s+(?:from|using|with)\s+(?:a\s+)?(?:zip|git|repository)\b",
                stripped,
                re.IGNORECASE,
            )
        repo_match = re.search(r"https?://[^\s]+", stripped)
        arguments: dict[str, Any] = {}
        if project_match:
            arguments["project_id"] = project_match.group(1)
        if repo_match:
            arguments["repo_url"] = repo_match.group(0).rstrip(".,)")
        return HarnessPlannerDecision(
            action=(
                _PLANNER_CALL_CAPABILITY
                if "project_id" in arguments
                else _PLANNER_ASK_CLARIFICATION
            ),
            mode="project_onboarding",
            capability_id=PROJECT_ONBOARDING_CAPABILITY.id,
            arguments=arguments,
            confidence=0.7,
            user_visible_reason="The user asked to onboard a project.",
            direct_answer=(
                None
                if "project_id" in arguments
                else "What should the new project be called?"
            ),
            matched_rule="project_onboarding",
        )
    if _INVESTIGATION_RE.search(stripped):
        return HarnessPlannerDecision(
            action=_PLANNER_CALL_CAPABILITY,
            mode="operational_investigation",
            capability_id=_INVESTIGATION_CAPABILITY_ID,
            arguments={},
            confidence=0.82,
            user_visible_reason="The user asked for an operational investigation.",
            matched_rule="investigation",
        )
    if _CONNECTOR_INVENTORY_RE.search(stripped):
        return HarnessPlannerDecision(
            action=_PLANNER_CALL_CAPABILITY,
            mode="connector_inventory",
            capability_id=_CONNECTOR_CAPABILITY_ID,
            arguments={},
            confidence=0.82,
            user_visible_reason="The user asked about available connectors or sources.",
            matched_rule="connector_inventory",
        )
    if _DOC_QA_RE.search(stripped) and not _DOC_GENERATION_RE.search(stripped):
        return HarnessPlannerDecision(
            action=_PLANNER_USE_RAG, mode="rag", confidence=0.8, matched_rule="doc_qa"
        )
    if _DOCUMENTATION_PACK_CREATE_RE.search(stripped):
        return HarnessPlannerDecision(
            action=_PLANNER_ASK_CLARIFICATION,
            mode="create_documentation_pack",
            capability_id=DOCUMENTATION_PACK_CREATE_CAPABILITY.id,
            confidence=0.8,
            user_visible_reason="The user wants to design a reusable documentation Pack.",
            direct_answer=(
                "What should this Pack document, and who is the intended reader? "
                "I’ll use that to propose the document set and sections before saving it."
            ),
            matched_rule="documentation_pack_create",
        )
    if _DOC_GENERATION_RE.search(stripped):
        return HarnessPlannerDecision(
            action=_PLANNER_CALL_CAPABILITY,
            mode="generate_docs",
            capability_id=_CAPABILITY_ID,
            arguments={},
            missing_slots=("project_id",),
            confidence=0.88,
            user_visible_reason="The user asked to generate documentation.",
            matched_rule="doc_generation",
        )
    return HarnessPlannerDecision(action=_PLANNER_USE_RAG, mode="rag", matched_rule="none")


async def plan_model_harness_chat_turn(
    *,
    query: str,
    conversation: Conversation,
    db: AsyncSession,
    project_ids: list[str] | None,
    user_permissions: set[str] | None,
    access_token: str | None,
    provider: McpDocumentationProvider | None = None,
) -> HarnessPlannerDecision:
    """Use the selected model and runtime-safe manifest as the primary router."""
    continuation = await _active_continuation(db, conversation)
    if continuation is not None:
        resumed = await _resume_continuation_decision(
            continuation=continuation,
            query=query,
            conversation=conversation,
            db=db,
            project_ids=project_ids,
            access_token=access_token,
            provider=provider,
        )
        if resumed is not None:
            return _record_planner_decision(resumed)
    docs_provider = provider or McpDocumentationProvider()
    project_options = await _load_project_options(
        project_ids=project_ids,
        access_token=access_token,
        provider=docs_provider,
    )
    registry = await build_request_capability_registry(
        ConnectorRepository(db), project_ids=project_ids
    )
    unavailable_requested_connectors = _unavailable_requested_connector_labels(
        query, registry.list(enabled_only=True)
    )
    pending_summary = await _pending_workflow_summary(db, conversation)
    focus = await _conversation_focus(db, conversation)
    conversation_history = await _planner_conversation_history(db, conversation)
    prior_plan = await _latest_persisted_turn_plan(db, conversation)
    result = await plan_harness_turn_detailed(
        user_message=query,
        manifest=build_runtime_capability_manifest(
            registry,
            scope=ManifestScope(permissions=user_permissions),
        ),
        project_options=project_options,
        pending_workflow_summary=pending_summary,
        conversation_history=conversation_history,
    )
    model_decision = result.decision
    decision: HarnessPlannerDecision | None = None
    if result.decision_source == "model":
        if model_decision.action == PlannerAction.EXECUTE:
            first = model_decision.tool_calls[0]
            mode = _mode_for_capability(first.capability_id)
            if mode is not None:
                action = _PLANNER_USE_RAG if mode == "document_answer" else _PLANNER_CALL_CAPABILITY
                decision = HarnessPlannerDecision(
                    action=action,
                    mode=mode,
                    capability_id=first.capability_id,
                    arguments=dict(first.arguments),
                    confidence=model_decision.confidence,
                    user_visible_reason="Nexus selected an available capability for this request.",
                    tool_calls=tuple(
                        {
                            "capability_id": call.capability_id,
                            "arguments": dict(call.arguments),
                        }
                        for call in model_decision.tool_calls
                    ),
                )
        elif model_decision.action == PlannerAction.CLARIFY:
            deterministic = plan_harness_chat_turn(query)
            if deterministic.mode in {"generate_docs", "create_documentation_pack"}:
                decision = replace(
                    deterministic,
                    confidence=model_decision.confidence,
                    user_visible_reason=(
                        "Documentation generation needs a project scope."
                        if deterministic.mode == "generate_docs"
                        else "Reusable Pack creation needs a few design choices."
                    ),
                    direct_answer=(
                        model_decision.clarification
                        or deterministic.direct_answer
                    ),
                )
            elif (
                prior_plan is not None
                and prior_plan.task_type is TurnTaskType.DOCUMENTATION_PACK_MANAGEMENT
            ):
                decision = HarnessPlannerDecision(
                    action=_PLANNER_ASK_CLARIFICATION,
                    mode="create_documentation_pack",
                    capability_id=DOCUMENTATION_PACK_CREATE_CAPABILITY.id,
                    confidence=model_decision.confidence,
                    user_visible_reason="Continuing the reusable Pack design.",
                    direct_answer=(
                        model_decision.clarification
                        or "What should I refine before saving this reusable Pack?"
                    ),
                )
            else:
                decision = HarnessPlannerDecision(
                    action=_PLANNER_ASK_CLARIFICATION,
                    mode="model_clarification",
                    confidence=model_decision.confidence,
                    user_visible_reason=model_decision.clarification or "I need one detail before continuing.",
                    direct_answer=model_decision.clarification,
                )
        elif model_decision.action == PlannerAction.ANSWER and model_decision.answer:
            decision = HarnessPlannerDecision(
                action=_PLANNER_ANSWER_DIRECT,
                mode="model_answer",
                confidence=model_decision.confidence,
                user_visible_reason="Nexus answered without running a workflow.",
                direct_answer=model_decision.answer,
            )
        elif model_decision.action in {PlannerAction.CONTINUE, PlannerAction.CANCEL}:
            decision = HarnessPlannerDecision(
                action=_PLANNER_ASK_CLARIFICATION,
                mode=model_decision.action.value,
                confidence=model_decision.confidence,
                user_visible_reason="Choose the pending workflow action shown in this conversation.",
                direct_answer="Choose the pending workflow action shown above so I can continue safely.",
            )
        if decision is not None:
            decision = replace(
                decision,
                decision_source="model",
                planner_note=result.note,
                planner_latency_ms=result.latency_ms,
            )

    if decision is None:
        # Compatibility only: preserve known safe flows when the configured
        # model cannot plan. Unknown requests return to ordinary RAG.
        fallback = _scope_safe_fallback_decision(
            query=query,
            fallback=plan_harness_chat_turn(query),
            focus=focus,
        )
        decision = replace(
            fallback,
            decision_source="regex_fallback",
            fallback_reason=result.fallback_reason or "unmappable_decision",
            planner_note=result.note,
            planner_latency_ms=result.latency_ms,
        )
    if _explicit_document_switch(query) or _is_document_only_request(query) or _is_migration_discovery(query, conversation_history):
        migration_discovery = _is_migration_discovery(query, conversation_history)
        decision = HarnessPlannerDecision(
            action=_PLANNER_USE_RAG,
            mode="document_answer",
            confidence=max(decision.confidence, 0.9),
            user_visible_reason="The request requires project documentation, not runtime evidence.",
            decision_source="evidence_policy_guard",
            fallback_reason=decision.fallback_reason,
            planner_note=decision.planner_note,
            planner_latency_ms=decision.planner_latency_ms,
            matched_rule="document_evidence_only",
            arguments=(
                {"output_contract": "migration_proposal"}
                if migration_discovery
                else decision.arguments
            ),
        )
    elif _is_runtime_aggregation(query):
        decision = HarnessPlannerDecision(
            action=_PLANNER_CALL_CAPABILITY,
            mode="runtime_answer",
            capability_id=RUNTIME_EVIDENCE_CAPABILITY.id,
            arguments={"question": query},
            confidence=max(decision.confidence, 0.9),
            user_visible_reason="The request is a runtime evidence question, not an incident diagnosis.",
            decision_source="evidence_policy_guard",
            fallback_reason=decision.fallback_reason,
            planner_note=decision.planner_note,
            planner_latency_ms=decision.planner_latency_ms,
            matched_rule="runtime_aggregation",
        )
    if _is_recomposition_request(query) and prior_plan is not None:
        if prior_plan.task_type in {
            TurnTaskType.DOCUMENT_ANSWER,
            TurnTaskType.RUNTIME_ANSWER,
        }:
            runtime = prior_plan.task_type is TurnTaskType.RUNTIME_ANSWER
            prior_run_id = (
                await _latest_evidence_run_id(db, conversation) if runtime else None
            )
            decision = HarnessPlannerDecision(
                action=_PLANNER_CALL_CAPABILITY if runtime else _PLANNER_USE_RAG,
                mode="evidence_recomposition" if runtime else "document_answer",
                capability_id=RUNTIME_EVIDENCE_CAPABILITY.id if runtime else None,
                arguments={
                    "output_contract": prior_plan.output_contract,
                    **(
                        {"recompose_from_run_id": prior_run_id}
                        if prior_run_id is not None
                        else {}
                    ),
                },
                confidence=max(decision.confidence, 0.95),
                user_visible_reason="Reformatting the prior answer without collecting new evidence.",
                decision_source="continuation_guard",
                planner_note=decision.planner_note,
                planner_latency_ms=decision.planner_latency_ms,
                matched_rule="evidence_recomposition",
                turn_plan=(
                    prior_plan.model_copy(
                        update={
                            "continuation": {
                                **prior_plan.continuation,
                                "state": "recomposed",
                                "request": query.strip()[:500],
                            },
                            "decision_source": "continuation_guard",
                        }
                    )
                    if runtime and prior_run_id is not None
                    else None
                ),
            )
    if (
        unavailable_requested_connectors
        and re.search(r"\b(search|check|find|read|show|messages?|logs?|investigate)\b", query, re.IGNORECASE)
        and decision.mode not in {"operational_investigation", "evidence_investigation"}
    ):
        decision = HarnessPlannerDecision(
            action=_PLANNER_CALL_CAPABILITY,
            mode="operational_investigation",
            capability_id=_INVESTIGATION_CAPABILITY_ID,
            arguments={},
            confidence=max(decision.confidence, 0.8),
            user_visible_reason="The requested connector is unavailable, so Nexus will ground the answer in available evidence and disclose the gap.",
            decision_source="capability_guard",
            fallback_reason=decision.fallback_reason,
            planner_note=decision.planner_note,
            planner_latency_ms=decision.planner_latency_ms,
            matched_rule="unavailable_requested_connector",
        )
    if decision.mode == "runtime_answer":
        runtime_scope, unauthorized = _runtime_scope_selection(
            query=query,
            arguments=dict(decision.arguments or {}),
            allowed_project_ids=[option.id for option in project_options],
            focus=focus,
        )
        if unauthorized:
            decision = HarnessPlannerDecision(
                action=_PLANNER_ANSWER_DIRECT,
                mode="runtime_scope_denied",
                confidence=1.0,
                user_visible_reason="The requested project is outside the caller's scope.",
                direct_answer="I do not have access to the requested project, so I did not query runtime data.",
                decision_source="scope_guard",
                matched_rule="runtime_scope_denied",
            )
        elif runtime_scope is not None:
            arguments = dict(decision.arguments or {})
            arguments.pop("project_id", None)
            arguments.pop("project_ids", None)
            if runtime_scope.kind is RuntimeScopeKind.SINGLE_PROJECT:
                arguments["project_id"] = runtime_scope.project_id
            elif runtime_scope.kind is RuntimeScopeKind.MULTI_PROJECT:
                arguments["project_ids"] = list(runtime_scope.project_ids)
            decision = replace(decision, arguments=arguments)
    decision = _with_turn_plan(
        decision,
        query=query,
        project_ids=project_ids,
        prior_plan=prior_plan,
    )
    return _record_planner_decision(decision)


async def _active_continuation(
    db: AsyncSession, conversation: Conversation
) -> HarnessConversationContinuationRow | None:
    row = (
        await db.execute(
            select(HarnessConversationContinuationRow)
            .where(
                HarnessConversationContinuationRow.conversation_id == conversation.id,
                HarnessConversationContinuationRow.owner_user_id == conversation.user_id,
                HarnessConversationContinuationRow.status == "active",
            )
            .order_by(HarnessConversationContinuationRow.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    return row if isinstance(row, HarnessConversationContinuationRow) else None


def _explicit_document_switch(query: str) -> bool:
    return bool(re.search(r"\b(?:use|using|from)\s+(?:the\s+)?(?:docs?|documentation|rag)\b", query, re.I))


async def _resume_continuation_decision(
    *,
    continuation: HarnessConversationContinuationRow,
    query: str,
    conversation: Conversation,
    db: AsyncSession,
    project_ids: list[str] | None,
    access_token: str | None,
    provider: McpDocumentationProvider | None,
) -> HarnessPlannerDecision | None:
    """Resolve a slot/reformat reply without granting the model a new lane."""
    try:
        envelope = ContinuationEnvelope.model_validate(continuation.envelope)
    except (TypeError, ValueError):
        return None
    if _explicit_document_switch(query):
        await db.execute(
            update(HarnessConversationContinuationRow)
            .where(
                HarnessConversationContinuationRow.id == continuation.id,
                HarnessConversationContinuationRow.version == continuation.version,
            )
            .values(status="superseded", version=continuation.version + 1)
        )
        return None
    plan = envelope.plan
    # A new substantive request can supersede a continuation, but slot-only and
    # referential turns must never be independently replanned.
    normalized = re.sub(r"\s+", " ", query.strip().casefold())
    if (
        len(normalized) > 180
        or _is_new_runtime_objective(query)
    ) and not _is_recomposition_request(query):
        await db.execute(
            update(HarnessConversationContinuationRow)
            .where(
                HarnessConversationContinuationRow.id == continuation.id,
                HarnessConversationContinuationRow.version == continuation.version,
            )
            .values(status="superseded", version=continuation.version + 1)
        )
        return None
    slots = {slot.name: slot.model_copy() for slot in envelope.slots}
    if "requested_sections" in slots and re.fullmatch(r"(?:all(?:\s+of\s+(?:these|them))?|all)", normalized):
        slots["requested_sections"] = slots["requested_sections"].model_copy(
            update={"value": ["executions", "health", "events", "failures", "performance"]}
        )
    if "project_id" in slots and slots["project_id"].value is None:
        candidates = project_ids or []
        if project_ids is None:
            candidates = [
                option.id
                for option in await _load_project_options(
                    project_ids=None,
                    access_token=access_token,
                    provider=provider or McpDocumentationProvider(),
                )
            ]
        exact = next((item for item in candidates if item.casefold() == normalized), None)
        if exact:
            slots["project_id"] = slots["project_id"].model_copy(update={"value": exact})
    time_window = _query_time_window(query)
    if "time_window" in slots and time_window is not None:
        slots["time_window"] = slots["time_window"].model_copy(
            update={"value": time_window.model_dump(mode="json")}
        )
    missing = [name for name, slot in slots.items() if slot.required and slot.value is None]
    resolved_scope = dict(plan.resolved_scope)
    if slots.get("project_id") and slots["project_id"].value:
        resolved_scope["project_id"] = slots["project_id"].value
    if slots.get("time_window") and slots["time_window"].value:
        resolved_scope["time_window"] = slots["time_window"].value
    updated_plan = plan.model_copy(
        update={
            "resolved_scope": resolved_scope,
            "missing_slots": missing,
            "continuation": {**plan.continuation, "state": "awaiting_user_input" if missing else "ready", "continuation_id": envelope.continuation_id, "version": envelope.version},
        }
    )
    if _is_recomposition_request(query) or re.fullmatch(r"show me the results then\??", normalized):
        run_id = await _latest_evidence_run_id(db, conversation)
        if run_id is not None:
            return HarnessPlannerDecision(
                action=_PLANNER_CALL_CAPABILITY,
                mode="evidence_recomposition",
                arguments={"recompose_from_run_id": run_id},
                confidence=1.0,
                user_visible_reason="Recomposing persisted evidence without a new execution.",
                decision_source="continuation_guard",
                matched_rule="persisted_evidence_recomposition",
                turn_plan=updated_plan,
            )
    if missing:
        prompt = "Which project should I use?" if "project_id" in missing else "What time window should I use?"
        return HarnessPlannerDecision(
            action=_PLANNER_ASK_CLARIFICATION,
            mode="continuation_clarification",
            missing_slots=tuple(missing),
            confidence=1.0,
            user_visible_reason="Continuing the pending request.",
            direct_answer=prompt,
            decision_source="continuation_guard",
            matched_rule="continuation_slot_fill",
            turn_plan=updated_plan,
        )
    if updated_plan.task_type is TurnTaskType.RUNTIME_ANSWER:
        runtime_arguments: dict[str, Any] = {"question": plan.objective}
        runtime_scope = _runtime_scope_from_plan(updated_plan)
        if runtime_scope is not None:
            if runtime_scope.kind is RuntimeScopeKind.SINGLE_PROJECT:
                runtime_arguments["project_id"] = runtime_scope.project_id
            elif runtime_scope.kind is RuntimeScopeKind.MULTI_PROJECT:
                runtime_arguments["project_ids"] = list(runtime_scope.project_ids)
        elif resolved_scope.get("project_id"):
            runtime_arguments["project_id"] = resolved_scope["project_id"]
        return HarnessPlannerDecision(
            action=_PLANNER_CALL_CAPABILITY,
            mode="runtime_answer",
            capability_id=RUNTIME_EVIDENCE_CAPABILITY.id,
            arguments=runtime_arguments,
            confidence=1.0,
            user_visible_reason="Executing the selected runtime evidence capability.",
            decision_source="continuation_guard",
            matched_rule="continuation_runtime_resume",
            turn_plan=updated_plan,
        )
    return None


async def _latest_evidence_run_id(db: AsyncSession, conversation: Conversation) -> int | None:
    result = await db.execute(
        select(ConversationMessage.workflow_run_id)
        .where(
            ConversationMessage.conversation_id == conversation.id,
            ConversationMessage.role == "assistant",
            ConversationMessage.workflow_run_id.is_not(None),
        )
        .order_by(ConversationMessage.id.desc())
        .limit(8)
    )
    for run_id in result.scalars():
        if run_id and await HarnessEvidenceRepository(db).list_used(run_id):
            return run_id
    return None


def _is_document_only_request(query: str) -> bool:
    normalized = query.casefold()
    return bool(
        re.search(r"\b(use|using|from|according to)\b.{0,40}\b(documentation|docs?|rag)\b", normalized)
        and re.search(r"\b(don'?t|do not|without|no)\b.{0,30}\b(runtime|logs?|syncs?|live)\b", normalized)
    )


def _is_new_runtime_objective(query: str) -> bool:
    """Recognize a fresh operational request while a prior turn awaits slots."""
    normalized = query.casefold()
    return bool(
        re.search(r"\b(for|on)\s+[a-z][a-z0-9_-]*\b", normalized)
        and re.search(
            r"\b(executions?|health|events?|failures?|performance|runtime|logs?)\b",
            normalized,
        )
    )


def _is_migration_discovery(
    query: str, conversation_history: list[dict[str, str]]
) -> bool:
    context = " ".join(
        [*(item["content"] for item in conversation_history[-6:]), query]
    ).casefold()
    return bool(re.search(r"\b(migrat\w*|azure data factory|\badf\b)\b", context))


async def _planner_conversation_history(
    db: AsyncSession, conversation: Conversation
) -> list[dict[str, str]]:
    messages = list(conversation.__dict__.get("messages") or [])[-8:]
    if not messages:
        messages = list(
            (
                await db.execute(
                    select(ConversationMessage)
                    .where(
                        ConversationMessage.conversation_id == conversation.id,
                        ConversationMessage.deleted_at.is_(None),
                    )
                    .order_by(ConversationMessage.id.desc())
                    .limit(8)
                )
            ).scalars()
        )[::-1]
    return [
        {"role": message.role, "content": message.content}
        for message in messages
        if message.role in {"user", "assistant"}
    ]


def _is_runtime_aggregation(query: str) -> bool:
    normalized = query.casefold()
    return bool(
        re.search(r"\b(runtime|logs?|executions?|runs?|engines?|sources?)\b", normalized)
        and re.search(r"\b(which|list|rank|count|most|least|how many|frequency)\b", normalized)
        and not re.search(r"\b(why|root cause|diagnose|investigate)\b", normalized)
    )


def _is_recomposition_request(query: str) -> bool:
    normalized = re.sub(r"\s+", " ", query.strip().casefold())
    if re.search(
        r"\b(latest|current|now|again|refresh|recheck|new evidence|new logs?)\b",
        normalized,
    ):
        return False
    return bool(
        re.search(
            r"\b(give me better output|format (?:it|that)|reformat|make (?:it|that) "
            r"clearer|summarize that|show (?:me )?(?:the )?results(?: then)?|"
            r"show (?:it|that) as (?:a )?(?:table|list))\b",
            normalized,
        )
    )


def _scope_safe_fallback_decision(
    *,
    query: str,
    fallback: HarnessPlannerDecision,
    focus: ConversationalFocus,
) -> HarnessPlannerDecision:
    """Keep malformed model output from broadening a focused follow-up to global RAG."""
    project = focus.project.stable_id if focus.project else None
    if not project or fallback.action != _PLANNER_USE_RAG:
        return fallback
    normalized = re.sub(r"\s+", " ", query.strip().casefold())
    continuation = bool(
        re.search(
            r"\b(same|again|instead|previous(?:ly)?|that|it|check again|still|now)\b",
            normalized,
        )
    )
    if not continuation:
        return fallback
    if focus.workflow_kind is WorkflowKind.DOCUMENTATION_GENERATION:
        return HarnessPlannerDecision(
            action=_PLANNER_CALL_CAPABILITY,
            mode="documentation_generation_status",
            capability_id=_DOC_STATUS_CAPABILITY_ID,
            arguments={"project_id": project},
            confidence=0.6,
            user_visible_reason="Continuing the focused documentation workflow.",
            matched_rule="focused_documentation_fallback",
        )
    if focus.workflow_kind in {WorkflowKind.RUNTIME_HEALTH, WorkflowKind.SCAN}:
        return HarnessPlannerDecision(
            action=_PLANNER_CALL_CAPABILITY,
            mode="operational_investigation",
            capability_id=_INVESTIGATION_CAPABILITY_ID,
            arguments={"project_id": project},
            confidence=0.6,
            user_visible_reason="Continuing the focused operational investigation.",
            matched_rule="focused_operational_fallback",
        )
    return fallback


def _record_planner_decision(decision: HarnessPlannerDecision) -> HarnessPlannerDecision:
    """Emit the routing decision to logs and metrics; returns it unchanged."""
    logger.info(
        "Planner decision: source=%s action=%s mode=%s capability=%s confidence=%.2f "
        "fallback_reason=%s matched_rule=%s note=%s latency_ms=%d task_type=%s "
        "execution_mode=%s denied_evidence=%s",
        decision.decision_source,
        decision.action,
        decision.mode,
        decision.capability_id or "-",
        decision.confidence,
        decision.fallback_reason or "-",
        decision.matched_rule or "-",
        decision.planner_note or "-",
        decision.planner_latency_ms,
        decision.turn_plan.task_type.value if decision.turn_plan else "legacy",
        decision.turn_plan.execution_mode.value if decision.turn_plan else "legacy",
        ",".join(decision.turn_plan.evidence_policy.denied) if decision.turn_plan else "-",
    )
    harness_planner_decisions_total.labels(
        source=decision.decision_source, action=decision.action
    ).inc()
    if decision.fallback_reason:
        harness_planner_fallback_total.labels(reason=decision.fallback_reason).inc()
    return decision


_PENDING_SUMMARY_RECENT_FAILURE_SECONDS = 900
_PENDING_SUMMARY_MAX_CHARS = 500


async def _pending_workflow_summary(
    db: AsyncSession, conversation: Conversation
) -> str | None:
    """Summarize this conversation's in-flight (or just-failed) workflow.

    Gives the planner continuity: a follow-up like "so what do you need from
    me?" must be interpreted against the pending onboarding run, not as a
    fresh question. Conversation ownership is enforced at the route layer, so
    a conversation-scoped lookup cannot leak another user's runs.
    """
    repo = HarnessWorkflowRepository(db)
    runs = await repo.list_runs(None, conversation_id=conversation.id, limit=10)
    active_statuses = {
        WorkflowRunStatus.INTERRUPTED,
        WorkflowRunStatus.PENDING,
        WorkflowRunStatus.RUNNING,
        WorkflowRunStatus.TRIGGERED,
    }
    chosen = next((run for run in runs if run.status in active_statuses), None)
    if chosen is None:
        now = datetime.datetime.now(datetime.UTC)
        for run in runs:
            if run.status != WorkflowRunStatus.FAILED or run.finished_at is None:
                continue
            age = (now - run.finished_at).total_seconds()
            if 0 <= age <= _PENDING_SUMMARY_RECENT_FAILURE_SECONDS:
                chosen = run
                break
    if chosen is None:
        return None
    parts = [
        f"Workflow run {chosen.id} ({chosen.intent_mode}, project {chosen.project_id}) "
        f"is {chosen.status.value}"
    ]
    if chosen.status == WorkflowRunStatus.INTERRUPTED:
        interrupts = await repo.list_pending_interrupts_for_owner(chosen.id, chosen.user_id)
        if interrupts:
            parts.append(f"waiting for: {interrupts[0].prompt}")
    elif chosen.status == WorkflowRunStatus.FAILED and chosen.error_message:
        parts.append(f"it failed with: {chosen.error_message}")
    return "; ".join(parts)[:_PENDING_SUMMARY_MAX_CHARS]


def _mode_for_capability(capability_id: str) -> str | None:
    if capability_id.startswith("connector.instance_"):
        return "connector_evidence"
    if capability_id in {DOCUMENT_SEARCH_CAPABILITY.id, DOCUMENT_LIST_CAPABILITY.id}:
        return "document_answer"
    if capability_id == RUNTIME_EVIDENCE_CAPABILITY.id:
        return "runtime_answer"
    return {
        _CAPABILITY_ID: "generate_docs",
        DOCUMENTATION_PACK_CREATE_CAPABILITY.id: "create_documentation_pack",
        _DOC_STATUS_CAPABILITY_ID: "documentation_generation_status",
        _DOC_UPDATE_CAPABILITY_ID: "update_docs_from_evidence",
        _INVESTIGATION_CAPABILITY_ID: "operational_investigation",
        _CONNECTOR_CAPABILITY_ID: "connector_inventory",
        PROJECT_ONBOARDING_CAPABILITY.id: "project_onboarding",
    }.get(capability_id)


_ALL_EVIDENCE_SOURCES = {
    "indexed_documentation",
    "generated_documentation",
    "runtime_evidence",
    "connector_evidence",
}


def _with_turn_plan(
    decision: HarnessPlannerDecision,
    *,
    query: str,
    project_ids: list[str] | None,
    prior_plan: TurnPlan | None = None,
) -> HarnessPlannerDecision:
    """Attach the validated routing authority used by both API entry points."""
    if decision.turn_plan is not None:
        return decision
    arguments = decision.arguments or {}
    runtime_scope: RuntimeScope | None = None
    if decision.mode == "runtime_answer":
        runtime_scope, _ = _runtime_scope_selection(
            query=query,
            arguments=arguments,
            allowed_project_ids=project_ids,
        )
        project_id = runtime_scope.project_id if runtime_scope else None
    else:
        project_id = None
        proposed = arguments.get("project_id")
        if isinstance(proposed, str):
            project_id = proposed
        elif project_ids is not None and len(project_ids) == 1:
            project_id = project_ids[0]
    query_time_window = _query_time_window(query)

    task_type = TurnTaskType.DOCUMENT_ANSWER
    execution_mode = TurnExecutionMode.EVIDENCE
    requested_contract = arguments.get("output_contract")
    output_contract = (
        str(requested_contract)
        if isinstance(requested_contract, str) and requested_contract
        else _evidence_output_contract(query, runtime=False)
    )
    allowed = {"indexed_documentation", "generated_documentation"}
    preferred = ["indexed_documentation"]
    required: list[str] = []

    if decision.action == _PLANNER_ANSWER_DIRECT:
        task_type = TurnTaskType.DIRECT_ANSWER
        execution_mode = TurnExecutionMode.DIRECT
        output_contract = "conversational_answer"
        allowed = set()
        preferred = []
    elif decision.mode == "create_documentation_pack":
        task_type = TurnTaskType.DOCUMENTATION_PACK_MANAGEMENT
        execution_mode = (
            TurnExecutionMode.CLARIFICATION
            if decision.action == _PLANNER_ASK_CLARIFICATION
            else TurnExecutionMode.WORKFLOW
        )
        output_contract = (
            "typed_clarification"
            if decision.action == _PLANNER_ASK_CLARIFICATION
            else "documentation_pack_receipt"
        )
        allowed = set()
        preferred = []
    elif decision.action == _PLANNER_ASK_CLARIFICATION:
        task_type = TurnTaskType.CLARIFICATION
        execution_mode = TurnExecutionMode.CLARIFICATION
        output_contract = "typed_clarification"
        allowed = set()
        preferred = []
    elif decision.mode == "runtime_answer":
        task_type = TurnTaskType.RUNTIME_ANSWER
        output_contract = _evidence_output_contract(query, runtime=True)
        allowed = {"runtime_evidence"}
        preferred = ["runtime_evidence"]
        required = ["runtime_evidence"]
    elif decision.mode in {"operational_investigation", "evidence_investigation"}:
        task_type = TurnTaskType.OPERATIONAL_INVESTIGATION
        execution_mode = TurnExecutionMode.WORKFLOW
        output_contract = "incident_report"
        allowed = set(_ALL_EVIDENCE_SOURCES)
        preferred = ["runtime_evidence", "indexed_documentation"]
        required = ["runtime_evidence"]
    elif decision.mode == "generate_docs":
        task_type = TurnTaskType.DOCUMENT_GENERATION
        execution_mode = TurnExecutionMode.WORKFLOW
        output_contract = "workflow_receipt"
        allowed = set()
        preferred = []
    elif decision.mode == "update_docs_from_evidence":
        task_type = TurnTaskType.DOCUMENT_UPDATE
        execution_mode = TurnExecutionMode.WORKFLOW
        output_contract = "documentation_change_receipt"
        allowed = set()
        preferred = []
    elif decision.mode == "project_onboarding":
        task_type = TurnTaskType.PROJECT_ONBOARDING
        execution_mode = TurnExecutionMode.WORKFLOW
        output_contract = "onboarding_workflow_receipt"
        allowed = set()
        preferred = []
    elif decision.mode == "connector_inventory":
        task_type = TurnTaskType.CONNECTOR_INVENTORY
        execution_mode = TurnExecutionMode.WORKFLOW
        output_contract = "connector_inventory"
        allowed = {"connector_evidence"}
        preferred = ["connector_evidence"]
        required = ["connector_evidence"]
    elif decision.mode == "connector_evidence":
        execution_mode = TurnExecutionMode.WORKFLOW
        output_contract = "grounded_answer"
        allowed = {"connector_evidence"}
        preferred = ["connector_evidence"]

    capability_ids = [
        str(item.get("capability_id"))
        for item in decision.tool_calls
        if item.get("capability_id")
    ]
    if decision.capability_id and decision.capability_id not in capability_ids:
        capability_ids.insert(0, decision.capability_id)
    plan = TurnPlan(
        objective=query.strip() or "Answer the current conversation turn.",
        task_type=task_type,
        resolved_scope={
            "project_id": project_id,
            "project_ids": project_ids,
            "runtime_scope": (
                runtime_scope.model_dump(mode="json") if runtime_scope is not None else None
            ),
            "time_window": (
                query_time_window.model_dump(mode="json")
                if query_time_window is not None
                else None
            ),
        },
        evidence_policy=EvidencePolicy(
            allowed=sorted(allowed),
            denied=sorted(_ALL_EVIDENCE_SOURCES - allowed),
            preferred=preferred,
            required=required,
        ),
        execution_mode=execution_mode,
        output_contract=output_contract,
        confidence=decision.confidence,
        missing_slots=(
            (
                list(decision.missing_slots)
                or [
                    name
                    for name, value in (
                        (
                            "project_id",
                            project_id if runtime_scope is None and not _runtime_request_allows_global(query) else runtime_scope,
                        ),
                        (
                            "time_window",
                            query_time_window
                            if not _runtime_request_allows_global(query)
                            else runtime_scope,
                        ),
                    )
                    if value is None
                ]
            )
            if decision.mode == "runtime_answer"
            else list(decision.missing_slots)
            if decision.missing_slots
            else (["user_input"] if execution_mode is TurnExecutionMode.CLARIFICATION else [])
        ),
        continuation={
            "mode": decision.mode,
            "state": (
                "awaiting_user_input"
                if execution_mode is TurnExecutionMode.CLARIFICATION
                or (
                    decision.mode == "runtime_answer"
                    and (
                        runtime_scope is None
                        or (
                            not _runtime_request_allows_global(query)
                            and query_time_window is None
                        )
                    )
                )
                else "none"
            ),
            "prompt": (
                decision.direct_answer
                if execution_mode is TurnExecutionMode.CLARIFICATION
                else None
            ),
        },
        capability_ids=capability_ids,
        decision_source=decision.decision_source,
    )
    if prior_plan is not None and _is_objective_continuation(query, plan, prior_plan):
        plan = plan.model_copy(
            update={
                "objective": prior_plan.objective,
                "output_contract": prior_plan.output_contract,
                "continuation": {
                    **plan.continuation,
                    "state": "continued",
                    "request": query.strip()[:500],
                },
            }
        )
    return replace(decision, turn_plan=plan)


async def _latest_persisted_turn_plan(
    db: AsyncSession, conversation: Conversation
) -> TurnPlan | None:
    messages = list(conversation.__dict__.get("messages") or [])
    if not messages:
        messages = list(
            (
                await db.execute(
                    select(ConversationMessage)
                    .where(
                        ConversationMessage.conversation_id == conversation.id,
                        ConversationMessage.deleted_at.is_(None),
                        ConversationMessage.turn_plan.is_not(None),
                    )
                    .order_by(ConversationMessage.id.desc())
                    .limit(4)
                )
            ).scalars()
        )
    messages.sort(key=lambda message: message.id or 0)
    for message in reversed(messages):
        if not isinstance(message.turn_plan, dict):
            continue
        try:
            return TurnPlan.model_validate(message.turn_plan)
        except (TypeError, ValueError):
            continue
    return None


def _is_objective_continuation(query: str, plan: TurnPlan, prior: TurnPlan) -> bool:
    if plan.task_type is not prior.task_type:
        return False
    current_project = plan.resolved_scope.get("project_id")
    prior_project = prior.resolved_scope.get("project_id")
    if current_project and prior_project and current_project != prior_project:
        return False
    normalized = re.sub(r"\s+", " ", query.strip().casefold())
    if len(normalized) > 160:
        return False
    return bool(
        re.search(
            r"^(?:option\s+\d|both\b|(?:yes|no|okay|ok)\b|continue\b|"
            r"give me better output\b|(?:re)?format\b|make (?:it|that)\b|"
            r"show (?:it|that)\b|summarize (?:it|that)\b)",
            normalized,
        )
    )


def _evidence_output_contract(query: str, *, runtime: bool) -> str:
    """Choose answer shape independently from evidence-source routing."""
    normalized = re.sub(r"\s+", " ", query.strip().casefold())
    if runtime and re.search(r"\b(rank|ranking|most|least|count|how many|frequency)\b", normalized):
        return "runtime_ranking"
    if re.search(r"\b(migrat\w*|azure data factory|\badf\b)\b", normalized):
        return "migration_proposal"
    if re.search(r"\b(compare|comparison|versus|\bvs\b|difference)\b", normalized):
        return "comparison"
    if re.search(r"\b(list|inventory|all)\b", normalized) and re.search(
        r"\b(jobs?|components?|routes?|pipelines?|flows?)\b", normalized
    ):
        return "component_inventory"
    return "runtime_answer" if runtime else "documentation_explanation"


def _is_capability_question(query: str) -> bool:
    normalized = re.sub(r"\s+", " ", query.strip().lower())
    if not normalized:
        return False
    overview_phrases = (
        "what can you do",
        "what tools",
        "which tools",
        "list tools",
        "show tools",
        "your capabilities",
        "what are your capabilities",
        "are you agentic",
    )
    if any(phrase in normalized for phrase in overview_phrases):
        return True
    if not re.search(r"\b(can you|could you|are you able|able to|can u|u can|you can)\b", normalized):
        return False
    return bool(
        re.search(
            r"\b(generate|regenerate|create|make|build|publish|run|start|trigger|update)\b"
            r".{0,80}\b(docs?|documentation|runbook|website)\b|"
            r"\b(docs?|documentation|runbook|website)\b"
            r".{0,80}\b(generate|regenerate|create|make|build|publish|run|start|trigger|update)\b|"
            r"\b(logs?|runtime|connectors?|sources?|tools?|capabilities)\b",
            normalized,
        )
    )


def resolve_project_scope(project_ids: list[str] | None) -> str | None:
    """Resolve a single project id from already-authorized route scope."""
    if project_ids is None:
        return None
    if len(project_ids) == 1:
        return project_ids[0]
    return None


def _runtime_scope_selection(
    *,
    query: str,
    arguments: dict[str, Any],
    allowed_project_ids: list[str] | None,
    focus: ConversationalFocus | None = None,
) -> tuple[RuntimeScope | None, tuple[str, ...]]:
    """Resolve only authenticated project identifiers into a runtime scope.

    A runtime engine or job mentioned in the query is intentionally ignored by
    this resolver. Only Nexus project ids from the caller-visible inventory can
    become project scope.
    """

    selected: list[str] = []
    raw_project_ids = arguments.get("project_ids")
    if isinstance(raw_project_ids, list) and all(isinstance(value, str) for value in raw_project_ids):
        selected = [value.strip() for value in raw_project_ids if value.strip()]
    elif isinstance(arguments.get("project_id"), str) and arguments["project_id"].strip():
        selected = [arguments["project_id"].strip()]
    elif allowed_project_ids:
        selected = _matching_project_ids_in_query(query, allowed_project_ids)
        if not selected and len(allowed_project_ids) == 1:
            selected = [allowed_project_ids[0]]
        elif not selected and focus and focus.project and focus.project.stable_id in allowed_project_ids:
            selected = [focus.project.stable_id]

    selected = list(dict.fromkeys(selected))
    if allowed_project_ids is not None:
        unauthorized = tuple(value for value in selected if value not in allowed_project_ids)
        if unauthorized:
            return None, unauthorized
    if len(selected) == 1:
        return RuntimeScope(kind=RuntimeScopeKind.SINGLE_PROJECT, project_ids=tuple(selected)), ()
    if len(selected) > 1:
        return RuntimeScope(kind=RuntimeScopeKind.MULTI_PROJECT, project_ids=tuple(selected)), ()
    if _runtime_request_allows_global(query):
        return RuntimeScope(kind=RuntimeScopeKind.GLOBAL), ()
    return None, ()


def _matching_project_ids_in_query(query: str, project_ids: list[str]) -> list[str]:
    normalized = query.casefold()
    return [
        project_id
        for project_id in project_ids
        if re.search(
            rf"(?<![a-z0-9_-]){re.escape(project_id.casefold())}(?![a-z0-9_-])",
            normalized,
        )
    ]


def _runtime_request_allows_global(query: str) -> bool:
    normalized = query.casefold()
    return bool(
        re.search(r"\b(list|show|which|what)\b", normalized)
        and re.search(r"\b(runtime\s+)?(engines?|sources?)\b", normalized)
        and not re.search(r"\b(jobs?|executions?|runs?|failures?|logs?)\b", normalized)
    )


def _runtime_scope_from_plan(plan: TurnPlan | None) -> RuntimeScope | None:
    if plan is None:
        return None
    raw = plan.resolved_scope.get("runtime_scope")
    if isinstance(raw, dict):
        try:
            return RuntimeScope.model_validate(raw)
        except (TypeError, ValueError):
            return None
    # Compatibility for continuations persisted before typed runtime scopes.
    project_ids = plan.resolved_scope.get("project_ids")
    if isinstance(project_ids, list):
        normalized = tuple(str(value) for value in project_ids if value)
        if len(normalized) > 1:
            return RuntimeScope(kind=RuntimeScopeKind.MULTI_PROJECT, project_ids=normalized)
        if len(normalized) == 1:
            return RuntimeScope(
                kind=RuntimeScopeKind.SINGLE_PROJECT,
                project_ids=normalized,
            )
    project_id = plan.resolved_scope.get("project_id")
    if isinstance(project_id, str) and project_id:
        return RuntimeScope(
            kind=RuntimeScopeKind.SINGLE_PROJECT,
            project_ids=(project_id,),
        )
    return None


async def _load_project_options(
    *,
    project_ids: list[str] | None,
    access_token: str | None,
    provider: McpDocumentationProvider,
) -> list[ProjectOption]:
    if project_ids is not None:
        return _project_options_from_scope(project_ids)
    try:
        projects = await provider.list_projects(access_token=access_token)
    except McpDocumentationProviderError as exc:
        logger.info("Harness planner could not list MCP projects safely: %s", exc.safe_message)
        return []
    return [_project_option_from_mcp(project) for project in projects]


def _project_options_from_scope(project_ids: list[str] | None) -> list[ProjectOption]:
    if project_ids is None:
        return []
    return [
        ProjectOption(id=project_id, display_name=project_id)
        for project_id in project_ids
    ]


def _project_option_from_mcp(project: McpProjectInfo) -> ProjectOption:
    return ProjectOption(
        id=project.id,
        display_name=project.display_name or project.id,
        status=project.status or None,
        platform=project.platform or None,
    )


def _resolve_project_for_decision(
    *,
    query: str,
    project_ids: list[str] | None,
    project_options: list[ProjectOption],
    focus: ConversationalFocus | None = None,
) -> str | None:
    scoped = resolve_project_scope(project_ids)
    if scoped:
        return scoped
    candidates = [
        EntityCandidate(
            entity_type=EntityType.PROJECT,
            stable_id=option.id,
            canonical_name=option.display_name or option.id,
            aliases=((option.display_name,) if option.display_name else ()),
        )
        for option in project_options
    ]
    if query.strip() and candidates:
        resolution = resolve_entity(query, EntityType.PROJECT, candidates)
        if resolution.status is ResolutionStatus.RESOLVED and resolution.reference:
            return resolution.reference.stable_id
    allowed = {option.id for option in project_options}
    if focus and focus.project and focus.project.stable_id in allowed:
        return focus.project.stable_id
    return None


def _project_from_planner_arguments(
    decision: HarnessPlannerDecision,
    resolved_project_id: str | None,
    project_options: list[ProjectOption],
) -> str | None:
    arguments = decision.arguments or {}
    proposed = arguments.get("project_id")
    if not isinstance(proposed, str):
        if decision.mode == "project_onboarding":
            return None
        return resolved_project_id
    if decision.mode == "project_onboarding":
        reserved = {
            "a", "an", "called", "create", "for", "from", "git", "named",
            "new", "project", "repository", "repo", "source", "the",
        }
        if (
            re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,99}", proposed)
            and proposed.lower() not in reserved
        ):
            return proposed
        return None
    allowed = {option.id for option in project_options}
    return proposed if proposed in allowed else resolved_project_id


def _match_project_in_query(query: str, project_options: list[ProjectOption]) -> str | None:
    haystack = query.lower()
    for option in sorted(project_options, key=lambda item: len(item.id), reverse=True):
        identifiers = {option.id, option.display_name}
        for identifier in identifiers:
            normalized = identifier.strip().lower()
            if not normalized:
                continue
            if re.search(rf"(?<![a-z0-9_-]){re.escape(normalized)}(?![a-z0-9_-])", haystack):
                return option.id
    return None


def _documentation_pack_name_from_query(query: str) -> str | None:
    quoted = re.search(
        r"\busing\s+(?:the\s+)?(?:documentation\s+)?pack\s+[\"']([^\"']+)[\"']",
        query,
        re.IGNORECASE,
    )
    if quoted:
        return quoted.group(1).strip()
    unquoted = re.search(
        r"\busing\s+(?:the\s+)?(?:documentation\s+)?pack\s+(.+?)\s*$",
        query,
        re.IGNORECASE,
    )
    return unquoted.group(1).strip(" .") if unquoted else None


async def _resolve_documentation_pack_id(
    *,
    arguments: dict[str, Any],
    query: str,
    provider: McpDocumentationProvider,
    access_token: str | None,
) -> int | None:
    explicit_id = arguments.get("plugin_pack_id")
    if isinstance(explicit_id, int) and not isinstance(explicit_id, bool) and explicit_id > 0:
        return explicit_id
    raw_name = (
        arguments.get("documentation_pack")
        or arguments.get("pack_name")
        or _documentation_pack_name_from_query(query)
    )
    if not isinstance(raw_name, str) or not raw_name.strip():
        return None
    requested = re.sub(r"[\s_-]+", " ", raw_name.strip()).casefold()
    packs = await provider.list_documentation_packs(access_token=access_token)
    matches = [
        pack
        for pack in packs
        if re.sub(r"[\s_-]+", " ", pack.name.strip()).casefold() == requested
    ]
    if len(matches) == 1:
        return matches[0].id
    available = ", ".join(f"`{pack.name}`" for pack in packs[:8]) or "none"
    raise McpDocumentationProviderError(
        f"Documentation Pack `{raw_name.strip()}` was not found. Available Packs: {available}."
    )


async def _conversation_focus(
    db: AsyncSession,
    conversation: Conversation,
) -> ConversationalFocus:
    runs = await HarnessWorkflowRepository(db).list_runs(
        None, conversation_id=conversation.id, limit=20
    )
    latest = next((run for run in runs if run.project_id is not None), None)
    if latest is None:
        return ConversationalFocus()
    workflow_kind = _workflow_kind_for_run(latest)
    references = [
        EntityReference(
            entity_type=EntityType.PROJECT,
            stable_id=latest.project_id,
            canonical_name=latest.project_id,
        ),
        EntityReference(
            entity_type=EntityType.WORKFLOW_RUN,
            stable_id=str(latest.id),
            canonical_name=f"{latest.intent_mode} run {latest.id}",
            workflow_kind=workflow_kind,
        ),
    ]
    return merge_conversational_focus(
        references=references,
        workflow_kind=workflow_kind,
    )


def _workflow_kind_for_run(run: WorkflowRun) -> WorkflowKind | None:
    if run.capability_id == _CAPABILITY_ID or run.intent_mode == "generate_docs":
        return WorkflowKind.DOCUMENTATION_GENERATION
    if run.capability_id == PROJECT_ONBOARDING_CAPABILITY.id:
        return WorkflowKind.ONBOARDING
    if run.intent_mode == "project_scan":
        return WorkflowKind.SCAN
    if run.intent_mode in {"operational_investigation", "evidence_investigation"}:
        return WorkflowKind.RUNTIME_HEALTH
    return None


async def run_harness_chat_turn(
    *,
    conversation: Conversation,
    query: str,
    db: AsyncSession,
    user_id: int,
    project_ids: list[str] | None,
    user_groups: list[str] | None = None,
    user_permissions: set[str] | None = None,
    qdrant: AsyncQdrantClient | None = None,
    access_token: str | None = None,
    provider: McpDocumentationProvider | None = None,
    planner_decision: HarnessPlannerDecision | None = None,
) -> MessageResponse:
    """Run a harness-triggered chat turn and persist the exchange."""
    start = time.perf_counter()
    decision = _with_turn_plan(
        planner_decision or plan_harness_chat_turn(query),
        query=query,
        project_ids=project_ids,
    )
    user_msg = ConversationMessage(
        conversation_id=conversation.id,
        role="user",
        content=query,
        turn_plan=decision.turn_plan.model_dump(mode="json") if decision.turn_plan else None,
    )
    db.add(user_msg)
    await db.flush()
    if decision.mode != "evidence_recomposition":
        await _upsert_continuation(
            db=db,
            conversation=conversation,
            user_id=user_id,
            user_message_id=user_msg.id,
            plan=decision.turn_plan,
        )

    docs_provider = provider or McpDocumentationProvider()
    if decision.action == _PLANNER_ANSWER_DIRECT:
        project_options = _project_options_from_scope(project_ids)
    else:
        project_options = await _load_project_options(
            project_ids=project_ids,
            access_token=access_token,
            provider=docs_provider,
        )
    focus = await _conversation_focus(db, conversation)
    project_id = _resolve_project_for_decision(
        query=query,
        project_ids=project_ids,
        project_options=project_options,
        focus=focus,
    )
    project_id = _project_from_planner_arguments(decision, project_id, project_options)
    runtime_scope = _runtime_scope_from_plan(decision.turn_plan)
    runtime_scope_denied = bool(
        decision.mode == "runtime_answer"
        and runtime_scope is not None
        and project_options
        and any(
            selected not in {option.id for option in project_options}
            for selected in runtime_scope.project_ids
        )
    )
    if decision.mode == "runtime_answer" and runtime_scope is not None:
        project_id = runtime_scope.project_id
    workflow_run_id: int | None = None

    if decision.action == _PLANNER_ANSWER_DIRECT:
        answer = decision.direct_answer or _capability_awareness_answer(
            project_options, permissions=user_permissions
        )
        return await _persist_assistant_message(
            conversation=conversation,
            db=db,
            answer=answer,
            latency_ms=_latency_ms(start),
        )

    if decision.action == _PLANNER_ASK_CLARIFICATION:
        answer = decision.direct_answer or _project_scope_clarification(
            capability_label=_capability_label(decision), project_options=project_options
        )
        return await _persist_assistant_message(
            conversation=conversation,
            db=db,
            answer=answer,
            latency_ms=_latency_ms(start),
        )

    if decision.action != _PLANNER_CALL_CAPABILITY:
        answer = _capability_fallback_answer()
        return await _persist_assistant_message(
            conversation=conversation,
            db=db,
            answer=answer,
            latency_ms=_latency_ms(start),
        )

    if decision.mode == "update_docs_from_evidence":
        workflow_run_id = None
        if project_id is None:
            answer = _project_scope_clarification(
                capability_label="update documentation",
                project_options=project_options,
            )
        else:
            update_run, answer, _ = await _execute_doc_update_from_evidence(
                conversation=conversation,
                query=query,
                db=db,
                user_id=user_id,
                project_id=project_id,
                access_token=access_token,
                provider=docs_provider,
                user_permissions=user_permissions,
            )
            workflow_run_id = update_run.id
        return await _persist_assistant_message(
            conversation=conversation,
            db=db,
            answer=answer,
            latency_ms=_latency_ms(start),
            workflow_run_id=workflow_run_id,
        )

    if decision.mode == "documentation_generation_status":
        workflow_run_id = None
        if project_id is None:
            answer = _project_scope_clarification(
                capability_label="check documentation generation status",
                project_options=project_options,
            )
        else:
            status_run, answer, _ = await _execute_documentation_generation_status(
                conversation=conversation,
                query=query,
                db=db,
                user_id=user_id,
                project_id=project_id,
                project_scope=project_ids,
                user_permissions=user_permissions,
                access_token=access_token,
                provider=docs_provider,
            )
            workflow_run_id = status_run.id
        return await _persist_assistant_message(
            conversation=conversation,
            db=db,
            answer=answer,
            latency_ms=_latency_ms(start),
            workflow_run_id=workflow_run_id,
        )

    if decision.mode == "create_documentation_pack":
        run, answer, _ = await _execute_documentation_pack_creation(
            conversation=conversation,
            query=query,
            db=db,
            user_id=user_id,
            access_token=access_token,
            provider=docs_provider,
            decision=decision,
            user_permissions=user_permissions,
        )
        return await _persist_assistant_message(
            conversation=conversation,
            db=db,
            answer=answer,
            latency_ms=_latency_ms(start),
            workflow_run_id=run.id,
        )

    if decision.mode == "connector_evidence":
        connector_run, answer, _ = await _execute_model_selected_connector(
            conversation=conversation,
            query=query,
            db=db,
            user_id=user_id,
            project_id=project_id,
            project_scope=project_ids,
            user_permissions=user_permissions,
            decision=decision,
        )
        return await _persist_assistant_message(
            conversation=conversation,
            db=db,
            answer=answer,
            latency_ms=_latency_ms(start),
            workflow_run_id=connector_run.id,
        )

    if decision.mode == "project_onboarding" and project_id is None:
        answer = "What should the new project be called?"
        return await _persist_assistant_message(
            conversation=conversation,
            db=db,
            answer=answer,
            latency_ms=_latency_ms(start),
        )

    if decision.mode == "project_onboarding":
        run, answer, _ = await _execute_project_onboarding(
            conversation=conversation,
            query=query,
            db=db,
            user_id=user_id,
            project_id=project_id,
            project_scope=project_ids,
            user_permissions=user_permissions,
            access_token=access_token,
            decision=decision,
            docs_provider=docs_provider,
        )
        return await _persist_assistant_message(
            conversation=conversation,
            db=db,
            answer=answer,
            latency_ms=_latency_ms(start),
            workflow_run_id=run.id,
        )

    if decision.mode == "evidence_recomposition":
        workflow_run_id = int((decision.arguments or {}).get("recompose_from_run_id") or 0) or None
        answer = await _recompose_persisted_evidence(db, workflow_run_id, decision.turn_plan)
        return await _persist_assistant_message(
            conversation=conversation, db=db, answer=answer, latency_ms=_latency_ms(start), workflow_run_id=workflow_run_id
        )

    if decision.mode in {
        "operational_investigation",
        "evidence_investigation",
        "runtime_answer",
        "connector_inventory",
    }:
        workflow_run_id = None
        if decision.mode == "runtime_answer" and runtime_scope_denied:
            answer = "I do not have access to the requested project, so I did not query runtime data."
        elif decision.mode == "runtime_answer" and runtime_scope is None:
            answer = _project_scope_clarification(
                capability_label=_capability_label(decision),
                project_options=project_options,
            )
        elif decision.mode in {"operational_investigation", "evidence_investigation", "runtime_answer"}:
            investigation_run, answer, _ = await _execute_agentic_evidence_investigation(
                conversation=conversation,
                query=query,
                db=db,
                user_id=user_id,
                project_id=project_id,
                runtime_scope=runtime_scope,
                project_scope=project_ids,
                user_groups=user_groups or [],
                qdrant=qdrant,
                access_token=access_token,
                provider=docs_provider,
                decision=decision,
                user_permissions=user_permissions,
            )
            workflow_run_id = investigation_run.id
            if decision.mode == "runtime_answer":
                await _complete_runtime_continuation(db, conversation, user_id)
        else:
            inventory_run, answer, _ = await _execute_connector_inventory(
                conversation=conversation,
                query=query,
                db=db,
                user_id=user_id,
                project_id=project_id,
                project_scope=project_ids,
            )
            workflow_run_id = inventory_run.id
        return await _persist_assistant_message(
            conversation=conversation,
            db=db,
            answer=answer,
            latency_ms=_latency_ms(start),
            workflow_run_id=workflow_run_id,
        )

    if project_id is None:
        run, activities = await _create_project_scope_interrupt(
            conversation=conversation,
            query=query,
            db=db,
            user_id=user_id,
            project_options=project_options,
            decision=decision,
            user_permissions=user_permissions,
            access_token=access_token,
            provider=docs_provider,
        )
        answer = _project_scope_clarification(
            capability_label="generate documentation",
            project_options=project_options,
        )
        return await _persist_assistant_message(
            conversation=conversation,
            db=db,
            answer=answer,
            latency_ms=_latency_ms(start),
            workflow_run_id=run.id,
        )

    run, result, error_message, _ = await _trigger_documentation_generation(
        conversation=conversation,
        query=query,
        db=db,
        user_id=user_id,
        project_id=project_id,
        access_token=access_token,
        provider=docs_provider,
        decision=decision,
        user_permissions=user_permissions,
    )

    if error_message:
        answer = _doc_generation_failed_answer(project_id, run, error_message)
    else:
        assert result is not None
        answer = _success_answer(project_id, run, result)

    return await _persist_assistant_message(
        conversation=conversation,
        db=db,
        answer=answer,
        latency_ms=_latency_ms(start),
        workflow_run_id=run.id,
    )


async def run_harness_chat_turn_stream(
    *,
    conversation: Conversation,
    query: str,
    db: AsyncSession,
    user_id: int,
    project_ids: list[str] | None,
    user_groups: list[str] | None = None,
    user_permissions: set[str] | None = None,
    qdrant: AsyncQdrantClient | None = None,
    access_token: str | None = None,
    provider: McpDocumentationProvider | None = None,
    planner_decision: HarnessPlannerDecision | None = None,
) -> AsyncGenerator[tuple[str, str], None]:
    """Streaming variant of :func:`run_harness_chat_turn`."""
    start = time.perf_counter()
    decision = _with_turn_plan(
        planner_decision or plan_harness_chat_turn(query),
        query=query,
        project_ids=project_ids,
    )
    user_msg = ConversationMessage(
        conversation_id=conversation.id,
        role="user",
        content=query,
        turn_plan=decision.turn_plan.model_dump(mode="json") if decision.turn_plan else None,
    )
    db.add(user_msg)
    await db.flush()
    if decision.mode != "evidence_recomposition":
        await _upsert_continuation(
            db=db,
            conversation=conversation,
            user_id=user_id,
            user_message_id=user_msg.id,
            plan=decision.turn_plan,
        )

    docs_provider = provider or McpDocumentationProvider()
    if decision.action == _PLANNER_ANSWER_DIRECT:
        project_options = _project_options_from_scope(project_ids)
    else:
        project_options = await _load_project_options(
            project_ids=project_ids,
            access_token=access_token,
            provider=docs_provider,
        )
    focus = await _conversation_focus(db, conversation)
    project_id = _resolve_project_for_decision(
        query=query,
        project_ids=project_ids,
        project_options=project_options,
        focus=focus,
    )
    project_id = _project_from_planner_arguments(decision, project_id, project_options)
    runtime_scope = _runtime_scope_from_plan(decision.turn_plan)
    runtime_scope_denied = bool(
        decision.mode == "runtime_answer"
        and runtime_scope is not None
        and project_options
        and any(
            selected not in {option.id for option in project_options}
            for selected in runtime_scope.project_ids
        )
    )
    if decision.mode == "runtime_answer" and runtime_scope is not None:
        project_id = runtime_scope.project_id
    workflow_run_id: int | None = None

    emits_persisted_planner = (
        decision.action == _PLANNER_CALL_CAPABILITY
        and decision.mode == "generate_docs"
        and project_id is not None
    )
    if not emits_persisted_planner:
        yield (
            "harness_activity",
            json.dumps(_planner_adhoc_activity(decision)),
        )

    if decision.action == _PLANNER_ANSWER_DIRECT:
        answer = decision.direct_answer or _capability_awareness_answer(
            project_options, permissions=user_permissions
        )
    elif decision.action == _PLANNER_ASK_CLARIFICATION:
        answer = decision.direct_answer or _project_scope_clarification(
            capability_label=_capability_label(decision), project_options=project_options
        )
        if decision.mode != "model_clarification":
            yield (
                "harness_activity",
                json.dumps(_scope_required_activity(decision, project_options)),
            )
    elif decision.action != _PLANNER_CALL_CAPABILITY:
        answer = _capability_fallback_answer()
    elif decision.mode == "update_docs_from_evidence":
        if project_id is None:
            answer = _project_scope_clarification(
                capability_label="update documentation",
                project_options=project_options,
            )
            yield (
                "harness_activity",
                json.dumps(_scope_required_activity(decision, project_options)),
            )
        else:
            update_run, answer, activities = await _execute_doc_update_from_evidence(
                conversation=conversation,
                query=query,
                db=db,
                user_id=user_id,
                project_id=project_id,
                access_token=access_token,
                provider=docs_provider,
                user_permissions=user_permissions,
            )
            workflow_run_id = update_run.id
            for activity in activities:
                yield ("harness_activity", json.dumps(_activity_payload(activity)))
            pending = await _workflow_service(db).pending_interrupts(update_run.id, user_id)
            if pending:
                yield (
                    "pending_action",
                    json.dumps(
                        {
                            "workflow_run_id": update_run.id,
                            "interrupt_id": pending[0].id,
                            "expected_version": update_run.version,
                            "kind": pending[0].interrupt_type.value,
                            "prompt": pending[0].prompt,
                            "choices": pending[0].options,
                            "expires_at": pending[0].expires_at.isoformat(),
                        }
                    ),
                )
    elif decision.mode == "documentation_generation_status":
        if project_id is None:
            answer = _project_scope_clarification(
                capability_label="check documentation generation status",
                project_options=project_options,
            )
            yield (
                "harness_activity",
                json.dumps(_scope_required_activity(decision, project_options)),
            )
        else:
            status_run, answer, activities = await _execute_documentation_generation_status(
                conversation=conversation,
                query=query,
                db=db,
                user_id=user_id,
                project_id=project_id,
                project_scope=project_ids,
                user_permissions=user_permissions,
                access_token=access_token,
                provider=docs_provider,
            )
            workflow_run_id = status_run.id
            for activity in activities:
                yield ("harness_activity", json.dumps(_activity_payload(activity)))
    elif decision.mode == "create_documentation_pack":
        pack_run, answer, activities = await _execute_documentation_pack_creation(
            conversation=conversation,
            query=query,
            db=db,
            user_id=user_id,
            access_token=access_token,
            provider=docs_provider,
            decision=decision,
            user_permissions=user_permissions,
        )
        workflow_run_id = pack_run.id
        for activity in activities:
            yield ("harness_activity", json.dumps(_activity_payload(activity)))
    elif decision.mode == "connector_evidence":
        connector_run, answer, activities = await _execute_model_selected_connector(
            conversation=conversation,
            query=query,
            db=db,
            user_id=user_id,
            project_id=project_id,
            project_scope=project_ids,
            user_permissions=user_permissions,
            decision=decision,
        )
        workflow_run_id = connector_run.id
        for activity in activities:
            yield ("harness_activity", json.dumps(_activity_payload(activity)))
    elif decision.mode == "project_onboarding" and project_id is None:
        answer = "What should the new project be called?"
        yield (
            "harness_activity",
            json.dumps(_scope_required_activity(decision, project_options)),
        )
    elif decision.mode == "project_onboarding":
        run, answer, activities = await _execute_project_onboarding(
            conversation=conversation,
            query=query,
            db=db,
            user_id=user_id,
            project_id=project_id,
            project_scope=project_ids,
            user_permissions=user_permissions,
            access_token=access_token,
            decision=decision,
            docs_provider=docs_provider,
        )
        workflow_run_id = run.id
        for activity in activities:
            yield ("harness_activity", json.dumps(_activity_payload(activity)))
        pending = await _workflow_service(db).pending_interrupts(run.id, user_id)
        if pending:
            yield (
                "pending_action",
                json.dumps(
                    {
                        "workflow_run_id": run.id,
                        "interrupt_id": pending[0].id,
                        "expected_version": run.version,
                        "kind": pending[0].interrupt_type.value,
                        "prompt": pending[0].prompt,
                        "choices": pending[0].options,
                        "expires_at": pending[0].expires_at.isoformat(),
                    }
                ),
            )
    elif decision.mode == "evidence_recomposition":
        workflow_run_id = int((decision.arguments or {}).get("recompose_from_run_id") or 0) or None
        answer = await _recompose_persisted_evidence(db, workflow_run_id, decision.turn_plan)
    elif decision.mode in {
        "operational_investigation",
        "evidence_investigation",
        "runtime_answer",
        "connector_inventory",
    }:
        if decision.mode == "runtime_answer" and runtime_scope_denied:
            answer = "I do not have access to the requested project, so I did not query runtime data."
        elif decision.mode == "runtime_answer" and runtime_scope is None:
            answer = _project_scope_clarification(
                capability_label=_capability_label(decision),
                project_options=project_options,
            )
            yield (
                "harness_activity",
                json.dumps(_scope_required_activity(decision, project_options)),
            )
        elif decision.mode in {"operational_investigation", "evidence_investigation", "runtime_answer"}:
            investigation_run, answer, activities = await _execute_agentic_evidence_investigation(
                conversation=conversation,
                query=query,
                db=db,
                user_id=user_id,
                project_id=project_id,
                runtime_scope=runtime_scope,
                project_scope=project_ids,
                user_groups=user_groups or [],
                qdrant=qdrant,
                access_token=access_token,
                provider=docs_provider,
                decision=decision,
                user_permissions=user_permissions,
            )
            workflow_run_id = investigation_run.id
            if decision.mode == "runtime_answer":
                await _complete_runtime_continuation(db, conversation, user_id)
            for activity in activities:
                yield ("harness_activity", json.dumps(_activity_payload(activity)))
        else:
            inventory_run, answer, activities = await _execute_connector_inventory(
                conversation=conversation,
                query=query,
                db=db,
                user_id=user_id,
                project_id=project_id,
                project_scope=project_ids,
            )
            workflow_run_id = inventory_run.id
            for activity in activities:
                yield ("harness_activity", json.dumps(_activity_payload(activity)))
    elif project_id is None:
        run, activities = await _create_project_scope_interrupt(
            conversation=conversation,
            query=query,
            db=db,
            user_id=user_id,
            project_options=project_options,
            decision=decision,
            user_permissions=user_permissions,
            access_token=access_token,
            provider=docs_provider,
        )
        workflow_run_id = run.id
        answer = _project_scope_clarification(
            capability_label="generate documentation",
            project_options=project_options,
        )
        for activity in activities:
            yield ("harness_activity", json.dumps(_activity_payload(activity)))
        pending = await _workflow_service(db).pending_interrupts(run.id, user_id)
        if pending:
            yield (
                "pending_action",
                json.dumps(
                    {
                        "workflow_run_id": run.id,
                        "interrupt_id": pending[0].id,
                        "expected_version": run.version,
                        "kind": pending[0].interrupt_type.value,
                        "prompt": pending[0].prompt,
                        "choices": pending[0].options,
                        "expires_at": pending[0].expires_at.isoformat(),
                    }
                ),
            )
    else:
        run, result, error_message, activities = await _trigger_documentation_generation(
            conversation=conversation,
            query=query,
            db=db,
            user_id=user_id,
            project_id=project_id,
            access_token=access_token,
            provider=docs_provider,
            decision=decision,
            user_permissions=user_permissions,
        )
        workflow_run_id = run.id
        for activity in activities:
            yield ("harness_activity", json.dumps(_activity_payload(activity)))
        if error_message:
            answer = _doc_generation_failed_answer(project_id, run, error_message)
        else:
            assert result is not None
            answer = _success_answer(project_id, run, result)

    yield ("token", json.dumps(answer))
    assistant_msg = await _persist_assistant_message(
        conversation=conversation,
        db=db,
        answer=answer,
        latency_ms=_latency_ms(start),
        workflow_run_id=workflow_run_id,
    )
    if assistant_msg.structured_answer is not None:
        yield ("final_answer", json.dumps(assistant_msg.structured_answer))
    yield ("usage", json.dumps(_empty_usage(assistant_msg.latency_ms or 0)))
    yield ("done", json.dumps({"message_id": assistant_msg.id, "latency_ms": assistant_msg.latency_ms or 0}))


async def _execute_documentation_generation_status(
    *,
    conversation: Conversation,
    query: str,
    db: AsyncSession,
    user_id: int,
    project_id: str,
    project_scope: list[str] | None,
    user_permissions: set[str] | None,
    access_token: str | None,
    provider: McpDocumentationProvider | None,
) -> tuple[WorkflowRun, str, list[ActivityEvent]]:
    """Read MCP's run ledger and reconcile Nexus's matching workflow receipt."""
    registry = await _project_execution_registry(db, project_id, "documentation")
    service = HarnessWorkflowService(HarnessWorkflowRepository(db), registry)
    run = await service.start_run(
        conversation_id=conversation.id,
        user_id=user_id,
        project_id=project_id,
        intent_mode="documentation_generation_status",
        capability_id=_DOC_STATUS_CAPABILITY_ID,
        input_summary=_input_summary(query),
    )
    activities = [
        await service.record_activity(
            run,
            event_type="status.query_started",
            label="Checking documentation generation",
            status=ActivityEventStatus.RUNNING,
            summary=f"Checking the latest generation run for {project_id}.",
            safe_details={"project_id": project_id},
            runtime_node="status_lookup",
        )
    ]
    executor = HarnessCapabilityExecutor(
        registry,
        docs_provider=provider or McpDocumentationProvider(),
    )
    observation = await executor.execute(
        CapabilityExecutionRequest(
            capability_id=_DOC_STATUS_CAPABILITY_ID,
            arguments={"project_id": project_id},
        ),
        CapabilityExecutionContext(
            user_id=user_id,
            conversation_id=conversation.id,
            project_scope=project_scope,
            permissions=user_permissions,
            access_token=access_token,
        ),
    )
    if not observation.succeeded:
        message = observation.error_message or "I could not check documentation generation status."
        await service.mark_failed(run, error_message=message)
        activities.append(
            await service.record_activity(
                run,
                event_type="status.query_failed",
                label="Status check failed",
                status=ActivityEventStatus.FAILED,
                summary=message,
                safe_details={"project_id": project_id},
                runtime_node="status_lookup",
            )
        )
        return run, message, activities

    data = observation.data
    await _reconcile_documentation_workflow(
        db=db,
        conversation_id=conversation.id,
        project_id=project_id,
        status_run_id=run.id,
        external_status=str(data.get("status") or "unknown"),
        external_run_id=data.get("external_run_id"),
        error_message=data.get("error_message"),
    )
    run = await HarnessWorkflowRepository(db).update_run(
        [project_id],
        run.id,
        WorkflowRunUpdate(
            status=WorkflowRunStatus.SUCCEEDED,
            output_summary=observation.summary,
            artifact_links=observation.artifact_links,
            runtime_metadata={
                "resolution": {
                    "project": {
                        "entity_type": EntityType.PROJECT.value,
                        "stable_id": project_id,
                        "canonical_name": project_id,
                    },
                    "workflow_kind": WorkflowKind.DOCUMENTATION_GENERATION.value,
                },
                "external_generation": {
                    "run_id": data.get("external_run_id"),
                    "status": data.get("status"),
                },
            },
            finished_at=datetime.datetime.now(datetime.UTC),
        ),
    )
    answer = _documentation_generation_status_answer(project_id, data)
    activities.append(
        await service.record_activity(
            run,
            event_type="status.query_completed",
            label="Documentation status resolved",
            status=ActivityEventStatus.SUCCEEDED,
            summary=answer,
            safe_details={
                "project_id": project_id,
                "status": data.get("status"),
                "document_count": data.get("doc_count", 0),
                "client_ready": data.get("client_ready"),
            },
            runtime_node="status_resolved",
        )
    )
    return run, answer, activities


async def _reconcile_documentation_workflow(
    *,
    db: AsyncSession,
    conversation_id: int,
    project_id: str,
    status_run_id: int,
    external_status: str,
    external_run_id: Any,
    error_message: Any,
) -> None:
    normalized = external_status.lower()
    if normalized in {"completed", "succeeded", "ready"}:
        terminal = WorkflowRunStatus.SUCCEEDED
    elif normalized in {"failed", "error", "cancelled"}:
        terminal = WorkflowRunStatus.FAILED
    else:
        return
    repo = HarnessWorkflowRepository(db)
    candidates = await repo.list_runs(
        [project_id],
        conversation_id=conversation_id,
        project_id=project_id,
        limit=20,
    )
    prior = next(
        (
            candidate
            for candidate in candidates
            if candidate.id != status_run_id
            and candidate.capability_id in {_CAPABILITY_ID, PROJECT_ONBOARDING_CAPABILITY.id}
            and candidate.status in {
                WorkflowRunStatus.PENDING,
                WorkflowRunStatus.RUNNING,
                WorkflowRunStatus.TRIGGERED,
            }
        ),
        None,
    )
    if prior is None:
        return
    metadata = dict(prior.runtime_metadata)
    metadata["external_generation"] = {
        "run_id": external_run_id,
        "status": normalized,
        "reconciled_at": datetime.datetime.now(datetime.UTC).isoformat(),
    }
    await repo.update_run(
        [project_id],
        prior.id,
        WorkflowRunUpdate(
            status=terminal,
            output_summary=(
                f"Documentation generation {normalized}."
                if terminal == WorkflowRunStatus.SUCCEEDED
                else None
            ),
            error_message=(
                str(error_message or f"Documentation generation {normalized}.")[:500]
                if terminal == WorkflowRunStatus.FAILED
                else None
            ),
            runtime_metadata=metadata,
            finished_at=datetime.datetime.now(datetime.UTC),
        ),
        expected_version=prior.version,
    )


def _documentation_generation_status_answer(project_id: str, data: dict[str, Any]) -> str:
    if not data.get("found"):
        return f"I could not find a documentation generation run for `{project_id}`."
    status = str(data.get("status") or "unknown").lower()
    doc_count = int(data.get("doc_count") or 0)
    if status in {"completed", "succeeded", "ready"}:
        answer = (
            f"Documentation generation for `{project_id}` finished and produced "
            f"{doc_count} document{'s' if doc_count != 1 else ''}."
        )
        if data.get("client_ready") is False:
            answer += " The output still needs review before it is client-ready."
        elif data.get("client_ready") is True:
            answer += " The output passed its readiness checks."
        if data.get("quality_score") is not None:
            answer += f" Quality score: {data['quality_score']}."
        failed = [str(item).replace("_", " ") for item in data.get("failed_gates", [])]
        if failed:
            answer += f" Checks needing attention: {', '.join(failed[:4])}."
        return answer
    if status in {"failed", "error", "cancelled"}:
        detail = str(data.get("error_message") or "No additional failure detail was provided.")
        return f"Documentation generation for `{project_id}` {status}. {detail}"
    if status in {"queued", "pending", "running", "triggered"}:
        return f"Documentation generation for `{project_id}` is still {status}."
    return f"The latest documentation generation status for `{project_id}` is {status}."


async def _execute_documentation_pack_creation(
    *,
    conversation: Conversation,
    query: str,
    db: AsyncSession,
    user_id: int,
    access_token: str | None,
    provider: McpDocumentationProvider,
    decision: HarnessPlannerDecision,
    user_permissions: set[str] | None,
) -> tuple[WorkflowRun, str, list[ActivityEvent]]:
    registry = default_capability_registry()
    service = HarnessWorkflowService(HarnessWorkflowRepository(db), registry)
    run = await service.start_run(
        conversation_id=conversation.id,
        user_id=user_id,
        project_id=None,
        intent_mode="create_documentation_pack",
        capability_id=DOCUMENTATION_PACK_CREATE_CAPABILITY.id,
        input_summary=_input_summary(query),
    )
    activities = [
        await service.record_activity(
            run,
            event_type="planner.decision",
            label="Planned Pack creation",
            status=ActivityEventStatus.SUCCEEDED,
            summary=_planner_summary(decision),
            safe_details=_planner_safe_details(decision),
            runtime_node="plan",
        )
    ]
    observation = await HarnessCapabilityExecutor(
        registry,
        docs_provider=provider,
    ).execute(
        CapabilityExecutionRequest(
            capability_id=DOCUMENTATION_PACK_CREATE_CAPABILITY.id,
            arguments=dict(decision.arguments or {}),
            idempotency_key=f"pack-create:{run.id}",
        ),
        CapabilityExecutionContext(
            user_id=user_id,
            conversation_id=conversation.id,
            permissions=user_permissions,
            approvals={DOCUMENTATION_PACK_CREATE_CAPABILITY.id},
            access_token=access_token,
        ),
    )
    succeeded = observation.succeeded
    activities.append(
        await service.record_activity(
            run,
            event_type="capability.completed" if succeeded else "capability.failed",
            label="Created documentation Pack" if succeeded else "Documentation Pack creation failed",
            status=(ActivityEventStatus.SUCCEEDED if succeeded else ActivityEventStatus.FAILED),
            summary=observation.summary or observation.error_message,
            safe_details=observation.safe_details,
            runtime_node="execute",
        )
    )
    updated = await HarnessWorkflowRepository(db).update_run(
        [],
        run.id,
        WorkflowRunUpdate(
            status=(WorkflowRunStatus.SUCCEEDED if succeeded else WorkflowRunStatus.FAILED),
            output_summary=observation.summary if succeeded else None,
            error_message=None if succeeded else observation.error_message,
            runtime_provider_id="nexus.harness",
            current_node="complete" if succeeded else "failed",
            finished_at=datetime.datetime.now(datetime.UTC),
        ),
        owner_user_id=user_id,
    )
    if not succeeded:
        return updated, observation.error_message or "The documentation Pack could not be created.", activities
    pack_name = str(observation.data.get("name") or "the new Pack")
    return (
        updated,
        f"Created reusable documentation Pack `{pack_name}`. It is now available for onboarding, project defaults, and `/docs` runs.",
        activities,
    )


async def _trigger_documentation_generation(
    *,
    conversation: Conversation,
    query: str,
    db: AsyncSession,
    user_id: int,
    project_id: str,
    access_token: str | None,
    provider: McpDocumentationProvider | None,
    decision: HarnessPlannerDecision | None = None,
    user_permissions: set[str] | None = None,
) -> tuple[WorkflowRun, McpDocumentationGenerationResult | None, str | None, list[ActivityEvent]]:
    registry = await _project_execution_registry(db, project_id, "documentation")
    service = HarnessWorkflowService(HarnessWorkflowRepository(db), registry)
    run = await service.start_run(
        conversation_id=conversation.id,
        user_id=user_id,
        project_id=project_id,
        intent_mode="generate_docs",
        capability_id=_CAPABILITY_ID,
        input_summary=_input_summary(query),
    )
    activities: list[ActivityEvent] = []
    if decision is not None:
        activities.append(await service.record_activity(
            run,
            event_type="planner.decision",
            label="Planned harness response",
            status=ActivityEventStatus.SUCCEEDED,
            summary=_planner_summary(decision),
            safe_details=_planner_safe_details(decision),
        ))
    activities.append(await service.record_activity(
        run,
        event_type="capability.selected",
        label="Selected documentation capability",
        status=ActivityEventStatus.SUCCEEDED,
        summary="Using the documentation generation capability.",
        safe_details={"capability": "documentation generation", "risk": "internal workflow"},
    ))
    activities.append(await service.record_activity(
        run,
        event_type="scope",
        label="Resolved project scope",
        status=ActivityEventStatus.SUCCEEDED,
        summary=f"Using project `{project_id}`.",
        safe_details={"project_id": project_id},
    ))
    provider = provider or McpDocumentationProvider()
    try:
        plugin_pack_id = await _resolve_documentation_pack_id(
            arguments=dict(decision.arguments or {}) if decision is not None else {},
            query=query,
            provider=provider,
            access_token=access_token,
        )
    except McpDocumentationProviderError as exc:
        failed = await HarnessWorkflowRepository(db).update_run(
            [project_id],
            run.id,
            WorkflowRunUpdate(
                status=WorkflowRunStatus.FAILED,
                error_message=exc.safe_message,
                current_node="resolve_documentation_pack",
                finished_at=datetime.datetime.now(datetime.UTC),
            ),
            owner_user_id=user_id,
        )
        return failed, None, exc.safe_message, activities
    runtime_arguments: dict[str, Any] = {"project_id": project_id, "resume": True}
    if plugin_pack_id is not None:
        runtime_arguments["plugin_pack_id"] = plugin_pack_id
        activities.append(await service.record_activity(
            run,
            event_type="scope.documentation_pack",
            label="Resolved documentation Pack",
            status=ActivityEventStatus.SUCCEEDED,
            summary="Using the requested reusable documentation Pack for this run.",
            safe_details={"plugin_pack_id": plugin_pack_id},
        ))
    runtime_result = await default_langgraph_runtime_provider().run_turn(
        RuntimeTurnRequest(
            message=query,
            manifest=build_runtime_capability_manifest(
                registry,
                scope=ManifestScope(permissions=user_permissions),
            ),
            planned_capability_id=_CAPABILITY_ID,
            arguments=runtime_arguments,
            workflow_run_id=run.id,
        ),
        executor=HarnessCapabilityExecutor(
            registry,
            docs_provider=provider,
        ),
        context=CapabilityExecutionContext(
            user_id=user_id,
            conversation_id=conversation.id,
            project_scope=[project_id],
            permissions=user_permissions,
            access_token=access_token,
        ),
    )

    for runtime_activity in runtime_result.activity:
        activities.append(
            await service.record_activity(
                run,
                event_type=str(runtime_activity.get("event_type") or "runtime.event"),
                label=str(runtime_activity.get("label") or "Runtime activity"),
                status=_activity_status(runtime_activity.get("status")),
                summary=runtime_activity.get("summary"),
                safe_details=dict(runtime_activity.get("safe_details") or {}),
                runtime_node=runtime_activity.get("runtime_node"),
                tool_call_id=runtime_result.runtime_run_id,
            )
        )

    observation = runtime_result.observation

    if observation is None or not observation.succeeded:
        error_message = (
            observation.error_message if observation is not None else runtime_result.final_summary
        ) or "Documentation generation failed to start."
        if runtime_result.status == "interrupted":
            await service.mark_interrupted(
                run,
                output_summary=error_message,
                runtime_provider_id=runtime_result.runtime_provider,
                runtime_thread_id=runtime_result.thread_id,
                runtime_run_id=runtime_result.runtime_run_id,
                current_node=runtime_result.current_node,
                resume_token=runtime_result.thread_id,
                runtime_metadata=runtime_result.runtime_metadata,
            )
        elif runtime_result.status == "indeterminate":
            run = await service.mark_indeterminate(
                run,
                output_summary="Documentation generation may have been accepted; Nexus is reconciling its status.",
                runtime_provider_id=runtime_result.runtime_provider,
                runtime_thread_id=runtime_result.thread_id,
                runtime_run_id=runtime_result.runtime_run_id,
                current_node=runtime_result.current_node,
                runtime_metadata=runtime_result.runtime_metadata,
            )
        else:
            await service.mark_failed(
                run,
                error_message=error_message,
                runtime_provider_id=runtime_result.runtime_provider,
                runtime_thread_id=runtime_result.thread_id,
                runtime_run_id=runtime_result.runtime_run_id,
                current_node=runtime_result.current_node,
                runtime_metadata=runtime_result.runtime_metadata,
            )
        activities.append(await service.record_activity(
            run,
            event_type="runtime.result",
            label="Documentation workflow did not start",
            status=ActivityEventStatus.FAILED,
            summary=error_message,
            safe_details={"project_id": project_id, "runtime": runtime_result.runtime_provider},
            runtime_node=runtime_result.current_node,
            tool_call_id=runtime_result.runtime_run_id,
        ))
        return run, None, error_message, activities

    result = McpDocumentationGenerationResult(
        project_id=str(observation.data.get("project_id") or project_id),
        status=str(observation.data.get("status") or "generation_triggered"),
        message=observation.summary or "Documentation generation started",
        status_path=next(
            (
                str(link["url"])
                for link in observation.artifact_links
                if link.get("kind") == "status" and link.get("url")
            ),
            f"/mcp/generate-runs/{project_id}",
        ),
        docs_path=next(
            (
                str(link["url"])
                for link in observation.artifact_links
                if link.get("kind") == "docs" and link.get("url")
            ),
            f"/projects/{project_id}/docs",
        ),
    )

    await service.mark_triggered(
        run,
        output_summary=observation.summary,
        artifact_links=observation.artifact_links,
        runtime_provider_id=runtime_result.runtime_provider,
        runtime_thread_id=runtime_result.thread_id,
        runtime_run_id=runtime_result.runtime_run_id,
        current_node=runtime_result.current_node,
        artifact_receipts=observation.artifact_links,
        runtime_metadata=runtime_result.runtime_metadata,
    )
    return run, result, None, activities


async def resume_documentation_generation_workflow(
    *,
    run: WorkflowRun,
    project_id: str,
    db: AsyncSession,
    user_id: int,
    project_scope: list[str] | None,
    user_permissions: set[str] | None,
    access_token: str | None,
    provider: McpDocumentationProvider | None = None,
) -> tuple[WorkflowRun, list[ActivityEvent]]:
    """Continue one owner-authorized docs workflow after project clarification."""
    if project_scope is not None and project_id not in project_scope:
        raise ValueError("Project access required")
    registry = await _project_execution_registry(db, project_id, "documentation")
    docs_provider = provider or McpDocumentationProvider()
    executor = HarnessCapabilityExecutor(registry, docs_provider=docs_provider)
    context = CapabilityExecutionContext(
        user_id=user_id,
        conversation_id=run.conversation_id,
        project_scope=[project_id],
        permissions=user_permissions,
        access_token=access_token,
    )
    request = RuntimeTurnRequest(
        message=run.input_summary or "Generate documentation",
        manifest=build_runtime_capability_manifest(
            registry,
            scope=ManifestScope(permissions=user_permissions),
        ),
        planned_capability_id=_CAPABILITY_ID,
        arguments={},
        workflow_run_id=run.id,
        thread_id=run.runtime_thread_id or f"harness-run-{run.id}",
    )
    runtime = default_langgraph_runtime_provider()
    runtime_result = await runtime.resume_turn(
        request,
        response={"project_id": project_id},
        executor=executor,
        context=context,
    )
    # Nexus owns durable interrupt state. If the process-lifetime LangGraph
    # checkpoint was lost during a restart, reconstruct the same workflow run
    # from its persisted pending input and current authorization context.
    if (
        runtime_result.status == "failed"
        and runtime_result.current_node == "runtime_error"
    ):
        runtime_result = await runtime.run_turn(
            request.model_copy(update={"arguments": {"project_id": project_id}}),
            executor=executor,
            context=context,
        )

    service = HarnessWorkflowService(HarnessWorkflowRepository(db), registry)
    activities: list[ActivityEvent] = []
    for runtime_activity in runtime_result.activity:
        activities.append(
            await service.record_activity(
                run,
                event_type=str(runtime_activity.get("event_type") or "runtime.event"),
                label=str(runtime_activity.get("label") or "Runtime activity"),
                status=_activity_status(runtime_activity.get("status")),
                summary=runtime_activity.get("summary"),
                safe_details=dict(runtime_activity.get("safe_details") or {}),
                runtime_node=runtime_activity.get("runtime_node"),
                tool_call_id=runtime_result.runtime_run_id,
            )
        )

    observation = runtime_result.observation
    repo = HarnessWorkflowRepository(db)
    if observation is not None and observation.succeeded:
        updated = await repo.update_run(
            [],
            run.id,
            WorkflowRunUpdate(
                project_id=project_id,
                status=WorkflowRunStatus.TRIGGERED,
                output_summary=observation.summary,
                artifact_links=observation.artifact_links,
                runtime_provider_id=runtime_result.runtime_provider,
                runtime_thread_id=runtime_result.thread_id,
                runtime_run_id=runtime_result.runtime_run_id,
                current_node=runtime_result.current_node,
                artifact_receipts=observation.artifact_links,
                runtime_metadata=runtime_result.runtime_metadata,
            ),
            owner_user_id=user_id,
        )
        activities.append(
            await service.record_activity(
                updated,
                event_type="workflow.resumed",
                label="Documentation workflow resumed",
                status=ActivityEventStatus.SUCCEEDED,
                summary=f"Documentation generation started for `{project_id}`.",
                safe_details={"project_id": project_id},
                runtime_node=runtime_result.current_node,
            )
        )
        return updated, activities

    error = (
        observation.error_message if observation is not None else runtime_result.final_summary
    ) or "Documentation generation could not continue."
    updated = await repo.update_run(
        [],
        run.id,
        WorkflowRunUpdate(
            project_id=project_id,
            status=WorkflowRunStatus.FAILED,
            error_message=error,
            runtime_provider_id=runtime_result.runtime_provider,
            runtime_thread_id=runtime_result.thread_id,
            runtime_run_id=runtime_result.runtime_run_id,
            current_node=runtime_result.current_node,
            runtime_metadata=runtime_result.runtime_metadata,
            finished_at=datetime.datetime.now(datetime.UTC),
        ),
        owner_user_id=user_id,
    )
    return updated, activities


async def _create_project_scope_interrupt(
    *,
    conversation: Conversation,
    query: str,
    db: AsyncSession,
    user_id: int,
    project_options: list[ProjectOption],
    decision: HarnessPlannerDecision,
    user_permissions: set[str] | None,
    access_token: str | None,
    provider: McpDocumentationProvider,
) -> tuple[WorkflowRun, list[ActivityEvent]]:
    service = _workflow_service(db)
    run = await service.start_run(
        conversation_id=conversation.id,
        user_id=user_id,
        project_id=_PENDING_PROJECT_SCOPE_ID,
        intent_mode="generate_docs",
        capability_id=_CAPABILITY_ID,
        input_summary=_input_summary(query),
    )
    available_projects = [
        option.id
        for option in sorted(project_options, key=lambda item: item.id.lower())
    ][:8]
    runtime_result = await default_langgraph_runtime_provider().run_turn(
        RuntimeTurnRequest(
            message=query,
            manifest=build_runtime_capability_manifest(
                default_capability_registry(),
                scope=ManifestScope(permissions=user_permissions),
            ),
            planned_capability_id=_CAPABILITY_ID,
            arguments={},
            workflow_run_id=run.id,
        ),
        executor=HarnessCapabilityExecutor(
            default_capability_registry(),
            docs_provider=provider,
        ),
        context=CapabilityExecutionContext(
            user_id=user_id,
            conversation_id=conversation.id,
            project_scope=available_projects,
            permissions=user_permissions,
            access_token=access_token,
        ),
    )
    pending = runtime_result.interrupt
    interrupt_metadata = {
        **runtime_result.runtime_metadata,
        "interrupt": {
            "type": "missing_project_scope",
            "missing_slots": ["project_id"],
            "available_projects": available_projects,
            "langgraph_interrupt_id": pending.interrupt_id if pending else None,
        },
        "resume": {"ready": pending is not None},
    }
    activities = [
        await service.record_activity(
            run,
            event_type="planner.decision",
            label="Planned harness response",
            status=ActivityEventStatus.SUCCEEDED,
            summary=_planner_summary(decision),
            safe_details=_planner_safe_details(decision),
            runtime_node="plan",
        ),
        await service.record_activity(
            run,
            event_type="clarification.requested",
            label="Project scope required",
            status=ActivityEventStatus.RUNNING,
            summary="Choose one project before Nexus can generate documentation.",
            safe_details={
                "runtime_node": "await_project_scope",
                "available_projects": available_projects,
                "resume_ready": True,
            },
            runtime_node="await_project_scope",
            tool_call_id=runtime_result.runtime_run_id,
        ),
    ]
    from src.harness.models import WorkflowInterruptType

    updated = await service.mark_interrupted(
        run,
        output_summary="Project scope is required before documentation generation can start.",
        runtime_provider_id=runtime_result.runtime_provider,
        runtime_thread_id=runtime_result.thread_id,
        runtime_run_id=runtime_result.runtime_run_id,
        current_node="await_project_scope",
        runtime_metadata=interrupt_metadata,
        interrupt_type=WorkflowInterruptType.CLARIFICATION,
        prompt=(
            pending.prompt
            if pending is not None
            else "Which project should I use to generate documentation?"
        ),
        options=[
            {"value": option.id, "label": option.display_name}
            for option in project_options[:8]
        ],
        scope_snapshot={"available_projects": available_projects},
        policy_snapshot={"capability": "documentation generation"},
        normalized_arguments={"project_id": None},
    )
    return updated, activities


async def _execute_agentic_evidence_investigation(
    *,
    conversation: Conversation,
    query: str,
    db: AsyncSession,
    user_id: int,
    project_id: str | None,
    runtime_scope: RuntimeScope | None,
    project_scope: list[str] | None,
    user_groups: list[str],
    qdrant: AsyncQdrantClient | None,
    access_token: str | None,
    provider: McpDocumentationProvider | None,
    decision: HarnessPlannerDecision,
    user_permissions: set[str] | None,
) -> tuple[WorkflowRun, str, list[ActivityEvent]]:
    connector_repo = ConnectorRepository(db)
    registry = await build_request_capability_registry(
        connector_repo, project_ids=project_scope
    )
    service = HarnessWorkflowService(HarnessWorkflowRepository(db), registry)
    selected_runtime_projects = (
        list(runtime_scope.project_ids) if runtime_scope is not None else None
    )
    effective_runtime_scope = (
        [
            selected
            for selected in selected_runtime_projects
            if project_scope is None or selected in project_scope
        ]
        if selected_runtime_projects is not None
        else project_scope
    )
    run = await service.start_run(
        conversation_id=conversation.id,
        user_id=user_id,
        project_id=project_id,
        intent_mode="evidence_investigation",
        capability_id=OPERATIONAL_INVESTIGATION_CAPABILITY.id,
        input_summary=_input_summary(query),
    )
    docs_provider = provider or McpDocumentationProvider()

    async def doc_search_handler(arguments, _context):  # noqa: ANN001
        if qdrant is None:
            return []
        return await search_document_evidence(
            qdrant=qdrant,
            query=str(arguments.get("query") or query),
            user_groups=user_groups,
            project_ids=[project_id] if project_id else [],
            top_k=int(arguments.get("top_k", 5)),
        )

    async def doc_list_handler(arguments, _context):  # noqa: ANN001
        return await list_generated_doc_evidence(
            provider=docs_provider,
            project_id=str(arguments.get("project_id") or project_id),
            access_token=access_token,
        )

    runtime_answer_holder: dict[str, str] = {}

    async def runtime_handler(arguments, _context):  # noqa: ANN001
        result = await query_runtime_evidence(
            question=str(arguments.get("question") or query),
            # The typed plan owns runtime scope. A tool/model argument cannot
            # turn an engine or job name into a project selector.
            project_id=project_id,
            project_ids=selected_runtime_projects,
            project_scope=effective_runtime_scope,
            db=db,
            source_key=(
                str(arguments["source_key"])
                if arguments.get("source_key") is not None
                else None
            ),
            since=_parse_evidence_datetime(arguments.get("since")),
            until=_parse_evidence_datetime(arguments.get("until")),
        )
        if result.answer_draft:
            runtime_answer_holder["answer"] = result.answer_draft
        return result

    handlers = {
        DOCUMENT_SEARCH_CAPABILITY.id: doc_search_handler,
        DOCUMENT_LIST_CAPABILITY.id: doc_list_handler,
        RUNTIME_EVIDENCE_CAPABILITY.id: runtime_handler,
    }
    executor = HarnessCapabilityExecutor(
        registry,
        docs_provider=docs_provider,
        connector_service=ConnectorService(
            connector_repo, default_connector_provider_registry()
        ),
        evidence_handlers=handlers,
    )
    turn_plan = decision.turn_plan
    runtime_only = (
        turn_plan is not None
        and turn_plan.task_type is TurnTaskType.RUNTIME_ANSWER
        and turn_plan.evidence_policy.allowed == ["runtime_evidence"]
    )
    runtime_question = turn_plan.objective if runtime_only else query
    time_window = (
        _time_window_from_scope(turn_plan.resolved_scope.get("time_window"))
        if runtime_only
        else _query_time_window(query)
    )
    source_classes = {
        DOCUMENT_SEARCH_CAPABILITY.id: EvidenceSourceClass.INDEXED_DOC,
        DOCUMENT_LIST_CAPABILITY.id: EvidenceSourceClass.GENERATED_DOC,
        RUNTIME_EVIDENCE_CAPABILITY.id: EvidenceSourceClass.CURRENT_RUNTIME,
    }
    available_capabilities = {
        capability.id: capability for capability in registry.list(enabled_only=True)
    }
    connector_capabilities = [
        capability
        for capability in available_capabilities.values()
        if capability.id.startswith("connector.instance_")
    ]
    runtime_arguments: dict[str, Any] = {"question": runtime_question}
    if project_id is not None:
        runtime_arguments["project_id"] = project_id
    elif selected_runtime_projects:
        runtime_arguments["project_ids"] = selected_runtime_projects
    if time_window is not None:
        runtime_arguments.update(
            since=time_window.start.isoformat(), until=time_window.end.isoformat()
        )
    mandatory_calls = [
        InvestigationCall(
            capability_id=RUNTIME_EVIDENCE_CAPABILITY.id,
            source_class=EvidenceSourceClass.CURRENT_RUNTIME,
            arguments=runtime_arguments,
        ),
    ]
    if not runtime_only:
        mandatory_calls = [
            InvestigationCall(
                capability_id=DOCUMENT_SEARCH_CAPABILITY.id,
                source_class=EvidenceSourceClass.INDEXED_DOC,
                arguments={"query": query, "project_id": project_id, "top_k": 5},
            ),
            InvestigationCall(
                capability_id=DOCUMENT_LIST_CAPABILITY.id,
                source_class=EvidenceSourceClass.GENERATED_DOC,
                arguments={"project_id": project_id},
            ),
            *mandatory_calls,
            *[
            InvestigationCall(
                capability_id=capability.id,
                source_class=EvidenceSourceClass.CONNECTOR,
                arguments={"query": query, "project_id": project_id, "limit": 10},
            )
            for capability in connector_capabilities[:2]
            ],
        ]
    proposed_calls: list[InvestigationCall] = []
    for item in decision.tool_calls:
        capability_id = str(item.get("capability_id") or "")
        capability = available_capabilities.get(capability_id)
        if capability is None:
            continue
        arguments = dict(item.get("arguments") or {})
        if capability_id == RUNTIME_EVIDENCE_CAPABILITY.id:
            arguments.pop("project_id", None)
            arguments.pop("project_ids", None)
            if project_id is not None:
                arguments["project_id"] = project_id
            elif selected_runtime_projects:
                arguments["project_ids"] = selected_runtime_projects
            arguments["question"] = str(
                arguments.pop("query", None) or arguments.get("question") or query
            )
            if time_window is not None:
                arguments.update(
                    since=time_window.start.isoformat(), until=time_window.end.isoformat()
                )
        elif capability_id != DOCUMENT_LIST_CAPABILITY.id:
            arguments["project_id"] = project_id
            arguments["query"] = str(arguments.get("query") or query)
        allowed = capability.input_schema.get("properties", {})
        arguments = {key: value for key, value in arguments.items() if key in allowed}
        proposed_calls.append(
            InvestigationCall(
                capability_id=capability_id,
                source_class=(
                    EvidenceSourceClass.CONNECTOR
                    if capability_id.startswith("connector.instance_")
                    else source_classes.get(capability_id, EvidenceSourceClass.SOURCE_TOPOLOGY)
                ),
                arguments=arguments,
            )
        )
    calls_by_capability = {call.capability_id: call for call in mandatory_calls}
    calls_by_capability.update({call.capability_id: call for call in proposed_calls})
    calls = list(calls_by_capability.values())[:12]
    unavailable_requested_connectors = [
        f"{label} connector"
        for label in _unavailable_requested_connector_labels(
            query, connector_capabilities
        )
    ]
    result = await default_operational_investigation_workflow().run(
        OperationalInvestigationRequest(
            workflow_run_id=run.id,
            question=runtime_question,
            project_ref=project_id,
            entity_refs=selected_runtime_projects or ([project_id] if project_id else []),
            calls=calls,
            required_sources=(
                ["runtime evidence"] if runtime_only else ["documentation", "runtime evidence", "connector/log evidence"]
            ),
            additional_required_sources=unavailable_requested_connectors,
        ),
        executor=executor,
        context=CapabilityExecutionContext(
            user_id=user_id,
            conversation_id=conversation.id,
            project_scope=effective_runtime_scope,
            permissions=user_permissions,
            access_token=access_token,
        ),
    )
    runtime_draft = runtime_answer_holder.get("answer") or next(
        (
            str(item.metadata["runtime_answer"])
            for item in result.evidence
            if isinstance(item.metadata.get("runtime_answer"), str)
            and item.metadata["runtime_answer"].strip()
        ),
        None,
    )
    activities: list[ActivityEvent] = []
    for item in result.activity:
        activities.append(
            await service.record_activity(
                run,
                event_type=str(item.get("event_type") or "evidence.event"),
                label=str(item.get("label") or "Evidence activity"),
                status=_activity_status(item.get("status")),
                summary=item.get("summary"),
                safe_details=dict(item.get("safe_details") or {}),
                runtime_node=item.get("runtime_node"),
            )
        )
    evidence_repo = HarnessEvidenceRepository(db)
    persisted = await evidence_repo.collect(run.id, result.evidence)
    conflicted = group_conflicts(persisted)
    await evidence_repo.persist_conflicts(run.id, conflicted)
    ranked = rank_evidence(
        conflicted,
        EvidenceRankingContext(
            project_ref=project_id,
            preferred_sources=(
                EvidenceSourceClass.CURRENT_RUNTIME,
                EvidenceSourceClass.CONNECTOR,
                EvidenceSourceClass.INDEXED_DOC,
                EvidenceSourceClass.GENERATED_DOC,
            ),
        ),
    )
    ranked_ids = list(
        dict.fromkeys(item.id for item in ranked if item.id is not None)
    )[:10]
    used = await evidence_repo.mark_used(run.id, ranked_ids)
    refs = [
        {
            "id": item.id,
            "source_type": item.source_class.value,
            "uri": item.uri,
        }
        for item in used
    ]
    has_required_runtime_observation = any(
        item.source_class is EvidenceSourceClass.CURRENT_RUNTIME for item in persisted
    )
    missing_required_sources = list(result.answer.missing_required_sources)
    if not has_required_runtime_observation and "runtime evidence" not in missing_required_sources:
        missing_required_sources.append("runtime evidence")
    final_answer = OperationalAnswerComposer().compose(
        conflicted,
        missing_required_sources=missing_required_sources,
        artifacts=result.answer.artifacts,
    )
    await HarnessWorkflowRepository(db).update_run(
        [project_id] if project_id is not None else None,
        run.id,
        WorkflowRunUpdate(
            status=(
                WorkflowRunStatus.SUCCEEDED
                if persisted and has_required_runtime_observation
                else WorkflowRunStatus.FAILED
            ),
            output_summary=f"Collected {len(persisted)} and used {len(used)} evidence item(s).",
            evidence_refs=refs,
            artifact_links=[
                {
                    "label": "Workflow Evidence",
                    "url": f"/api/v1/harness/workflows/{run.id}",
                    "kind": "evidence",
                }
            ],
            runtime_provider_id="langgraph.nexus",
            current_node="finalize",
            runtime_metadata={
                "graph": "operational_investigation",
                "tool_count": len(calls),
                "source_types": sorted({item.source_class.value for item in persisted}),
                "time_window": (
                    time_window.model_dump(mode="json") if time_window is not None else None
                ),
                "runtime_question": runtime_question,
                "missing_required_sources": missing_required_sources,
                "investigation_answer": final_answer.model_dump(mode="json"),
                "resolution": {
                    "project": (
                        {
                            "entity_type": EntityType.PROJECT.value,
                            "stable_id": project_id,
                            "canonical_name": project_id,
                        }
                        if project_id is not None
                        else None
                    ),
                    "runtime_scope": (
                        runtime_scope.model_dump(mode="json")
                        if runtime_scope is not None
                        else None
                    ),
                    "time_window": (
                        time_window.model_dump(mode="json")
                        if time_window is not None
                        else None
                    ),
                },
            },
            finished_at=datetime.datetime.now(datetime.UTC),
        ),
    )
    return run, (
        runtime_draft
        if runtime_only and runtime_draft is not None
        else final_answer.as_text()
    ), activities


def _requested_connector_labels(query: str) -> list[str]:
    labels = ("Slack", "Microsoft Teams", "Outlook", "Confluence", "Elastic")
    aliases = {
        "Slack": ("slack",),
        "Microsoft Teams": ("microsoft teams", "ms teams", "teams"),
        "Outlook": ("outlook",),
        "Confluence": ("confluence",),
        "Elastic": ("elastic", "elasticsearch"),
    }
    normalized = query.casefold()
    return [label for label in labels if any(alias in normalized for alias in aliases[label])]


def _unavailable_requested_connector_labels(
    query: str, capabilities: list[Any]
) -> list[str]:
    configured = " ".join(
        str(capability.label).casefold()
        for capability in capabilities
        if str(capability.id).startswith("connector.instance_")
    )
    return [
        label
        for label in _requested_connector_labels(query)
        if label.casefold() not in configured
        and label.casefold().split()[-1] not in configured
    ]


def _evidence_snapshot_from_observation(
    *,
    item: dict[str, Any],
    observation: Any,
    project_id: str,
    index: int,
) -> EvidenceSnapshot:
    source_type = str(item.get("source_type") or "indexed_doc").lower()
    if "runtime" in source_type:
        source_class = EvidenceSourceClass.CURRENT_RUNTIME
    elif "generated" in source_type:
        source_class = EvidenceSourceClass.GENERATED_DOC
    elif "connector" in source_type:
        source_class = EvidenceSourceClass.CONNECTOR
    elif "topology" in source_type or "source" in source_type:
        source_class = EvidenceSourceClass.SOURCE_TOPOLOGY
    else:
        source_class = EvidenceSourceClass.INDEXED_DOC
    summary = str(item.get("summary") or item.get("excerpt") or "Evidence item")[:4000]
    schedule = re.search(r"\b([01]\d|2[0-3]):[0-5]\d\b", summary)
    observed_at = _parse_evidence_datetime(item.get("observed_at"))
    freshness = 0.9 if source_class == EvidenceSourceClass.CURRENT_RUNTIME else 0.6
    authority = 0.9 if source_class == EvidenceSourceClass.CURRENT_RUNTIME else 0.7
    return EvidenceSnapshot(
        source_class=source_class,
        source_id=str(item.get("id") or f"{observation.capability_id}:{index}"),
        capability_id=observation.capability_id,
        project_ref=project_id,
        connector_ref=(observation.capability_id if source_class == EvidenceSourceClass.CONNECTOR else None),
        title=str(item.get("title") or "Evidence")[:500],
        summary=summary,
        uri=str(item.get("uri"))[:1000] if item.get("uri") else None,
        observed_at=observed_at,
        freshness=freshness,
        confidence=float(item.get("confidence") or 0.7),
        authority=authority,
        score=(float(item["score"]) if isinstance(item.get("score"), (int, float)) else None),
        claim_key="schedule" if schedule else None,
        claim_value=schedule.group(0) if schedule else None,
        metadata=item.get("metadata") if isinstance(item.get("metadata"), dict) else {},
    )


def _parse_evidence_datetime(value: Any) -> datetime.datetime | None:
    if isinstance(value, datetime.datetime):
        return value if value.tzinfo else value.replace(tzinfo=datetime.UTC)
    if isinstance(value, str) and value:
        try:
            parsed = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=datetime.UTC)
    return None


_TIME_WINDOW_IN_QUERY_RE = re.compile(
    r"\b(?:past|last)\s+\d+\s*(?:hours?|hrs?|days?)\b|\b(?:today|yesterday)\b",
    re.IGNORECASE,
)


def _query_time_window(query: str):
    match = _TIME_WINDOW_IN_QUERY_RE.search(query)
    if match is None:
        return None
    try:
        return resolve_time_window(match.group(0))
    except ValueError:
        return None


def _time_window_from_scope(value: Any):
    if not isinstance(value, dict):
        return None
    start = _parse_evidence_datetime(value.get("start"))
    end = _parse_evidence_datetime(value.get("end"))
    if start is None or end is None:
        return None
    try:
        return resolve_time_window(start=start, end=end)
    except (TypeError, ValueError):
        return None


async def _execute_operational_investigation(
    *,
    conversation: Conversation,
    query: str,
    db: AsyncSession,
    user_id: int,
    project_id: str,
    project_scope: list[str] | None,
    user_groups: list[str],
    qdrant: AsyncQdrantClient | None,
    access_token: str | None,
    provider: McpDocumentationProvider | None,
) -> tuple[WorkflowRun, str, list[ActivityEvent]]:
    service = _workflow_service(db)
    run = await service.start_run(
        conversation_id=conversation.id,
        user_id=user_id,
        project_id=project_id,
        intent_mode="operational_investigation",
        capability_id=_INVESTIGATION_CAPABILITY_ID,
        input_summary=_input_summary(query),
    )
    activities: list[ActivityEvent] = []
    activities.append(await service.record_activity(
        run,
        event_type="scope",
        label="Resolved project scope",
        status=ActivityEventStatus.SUCCEEDED,
        summary=f"Investigating project `{project_id}`.",
        safe_details={"project_id": project_id},
    ))

    doc_search_items: list[EvidenceItem] = []
    if qdrant is None:
        activities.append(await service.record_activity(
            run,
            event_type="evidence.docs_search",
            label="Document evidence unavailable",
            status=ActivityEventStatus.SKIPPED,
            summary="Qdrant client was not available for this harness run.",
        ))
    else:
        doc_result = await search_document_evidence(
            qdrant=qdrant,
            query=query,
            user_groups=user_groups,
            project_ids=[project_id],
            top_k=5,
        )
        doc_search_items = doc_result.items
        activities.append(await service.record_activity(
            run,
            event_type="evidence.docs_search",
            label="Searched documentation",
            status=ActivityEventStatus.SUCCEEDED,
            summary=f"Found {len(doc_search_items)} documentation evidence item(s).",
            safe_details={
                "capability_id": doc_result.capability_id,
                "evidence_count": len(doc_search_items),
                "evidence_refs": evidence_refs(doc_search_items),
            },
        ))

    docs_provider = provider or McpDocumentationProvider()
    doc_list_result = await list_generated_doc_evidence(
        provider=docs_provider,
        project_id=project_id,
        access_token=access_token,
    )
    doc_metadata_items = doc_list_result.items
    activities.append(await service.record_activity(
        run,
        event_type="evidence.docs_metadata",
        label="Checked generated documentation metadata",
        status=ActivityEventStatus.SUCCEEDED
        if doc_list_result.status == "succeeded"
        else ActivityEventStatus.FAILED,
        summary=(
            f"Found {len(doc_metadata_items)} generated doc(s)."
            if doc_list_result.status == "succeeded"
            else doc_list_result.error_message
        ),
        safe_details={
            "capability_id": doc_list_result.capability_id,
            "evidence_count": len(doc_metadata_items),
            "evidence_refs": evidence_refs(doc_metadata_items),
        },
    ))

    runtime_result = await query_runtime_evidence(
        question=query,
        project_id=project_id,
        project_scope=project_scope,
        db=db,
    )
    runtime_items = runtime_result.items
    activities.append(await service.record_activity(
        run,
        event_type="evidence.runtime",
        label="Queried runtime evidence",
        status=ActivityEventStatus.SUCCEEDED
        if runtime_result.items
        else ActivityEventStatus.SKIPPED,
        summary=(
            f"Found {len(runtime_items)} runtime evidence item(s)."
            if runtime_items
            else runtime_result.error_message or "No runtime evidence was returned."
        ),
        safe_details={
            "capability_id": runtime_result.capability_id,
            "evidence_count": len(runtime_items),
            "evidence_refs": evidence_refs(runtime_items),
        },
    ))

    answer = _investigation_answer(
        project_id=project_id,
        doc_items=doc_search_items,
        doc_metadata_items=doc_metadata_items,
        runtime_items=runtime_items,
        runtime_error=runtime_result.error_message,
    )
    await service.mark_succeeded(
        run,
        output_summary=f"Investigation completed with {len(doc_search_items) + len(doc_metadata_items) + len(runtime_items)} evidence item(s).",
        artifact_links=[
            {"label": "Workflow Evidence", "url": f"/api/v1/harness/workflows/{run.id}", "kind": "workflow"},
        ],
    )
    activities.append(await service.record_activity(
        run,
        event_type="workflow.completed",
        label="Investigation completed",
        status=ActivityEventStatus.SUCCEEDED,
        summary="Produced an evidence-backed investigation answer.",
        safe_details={
            "workflow_run_id": run.id,
            "evidence_count": len(doc_search_items) + len(doc_metadata_items) + len(runtime_items),
        },
    ))
    return run, answer, activities


async def _execute_connector_inventory(
    *,
    conversation: Conversation,
    query: str,
    db: AsyncSession,
    user_id: int,
    project_id: str,
    project_scope: list[str] | None,
) -> tuple[WorkflowRun, str, list[ActivityEvent]]:
    service = _workflow_service(db)
    run = await service.start_run(
        conversation_id=conversation.id,
        user_id=user_id,
        project_id=project_id,
        intent_mode="connector_inventory",
        capability_id=_CONNECTOR_CAPABILITY_ID,
        input_summary=_input_summary(query),
    )
    connector_result = await read_connector_evidence(
        db=db,
        project_scope=project_scope,
        project_id=project_id,
    )
    activity = await service.record_activity(
        run,
        event_type="evidence.connectors",
        label="Read connector metadata",
        status=ActivityEventStatus.SUCCEEDED,
        summary=f"Found {len(connector_result.items)} configured connector source(s).",
        safe_details={
            "capability_id": connector_result.capability_id,
            "evidence_count": len(connector_result.items),
            "evidence_refs": evidence_refs(connector_result.items),
        },
    )
    answer = _connector_inventory_answer(project_id, connector_result.items)
    await service.mark_succeeded(
        run,
        output_summary=f"Connector inventory completed with {len(connector_result.items)} item(s).",
        artifact_links=[
            {"label": "Runtime Sources", "url": "/admin/runtime", "kind": "admin"},
            {"label": "Workflow Evidence", "url": f"/api/v1/harness/workflows/{run.id}", "kind": "workflow"},
        ],
    )
    final_activity = await service.record_activity(
        run,
        event_type="workflow.completed",
        label="Connector inventory completed",
        status=ActivityEventStatus.SUCCEEDED,
        summary="Produced a read-only connector inventory answer.",
        safe_details={"workflow_run_id": run.id, "evidence_count": len(connector_result.items)},
    )
    return run, answer, [activity, final_activity]


async def _execute_project_onboarding(
    *,
    conversation: Conversation,
    query: str,
    db: AsyncSession,
    user_id: int,
    project_id: str | None,
    project_scope: list[str] | None,
    user_permissions: set[str] | None,
    access_token: str | None,
    decision: HarnessPlannerDecision,
    docs_provider: McpDocumentationProvider,
) -> tuple[WorkflowRun, str, list[ActivityEvent]]:
    if project_id is None:
        raise ValueError("A project name is required to start onboarding")
    registry = await _project_execution_registry(db, project_id, "onboarding")
    service = HarnessWorkflowService(HarnessWorkflowRepository(db), registry)
    run = await service.start_run(
        conversation_id=conversation.id,
        user_id=user_id,
        project_id=project_id,
        intent_mode="project_onboarding",
        capability_id=PROJECT_ONBOARDING_CAPABILITY.id,
        input_summary=_input_summary(query),
    )
    arguments = dict(decision.arguments or {})
    selected_pack_ids = list(
        arguments.get("selected_pack_ids") or arguments.get("pack_ids") or []
    )
    if selected_pack_ids:
        pack_service = ProjectPackService(db, base_capability_registry())
        catalog = await pack_service.catalog(project_id)
        catalog.resolve(
            PackActivation(enabled_pack_ids=frozenset(selected_pack_ids))
        )
    try:
        plugin_pack_id = await _resolve_documentation_pack_id(
            arguments=arguments,
            query=query,
            provider=docs_provider,
            access_token=access_token,
        )
    except McpDocumentationProviderError as exc:
        updated = await HarnessWorkflowRepository(db).update_run(
            list(project_scope or []),
            run.id,
            WorkflowRunUpdate(
                status=WorkflowRunStatus.FAILED,
                error_message=exc.safe_message,
                current_node="resolve_documentation_pack",
                finished_at=datetime.datetime.now(datetime.UTC),
            ),
            owner_user_id=user_id,
        )
        return updated, exc.safe_message, []
    request = OnboardingRequest(
        workflow_run_id=run.id,
        project_id=project_id,
        repo_url=arguments.get("repo_url"),
        source_type=(
            str(arguments.get("source_type")).lower()
            if arguments.get("source_type") in {"git", "zip"}
            else "zip" if "zip upload" in query.lower() else None
        ),
        platform=arguments.get("platform") or "generic",
        installation_id=arguments.get("installation_id"),
        upload_receipt=arguments.get("upload_receipt"),
        selected_pack_ids=selected_pack_ids,
        plugin_pack_id=plugin_pack_id,
    )
    allowed_scope = list(dict.fromkeys([*(project_scope or []), project_id]))
    from src.harness.models import (
        ATTACH_GIT_SOURCE_CAPABILITY,
        ATTACH_ZIP_SOURCE_CAPABILITY,
    )

    context = CapabilityExecutionContext(
        user_id=user_id,
        conversation_id=conversation.id,
        project_scope=allowed_scope,
        permissions=user_permissions,
        approvals={ATTACH_GIT_SOURCE_CAPABILITY.id, ATTACH_ZIP_SOURCE_CAPABILITY.id},
        access_token=access_token,
    )
    executor = HarnessCapabilityExecutor(
        registry,
        docs_provider=docs_provider,
        projects_provider=McpProjectsProvider(),
    )
    result = await default_project_onboarding_workflow().run(
        request,
        executor=executor,
        context=context,
    )
    activities: list[ActivityEvent] = []
    for item in result.activity:
        activities.append(
            await service.record_activity(
                run,
                event_type=str(item.get("event_type") or "onboarding.event"),
                label=str(item.get("label") or "Project onboarding"),
                status=_activity_status(item.get("status")),
                summary=item.get("summary"),
                safe_details=dict(item.get("safe_details") or {}),
                runtime_node=item.get("runtime_node"),
            )
        )
    if result.status == "interrupted":
        from src.harness.models import WorkflowInterruptType

        interrupt_options = _onboarding_interrupt_options(result.interrupt_fields)
        updated = await service.mark_interrupted(
            run,
            output_summary="Project source information is required.",
            runtime_provider_id="langgraph.nexus",
            runtime_thread_id=result.thread_id,
            current_node=result.current_node,
            runtime_metadata={
                "graph": "project_onboarding",
                "interrupt": {
                    "langgraph_interrupt_id": result.interrupt_id,
                    "missing_slots": result.interrupt_fields,
                },
                "arguments": {
                    "project_id": project_id,
                    "platform": request.platform,
                    "source_type": request.source_type,
                    "selected_pack_ids": request.selected_pack_ids,
                    "plugin_pack_id": request.plugin_pack_id,
                },
            },
            interrupt_type=WorkflowInterruptType.CLARIFICATION,
            prompt=result.interrupt_prompt or "Which source should Nexus use?",
            options=interrupt_options,
            scope_snapshot={
                "project_id": project_id,
                "platform": request.platform,
                "source_type": request.source_type,
                "required_fields": result.interrupt_fields,
                "available_projects": allowed_scope,
            },
            policy_snapshot={"capability": "project onboarding"},
            normalized_arguments={
                "project_id": project_id,
                "repo_url": None,
                "platform": request.platform,
                "selected_pack_ids": request.selected_pack_ids,
                "plugin_pack_id": request.plugin_pack_id,
            },
        )
        return (
            updated,
            result.interrupt_prompt or f"Choose a source for `{project_id}`.",
            activities,
        )

    pack_activation = None
    if request.selected_pack_ids:
        pack_activation = await ProjectPackService(
            db, base_capability_registry()
        ).activate(
            project_id=project_id,
            workflow_key="documentation",
            active_pack_ids=request.selected_pack_ids,
            user_id=user_id,
        )
    if request.plugin_pack_id is not None and result.status != "failed":
        await docs_provider.set_project_documentation_pack(
            project_id=project_id,
            plugin_pack_id=request.plugin_pack_id,
            access_token=access_token,
        )
    repo = HarnessWorkflowRepository(db)
    if result.status == "failed":
        summary = next(
            (
                observation.error_message or observation.summary
                for observation in reversed(result.observations)
                if not observation.succeeded
            ),
            "Project onboarding could not continue.",
        )
        updated = await repo.update_run(
            [project_id],
            run.id,
            WorkflowRunUpdate(
                status=WorkflowRunStatus.FAILED,
                error_message=summary,
                runtime_provider_id="langgraph.nexus",
                runtime_thread_id=result.thread_id,
                current_node=result.current_node,
                finished_at=datetime.datetime.now(datetime.UTC),
            ),
        )
        return updated, summary, activities

    updated = await repo.update_run(
        [project_id],
        run.id,
        WorkflowRunUpdate(
            status=WorkflowRunStatus.TRIGGERED,
            output_summary="Project onboarding started.",
            artifact_links=result.artifact_links,
            artifact_receipts=result.artifact_links,
            runtime_provider_id="langgraph.nexus",
            runtime_thread_id=result.thread_id,
            current_node=result.current_node,
            runtime_metadata={
                "graph": "project_onboarding",
                **(
                    {"pack_snapshot": pack_activation.snapshot.model_dump(mode="json")}
                    if pack_activation is not None
                    else {}
                ),
            },
        ),
    )
    links = "\n".join(
        f"- [{item.get('label', 'Open')}]({item.get('url')})"
        for item in result.artifact_links
        if item.get("url")
    )
    answer = f"Project `{project_id}` is onboarded and its scan/documentation workflows have started."
    if links:
        answer += f"\n\n{links}"
    return updated, answer, activities


async def resume_project_onboarding_workflow(
    *,
    run: WorkflowRun,
    response_value: str,
    required_fields: list[str] | None,
    db: AsyncSession,
    user_id: int,
    project_scope: list[str] | None,
    user_permissions: set[str] | None,
    access_token: str | None,
) -> tuple[WorkflowRun, list[ActivityEvent]]:
    metadata_arguments = run.runtime_metadata.get("arguments")
    arguments = metadata_arguments if isinstance(metadata_arguments, dict) else {}
    project_id = str(arguments.get("project_id") or run.project_id)
    platform = str(arguments.get("platform") or "generic")
    source_type = arguments.get("source_type")
    if source_type not in {"git", "zip"}:
        source_type = None
    selected_pack_ids = [
        str(value)
        for value in arguments.get("selected_pack_ids", [])
        if isinstance(value, str)
    ]
    plugin_pack_id = arguments.get("plugin_pack_id")
    if not isinstance(plugin_pack_id, int) or isinstance(plugin_pack_id, bool) or plugin_pack_id < 1:
        plugin_pack_id = None
    registry = await _project_execution_registry(db, project_id, "onboarding")
    from src.harness.models import (
        ATTACH_GIT_SOURCE_CAPABILITY,
        ATTACH_ZIP_SOURCE_CAPABILITY,
        WorkflowInterruptType,
    )

    context = CapabilityExecutionContext(
        user_id=user_id,
        conversation_id=run.conversation_id,
        project_scope=list(dict.fromkeys([*(project_scope or []), project_id])),
        permissions=user_permissions,
        approvals={ATTACH_GIT_SOURCE_CAPABILITY.id, ATTACH_ZIP_SOURCE_CAPABILITY.id},
        access_token=access_token,
    )
    docs_provider = McpDocumentationProvider()
    executor = HarnessCapabilityExecutor(
        registry,
        docs_provider=docs_provider,
        projects_provider=McpProjectsProvider(),
    )
    request = OnboardingRequest(
        workflow_run_id=run.id,
        project_id=project_id,
        platform=platform,
        source_type=source_type,
        selected_pack_ids=selected_pack_ids,
        plugin_pack_id=plugin_pack_id,
        thread_id=run.runtime_thread_id,
    )
    workflow = default_project_onboarding_workflow()
    # The single resume answer may satisfy either pending clarification: a
    # repository URL, or a platform name when the platform interrupt paused
    # the run. Each interrupt reads only its own key.
    fields = set(required_fields or [])
    response: dict[str, Any] = {"platform": platform}
    if "upload_receipt" in fields:
        response.update({"source_type": "zip", "upload_receipt": response_value})
    elif "source_type" in fields:
        response["source_type"] = response_value.strip().lower()
    elif "platform" in fields:
        response["platform"] = normalize_project_platform(response_value) or platform
    else:
        response.update({"source_type": "git", "repo_url": response_value})
    try:
        result = await workflow.resume(
            request,
            response=response,
            executor=executor,
            context=context,
        )
    except Exception:
        request_updates: dict[str, Any] = {}
        if "upload_receipt" in fields:
            request_updates["upload_receipt"] = response_value
        elif "source_type" not in fields:
            request_updates["repo_url"] = response_value
        result = await workflow.run(
            request.model_copy(update=request_updates),
            executor=executor,
            context=context,
        )
    service = HarnessWorkflowService(HarnessWorkflowRepository(db), registry)
    activities: list[ActivityEvent] = []
    for item in result.activity:
        activities.append(
            await service.record_activity(
                run,
                event_type=str(item.get("event_type") or "onboarding.event"),
                label=str(item.get("label") or "Project onboarding"),
                status=_activity_status(item.get("status")),
                summary=item.get("summary"),
                safe_details=dict(item.get("safe_details") or {}),
                runtime_node=item.get("runtime_node"),
            )
        )
    if result.status == "interrupted":
        updated = await service.mark_interrupted(
            run,
            output_summary="Additional project source information is required.",
            runtime_provider_id="langgraph.nexus",
            runtime_thread_id=result.thread_id,
            current_node=result.current_node,
            runtime_metadata={
                "graph": "project_onboarding",
                "interrupt": {
                    "langgraph_interrupt_id": result.interrupt_id,
                    "missing_slots": result.interrupt_fields,
                },
                "arguments": {
                    "project_id": project_id,
                    "platform": platform,
                    "source_type": source_type,
                    "selected_pack_ids": selected_pack_ids,
                    "plugin_pack_id": plugin_pack_id,
                },
            },
            interrupt_type=WorkflowInterruptType.CLARIFICATION,
            prompt=result.interrupt_prompt or "Additional source information is required.",
            options=_onboarding_interrupt_options(result.interrupt_fields),
            scope_snapshot={
                "project_id": project_id,
                "platform": platform,
                "source_type": source_type,
                "required_fields": result.interrupt_fields,
                "available_projects": list(
                    dict.fromkeys([*(project_scope or []), project_id])
                ),
            },
            policy_snapshot={"capability": "project onboarding"},
            normalized_arguments={
                "project_id": project_id,
                "platform": platform,
                "selected_pack_ids": selected_pack_ids,
                "plugin_pack_id": plugin_pack_id,
                "required_fields": result.interrupt_fields,
            },
        )
        return updated, activities

    status = WorkflowRunStatus.TRIGGERED if result.status != "failed" else WorkflowRunStatus.FAILED
    error = next(
        (
            observation.error_message or observation.summary
            for observation in reversed(result.observations)
            if not observation.succeeded
        ),
        None,
    )
    source_receipts = [
        {
            "kind": "zip_source",
            "filename": observation.data.get("filename"),
            "size_bytes": observation.data.get("size_bytes"),
            "sha256": observation.data.get("sha256"),
        }
        for observation in result.observations
        if observation.capability_id == ATTACH_ZIP_SOURCE_CAPABILITY.id
        and observation.succeeded
    ]
    pack_activation = None
    if selected_pack_ids and result.status != "failed":
        pack_activation = await ProjectPackService(
            db, base_capability_registry()
        ).activate(
            project_id=project_id,
            workflow_key="documentation",
            active_pack_ids=selected_pack_ids,
            user_id=user_id,
        )
    if plugin_pack_id is not None and result.status != "failed":
        await docs_provider.set_project_documentation_pack(
            project_id=project_id,
            plugin_pack_id=plugin_pack_id,
            access_token=access_token,
        )
    updated = await HarnessWorkflowRepository(db).update_run(
        [],
        run.id,
        WorkflowRunUpdate(
            status=status,
            output_summary="Project onboarding resumed." if status == WorkflowRunStatus.TRIGGERED else None,
            error_message=error,
            artifact_links=result.artifact_links,
            artifact_receipts=[*result.artifact_links, *source_receipts],
            runtime_provider_id="langgraph.nexus",
            runtime_thread_id=result.thread_id,
            current_node=result.current_node,
            runtime_metadata={
                "graph": "project_onboarding",
                **(
                    {"pack_snapshot": pack_activation.snapshot.model_dump(mode="json")}
                    if pack_activation is not None
                    else {}
                ),
            },
            finished_at=(datetime.datetime.now(datetime.UTC) if status == WorkflowRunStatus.FAILED else None),
        ),
        owner_user_id=user_id,
    )
    return updated, activities


def _onboarding_interrupt_options(required_fields: list[str]) -> list[dict[str, str]]:
    if "source_type" not in required_fields:
        return []
    return [
        {"label": "Git repository", "value": "git"},
        {"label": "ZIP upload", "value": "zip"},
    ]


async def _execute_model_selected_connector(
    *,
    conversation: Conversation,
    query: str,
    db: AsyncSession,
    user_id: int,
    project_id: str | None,
    project_scope: list[str] | None,
    user_permissions: set[str] | None,
    decision: HarnessPlannerDecision,
) -> tuple[WorkflowRun, str, list[ActivityEvent]]:
    capability_id = decision.capability_id or ""
    connector_repo = ConnectorRepository(db)
    registry = await build_request_capability_registry(
        connector_repo, project_ids=project_scope
    )
    service = HarnessWorkflowService(HarnessWorkflowRepository(db), registry)
    run = await service.start_run(
        conversation_id=conversation.id,
        user_id=user_id,
        project_id=project_id or _PENDING_PROJECT_SCOPE_ID,
        intent_mode="connector_evidence",
        capability_id=capability_id,
        input_summary=_input_summary(query),
    )
    arguments = dict(decision.arguments or {})
    arguments.setdefault("query", query)
    if project_id is not None:
        arguments.setdefault("project_id", project_id)
    runtime_result = await default_langgraph_runtime_provider().run_turn(
        RuntimeTurnRequest(
            message=query,
            manifest=build_runtime_capability_manifest(
                registry,
                scope=ManifestScope(
                    project_ids=project_scope,
                    permissions=user_permissions,
                ),
            ),
            planned_capability_id=capability_id,
            arguments=arguments,
            workflow_run_id=run.id,
        ),
        executor=HarnessCapabilityExecutor(
            registry,
            connector_service=ConnectorService(
                connector_repo, default_connector_provider_registry()
            ),
        ),
        context=CapabilityExecutionContext(
            user_id=user_id,
            conversation_id=conversation.id,
            project_scope=project_scope,
            permissions=user_permissions,
        ),
    )
    activities: list[ActivityEvent] = []
    for item in runtime_result.activity:
        activities.append(
            await service.record_activity(
                run,
                event_type=str(item.get("event_type") or "runtime.event"),
                label=str(item.get("label") or "Connector activity"),
                status=_activity_status(item.get("status")),
                summary=item.get("summary"),
                safe_details=dict(item.get("safe_details") or {}),
                runtime_node=item.get("runtime_node"),
                tool_call_id=runtime_result.runtime_run_id,
            )
        )
    observation = runtime_result.observation
    if observation is None or not observation.succeeded:
        error = (
            observation.error_message if observation is not None else runtime_result.final_summary
        ) or "The connector could not answer this request."
        await service.mark_failed(run, error_message=error)
        return run, error, activities

    raw_items = observation.data.get("items")
    items = [item for item in raw_items if isinstance(item, dict)] if isinstance(raw_items, list) else []
    refs = [
        {"id": str(item.get("id") or ""), "source_type": "connector"}
        for item in items
        if item.get("id")
    ]
    await HarnessWorkflowRepository(db).update_run(
        [run.project_id],
        run.id,
        WorkflowRunUpdate(
            status=WorkflowRunStatus.SUCCEEDED,
            output_summary=observation.summary,
            evidence_refs=refs,
            runtime_provider_id=runtime_result.runtime_provider,
            runtime_thread_id=runtime_result.thread_id,
            runtime_run_id=runtime_result.runtime_run_id,
            current_node=runtime_result.current_node,
            runtime_metadata=runtime_result.runtime_metadata,
            finished_at=datetime.datetime.now(datetime.UTC),
        ),
    )
    if not items:
        return run, "The connected source did not return matching evidence.", activities
    lines = [
        f"- **{item.get('title', 'Evidence')}**: {item.get('summary', '')}"
        for item in items[:8]
    ]
    answer = "I checked the connected source and found:\n\n" + "\n".join(lines)
    return run, answer, activities


async def _execute_doc_update_from_evidence(
    *,
    conversation: Conversation,
    query: str,
    db: AsyncSession,
    user_id: int,
    project_id: str,
    access_token: str | None,
    provider: McpDocumentationProvider | None,
    user_permissions: set[str] | None,
) -> tuple[WorkflowRun, str, list[ActivityEvent]]:
    service = _workflow_service(db)
    run = await service.start_run(
        conversation_id=conversation.id,
        user_id=user_id,
        project_id=project_id,
        intent_mode="update_docs_from_evidence",
        capability_id=_DOC_UPDATE_CAPABILITY_ID,
        input_summary=_input_summary(query),
    )
    activities: list[ActivityEvent] = []
    activities.append(await service.record_activity(
        run,
        event_type="scope",
        label="Resolved project scope",
        status=ActivityEventStatus.SUCCEEDED,
        summary=f"Updating documentation for project `{project_id}`.",
        safe_details={"project_id": project_id},
    ))

    update_request = _extract_doc_update_request(query)
    if update_request is None:
        activities.append(await service.record_activity(
            run,
            event_type="capability_skipped",
            label="Documentation update needs target and evidence",
            status=ActivityEventStatus.SKIPPED,
            summary="No documentation file and explicit evidence were both present in the request.",
            safe_details={"project_id": project_id, "write_performed": False},
        ))
        await service.mark_failed(
            run,
            error_message="Target documentation file and explicit evidence are required.",
        )
        return run, _doc_update_clarification(run), activities

    activities.append(await service.record_activity(
        run,
        event_type="evidence.parsed",
        label="Parsed documentation update request",
        status=ActivityEventStatus.SUCCEEDED,
        summary=f"Target doc `{update_request['filename']}` and explicit chat evidence were found.",
        safe_details={
            "project_id": project_id,
            "target_doc": update_request["filename"],
            "evidence_refs": update_request["evidence_refs"],
        },
    ))
    docs_provider = provider or McpDocumentationProvider()
    try:
        base_version = await docs_provider.get_document_version(
            project_id=project_id,
            filename=update_request["filename"],
            access_token=access_token,
        )
    except McpDocumentationProviderError as exc:
        await service.mark_failed(run, error_message=exc.safe_message)
        activities.append(
            await service.record_activity(
                run,
                event_type="proposal.failed",
                label="Could not inspect target document",
                status=ActivityEventStatus.FAILED,
                summary=exc.safe_message,
                safe_details={"project_id": project_id, "target_doc": update_request["filename"]},
            )
        )
        return run, exc.safe_message, activities
    arguments = {
        "project_id": project_id,
        "target_doc": update_request["filename"],
        "evidence_text": update_request["evidence_text"],
        "evidence_refs": update_request["evidence_refs"],
        "workflow_run_id": str(run.id),
        "section": update_request.get("section"),
        "expected_base_sha": base_version.sha256,
    }
    registry = default_capability_registry()
    workflow_request = DocumentationUpdateRequest(
        workflow_run_id=run.id,
        project_id=project_id,
        target_doc=update_request["filename"],
        evidence_refs=update_request["evidence_refs"],
        evidence_text=update_request["evidence_text"],
        expected_base_sha=base_version.sha256,
        section=update_request.get("section"),
    )
    runtime_result = await default_documentation_update_workflow().run(
        workflow_request,
        executor=HarnessCapabilityExecutor(registry, docs_provider=docs_provider),
        context=CapabilityExecutionContext(
            user_id=user_id,
            conversation_id=conversation.id,
            project_scope=[project_id],
            permissions=user_permissions,
            access_token=access_token,
        ),
    )
    for item in runtime_result.activity:
        activities.append(
            await service.record_activity(
                run,
                event_type=str(item.get("event_type") or "runtime.event"),
                label=str(item.get("label") or "Documentation update"),
                status=_activity_status(item.get("status")),
                summary=item.get("summary"),
                safe_details=dict(item.get("safe_details") or {}),
                runtime_node=item.get("runtime_node"),
            )
        )
    if runtime_result.status == "interrupted":
        from src.harness.models import WorkflowInterruptType

        updated = await service.mark_interrupted(
            run,
            output_summary="Approval is required before updating documentation.",
            runtime_provider_id="langgraph.nexus",
            runtime_thread_id=runtime_result.thread_id,
            current_node=runtime_result.current_node,
            runtime_metadata={
                "graph": "documentation_update",
                "proposal": {
                    "target_doc": update_request["filename"],
                    "section": update_request.get("section"),
                    "evidence_refs": update_request["evidence_refs"],
                    "expected_base_sha": base_version.sha256,
                },
                "interrupt": {
                    "langgraph_interrupt_id": runtime_result.interrupt_id,
                    "type": "approval",
                },
            },
            interrupt_type=WorkflowInterruptType.APPROVAL,
            prompt=(
                f"Update `{update_request['filename']}` in `{project_id}` from the supplied evidence?"
            ),
            policy_snapshot={"risk": "writes_content"},
            scope_snapshot={
                "project_id": project_id,
                "execution_arguments": arguments,
            },
            normalized_arguments=arguments,
        )
        return (
            updated,
            f"I’m ready to update `{update_request['filename']}` in `{project_id}` from the supplied evidence. Please approve the change before I write it.",
            activities,
        )

    observation = runtime_result.observation
    if observation is None or not observation.succeeded:
        error = (
            observation.error_message if observation is not None else None
        ) or "Documentation update failed."
        await service.mark_failed(run, error_message=error)
        return run, _doc_update_failed_answer(project_id, run, error), activities
    updated = await HarnessWorkflowRepository(db).update_run(
        [project_id],
        run.id,
        WorkflowRunUpdate(
            status=WorkflowRunStatus.SUCCEEDED,
            output_summary=observation.summary,
            artifact_links=observation.artifact_links,
            artifact_receipts=observation.artifact_links,
            runtime_provider_id="langgraph.nexus",
            runtime_thread_id=runtime_result.thread_id,
            current_node=runtime_result.current_node,
            runtime_metadata={"graph": "documentation_update"},
            finished_at=datetime.datetime.now(datetime.UTC),
        ),
    )
    return updated, observation.summary or "Documentation updated.", activities


async def resume_documentation_update_workflow(
    *,
    run: WorkflowRun,
    approved: bool,
    db: AsyncSession,
    user_id: int,
    project_scope: list[str] | None,
    user_permissions: set[str] | None,
    access_token: str | None,
    arguments: dict[str, Any],
) -> tuple[WorkflowRun, list[ActivityEvent]]:
    raw_arguments = arguments
    project_id = str(raw_arguments.get("project_id") or "")
    if project_scope is not None and project_id not in project_scope:
        raise ValueError("Project access required")
    registry = default_capability_registry()
    request = DocumentationUpdateRequest(
        workflow_run_id=run.id,
        project_id=project_id,
        target_doc=str(raw_arguments.get("target_doc") or ""),
        evidence_refs=[str(item) for item in raw_arguments.get("evidence_refs", [])],
        evidence_text=str(raw_arguments.get("evidence_text") or ""),
        expected_base_sha=str(raw_arguments.get("expected_base_sha") or ""),
        section=(str(raw_arguments["section"]) if raw_arguments.get("section") else None),
        thread_id=run.runtime_thread_id,
    )
    context = CapabilityExecutionContext(
        user_id=user_id,
        conversation_id=run.conversation_id,
        project_scope=[project_id],
        permissions=user_permissions,
        access_token=access_token,
    )
    executor = HarnessCapabilityExecutor(
        registry, docs_provider=McpDocumentationProvider()
    )
    workflow = default_documentation_update_workflow()
    try:
        runtime_result = await workflow.resume(
            request,
            approved=approved,
            executor=executor,
            context=context,
        )
    except Exception:
        fallback_context = context.model_copy(
            update={"approvals": {_DOC_UPDATE_CAPABILITY_ID} if approved else set()}
        )
        if not approved:
            runtime_result = await workflow.run(
                request,
                executor=executor,
                context=context,
            )
        else:
            runtime_result = await workflow.run(
                request,
                executor=executor,
                context=fallback_context,
            )
    service = _workflow_service(db)
    activities: list[ActivityEvent] = []
    for item in runtime_result.activity:
        activity_status = _activity_status(item.get("status"))
        label = (
            "Documentation update failed"
            if activity_status is ActivityEventStatus.FAILED
            else str(item.get("label") or "Documentation update")
        )
        activities.append(
            await service.record_activity(
                run,
                event_type=str(item.get("event_type") or "runtime.event"),
                label=label,
                status=activity_status,
                summary=item.get("summary"),
                safe_details=dict(item.get("safe_details") or {}),
                runtime_node=item.get("runtime_node"),
            )
        )
    repo = HarnessWorkflowRepository(db)
    if not approved or runtime_result.status == "cancelled":
        updated = await repo.update_run(
            [],
            run.id,
            WorkflowRunUpdate(
                status=WorkflowRunStatus.CANCELLED,
                cancellation_reason="Documentation update was not approved.",
                cancelled_at=datetime.datetime.now(datetime.UTC),
                cancelled_by_user_id=user_id,
                finished_at=datetime.datetime.now(datetime.UTC),
                runtime_provider_id="langgraph.nexus",
                runtime_thread_id=runtime_result.thread_id,
                current_node=runtime_result.current_node,
                runtime_metadata={"graph": "documentation_update"},
            ),
            owner_user_id=user_id,
        )
        return updated, activities
    observation = runtime_result.observation
    if observation is None or not observation.succeeded:
        error = (
            observation.error_message if observation is not None else None
        ) or "Documentation update failed."
        updated = await repo.update_run(
            [],
            run.id,
            WorkflowRunUpdate(
                status=WorkflowRunStatus.FAILED,
                error_message=error,
                finished_at=datetime.datetime.now(datetime.UTC),
                runtime_provider_id="langgraph.nexus",
                runtime_thread_id=runtime_result.thread_id,
                current_node=runtime_result.current_node,
                runtime_metadata={"graph": "documentation_update"},
            ),
            owner_user_id=user_id,
        )
        return updated, activities
    updated = await repo.update_run(
        [],
        run.id,
        WorkflowRunUpdate(
            status=WorkflowRunStatus.SUCCEEDED,
            output_summary=observation.summary,
            artifact_links=observation.artifact_links,
            artifact_receipts=observation.artifact_links,
            runtime_provider_id="langgraph.nexus",
            runtime_thread_id=runtime_result.thread_id,
            current_node=runtime_result.current_node,
            runtime_metadata={
                "graph": "documentation_update",
                "receipt": observation.data,
            },
            finished_at=datetime.datetime.now(datetime.UTC),
        ),
        owner_user_id=user_id,
    )
    return updated, activities


async def _persist_assistant_message(
    *,
    conversation: Conversation,
    db: AsyncSession,
    answer: str,
    latency_ms: int,
    workflow_run_id: int | None = None,
) -> MessageResponse:
    turn_plan = (
        await db.execute(
            select(ConversationMessage.turn_plan)
            .where(
                ConversationMessage.conversation_id == conversation.id,
                ConversationMessage.role == "user",
                ConversationMessage.deleted_at.is_(None),
            )
            .order_by(ConversationMessage.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    structured_answer = await _workflow_structured_answer(
        db=db,
        workflow_run_id=workflow_run_id,
        turn_plan=turn_plan,
        answer=answer,
    )
    assistant_msg = ConversationMessage(
        conversation_id=conversation.id,
        role="assistant",
        content=answer,
        sources=None,
        latency_ms=latency_ms,
        workflow_run_id=workflow_run_id,
        turn_plan=turn_plan,
        structured_answer=structured_answer,
    )
    db.add(assistant_msg)
    await db.flush()
    return MessageResponse(
        id=assistant_msg.id,
        role="assistant",
        content=answer,
        sources=None,
        latency_ms=latency_ms,
        workflow_run_id=workflow_run_id,
        turn_plan=turn_plan,
        structured_answer=structured_answer,
        created_at=assistant_msg.created_at,
    )


async def _upsert_continuation(
    *,
    db: AsyncSession,
    conversation: Conversation,
    user_id: int,
    user_message_id: int,
    plan: TurnPlan | None,
) -> None:
    """Persist only resumable runtime requests, using an optimistic version."""
    if plan is None or plan.task_type is not TurnTaskType.RUNTIME_ANSWER:
        return
    runtime_scope = _runtime_scope_from_plan(plan)
    slots = []
    if runtime_scope is None:
        slots.append(
            ContinuationSlot(
                name="project_id",
                value=plan.resolved_scope.get("project_id"),
                options=[
                    {"value": item, "label": item}
                    for item in plan.resolved_scope.get("project_ids") or []
                ],
            )
        )
    if runtime_scope is None or runtime_scope.kind is not RuntimeScopeKind.GLOBAL:
        slots.append(ContinuationSlot(name="time_window", value=plan.resolved_scope.get("time_window")))
    slots.append(
        ContinuationSlot(
            name="requested_sections",
            value=plan.resolved_scope.get("requested_sections"),
            required=False,
        )
    )
    missing = [slot.name for slot in slots if slot.required and slot.value is None]
    existing = (
        await db.execute(
            select(HarnessConversationContinuationRow)
            .where(
                HarnessConversationContinuationRow.conversation_id == conversation.id,
                HarnessConversationContinuationRow.owner_user_id == conversation.user_id,
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if existing is None and not missing:
        return
    if existing is not None and existing.status != "active" and not missing:
        return
    envelope = ContinuationEnvelope(
        continuation_id=(str(existing.id) if existing else uuid.uuid4().hex),
        version=(existing.version + 1 if existing else 1),
        status="active",
        plan=plan.model_copy(update={"missing_slots": missing}),
        slots=slots,
        missing_slots=missing,
    )
    if existing is None:
        db.add(HarnessConversationContinuationRow(
            conversation_id=conversation.id,
            owner_user_id=user_id,
            status="active",
            version=1,
            envelope=envelope.model_dump(mode="json"),
            last_message_id=user_message_id,
        ))
        return
    updated = await db.execute(
        update(HarnessConversationContinuationRow)
        .where(
            HarnessConversationContinuationRow.id == existing.id,
            HarnessConversationContinuationRow.version == existing.version,
        )
        .values(
            status="active",
            version=envelope.version,
            envelope=envelope.model_dump(mode="json"),
            last_message_id=user_message_id,
        )
    )
    if updated.rowcount != 1:
        logger.warning("Continuation update lost optimistic race for conversation %s", conversation.id)


async def _complete_runtime_continuation(
    db: AsyncSession, conversation: Conversation, user_id: int
) -> None:
    """Consume the active runtime continuation after an execution attempt."""
    continuation = await _active_continuation(db, conversation)
    if continuation is None:
        return
    updated = await db.execute(
        update(HarnessConversationContinuationRow)
        .where(
            HarnessConversationContinuationRow.id == continuation.id,
            HarnessConversationContinuationRow.owner_user_id == user_id,
            HarnessConversationContinuationRow.status == "active",
            HarnessConversationContinuationRow.version == continuation.version,
        )
        .values(status="completed", version=continuation.version + 1)
    )
    if updated.rowcount != 1:
        logger.warning("Continuation completion lost optimistic race for conversation %s", conversation.id)


async def _recompose_persisted_evidence(
    db: AsyncSession, workflow_run_id: int | None, plan: TurnPlan | None
) -> str:
    if workflow_run_id is None or plan is None:
        return "I do not have persisted runtime results to reformat for this request."
    prior_answer = (
        await db.execute(
            select(ConversationMessage.content)
            .where(
                ConversationMessage.workflow_run_id == workflow_run_id,
                ConversationMessage.role == "assistant",
                ConversationMessage.deleted_at.is_(None),
            )
            .order_by(
                func.length(ConversationMessage.content).desc(),
                ConversationMessage.id.desc(),
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if isinstance(prior_answer, str) and prior_answer.strip():
        return prior_answer
    evidence = await HarnessEvidenceRepository(db).list_used(workflow_run_id)
    if not evidence:
        return "The prior runtime execution produced no persisted observations to reformat."
    structured = EvidenceAnswerSynthesizer().compose(
        question=plan.objective, plan=plan, evidence=evidence, draft=""
    )
    return structured.as_markdown()


async def _workflow_structured_answer(
    *,
    db: AsyncSession,
    workflow_run_id: int | None,
    turn_plan: dict[str, Any] | None,
    answer: str,
) -> dict[str, Any] | None:
    if workflow_run_id is None or not isinstance(turn_plan, dict):
        return None
    try:
        plan = TurnPlan.model_validate(turn_plan)
    except (TypeError, ValueError):
        return None
    evidence = await HarnessEvidenceRepository(db).list_used(workflow_run_id)
    if not evidence:
        return None
    run = await HarnessWorkflowRepository(db).get_run(None, workflow_run_id)
    try:
        investigation_payload = run.runtime_metadata.get("investigation_answer")
        if plan.output_contract == "incident_report" and isinstance(
            investigation_payload, dict
        ):
            structured = EvidenceAnswerSynthesizer().compose_incident(
                question=plan.objective,
                plan=plan,
                evidence=evidence,
                investigation=InvestigationAnswer.model_validate(investigation_payload),
            )
        else:
            structured = EvidenceAnswerSynthesizer().compose(
                question=plan.objective,
                plan=plan,
                evidence=evidence,
                draft=answer,
            )
    except ValueError as exc:
        logger.warning("Workflow structured answer validation failed: %s", exc)
        return None
    execution_status = {
        WorkflowRunStatus.SUCCEEDED: "completed",
        WorkflowRunStatus.FAILED: "failed",
        WorkflowRunStatus.CANCELLED: "cancelled",
    }.get(run.status, "running")
    artifacts = [
        {
            "label": str(item.get("label") or "Artifact"),
            "url": str(item.get("url") or ""),
            "kind": str(item.get("kind") or "artifact"),
        }
        for item in run.artifact_links
        if isinstance(item, dict) and item.get("url")
    ]
    missing_sources = run.runtime_metadata.get("missing_required_sources") or []
    limitations = list(structured.limitations)
    if (
        plan.output_contract != "incident_report"
        and isinstance(missing_sources, list)
        and missing_sources
    ):
        limitations.append(
            "Unavailable evidence: "
            + ", ".join(str(item) for item in missing_sources[:8])
            + "."
        )
    return structured.model_copy(
        update={
            "execution_status": execution_status,
            "answer_status": (
                structured.answer_status
                if structured.answer_status is EvidenceAnswerStatus.INSUFFICIENT
                else EvidenceAnswerStatus.PARTIAL
                if limitations
                else structured.answer_status
            ),
            "limitations": limitations,
            "artifacts": artifacts,
        }
    ).model_dump(mode="json")


def _workflow_service(db: AsyncSession) -> HarnessWorkflowService:
    return HarnessWorkflowService(
        HarnessWorkflowRepository(db),
        default_capability_registry(),
    )


async def _project_execution_registry(
    db: AsyncSession,
    project_id: str,
    workflow_key: str,
):
    base = base_capability_registry()
    resolved = await ProjectPackService(db, base).resolve_for_execution(
        project_id, workflow_key
    )
    if resolved is None:
        return default_capability_registry()
    return compile_resolved_pack_registry(base, resolved)


def _artifact_links(result: McpDocumentationGenerationResult) -> list[dict[str, Any]]:
    return [
        {"label": "Generation Status", "url": result.status_path, "kind": "status"},
        {"label": "Project Documentation", "url": result.docs_path, "kind": "documentation"},
    ]


def _success_answer(
    project_id: str,
    run: WorkflowRun,
    result: McpDocumentationGenerationResult,
) -> str:
    _ = run, result
    return (
        f"Documentation generation started for `{project_id}`. "
        "I’ll keep the workflow status here and the generated documents will appear "
        "in the project documentation view as they become available."
    )


def _doc_update_artifact_links(
    project_id: str,
    run: WorkflowRun,
    result: McpEvidenceDocUpdateResult,
) -> list[dict[str, Any]]:
    return [
        {"label": "Updated Documentation", "url": result.docs_path, "kind": "documentation"},
        {"label": "Project Documentation", "url": f"/projects/{project_id}/docs", "kind": "documentation"},
        {"label": "Workflow Evidence", "url": f"/api/v1/harness/workflows/{run.id}", "kind": "workflow"},
        {"label": "Update Receipt", "url": result.receipt_path, "kind": "receipt"},
    ]


def _doc_update_success_answer(
    project_id: str,
    run: WorkflowRun,
    result: McpEvidenceDocUpdateResult,
) -> str:
    refs = ", ".join(f"`{ref}`" for ref in result.evidence_refs) or "`chat_prompt`"
    return (
        f"Updated `{result.filename}` for `{project_id}` from provided evidence.\n\n"
        f"- Updated doc: `{result.docs_path}`\n"
        f"- Receipt: `{result.receipt_path}`\n"
        f"- Evidence refs: {refs}\n"
        f"- Workflow run: `{run.id}`"
    )


def _doc_update_failed_answer(project_id: str, run: WorkflowRun, error_message: str) -> str:
    return (
        f"I could not update documentation for `{project_id}`.\n\n"
        f"{error_message}\n\n"
        f"Workflow run: `{run.id}`"
    )


def _investigation_answer(
    *,
    project_id: str,
    doc_items: list[EvidenceItem],
    doc_metadata_items: list[EvidenceItem],
    runtime_items: list[EvidenceItem],
    runtime_error: str | None,
) -> str:
    lines = [
        f"## Operational Investigation: `{project_id}`",
        "",
        "I checked indexed documentation, generated documentation metadata, and runtime evidence through the harness.",
        "",
    ]
    failure_items = [item for item in runtime_items if item.source_type == "runtime_failure"]
    if failure_items:
        lines.append("### Runtime Findings")
        for item in failure_items[:3]:
            execution_id = item.metadata.get("execution_id")
            suffix = f" (`{execution_id}`)" if execution_id else ""
            lines.append(f"- {item.title}{suffix}: {item.summary} [{item.id}]")
        lines.append("")
    elif runtime_error:
        lines.extend([
            "### Runtime Findings",
            f"- Runtime evidence was not available: {runtime_error}",
            "",
        ])
    else:
        lines.extend([
            "### Runtime Findings",
            "- No recent runtime failure evidence was returned by the configured runtime tools.",
            "",
        ])

    if doc_items:
        lines.append("### Documentation Evidence")
        for item in doc_items[:3]:
            lines.append(f"- {item.title}: {item.summary} [{item.id}]")
        lines.append("")
    else:
        lines.extend([
            "### Documentation Evidence",
            "- No matching indexed documentation evidence was found.",
            "",
        ])

    stale_note = _stale_docs_note(doc_metadata_items, runtime_items)
    if stale_note:
        lines.extend(["### Freshness", f"- {stale_note}", ""])

    evidence_ids = evidence_refs([*runtime_items, *doc_items, *doc_metadata_items], limit=10)
    if evidence_ids:
        lines.extend([
            "### Evidence Used",
            ", ".join(f"`{item}`" for item in evidence_ids),
        ])
    else:
        lines.extend([
            "### Evidence Used",
            "No evidence items were returned.",
        ])
    return "\n".join(lines).strip()


def _connector_inventory_answer(project_id: str, items: list[EvidenceItem]) -> str:
    if not items:
        return (
            f"No connector sources are configured for `{project_id}` yet.\n\n"
            "The harness connector catalog is available from `/api/v1/harness/connectors`, "
            "and source configuration remains managed from `/admin/runtime`."
        )
    lines = [
        f"## Connector Inventory: `{project_id}`",
        "",
    ]
    for item in items[:10]:
        enabled = item.metadata.get("enabled")
        status = item.metadata.get("status")
        source_type = item.metadata.get("source_type")
        lines.append(
            f"- {item.title}: `{source_type}` status `{status}`, enabled `{enabled}` [{item.id}]"
        )
    lines.extend([
        "",
        "Configuration and credential rotation remain managed from `/admin/runtime`.",
    ])
    return "\n".join(lines)


def _stale_docs_note(
    doc_metadata_items: list[EvidenceItem],
    runtime_items: list[EvidenceItem],
) -> str | None:
    latest_doc = latest_evidence_timestamp(doc_metadata_items)
    latest_runtime = latest_evidence_timestamp(runtime_items)
    if latest_doc is None or latest_runtime is None:
        return None
    if latest_runtime > latest_doc:
        return (
            "Runtime evidence is newer than the generated documentation metadata. "
            "Treat the docs as potentially stale for this incident; I did not rewrite them automatically."
        )
    return None


def _capability_awareness_answer(
    project_options: list[ProjectOption],
    *,
    permissions: set[str] | None = None,
) -> str:
    manifest = build_runtime_capability_manifest(
        default_capability_registry(),
        scope=ManifestScope(permissions=permissions),
    )
    by_id = {tool.id: tool for tool in manifest.tools}
    can_generate = _CAPABILITY_ID in by_id
    lines: list[str] = []
    if can_generate:
        lines.append(
            "Yes. I can generate project documentation from the main chat."
        )
    else:
        lines.append(
            "Documentation generation is not available right now."
        )

    lines.extend([
        "",
        "I can also help with:",
    ])
    lines.extend(user_facing_capability_lines(manifest.tools))

    lines.append("")
    lines.append(
        "For documentation generation I need one project scope before I can start the workflow."
    )
    project_list = _format_project_options(project_options)
    if project_list:
        lines.append(f"Projects in the current chat scope: {project_list}.")
    else:
        lines.append("Choose a project in Nexus, or name a project in the request.")
    return "\n".join(lines).strip()


def _capability_fallback_answer() -> str:
    return (
        "I could not map that request to a safe harness action, so I did not call a tool. "
        "Try asking what I can do, asking a documentation question, or naming a project for a workflow."
    )


def _capability_label(decision: HarnessPlannerDecision) -> str:
    labels = {
        "generate_docs": "generate documentation",
        "create_documentation_pack": "create a reusable documentation Pack",
        "update_docs_from_evidence": "update documentation",
        "operational_investigation": "run an operational investigation",
        "connector_inventory": "read connector metadata",
        "capability_summary": "answer from capabilities",
    }
    return labels.get(decision.mode, "run this workflow")


def _project_scope_clarification(
    *,
    capability_label: str = "generate documentation",
    project_options: list[ProjectOption] | None = None,
) -> str:
    lines = [
        f"Which project should I use to {capability_label}?",
    ]
    project_list = _format_project_options(project_options or [])
    if project_list:
        lines.append(f"I can see: {project_list}.")
    else:
        lines.append(
            "Choose a project in Nexus or include one project id in the request, then ask again."
        )
    return "\n".join(lines)


def _format_project_options(project_options: list[ProjectOption], *, limit: int = 8) -> str:
    if not project_options:
        return ""
    ordered = sorted(project_options, key=lambda option: option.id.lower())
    visible = ordered[:limit]
    labels: list[str] = []
    for option in visible:
        if option.display_name and option.display_name != option.id:
            labels.append(f"`{option.id}` ({option.display_name})")
        else:
            labels.append(f"`{option.id}`")
    remaining = len(ordered) - len(visible)
    if remaining > 0:
        labels.append(f"{remaining} more")
    return ", ".join(labels)


def _doc_generation_failed_answer(
    project_id: str,
    run: WorkflowRun,
    error_message: str,
) -> str:
    return (
        f"I could not start documentation generation for `{project_id}`.\n\n"
        f"{error_message}\n\n"
        f"- Workflow run: `{run.id}`"
    )


def _planner_summary(decision: HarnessPlannerDecision) -> str:
    if decision.action == _PLANNER_ANSWER_DIRECT:
        return "Answering from the live capability registry."
    if decision.action == _PLANNER_ASK_CLARIFICATION:
        return "A required slot is missing, so Nexus is asking for clarification."
    if decision.action == _PLANNER_CALL_CAPABILITY:
        return f"Selected `{decision.mode}` as the harness action."
    return "No harness workflow was selected."


def _planner_safe_details(decision: HarnessPlannerDecision) -> dict[str, Any]:
    details: dict[str, Any] = {
        "action": decision.action,
        "mode": decision.mode,
        "confidence": round(decision.confidence, 2),
        "decision_source": decision.decision_source,
    }
    if decision.capability_id:
        details["capability"] = _capability_label(decision)
    if decision.missing_slots:
        details["missing_slots"] = list(decision.missing_slots)
    if decision.fallback_reason:
        details["fallback_reason"] = decision.fallback_reason
    if decision.matched_rule:
        details["matched_rule"] = decision.matched_rule
    if decision.planner_note:
        details["planner_note"] = decision.planner_note
    return details


def _planner_adhoc_activity(decision: HarnessPlannerDecision) -> dict[str, Any]:
    return _adhoc_activity(
        event_type="planner.decision",
        label="Planned harness response",
        status="completed",
        summary=_planner_summary(decision),
        capability_id=decision.capability_id or "nexus.capability.answer",
        safe_details=_planner_safe_details(decision),
    )


def _scope_required_activity(
    decision: HarnessPlannerDecision,
    project_options: list[ProjectOption],
) -> dict[str, Any]:
    project_ids = [option.id for option in sorted(project_options, key=lambda option: option.id.lower())[:8]]
    return _adhoc_activity(
        event_type="scope",
        label="Project scope required",
        status="skipped",
        summary=f"Choose one project before Nexus can {_capability_label(decision)}.",
        capability_id=decision.capability_id or _CAPABILITY_ID,
        safe_details={"available_projects": project_ids},
    )


def _doc_update_clarification(run: WorkflowRun) -> str:
    return (
        "I can update a documentation page from explicit evidence, but I need both the target markdown file and the evidence text in the request.\n\n"
        "Example: `Update 04_RUNBOOK.md using evidence: the nightly sync now starts at 02:00 UTC.`\n\n"
        f"I did not change any documentation. Workflow run: `{run.id}`"
    )


def _extract_doc_update_request(query: str) -> dict[str, Any] | None:
    filename_match = _DOC_FILENAME_RE.search(query)
    if not filename_match:
        return None
    filename = filename_match.group(1)
    evidence_text = _extract_evidence_text(query, filename)
    if len(evidence_text) < 8:
        return None
    evidence_refs = _extract_evidence_refs(query)
    section_match = _DOC_SECTION_RE.search(query)
    return {
        "filename": filename,
        "evidence_text": evidence_text,
        "evidence_refs": evidence_refs,
        "section": section_match.group(1).strip() if section_match else None,
    }


def _extract_evidence_text(query: str, filename: str) -> str:
    after_filename = query.split(filename, 1)[1]
    match = _EVIDENCE_PREFIX_RE.search(after_filename)
    if match:
        return match.group(1).strip()
    match = _EVIDENCE_PREFIX_RE.search(query)
    if match:
        return match.group(1).strip()
    return ""


def _extract_evidence_refs(query: str) -> list[str]:
    refs = [
        ref.strip()
        for ref in re.findall(r"\[([A-Za-z][A-Za-z0-9_.:-]{2,160})\]", query)
        if ref.strip()
    ]
    return refs or ["chat_prompt"]


def _input_summary(query: str) -> str:
    stripped = re.sub(r"\s+", " ", query).strip()
    return stripped[:500]


def _latency_ms(start: float) -> int:
    return int((time.perf_counter() - start) * 1000)


def _activity_payload(activity: ActivityEvent) -> dict[str, Any]:
    return {
        "workflow_run_id": str(activity.workflow_run_id),
        "event_type": activity.event_type,
        "label": activity.label,
        "status": _ui_status(activity.status),
        "provider_id": activity.provider_id,
        "capability_id": activity.capability_id,
        "summary": activity.summary,
        "safe_details": _safe_details(activity.safe_details),
        "timestamp": activity.occurred_at.isoformat(),
    }


def _adhoc_activity(
    *,
    event_type: str,
    label: str,
    status: str,
    summary: str,
    capability_id: str = _CAPABILITY_ID,
    safe_details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "workflow_run_id": None,
        "event_type": event_type,
        "label": label,
        "status": status,
        "provider_id": None,
        "capability_id": capability_id,
        "summary": summary,
        "safe_details": _safe_details(safe_details),
        "timestamp": None,
    }


def _ui_status(status: ActivityEventStatus | str) -> str:
    value = status.value if isinstance(status, ActivityEventStatus) else str(status)
    if value in {ActivityEventStatus.SUCCEEDED.value, "success", "done"}:
        return "completed"
    if value in {ActivityEventStatus.FAILED.value, "error"}:
        return "failed"
    if value == ActivityEventStatus.SKIPPED.value:
        return "skipped"
    return "running"


def _activity_status(status: Any) -> ActivityEventStatus:
    value = str(status or "").lower()
    if value in {"succeeded", "success", "completed", "done", "triggered"}:
        return ActivityEventStatus.SUCCEEDED
    if value in {"failed", "error", "denied"}:
        return ActivityEventStatus.FAILED
    if value in {"skipped", "interrupted"}:
        return ActivityEventStatus.SKIPPED
    return ActivityEventStatus.RUNNING


def _safe_details(details: dict[str, Any] | None) -> dict[str, Any]:
    if not details:
        return {}
    safe: dict[str, Any] = {}
    for key, value in details.items():
        if isinstance(value, str | int | float | bool) or value is None:
            safe[key] = value
        elif isinstance(value, list):
            safe[key] = [
                item
                for item in value
                if isinstance(item, str | int | float | bool) or item is None
            ]
        else:
            safe[key] = str(value)
    return safe


def _empty_usage(latency_ms: int) -> dict[str, Any]:
    return {
        "input_tokens": 0,
        "output_tokens": 0,
        "model": "",
        "provider": "",
        "cost_usd": 0.0,
        "latency_ms": latency_ms,
    }
