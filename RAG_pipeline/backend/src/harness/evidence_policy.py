"""Closed evidence vocabulary and capability-to-turn semantics.

This module is intentionally independent of planner and registry implementations.
Capabilities declare their semantics once; chat planning consumes those declarations
instead of maintaining an ID-to-mode router.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol


class EvidenceSource(StrEnum):
    INDEXED_DOCUMENTATION = "indexed_documentation"
    GENERATED_DOCUMENTATION = "generated_documentation"
    RUNTIME_EVIDENCE = "runtime_evidence"
    CONNECTOR_EVIDENCE = "connector_evidence"


ALL_EVIDENCE_SOURCES = frozenset(EvidenceSource)


@dataclass(frozen=True)
class EvidenceSourceConstraint:
    """An explicit user constraint on the evidence layer for one turn."""

    required: frozenset[EvidenceSource] = frozenset()


def resolve_evidence_source_constraint(query: str) -> EvidenceSourceConstraint:
    """Recognize evidence-source requests without treating domain nouns as sources.

    Terms such as ``runtime`` and ``execution`` occur in both documentation and
    operational questions. Runtime evidence is therefore selected only by an
    explicit runtime/log reference or a freshness/observation request.
    Documentation attribution wins when it is explicitly requested.
    """

    normalized = " ".join(query.casefold().split())
    if _matches_documentation_attribution(normalized):
        return EvidenceSourceConstraint(
            required=frozenset({EvidenceSource.INDEXED_DOCUMENTATION})
        )
    if _matches_connector_attribution(normalized):
        return EvidenceSourceConstraint(
            required=frozenset({EvidenceSource.CONNECTOR_EVIDENCE})
        )
    if _matches_runtime_observation(normalized):
        return EvidenceSourceConstraint(
            required=frozenset({EvidenceSource.RUNTIME_EVIDENCE})
        )
    return EvidenceSourceConstraint()


def _matches_documentation_attribution(query: str) -> bool:
    import re

    return bool(
        re.search(
            r"\b(?:according to|from|using|use|based on|in)\s+(?:the\s+)?"
            r"(?:docs?|documentation|knowledge base|rag)\b",
            query,
        )
        or re.search(
            r"\b(?:docs?|documentation|knowledge base|rag)\s+(?:only|instead)\b",
            query,
        )
    )


def _matches_connector_attribution(query: str) -> bool:
    import re

    return bool(
        re.search(
            r"\b(?:according to|from|using|search)\s+(?:the\s+)?"
            r"(?:external\s+)?connector\b|\bconnector evidence\b",
            query,
        )
    )


def _matches_runtime_observation(query: str) -> bool:
    import re

    return bool(
        re.search(
            r"\b(?:according to|from|using|search|in)\s+(?:the\s+)?"
            r"(?:runtime\s+)?(?:logs?|executions?)\b",
            query,
        )
        or re.search(
            r"\b(?:current|latest|live)\s+(?:runtime|executions?|runs?|status|health|failures?)\b",
            query,
        )
        or re.search(
            r"\b(?:what|which|show|list|find|tell me)\b.{0,80}"
            r"\b(?:running|failed|failing|executions?|runs?|status|health)\b.{0,80}"
            r"\b(?:right now|currently|today|latest|live)\b",
            query,
        )
    )


@dataclass(frozen=True)
class CapabilityTurnSemantics:
    """Planner-safe execution semantics declared by a capability."""

    mode: str
    action: str
    task_type: str


class _CapabilityLike(Protocol):
    id: str
    task_types: list[str]
    planning_task_type: str | None
    evidence_sources: list[EvidenceSource]
    metadata: dict[str, object]


_TASK_SEMANTICS = {
    "document_answer": CapabilityTurnSemantics("document_answer", "use_rag", "document_answer"),
    "runtime_answer": CapabilityTurnSemantics("runtime_answer", "call_capability", "runtime_answer"),
    "operational_investigation": CapabilityTurnSemantics(
        "operational_investigation", "call_capability", "operational_investigation"
    ),
    "document_generation": CapabilityTurnSemantics("generate_docs", "call_capability", "document_generation"),
    "documentation_pack_creation": CapabilityTurnSemantics(
        "create_documentation_pack", "call_capability", "documentation_pack_creation"
    ),
    "documentation_generation_status": CapabilityTurnSemantics(
        "documentation_generation_status", "call_capability", "documentation_generation_status"
    ),
    "document_update": CapabilityTurnSemantics("update_docs_from_evidence", "call_capability", "document_update"),
    "project_onboarding": CapabilityTurnSemantics("project_onboarding", "call_capability", "project_onboarding"),
    "connector_evidence": CapabilityTurnSemantics("connector_evidence", "call_capability", "connector_evidence"),
}


def resolve_capability_turn_semantics(
    capability: _CapabilityLike,
) -> CapabilityTurnSemantics | None:
    """Resolve a capability's declared planner execution path.

    ``task_types`` may describe every supported use of a capability. When that
    is broader than one planner path, ``planning_task_type`` selects its normal
    request path without losing the full capability contract.
    """

    selected = capability.planning_task_type
    if selected is not None:
        return _TASK_SEMANTICS.get(selected)
    supported = {
        task_type for task_type in capability.task_types if task_type in _TASK_SEMANTICS
    }
    if len(supported) != 1:
        return None
    return _TASK_SEMANTICS[supported.pop()]


def validate_capability_turn_semantics(capability: _CapabilityLike) -> None:
    """Reject visible capabilities that could otherwise become unmappable."""

    if capability.metadata.get("runtime_hidden") is True:
        return
    if resolve_capability_turn_semantics(capability) is None:
        raise ValueError(
            f"Capability {capability.id!r} must declare one resolvable planning task type"
        )
    unknown_sources = set(capability.evidence_sources) - ALL_EVIDENCE_SOURCES
    if unknown_sources:
        raise ValueError(
            f"Capability {capability.id!r} declares unknown TurnPlan evidence sources: "
            f"{sorted(str(source) for source in unknown_sources)}"
        )
