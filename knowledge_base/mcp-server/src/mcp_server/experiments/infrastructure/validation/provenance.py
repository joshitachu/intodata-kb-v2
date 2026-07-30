"""Explicit provenance coverage calculations with no unknown-value imputation."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from mcp_server.experiments.domain.evaluation import ProvenanceMetrics


@dataclass(frozen=True, slots=True)
class CitationObservation:
    citation_id: str
    resolves_to_snapshot: bool | None

    def __post_init__(self) -> None:
        if not self.citation_id.strip():
            raise ValueError("citation_id must not be blank")


def _unique_values(values: Iterable[str] | None, field_name: str) -> frozenset[str] | None:
    if values is None:
        return None
    values_tuple = tuple(values)
    if any(not value.strip() for value in values_tuple):
        raise ValueError(f"{field_name} must not contain blank values")
    return frozenset(values_tuple)


def measure_provenance(
    *,
    citations: Iterable[CitationObservation] | None = None,
    evidence_item_ids: Iterable[str] | None = None,
    cited_evidence_item_ids: Iterable[str] | None = None,
    extracted_fact_ids: Iterable[str] | None = None,
    matched_extracted_fact_ids: Iterable[str] | None = None,
    unsupported_claim_count: int | None = None,
) -> ProvenanceMetrics:
    """Return ``None`` when an entire measurement is unavailable or incomplete."""
    citations_tuple = None if citations is None else tuple(citations)
    citation_count = None if citations_tuple is None else len(citations_tuple)
    resolved_citation_count = None
    if citations_tuple is not None and all(item.resolves_to_snapshot is not None for item in citations_tuple):
        resolved_citation_count = sum(item.resolves_to_snapshot for item in citations_tuple)
    evidence_items = _unique_values(evidence_item_ids, "evidence_item_ids")
    cited_evidence_items = _unique_values(cited_evidence_item_ids, "cited_evidence_item_ids")
    extracted_facts = _unique_values(extracted_fact_ids, "extracted_fact_ids")
    matched_extracted_facts = _unique_values(
        matched_extracted_fact_ids, "matched_extracted_fact_ids"
    )
    if evidence_items is not None and cited_evidence_items is not None:
        if not cited_evidence_items.issubset(evidence_items):
            raise ValueError("cited_evidence_item_ids must be a subset of evidence_item_ids")
    if extracted_facts is not None and matched_extracted_facts is not None:
        if not matched_extracted_facts.issubset(extracted_facts):
            raise ValueError("matched_extracted_fact_ids must be a subset of extracted_fact_ids")
    return ProvenanceMetrics(
        citation_count=citation_count,
        resolved_citation_count=resolved_citation_count,
        evidence_item_count=None if evidence_items is None else len(evidence_items),
        cited_evidence_item_count=None if cited_evidence_items is None else len(cited_evidence_items),
        unsupported_claim_count=unsupported_claim_count,
        extracted_fact_count=None if extracted_facts is None else len(extracted_facts),
        matched_extracted_fact_count=None
        if matched_extracted_facts is None
        else len(matched_extracted_facts),
    )
