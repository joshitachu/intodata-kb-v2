"""Deterministic resolution over caller-scoped external inventory."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Sequence

from .models import (
    AmbiguityOption,
    Confidence,
    ConfidenceLevel,
    ConversationalFocus,
    EntityCandidate,
    EntityMatch,
    EntityReference,
    EntityResolution,
    EntityType,
    MatchReason,
    ResolutionAmbiguity,
    ResolutionStatus,
    TimeWindow,
    WorkflowKind,
    WorkflowKindResolution,
)

_NON_WORD = re.compile(r"[^\w]+", re.UNICODE)
_SPACE = re.compile(r"\s+")


def normalize_reference_text(value: str) -> str:
    """Normalize display text without changing caller-owned identifiers."""

    normalized = unicodedata.normalize("NFKC", value).casefold()
    return _SPACE.sub(" ", _NON_WORD.sub(" ", normalized)).strip()


def confidence_level(score: float) -> ConfidenceLevel:
    if score >= 0.95:
        return ConfidenceLevel.EXACT
    if score >= 0.85:
        return ConfidenceLevel.HIGH
    if score >= 0.72:
        return ConfidenceLevel.MEDIUM
    return ConfidenceLevel.LOW


def merge_conversational_focus(
    current: ConversationalFocus | None = None,
    *,
    references: Iterable[EntityReference] = (),
    workflow_kind: WorkflowKind | None = None,
    time_window: TimeWindow | None = None,
) -> ConversationalFocus:
    """Merge newly resolved references into typed conversational focus."""

    values = {
        "project": current.project if current else None,
        "workflow_kind": current.workflow_kind if current else None,
        "workflow_run": current.workflow_run if current else None,
        "document": current.document if current else None,
        "runtime_entity": current.runtime_entity if current else None,
        "environment": current.environment if current else None,
        "time_window": current.time_window if current else None,
    }
    focus_fields = {
        EntityType.PROJECT: "project",
        EntityType.WORKFLOW_RUN: "workflow_run",
        EntityType.DOCUMENT: "document",
        EntityType.RUNTIME_ENTITY: "runtime_entity",
        EntityType.ENVIRONMENT: "environment",
    }
    for reference in references:
        field_name = focus_fields.get(reference.entity_type)
        if field_name is None:
            raise ValueError(
                f"{reference.entity_type.value} references cannot be conversational focus"
            )
        values[field_name] = reference
        if reference.entity_type is EntityType.WORKFLOW_RUN and reference.workflow_kind:
            values["workflow_kind"] = reference.workflow_kind
    if workflow_kind is not None:
        values["workflow_kind"] = workflow_kind
    if time_window is not None:
        values["time_window"] = time_window
    return ConversationalFocus(**values)


def resolve_entity(
    query: str,
    entity_type: EntityType,
    candidates: Sequence[EntityCandidate],
    *,
    minimum_score: float = 0.72,
    ambiguity_margin: float = 0.05,
) -> EntityResolution:
    """Resolve one entity from inventory already filtered to the caller's scope."""

    normalized_query = normalize_reference_text(query)
    if not normalized_query:
        raise ValueError("query must contain a resolvable value")
    if not 0 <= minimum_score <= 1 or not 0 <= ambiguity_margin <= 1:
        raise ValueError("score thresholds must be between zero and one")

    scored: list[tuple[float, MatchReason, EntityCandidate]] = []
    for candidate in candidates:
        if candidate.entity_type is not entity_type:
            continue
        score, reason = _score_candidate(normalized_query, candidate)
        if score >= minimum_score:
            scored.append((score, reason, candidate))

    scored.sort(
        key=lambda item: (
            -item[0],
            normalize_reference_text(item[2].canonical_name),
            item[2].stable_id,
        )
    )
    matches = tuple(
        EntityMatch(
            reference=candidate.to_reference(),
            confidence=Confidence(
                score=score,
                level=confidence_level(score),
                reason=reason,
            ),
        )
        for score, reason, candidate in scored[:10]
    )
    if not matches:
        return EntityResolution(
            query=query,
            entity_type=entity_type,
            status=ResolutionStatus.UNRESOLVED,
        )

    top = matches[0]
    if top.confidence.reason is MatchReason.STABLE_ID and top.confidence.score == 1.0:
        close_matches = tuple(
            match
            for match in matches
            if match.confidence.reason is MatchReason.STABLE_ID
            and match.confidence.score == top.confidence.score
        )
    else:
        close_matches = tuple(
            match
            for match in matches
            if top.confidence.score - match.confidence.score <= ambiguity_margin
        )
    if len(close_matches) > 1:
        options = tuple(
            AmbiguityOption(
                value=match.reference.stable_id,
                label=match.reference.canonical_name,
                score=match.confidence.score,
            )
            for match in close_matches
        )
        return EntityResolution(
            query=query,
            entity_type=entity_type,
            status=ResolutionStatus.AMBIGUOUS,
            matches=matches,
            ambiguity=ResolutionAmbiguity(
                prompt=f"Which {entity_type.value} did you mean?",
                options=options,
            ),
        )

    return EntityResolution(
        query=query,
        entity_type=entity_type,
        status=ResolutionStatus.RESOLVED,
        reference=top.reference,
        confidence=top.confidence,
        matches=matches,
    )


