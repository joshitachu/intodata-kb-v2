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
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from typing import Any

from qdrant_client import AsyncQdrantClient
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import Conversation, ConversationMessage
from src.harness import HarnessWorkflowRepository, HarnessWorkflowService, default_capability_registry
from src.harness.evidence import (
    evidence_refs,
    latest_evidence_timestamp,
    list_generated_doc_evidence,
    query_runtime_evidence,
    read_connector_evidence,
    search_document_evidence,
)
from src.harness.executor import (
    CapabilityExecutionContext,
    HarnessCapabilityExecutor,
)
from src.harness.manifest import (
    ManifestScope,
    build_runtime_capability_manifest,
    user_facing_capability_lines,
)
from src.harness.models import (
    ActivityEvent,
    ActivityEventStatus,
    DOCUMENTATION_GENERATION_CAPABILITY,
    DOCUMENT_UPDATE_FROM_EVIDENCE_CAPABILITY,
    EXTERNAL_CONNECTOR_READ_CAPABILITY,
    EvidenceItem,
    OPERATIONAL_INVESTIGATION_CAPABILITY,
    WorkflowRun,
)
from src.harness.providers.mcp_docs import (
    McpDocumentationProvider,
    McpDocumentationProviderError,
    McpEvidenceDocUpdateResult,
    McpDocumentationGenerationResult,
    McpProjectInfo,
)
from src.harness.runtime import LangGraphRuntimeProvider, RuntimeTurnRequest
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
_EVIDENCE_PREFIX_RE = re.compile(
    r"\b(?:using|with|from|evidence\s*:?)\b\s*(.+)$",
    re.IGNORECASE | re.DOTALL,
)

