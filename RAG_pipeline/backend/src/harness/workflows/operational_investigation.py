"""Six-stage operational investigation over guarded harness capabilities."""

from __future__ import annotations

import re
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field

from src.harness.evidence_store.composer import (
    InvestigationAnswer,
    OperationalAnswerComposer,
    classify_evidence_claims,
)
from src.harness.evidence_store.conflicts import group_conflicts
from src.harness.evidence_store.models import EvidenceSnapshot, EvidenceSourceClass
from src.harness.evidence_store.ranking import EvidenceRankingContext, rank_evidence
from src.harness.executor import (
    CapabilityExecutionContext,
    CapabilityExecutionRequest,
    CapabilityObservation,
    HarnessCapabilityExecutor,
)

_DOC_SOURCES = {EvidenceSourceClass.GENERATED_DOC, EvidenceSourceClass.INDEXED_DOC}
_RUNTIME_SOURCES = {
    EvidenceSourceClass.CURRENT_RUNTIME,
    EvidenceSourceClass.SOURCE_TOPOLOGY,
}
_REQUIRED_SOURCE_LABELS = ("documentation", "runtime evidence", "connector/log evidence")


class InvestigationCall(BaseModel):
    """An internal executor call annotated with its normalized evidence class."""

    capability_id: str
    source_class: EvidenceSourceClass
    arguments: dict[str, Any] = Field(default_factory=dict)


class OperationalInvestigationRequest(BaseModel):
    workflow_run_id: int
    question: str = Field(min_length=1, max_length=2000)
    project_ref: str | None = None
    entity_refs: list[str] = Field(default_factory=list)
    calls: list[InvestigationCall] = Field(default_factory=list, max_length=12)
    required_sources: list[str] = Field(
        default_factory=lambda: list(_REQUIRED_SOURCE_LABELS), max_length=8
    )
    additional_required_sources: list[str] = Field(default_factory=list, max_length=8)


class OperationalInvestigationResult(BaseModel):
    status: str
    current_node: str
    answer: InvestigationAnswer
    activity: list[dict[str, Any]] = Field(default_factory=list)
    sources_touched: list[str] = Field(default_factory=list)
    evidence: list[EvidenceSnapshot] = Field(default_factory=list)
    artifacts: list[dict[str, Any]] = Field(default_factory=list)


class _InvestigationState(TypedDict, total=False):
    workflow_run_id: int
    question: str
    project_ref: str | None
    entity_refs: list[str]
    calls: list[dict[str, Any]]
    additional_required_sources: list[str]
    required_sources: list[str]
    observations: list[dict[str, Any]]
    evidence: list[dict[str, Any]]
    missing_required_sources: list[str]
    artifacts: list[dict[str, Any]]
    answer: dict[str, Any]
    activity: list[dict[str, Any]]
    sources_touched: list[str]
    status: str
    current_node: str


