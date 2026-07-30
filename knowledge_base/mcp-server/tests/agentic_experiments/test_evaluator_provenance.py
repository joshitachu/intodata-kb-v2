from __future__ import annotations

import pytest

from mcp_server.experiments.infrastructure.validation.provenance import (
    CitationObservation,
    measure_provenance,
)


def test_provenance_unknowns_remain_none() -> None:
    metrics = measure_provenance()

    assert metrics.citation_count is None
    assert metrics.resolved_citation_count is None
    assert metrics.evidence_item_count is None


def test_incomplete_citation_resolution_stays_unknown() -> None:
    metrics = measure_provenance(
        citations=(CitationObservation("a", True), CitationObservation("b", None)),
        evidence_item_ids=("evidence-a", "evidence-b"),
        cited_evidence_item_ids=("evidence-a",),
    )

    assert metrics.citation_count == 2
    assert metrics.resolved_citation_count is None
    assert metrics.evidence_item_count == 2
    assert metrics.cited_evidence_item_count == 1


def test_complete_provenance_is_counted_without_guessing() -> None:
    metrics = measure_provenance(
        citations=(CitationObservation("a", True), CitationObservation("b", False)),
        extracted_fact_ids=("f1", "f2"),
        matched_extracted_fact_ids=("f1",),
        unsupported_claim_count=0,
    )

    assert metrics.resolved_citation_count == 1
    assert metrics.extracted_fact_count == 2
    assert metrics.matched_extracted_fact_count == 1
    assert metrics.unsupported_claim_count == 0


def test_provenance_rejects_claimed_coverage_outside_its_known_population() -> None:
    with pytest.raises(ValueError, match="subset"):
        measure_provenance(
            evidence_item_ids=("evidence-a",),
            cited_evidence_item_ids=("evidence-missing",),
        )