def _score_candidate(
    normalized_query: str, candidate: EntityCandidate
) -> tuple[float, MatchReason]:
    stable_id = normalize_reference_text(candidate.stable_id)
    canonical = normalize_reference_text(candidate.canonical_name)
    aliases = tuple(
        alias for alias in (normalize_reference_text(value) for value in candidate.aliases) if alias
    )

    if normalized_query == stable_id:
        return 1.0, MatchReason.STABLE_ID
    if normalized_query == canonical:
        return 0.99, MatchReason.CANONICAL_NAME
    compact_query = normalized_query.replace(" ", "")
    if compact_query and compact_query == stable_id.replace(" ", ""):
        return 0.95, MatchReason.STABLE_ID
    if compact_query and compact_query == canonical.replace(" ", ""):
        return 0.94, MatchReason.CANONICAL_NAME
    compact_stable = stable_id.replace(" ", "")
    compact_canonical = canonical.replace(" ", "")
    if len(compact_stable) >= 4 and compact_stable in compact_query:
        return 0.9, MatchReason.EMBEDDED_NAME
    if len(compact_canonical) >= 4 and compact_canonical in compact_query:
        return 0.89, MatchReason.EMBEDDED_NAME
    if normalized_query in aliases:
        return 0.96, MatchReason.ALIAS
    if _contains_phrase(normalized_query, stable_id):
        return 0.94, MatchReason.STABLE_ID
    if _contains_phrase(normalized_query, canonical):
        return 0.91, MatchReason.EMBEDDED_NAME
    if any(_contains_phrase(normalized_query, alias) for alias in aliases):
        return 0.89, MatchReason.EMBEDDED_NAME

    names = (canonical, *aliases)
    if len(normalized_query) >= 3 and any(name.startswith(normalized_query) for name in names):
        return 0.82, MatchReason.PREFIX

    query_tokens = set(normalized_query.split())
    if query_tokens and any(query_tokens < set(name.split()) for name in names):
        return 0.76, MatchReason.TOKEN_SUBSET
    return 0.0, MatchReason.TOKEN_SUBSET


def _contains_phrase(container: str, phrase: str) -> bool:
    if not phrase:
        return False
    return f" {phrase} " in f" {container} "


_WORKFLOW_ALIASES: dict[WorkflowKind, tuple[str, ...]] = {
    WorkflowKind.ONBOARDING: ("onboarding", "onboard", "project setup", "source setup"),
    WorkflowKind.SCAN: ("source scan", "repository scan", "repo scan", "scanning", "scan"),
    WorkflowKind.DOCUMENTATION_GENERATION: (
        "documentation generation",
        "document generation",
        "docs generation",
        "generate docs",
        "generation run",
    ),
    WorkflowKind.INDEXING: ("indexing", "index run", "publish index", "reindex"),
    WorkflowKind.RUNTIME_HEALTH: (
        "runtime health",
        "runtime status",
        "service health",
        "integration health",
    ),
    WorkflowKind.DOCUMENTATION_QUALITY: (
        "documentation quality",
        "doc quality",
        "quality gates",
        "client ready",
        "client readiness",
    ),
}


def resolve_workflow_kind(
    text: str,
    *,
    focus: ConversationalFocus | None = None,
    available_kinds: Iterable[WorkflowKind] = (),
) -> WorkflowKindResolution:
    """Resolve what "done" or "status" refers to without conflating workflows."""

    normalized = normalize_reference_text(text)
    available = tuple(dict.fromkeys(available_kinds))
    available_set = set(available)
    explicit = [
        kind
        for kind, aliases in _WORKFLOW_ALIASES.items()
        if (not available_set or kind in available_set)
        and any(_contains_phrase(normalized, alias) for alias in aliases)
    ]
    if len(explicit) == 1:
        return WorkflowKindResolution(
            status=ResolutionStatus.RESOLVED,
            workflow_kind=explicit[0],
            confidence=Confidence(
                score=0.99,
                level=ConfidenceLevel.EXACT,
                reason=MatchReason.CANONICAL_NAME,
            ),
        )
    if len(explicit) > 1:
        return _ambiguous_workflow_kinds(explicit)

    if focus is not None and focus.workflow_kind is not None:
        if not available_set or focus.workflow_kind in available_set:
            return WorkflowKindResolution(
                status=ResolutionStatus.RESOLVED,
                workflow_kind=focus.workflow_kind,
                confidence=Confidence(
                    score=0.9,
                    level=ConfidenceLevel.HIGH,
                    reason=MatchReason.CONVERSATIONAL_FOCUS,
                ),
            )
    if len(available) == 1:
        return WorkflowKindResolution(
            status=ResolutionStatus.RESOLVED,
            workflow_kind=available[0],
            confidence=Confidence(
                score=0.8,
                level=ConfidenceLevel.MEDIUM,
                reason=MatchReason.ONLY_AVAILABLE,
            ),
        )
    if len(available) > 1:
        return _ambiguous_workflow_kinds(available)
    return WorkflowKindResolution(status=ResolutionStatus.UNRESOLVED)


def _ambiguous_workflow_kinds(
    kinds: Iterable[WorkflowKind],
) -> WorkflowKindResolution:
    options = tuple(
        AmbiguityOption(value=kind.value, label=kind.value.replace("_", " "), score=0.0)
        for kind in kinds
    )
    return WorkflowKindResolution(
        status=ResolutionStatus.AMBIGUOUS,
        ambiguity=ResolutionAmbiguity(
            prompt="Which workflow status do you mean?",
            options=options,
        ),
    )