class OperationalInvestigationWorkflow:
    def __init__(self, *, composer: OperationalAnswerComposer | None = None) -> None:
        self._composer = composer or OperationalAnswerComposer()

    async def run(
        self,
        request: OperationalInvestigationRequest,
        *,
        executor: HarnessCapabilityExecutor,
        context: CapabilityExecutionContext,
    ) -> OperationalInvestigationResult:
        graph = self._build_graph(executor, context)
        final = await graph.ainvoke(
            {
                "workflow_run_id": request.workflow_run_id,
                "question": request.question,
                "project_ref": request.project_ref,
                "entity_refs": request.entity_refs,
                "calls": [call.model_dump(mode="json") for call in request.calls],
                "required_sources": request.required_sources,
                "additional_required_sources": request.additional_required_sources,
                "activity": [],
                "status": "running",
            }
        )
        return OperationalInvestigationResult(
            status=str(final["status"]),
            current_node=str(final["current_node"]),
            answer=InvestigationAnswer.model_validate(final["answer"]),
            activity=list(final.get("activity", [])),
            sources_touched=list(final.get("sources_touched", [])),
            evidence=[
                EvidenceSnapshot.model_validate(item)
                for item in final.get("evidence", [])
            ],
            artifacts=list(final.get("artifacts", [])),
        )

    def _build_graph(
        self,
        executor: HarnessCapabilityExecutor,
        context: CapabilityExecutionContext,
    ):
        builder = StateGraph(_InvestigationState)

        async def resolve(state: _InvestigationState) -> dict[str, Any]:
            calls: list[dict[str, Any]] = []
            for raw in state.get("calls", []):
                arguments = dict(raw.get("arguments") or {})
                if state.get("project_ref") and "project_id" not in arguments:
                    arguments["project_id"] = state["project_ref"]
                if raw.get("source_class") == EvidenceSourceClass.CURRENT_RUNTIME.value:
                    arguments["question"] = str(
                        arguments.pop("query", None)
                        or arguments.get("question")
                        or state["question"]
                    )
                elif "query" not in arguments and raw.get("source_class") != EvidenceSourceClass.GENERATED_DOC.value:
                    arguments["query"] = state["question"]
                calls.append({**raw, "arguments": arguments})
            return _stage_update(
                state,
                "resolve",
                "investigation.scope_resolved",
                "Resolved investigation scope",
                calls=calls,
            )

        async def collect(state: _InvestigationState) -> dict[str, Any]:
            async def execute(index: int, raw: dict[str, Any]):
                try:
                    observation = await executor.execute(
                        CapabilityExecutionRequest(
                            capability_id=str(raw["capability_id"]),
                            arguments=dict(raw.get("arguments") or {}),
                            idempotency_key=(
                                f"investigation:{state['workflow_run_id']}:{index}"
                            ),
                        ),
                        context,
                    )
                except Exception:
                    observation = CapabilityObservation(
                        capability_id=str(raw["capability_id"]),
                        status="failed",
                        summary="The evidence source could not answer this request.",
                    )
                return raw, observation

            # Calls may share the request's AsyncSession through their handlers.
            # SQLAlchemy AsyncSession is not safe for concurrent operations.
            collected = []
            for index, raw in enumerate(state.get("calls", [])):
                collected.append(await execute(index, raw))
            evidence: list[EvidenceSnapshot] = []
            observations: list[dict[str, Any]] = []
            artifacts: list[dict[str, Any]] = []
            touched: set[str] = set()
            for raw, observation in collected:
                source_class = EvidenceSourceClass(raw["source_class"])
                touched.add(_source_requirement(source_class))
                observations.append(
                    {
                        "status": observation.status,
                        "source_class": source_class.value,
                        "evidence_count": observation.safe_details.get("evidence_count", 0),
                    }
                )
                artifacts.extend(_safe_artifact_dicts(observation.artifact_links))
                if observation.succeeded:
                    for position, item in enumerate(observation.data.get("items", []), start=1):
                        snapshot = _snapshot_from_item(
                            item,
                            source_class=source_class,
                            capability_id=str(raw["capability_id"]),
                            project_ref=state.get("project_ref"),
                            position=position,
                        )
                        if snapshot is not None:
                            evidence.append(snapshot)
            return _stage_update(
                state,
                "collect",
                "investigation.evidence_collected",
                "Collected normalized evidence",
                observations=observations,
                evidence=[item.model_dump(mode="json") for item in evidence],
                artifacts=artifacts,
                sources_touched=sorted(touched),
                safe_details={"evidence_count": len(evidence), "source_count": len(touched)},
            )

        async def rank(state: _InvestigationState) -> dict[str, Any]:
            evidence = [EvidenceSnapshot.model_validate(item) for item in state.get("evidence", [])]
            grouped = group_conflicts(classify_evidence_claims(evidence))
            ranked = rank_evidence(
                grouped,
                EvidenceRankingContext(
                    project_ref=state.get("project_ref"),
                    entity_refs=frozenset(state.get("entity_refs", [])),
                    preferred_sources=(
                        EvidenceSourceClass.CURRENT_RUNTIME,
                        EvidenceSourceClass.CONNECTOR,
                        EvidenceSourceClass.INDEXED_DOC,
                        EvidenceSourceClass.GENERATED_DOC,
                    ),
                ),
            )
            used = [item.model_copy(update={"used_order": index}) for index, item in enumerate(ranked[:8], 1)]
            return _stage_update(
                state,
                "rank",
                "investigation.evidence_ranked",
                "Ranked evidence for synthesis",
                evidence=[item.model_dump(mode="json") for item in used],
                safe_details={"used_evidence_count": len(used)},
            )

        async def sufficiency(state: _InvestigationState) -> dict[str, Any]:
            evidence = [EvidenceSnapshot.model_validate(item) for item in state.get("evidence", [])]
            present = {_source_requirement(item.source_class) for item in evidence}
            missing = [label for label in state.get("required_sources", []) if label not in present]
            additional = state.get("additional_required_sources", [])
            if additional:
                missing = [
                    label for label in missing if label != "connector/log evidence"
                ]
                missing.extend(label for label in additional if label not in present)
            return _stage_update(
                state,
                "sufficiency",
                "investigation.sufficiency_checked",
                "Checked required evidence sources",
                missing_required_sources=missing,
                safe_details={"missing_required_sources": missing},
            )

        async def synthesize(state: _InvestigationState) -> dict[str, Any]:
            evidence = [EvidenceSnapshot.model_validate(item) for item in state.get("evidence", [])]
            answer = self._composer.compose(
                evidence,
                missing_required_sources=state.get("missing_required_sources", []),
                artifacts=state.get("artifacts", []),
            )
            status = "succeeded" if answer.answered else "insufficient"
            return _stage_update(
                state,
                "synthesize",
                "investigation.answer_composed",
                "Composed grounded investigation answer",
                answer=answer.model_dump(mode="json"),
                status=status,
                safe_details={"answered": answer.answered, "confidence": answer.confidence.value},
            )

        async def finalize(state: _InvestigationState) -> dict[str, Any]:
            return _stage_update(
                state,
                "finalize",
                "investigation.completed",
                "Operational investigation completed",
                status=state.get("status", "insufficient"),
                safe_details={
                    "result": state.get("status", "insufficient"),
                    "sources_touched": state.get("sources_touched", []),
                },
            )

        for name, node in (
            ("resolve", resolve),
            ("collect", collect),
            ("rank", rank),
            ("sufficiency", sufficiency),
            ("synthesize", synthesize),
            ("finalize", finalize),
        ):
            builder.add_node(name, node)
        builder.add_edge(START, "resolve")
        builder.add_edge("resolve", "collect")
        builder.add_edge("collect", "rank")
        builder.add_edge("rank", "sufficiency")
        builder.add_edge("sufficiency", "synthesize")
        builder.add_edge("synthesize", "finalize")
        builder.add_edge("finalize", END)
        return builder.compile()


