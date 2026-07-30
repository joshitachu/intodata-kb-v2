"""Contract tests for pure agentic-experiment evaluation values."""

from __future__ import annotations

import json
from dataclasses import FrozenInstanceError
from decimal import Decimal
from pathlib import Path

import pytest

from mcp_server.experiments.domain.evaluation import (
    ArtifactIdentity,
    ComparabilityReceipt,
    ComparisonVerdict,
    CostMetrics,
    DimensionComparison,
    DocumentAlignment,
    DocumentPair,
    EvaluationReceipt,
    EvaluationResult,
    PriceCardReceipt,
    ProvenanceMetrics,
    QualityMetrics,
    StructuralMetrics,
    TimingMetrics,
    UsageMetrics,
    ValidatorResult,
)


FIXTURES = Path(__file__).parents[1] / "fixtures" / "agentic_experiments"


def _artifact(branch: str, logical_name: str = "09_EXECUTIVE_SUMMARY.md") -> ArtifactIdentity:
    return ArtifactIdentity(
        branch=branch,  # type: ignore[arg-type]
        kind="document",
        logical_name=logical_name,
        sha256="a" * 64,
        size_bytes=123,
    )


def test_alitalend_run_44_fixture_is_bounded_and_preserves_known_unknowns() -> None:
    fixture = json.loads(
        (FIXTURES / "alitalend_golden" / "run_44_evaluation_receipt.json").read_text(
            encoding="utf-8"
        )
    )

    assert fixture["project_id"] == "alitalend"
    assert fixture["baseline_run_id"] == 44
    assert fixture["comparison_classification"] == "controlled_architectural_ab"
    assert fixture["resumed_lineage"]["resume_requested"] is True
    assert fixture["resumed_lineage"]["inherited_task_routes"][0]["paid_by_run_44"] is False
    assert fixture["metrics"] == {
        "duration_ms": 1_438_756,
        "input_tokens": 1_008_625,
        "output_tokens": 132_891,
        "billed_cost_usd": None,
        "historical_quality": "3.84",
    }
    assert len(fixture["documents"]) == 10
    assert len({document["id"] for document in fixture["documents"]}) == 10
    assert all(len(document["sha256"]) == 64 for document in fixture["documents"])


def test_structural_metric_cases_are_contract_data_not_an_evaluator() -> None:
    fixture = json.loads(
        (FIXTURES / "evaluation" / "structural_metric_cases.json").read_text(encoding="utf-8")
    )

    assert fixture["fixture_schema_version"] == "1.0"
    assert {case["id"] for case in fixture["cases"]} == {
        "empty_markdown",
        "headings_list_and_table",
    }
    for case in fixture["cases"]:
        expected = case["expected"]
        metrics = StructuralMetrics(**expected)
        assert metrics.document_count == 1
        assert metrics.word_count >= 0
        assert "markdown" in case


def test_evaluation_contract_keeps_billed_and_normalized_cost_distinct() -> None:
    price_card = PriceCardReceipt("approved-deepseek", "2026-07", "b" * 64)
    candidate_cost = CostMetrics(
        billed_cost_usd=Decimal("1.23"),
        normalized_cost_usd=Decimal("1.11"),
        price_card=price_card,
    )

    assert CostMetrics().billed_cost_usd is None
    assert candidate_cost.billed_cost_usd != candidate_cost.normalized_cost_usd
    with pytest.raises(ValueError, match="price_card"):
        CostMetrics(normalized_cost_usd=Decimal("1.11"))


def test_document_alignment_requires_explicit_absence_and_is_immutable() -> None:
    pair = DocumentPair(
        document_id="09_EXECUTIVE_SUMMARY.md",
        baseline=_artifact("baseline"),
        candidate=_artifact("candidate"),
        status="aligned",
    )
    alignment = DocumentAlignment(("09_EXECUTIVE_SUMMARY.md",), (pair,))

    assert alignment.pairs[0].status == "aligned"
    with pytest.raises(FrozenInstanceError):
        pair.status = "unmatched"  # type: ignore[misc]
    with pytest.raises(ValueError, match="missing_candidate"):
        DocumentPair(
            document_id="missing.md",
            baseline=_artifact("baseline", "missing.md"),
            candidate=_artifact("candidate", "missing.md"),
            status="missing_candidate",
        )


def test_complete_result_preserves_receipts_unknowns_and_informational_verdict() -> None:
    receipt = EvaluationReceipt("nexus-evaluator", "1.0", "c" * 64, "d" * 64)
    alignment = DocumentAlignment(
        ("09_EXECUTIVE_SUMMARY.md",),
        (
            DocumentPair(
                "09_EXECUTIVE_SUMMARY.md",
                _artifact("baseline"),
                _artifact("candidate"),
                "aligned",
            ),
        ),
    )
    result = EvaluationResult(
        receipt=receipt,
        alignment=alignment,
        baseline_structure=StructuralMetrics(1, 4, 4, 50, 20, 0),
        candidate_structure=StructuralMetrics(1, 5, 5, 60, 24, 1),
        baseline_provenance=ProvenanceMetrics(),
        candidate_provenance=ProvenanceMetrics(citation_count=3, resolved_citation_count=3),
        baseline_timing=TimingMetrics(experiment_wall_ms=1_438_756),
        candidate_timing=TimingMetrics(critical_path_ms=101, critical_path_task_ids=("write-doc",)),
        baseline_usage=UsageMetrics(input_tokens=1_008_625, output_tokens=132_891),
        candidate_usage=UsageMetrics(provider_calls=1),
        baseline_cost=CostMetrics(),
        candidate_cost=CostMetrics(),
        baseline_quality=QualityMetrics(historical_score=Decimal("3.84")),
        candidate_quality=QualityMetrics(
            validator_results=(ValidatorResult("markdown", "1", "passed"),)
        ),
        comparability=ComparabilityReceipt(limitations=("historical runtime envelope unavailable",)),
        verdict=ComparisonVerdict(
            "mixed",
            dimensions=(DimensionComparison("cost", "incomparable"),),
            limitations=("no automatic promotion",),
        ),
    )

    assert result.baseline_cost.billed_cost_usd is None
    assert result.verdict.candidate_verdict == "mixed"
    assert result.comparability.limitations == ("historical runtime envelope unavailable",)


@pytest.mark.parametrize(
    ("factory", "message"),
    [
        (lambda: _artifact("baseline").__class__("baseline", "document", "x", "BAD", 1), "SHA-256"),
        (lambda: StructuralMetrics(1, 0, 0, -1, 0, 0), "non-negative"),
        (lambda: TimingMetrics(critical_path_task_ids=("task",)), "critical_path"),
        (lambda: ProvenanceMetrics(citation_count=1, resolved_citation_count=2), "cannot exceed"),
    ],
)
def test_contract_rejects_ambiguous_or_invalid_values(factory, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        factory()
