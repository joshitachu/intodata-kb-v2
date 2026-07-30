from __future__ import annotations

from decimal import Decimal
import hashlib

import pytest

from mcp_server.experiments.application.evaluate_parity import (
    ParitySide,
    SingleDocumentParityRequest,
    TransientParityMaterial,
    evaluate_single_document_parity,
    fingerprint_output_contract,
    validate_output_contract,
)
from mcp_server.experiments.application.parity_report import (
    render_canonical_json_report,
    render_markdown_report,
)
from mcp_server.experiments.domain.evaluation import (
    ArtifactIdentity,
    CostMetrics,
    PriceCardReceipt,
    ProvenanceMetrics,
    TimingMetrics,
    UsageMetrics,
)
from mcp_server.experiments.domain.runtime import OutputContract


CONTRACT = OutputContract("report.md", "text/markdown", max_bytes=20)


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _artifact(branch: str, *, name: str = "report.md", size: int = 12) -> ArtifactIdentity:
    return ArtifactIdentity(branch, "document", name, "a" * 64, size)  # type: ignore[arg-type]


def _price_card() -> PriceCardReceipt:
    return PriceCardReceipt("approved-card", "1", "b" * 64)


def _side(
    branch: str,
    *,
    artifact: ArtifactIdentity | None = None,
    missing_artifact: bool = False,
    context: str = "context-v1",
    source: str | None = "source-v1",
    output_contract_hash: str | None = None,
    model_options: str = "route-options-v1",
    usage: UsageMetrics | None = None,
    timing: TimingMetrics | None = None,
    cost: CostMetrics | None = None,
) -> ParitySide:
    return ParitySide(
        artifact=None if missing_artifact else artifact or _artifact(branch),
        context_manifest_sha256=_hash(context),
        source_snapshot_sha256=None if source is None else _hash(source),
        output_contract_sha256=output_contract_hash or fingerprint_output_contract(CONTRACT),
        model_route_options_sha256=_hash(model_options),
        provenance=ProvenanceMetrics(
            citation_count=2,
            resolved_citation_count=1,
            evidence_item_count=3,
            cited_evidence_item_count=2,
            unsupported_claim_count=1,
        ),
        usage=usage or UsageMetrics(input_tokens=100, output_tokens=10, tool_calls=2),
        timing=timing or TimingMetrics(experiment_wall_ms=100, provider_latency_sum_ms=80),
        cost=cost or CostMetrics(),
    )


def _request(
    baseline: ParitySide,
    candidate: ParitySide,
    *,
    baseline_prompt: str = "write preserved report",
    candidate_prompt: str = "write preserved report",
    baseline_envelope: dict[str, object] | None = None,
    candidate_envelope: dict[str, object] | None = None,
) -> SingleDocumentParityRequest:
    return SingleDocumentParityRequest(
        document_id="report.md",
        output_contract=CONTRACT,
        baseline=baseline,
        candidate=candidate,
        baseline_transient=TransientParityMaterial(
            semantic_prompt=baseline_prompt,
            runtime_envelope=baseline_envelope if baseline_envelope is not None else {"temperature": 0},
        ),
        candidate_transient=TransientParityMaterial(
            semantic_prompt=candidate_prompt,
            runtime_envelope=candidate_envelope if candidate_envelope is not None else {"temperature": 0},
        ),
    )


def test_evaluates_generic_output_and_preserves_unknown_baseline_costs() -> None:
    baseline = _side("baseline")
    candidate = _side(
        "candidate",
        usage=UsageMetrics(input_tokens=90, output_tokens=9, tool_calls=1),
        timing=TimingMetrics(experiment_wall_ms=90, provider_latency_sum_ms=70),
        cost=CostMetrics(
            billed_cost_usd=Decimal("1.20"),
            normalized_cost_usd=Decimal("1.00"),
            price_card=_price_card(),
        ),
    )

    result = evaluate_single_document_parity(_request(baseline, candidate))

    assert result.baseline_contract.status == "passed"
    assert result.candidate_contract.status == "passed"
    assert result.comparability.semantic_status == "matched"
    assert result.comparability.semantic_parity_confirmed is True
    assert result.comparability.runtime_status == "matched"
    assert result.baseline.cost.billed_cost_usd is None
    assert result.baseline.cost.normalized_cost_usd is None
    costs = {comparison.dimension: comparison for comparison in result.comparisons}
    assert costs["normalized_cost_usd"].verdict == "incomparable"
    assert costs["provider_reported_cost_usd"].limitations == ("baseline value is unavailable",)


