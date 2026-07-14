"""Deterministic evidence ranking."""

from __future__ import annotations

from dataclasses import dataclass, field

from .models import EvidenceSnapshot, EvidenceSourceClass


@dataclass(frozen=True)
class EvidenceRankingContext:
    project_ref: str | None = None
    entity_refs: frozenset[str] = field(default_factory=frozenset)
    preferred_sources: tuple[EvidenceSourceClass, ...] = ()


def rank_evidence(
    evidence: list[EvidenceSnapshot], context: EvidenceRankingContext
) -> list[EvidenceSnapshot]:
    """Rank by scope, pack/source hints, authority, freshness, confidence, and score."""
    preferred = {
        source: len(context.preferred_sources) - index
        for index, source in enumerate(context.preferred_sources)
    }

    def key(item: EvidenceSnapshot) -> tuple:
        project_match = int(
            context.project_ref is not None and item.project_ref == context.project_ref
        )
        entity_match = int(item.entity_ref is not None and item.entity_ref in context.entity_refs)
        return (
            -project_match,
            -entity_match,
            -preferred.get(item.source_class, 0),
            -item.authority,
            -item.freshness,
            -item.confidence,
            -(item.score if item.score is not None else -1.0),
            item.source_class.value,
            item.source_id,
        )

    return sorted(evidence, key=key)
