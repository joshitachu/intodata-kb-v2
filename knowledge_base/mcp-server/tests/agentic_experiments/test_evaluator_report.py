from __future__ import annotations

from decimal import Decimal

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
)
from mcp_server.experiments.infrastructure.validation.report import (
    compare_numeric_dimension,
    render_json_report,
    render_markdown_report,
)


def _result() -> EvaluationResult:
    baseline = ArtifactIdentity("baseline", "document", "a.md", "a" * 64, 1)
    candidate = ArtifactIdentity("candidate", "document", "a.md", "b" * 64, 2)
    return EvaluationResult(
        receipt=EvaluationReceipt("evaluator", "1", "c" * 64, "d" * 64),
        alignment=DocumentAlignment(("a.md",), (DocumentPair("a.md", baseline, candidate, "aligned"),)),
        baseline_structure=StructuralMetrics(1, 1, 1, 1, 1, 0),
        candidate_structure=StructuralMetrics(1, 1, 1, 1, 1, 0),
        baseline_provenance=ProvenanceMetrics(),
        candidate_provenance=ProvenanceMetrics(),
        baseline_timing=TimingMetrics(),
        candidate_timing=TimingMetrics(),
        baseline_usage=UsageMetrics(input_tokens=1),
        candidate_usage=UsageMetrics(input_tokens=2),
        baseline_cost=CostMetrics(),
        candidate_cost=CostMetrics(
            normalized_cost_usd=Decimal("1.10"),
            price_card=PriceCardReceipt("card", "1", "e" * 64),
        ),
        baseline_quality=QualityMetrics(),
        candidate_quality=QualityMetrics(),
        comparability=ComparabilityReceipt(limitations=("historical metrics unavailable",)),
        verdict=ComparisonVerdict(
            "mixed", (DimensionComparison("cost", "incomparable", ("baseline billed cost is null",)),)
        ),
        limitations=("candidate remains unpublished",),
    )


def test_reports_are_deterministic_json_safe_and_mark_incomparable_values() -> None:
    result = _result()
    json_report = render_json_report(result)
    markdown_report = render_markdown_report(result)

    assert json_report == render_json_report(result)
    assert '"normalized_cost_usd": "1.10"' in json_report
    assert "Informational only" in markdown_report
    assert "cost: **incomparable**" in markdown_report
    assert "historical metrics unavailable" in markdown_report


def test_missing_numeric_values_create_an_incomparable_dimension() -> None:
    comparison = compare_numeric_dimension("cost", None, Decimal("1.0"), lower_is_better=True)

    assert comparison.verdict == "incomparable"
    assert comparison.limitations == ("baseline value is unavailable",)