def test_runtime_envelope_drift_does_not_negate_confirmed_semantic_parity() -> None:
    result = evaluate_single_document_parity(
        _request(
            _side("baseline"),
            _side("candidate"),
            candidate_envelope={"temperature": 1},
        )
    )

    assert result.comparability.semantic_parity_confirmed is True
    assert result.comparability.runtime_status == "different"
    assert "runtime_envelope_sha256 is different" in result.limitations


def test_context_source_options_and_contract_mismatches_prevent_semantic_confirmation() -> None:
    candidate = _side(
        "candidate",
        context="context-v2",
        source="source-v2",
        output_contract_hash="c" * 64,
        model_options="route-options-v2",
    )

    result = evaluate_single_document_parity(_request(_side("baseline"), candidate))
    markdown = render_markdown_report(result)

    assert result.comparability.semantic_status == "different"
    assert result.comparability.semantic_parity_confirmed is False
    assert result.verdict.candidate_verdict == "not_evaluated"
    assert "context_manifest_sha256 is different" in result.limitations
    assert "source_snapshot_sha256 is different" in result.limitations
    assert "model_route_options_sha256 is different" in result.limitations
    assert "output_contract_sha256 is different" in result.limitations
    assert "| context_manifest_sha256 | different |" in markdown
    assert "| output_contract_sha256 | different |" in markdown


def test_unknown_source_snapshot_remains_unknown_and_never_confirms_semantic_parity() -> None:
    result = evaluate_single_document_parity(_request(_side("baseline", source=None), _side("candidate", source=None)))

    assert result.comparability.source_snapshot_sha256.status == "unknown"
    assert result.comparability.semantic_status == "unknown"
    assert result.comparability.semantic_parity_confirmed is False
    assert result.verdict.candidate_verdict == "not_evaluated"


def test_forged_branch_or_missing_required_baseline_makes_verdict_invalid() -> None:
    forged_branch = evaluate_single_document_parity(
        _request(_side("baseline", artifact=_artifact("candidate")), _side("candidate"))
    )
    missing_baseline = evaluate_single_document_parity(
        _request(_side("baseline", missing_artifact=True), _side("candidate"))
    )

    assert forged_branch.baseline_contract.status == "failed"
    assert any(check.field == "artifact_branch" and check.status == "failed" for check in forged_branch.baseline_contract.checks)
    assert forged_branch.verdict.candidate_verdict == "invalid"
    assert missing_baseline.baseline_contract.status == "failed"
    assert missing_baseline.verdict.blocking_failures == ("baseline output contract validation failed",)


def test_optional_missing_output_is_not_imputed_as_a_passing_artifact() -> None:
    validation = validate_output_contract(OutputContract("report.md", "text/markdown", required=False), None)

    assert validation.status == "not_applicable"
    assert validation.checks[0].detail == "optional artifact is absent"


def test_reports_are_deterministic_and_cannot_expose_transient_raw_data() -> None:
    prompt = "do not persist this raw prompt"
    envelope = {"runtime_secret_like_setting": "do not persist this envelope"}
    result = evaluate_single_document_parity(
        _request(_side("baseline"), _side("candidate"), baseline_prompt=prompt, candidate_prompt=prompt, baseline_envelope=envelope, candidate_envelope=envelope)
    )

    json_report = render_canonical_json_report(result)
    markdown = render_markdown_report(result)

    assert json_report == render_canonical_json_report(result)
    assert '"billed_cost_usd":null' in json_report
    assert prompt not in json_report
    assert "runtime_secret_like_setting" not in json_report
    assert "do not persist this envelope" not in json_report
    assert "Semantic parity confirmed: **yes**" in markdown
    assert "Provider-reported cost (USD)" in markdown
    assert "Evidence items" in markdown
    assert "Citations" in markdown
    assert "Candidate verdict:" in markdown


def test_transient_data_must_match_a_claimed_fingerprint() -> None:
    side = _side("baseline")
    forged = ParitySide(
        artifact=side.artifact,
        semantic_prompt_sha256="c" * 64,
        context_manifest_sha256=side.context_manifest_sha256,
        source_snapshot_sha256=side.source_snapshot_sha256,
        output_contract_sha256=side.output_contract_sha256,
        model_route_options_sha256=side.model_route_options_sha256,
    )
    with pytest.raises(ValueError, match="does not match transient semantic_prompt"):
        evaluate_single_document_parity(_request(forged, _side("candidate")))