def _snapshot_from_item(
    raw: Any,
    *,
    source_class: EvidenceSourceClass,
    capability_id: str,
    project_ref: str | None,
    position: int,
) -> EvidenceSnapshot | None:
    if not isinstance(raw, dict):
        return None
    values = dict(raw)
    values["source_class"] = source_class
    values["source_id"] = str(values.pop("id", None) or values.get("source_id") or f"item-{position}")
    values["title"] = str(values.get("title") or "Evidence observation")
    values["summary"] = str(values.get("summary") or values.get("excerpt") or "Evidence was collected.")
    values["project_ref"] = values.get("project_ref") or project_ref
    values["capability_id"] = capability_id
    metadata = values.get("metadata") if isinstance(values.get("metadata"), dict) else {}
    if not values.get("claim_key"):
        schedule = re.search(r"\b([01]\d|2[0-3]):[0-5]\d\b", values["summary"])
        status = metadata.get("status")
        if schedule is not None:
            values["claim_key"] = "schedule"
            values["claim_value"] = schedule.group(0)
        elif status is not None:
            values["claim_key"] = "current_status"
            values["claim_value"] = str(status)
        elif source_class is EvidenceSourceClass.CURRENT_RUNTIME and "failure" in values["source_id"]:
            values["claim_key"] = "likely_cause"
            values["claim_value"] = values["summary"]
    values.pop("source_type", None)
    allowed = set(EvidenceSnapshot.model_fields)
    try:
        return EvidenceSnapshot.model_validate({key: value for key, value in values.items() if key in allowed})
    except (TypeError, ValueError):
        return None


def _source_requirement(source: EvidenceSourceClass) -> str:
    if source in _DOC_SOURCES:
        return "documentation"
    if source in _RUNTIME_SOURCES:
        return "runtime evidence"
    return "connector/log evidence"


def _safe_artifact_dicts(raw_links: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for item in raw_links:
        if not isinstance(item.get("label"), str) or not isinstance(item.get("url"), str):
            continue
        if item.get("kind") is not None and not isinstance(item.get("kind"), str):
            continue
        result.append(
            {"label": item["label"], "url": item["url"], "kind": item.get("kind")}
        )
    return result


def _stage_update(
    state: _InvestigationState,
    node: str,
    event_type: str,
    label: str,
    *,
    safe_details: dict[str, Any] | None = None,
    **changes: Any,
) -> dict[str, Any]:
    return {
        **changes,
        "current_node": node,
        "activity": [
            *state.get("activity", []),
            {
                "event_type": event_type,
                "label": label,
                "status": "completed",
                "runtime_node": node,
                "safe_details": safe_details or {},
            },
        ],
    }


_DEFAULT_WORKFLOW: OperationalInvestigationWorkflow | None = None


def default_operational_investigation_workflow() -> OperationalInvestigationWorkflow:
    global _DEFAULT_WORKFLOW
    if _DEFAULT_WORKFLOW is None:
        _DEFAULT_WORKFLOW = OperationalInvestigationWorkflow()
    return _DEFAULT_WORKFLOW