_CAPABILITY_ID = DOCUMENTATION_GENERATION_CAPABILITY.id
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
    """Structured planner decision for chat harness routing."""

    action: str
    mode: str
    capability_id: str | None = None
    arguments: dict[str, Any] | None = None
    missing_slots: tuple[str, ...] = ()
    confidence: float = 1.0
    user_visible_reason: str = ""


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
        return HarnessPlannerDecision(action=_PLANNER_USE_RAG, mode="rag")
    if _is_capability_question(stripped):
        return HarnessPlannerDecision(
            action=_PLANNER_ANSWER_DIRECT,
            mode="capability_summary",
            confidence=0.92,
            user_visible_reason="The user asked what Nexus can do.",
        )
    if _DOC_UPDATE_FROM_EVIDENCE_RE.search(stripped):
        return HarnessPlannerDecision(
            action=_PLANNER_CALL_CAPABILITY,
            mode="update_docs_from_evidence",
            capability_id=_DOC_UPDATE_CAPABILITY_ID,
            arguments={},
            confidence=0.86,
            user_visible_reason="The user asked to update documentation from evidence.",
        )
    if _INVESTIGATION_RE.search(stripped):
        return HarnessPlannerDecision(
            action=_PLANNER_CALL_CAPABILITY,
            mode="operational_investigation",
            capability_id=_INVESTIGATION_CAPABILITY_ID,
            arguments={},
            confidence=0.82,
            user_visible_reason="The user asked for an operational investigation.",
        )
    if _CONNECTOR_INVENTORY_RE.search(stripped):
        return HarnessPlannerDecision(
            action=_PLANNER_CALL_CAPABILITY,
            mode="connector_inventory",
            capability_id=_CONNECTOR_CAPABILITY_ID,
            arguments={},
            confidence=0.82,
            user_visible_reason="The user asked about available connectors or sources.",
        )
    if _DOC_QA_RE.search(stripped) and not _DOC_GENERATION_RE.search(stripped):
        return HarnessPlannerDecision(action=_PLANNER_USE_RAG, mode="rag", confidence=0.8)
    if _DOC_GENERATION_RE.search(stripped):
        return HarnessPlannerDecision(
            action=_PLANNER_CALL_CAPABILITY,
            mode="generate_docs",
            capability_id=_CAPABILITY_ID,
            arguments={},
            missing_slots=("project_id",),
            confidence=0.88,
            user_visible_reason="The user asked to generate documentation.",
        )
    return HarnessPlannerDecision(action=_PLANNER_USE_RAG, mode="rag")


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
) -> str | None:
    scoped = resolve_project_scope(project_ids)
    if scoped:
        return scoped
    return _match_project_in_query(query, project_options)


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
) -> MessageResponse:
    """Run a harness-triggered chat turn and persist the exchange."""
    start = time.perf_counter()
    user_msg = ConversationMessage(
        conversation_id=conversation.id,
        role="user",
        content=query,
    )
    db.add(user_msg)
    await db.flush()

    docs_provider = provider or McpDocumentationProvider()
    decision = plan_harness_chat_turn(query)
    if decision.action == _PLANNER_ANSWER_DIRECT:
        project_options = _project_options_from_scope(project_ids)
    else:
        project_options = await _load_project_options(
            project_ids=project_ids,
            access_token=access_token,
            provider=docs_provider,
        )
    project_id = _resolve_project_for_decision(
        query=query,
        project_ids=project_ids,
        project_options=project_options,
    )

    if decision.action == _PLANNER_ANSWER_DIRECT:
        answer = _capability_awareness_answer(project_options, permissions=user_permissions)
        return await _persist_assistant_message(
            conversation=conversation,
            db=db,
            answer=answer,
            latency_ms=_latency_ms(start),
        )

    if decision.action == _PLANNER_ASK_CLARIFICATION:
        answer = _project_scope_clarification(
            capability_label=_capability_label(decision),
            project_options=project_options,
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
        if project_id is None:
            answer = _project_scope_clarification(
                capability_label="update documentation",
                project_options=project_options,
            )
        else:
            _, answer, _ = await _execute_doc_update_from_evidence(
                conversation=conversation,
                query=query,
                db=db,
                user_id=user_id,
                project_id=project_id,
                access_token=access_token,
                provider=docs_provider,
            )
        return await _persist_assistant_message(
            conversation=conversation,
            db=db,
            answer=answer,
            latency_ms=_latency_ms(start),
        )

    if decision.mode in {"operational_investigation", "connector_inventory"}:
        if project_id is None:
            answer = _project_scope_clarification(
                capability_label=_capability_label(decision),
                project_options=project_options,
            )
        elif decision.mode == "operational_investigation":
            _, answer, _ = await _execute_operational_investigation(
                conversation=conversation,
                query=query,
                db=db,
                user_id=user_id,
                project_id=project_id,
                project_scope=project_ids,
                user_groups=user_groups or [],
                qdrant=qdrant,
                access_token=access_token,
                provider=docs_provider,
            )
        else:
            _, answer, _ = await _execute_connector_inventory(
                conversation=conversation,
                query=query,
                db=db,
                user_id=user_id,
                project_id=project_id,
                project_scope=project_ids,
            )
        return await _persist_assistant_message(
            conversation=conversation,
            db=db,
            answer=answer,
            latency_ms=_latency_ms(start),
        )

    if project_id is None:
        run, activities = await _create_project_scope_interrupt(
            conversation=conversation,
            query=query,
            db=db,
            user_id=user_id,
            project_options=project_options,
            decision=decision,
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
) -> AsyncGenerator[tuple[str, str], None]:
    """Streaming variant of :func:`run_harness_chat_turn`."""
    start = time.perf_counter()
    user_msg = ConversationMessage(
        conversation_id=conversation.id,
        role="user",
        content=query,
    )
    db.add(user_msg)
    await db.flush()

    docs_provider = provider or McpDocumentationProvider()
    decision = plan_harness_chat_turn(query)
    if decision.action == _PLANNER_ANSWER_DIRECT:
        project_options = _project_options_from_scope(project_ids)
    else:
        project_options = await _load_project_options(
            project_ids=project_ids,
            access_token=access_token,
            provider=docs_provider,
        )
    project_id = _resolve_project_for_decision(
        query=query,
        project_ids=project_ids,
        project_options=project_options,
    )

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
        answer = _capability_awareness_answer(project_options, permissions=user_permissions)
    elif decision.action == _PLANNER_ASK_CLARIFICATION:
        answer = _project_scope_clarification(
            capability_label=_capability_label(decision),
            project_options=project_options,
        )
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
            _, answer, activities = await _execute_doc_update_from_evidence(
                conversation=conversation,
                query=query,
                db=db,
                user_id=user_id,
                project_id=project_id,
                access_token=access_token,
                provider=docs_provider,
            )
            for activity in activities:
                yield ("harness_activity", json.dumps(_activity_payload(activity)))
    elif decision.mode in {"operational_investigation", "connector_inventory"}:
        if project_id is None:
            answer = _project_scope_clarification(
                capability_label=_capability_label(decision),
                project_options=project_options,
            )
            yield (
                "harness_activity",
                json.dumps(_scope_required_activity(decision, project_options)),
            )
        elif decision.mode == "operational_investigation":
            _, answer, activities = await _execute_operational_investigation(
                conversation=conversation,
                query=query,
                db=db,
                user_id=user_id,
                project_id=project_id,
                project_scope=project_ids,
                user_groups=user_groups or [],
                qdrant=qdrant,
                access_token=access_token,
                provider=docs_provider,
            )
            for activity in activities:
                yield ("harness_activity", json.dumps(_activity_payload(activity)))
        else:
            _, answer, activities = await _execute_connector_inventory(
                conversation=conversation,
                query=query,
                db=db,
                user_id=user_id,
                project_id=project_id,
                project_scope=project_ids,
            )
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
        )
        answer = _project_scope_clarification(
            capability_label="generate documentation",
            project_options=project_options,
        )
        for activity in activities:
            yield ("harness_activity", json.dumps(_activity_payload(activity)))
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
    )
    yield ("usage", json.dumps(_empty_usage(assistant_msg.latency_ms or 0)))
    yield ("done", json.dumps({"message_id": assistant_msg.id, "latency_ms": assistant_msg.latency_ms or 0}))


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
    service = _workflow_service(db)
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
    runtime_result = await LangGraphRuntimeProvider().run_turn(
        RuntimeTurnRequest(
            message=query,
            manifest=build_runtime_capability_manifest(
                default_capability_registry(),
                scope=ManifestScope(permissions=user_permissions),
            ),
            planned_capability_id=_CAPABILITY_ID,
            arguments={"project_id": project_id, "resume": True},
            workflow_run_id=run.id,
        ),
        executor=HarnessCapabilityExecutor(
            default_capability_registry(),
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
        else:
            await service.mark_failed(run, error_message=error_message)
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


async def _create_project_scope_interrupt(
    *,
    conversation: Conversation,
    query: str,
    db: AsyncSession,
    user_id: int,
    project_options: list[ProjectOption],
    decision: HarnessPlannerDecision,
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
    runtime_thread_id = f"harness-run-{run.id}"
    runtime_run_id = f"lg-interrupt-{run.id}"
    available_projects = [
        option.id
        for option in sorted(project_options, key=lambda item: item.id.lower())
    ][:8]
    interrupt_metadata = {
        "graph": "docs_generation",
        "interrupt": {
            "type": "missing_project_scope",
            "missing_slots": ["project_id"],
            "available_projects": available_projects,
        },
        "resume": {"ready": True},
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
            tool_call_id=runtime_run_id,
        ),
    ]
    await service.mark_interrupted(
        run,
        output_summary="Project scope is required before documentation generation can start.",
        runtime_provider_id="langgraph.nexus",
        runtime_thread_id=runtime_thread_id,
        runtime_run_id=runtime_run_id,
        current_node="await_project_scope",
        resume_token=runtime_thread_id,
        runtime_metadata=interrupt_metadata,
    )
    return run, activities


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


async def _execute_doc_update_from_evidence(
    *,
    conversation: Conversation,
    query: str,
    db: AsyncSession,
    user_id: int,
    project_id: str,
    access_token: str | None,
    provider: McpDocumentationProvider | None,
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
    activities.append(await service.record_activity(
        run,
        event_type="provider_trigger",
        label="Updating documentation from evidence",
        status=ActivityEventStatus.RUNNING,
        summary="Calling the MCP documentation evidence-update capability.",
        safe_details={
            "project_id": project_id,
            "target_doc": update_request["filename"],
            "provider": "mcp.docs",
        },
    ))

    docs_provider = provider or McpDocumentationProvider()
    try:
        result = await docs_provider.update_doc_from_evidence(
            project_id=project_id,
            filename=update_request["filename"],
            evidence_text=update_request["evidence_text"],
            evidence_refs=update_request["evidence_refs"],
            workflow_run_id=str(run.id),
            access_token=access_token,
        )
    except McpDocumentationProviderError as exc:
        await service.mark_failed(run, error_message=exc.safe_message)
        activities.append(await service.record_activity(
            run,
            event_type="provider_result",
            label="Documentation update failed",
            status=ActivityEventStatus.FAILED,
            summary=exc.safe_message,
            safe_details={
                "project_id": project_id,
                "target_doc": update_request["filename"],
                "write_performed": False,
            },
        ))
        return run, _doc_update_failed_answer(project_id, run, exc.safe_message), activities

    artifact_links = _doc_update_artifact_links(project_id, run, result)
    await service.mark_succeeded(
        run,
        output_summary=result.message,
        artifact_links=artifact_links,
    )
    activities.append(await service.record_activity(
        run,
        event_type="provider_result",
        label="Documentation updated",
        status=ActivityEventStatus.SUCCEEDED,
        summary=result.message,
        safe_details={
            "project_id": project_id,
            "target_doc": result.filename,
            "receipt_path": result.receipt_path,
            "evidence_refs": result.evidence_refs,
            "write_performed": True,
        },
    ))
    activities.append(await service.record_activity(
        run,
        event_type="workflow.completed",
        label="Documentation update completed",
        status=ActivityEventStatus.SUCCEEDED,
        summary="Updated a published documentation page from explicit evidence.",
        safe_details={"workflow_run_id": run.id, "target_doc": result.filename},
    ))
    return run, _doc_update_success_answer(project_id, run, result), activities


async def _persist_assistant_message(
    *,
    conversation: Conversation,
    db: AsyncSession,
    answer: str,
    latency_ms: int,
) -> MessageResponse:
    assistant_msg = ConversationMessage(
        conversation_id=conversation.id,
        role="assistant",
        content=answer,
        sources=None,
        latency_ms=latency_ms,
    )
    db.add(assistant_msg)
    await db.flush()
    return MessageResponse(
        id=assistant_msg.id,
        role="assistant",
        content=answer,
        sources=None,
        latency_ms=latency_ms,
        created_at=assistant_msg.created_at,
    )


def _workflow_service(db: AsyncSession) -> HarnessWorkflowService:
    return HarnessWorkflowService(
        HarnessWorkflowRepository(db),
        default_capability_registry(),
    )


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
    return (
        f"Documentation generation started for `{project_id}`.\n\n"
        f"{result.message}\n\n"
        f"- Generation status: `{result.status_path}`\n"
        f"- Project documentation: `{result.docs_path}`\n"
        f"- Workflow run: `{run.id}`"
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
    }
    if decision.capability_id:
        details["capability"] = _capability_label(decision)
    if decision.missing_slots:
        details["missing_slots"] = list(decision.missing_slots)
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
    return {
        "filename": filename,
        "evidence_text": evidence_text,
        "evidence_refs": evidence_refs,
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
