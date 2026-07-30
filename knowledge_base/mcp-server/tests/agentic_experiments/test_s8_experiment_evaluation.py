from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
import hashlib

from mcp_server.experiments.application.evaluate_experiment import (
    BranchRunObservation,
    DocumentEvaluationInput,
    ExperimentEvaluationRequest,
    TaskAttemptObservation,
    evaluate_experiment,
)
from mcp_server.experiments.application.evaluate_parity import (
    ParitySide,
    SingleDocumentParityRequest,
    TransientParityMaterial,
    fingerprint_output_contract,
)
from mcp_server.experiments.application.experiment_report import (
    render_canonical_json_report,
    render_markdown_report,
)
from mcp_server.experiments.domain.evaluation import (
    ArtifactIdentity,
    CostMetrics,
    EvaluationReceipt,
    PriceCardReceipt,
    ProvenanceMetrics,
    StructuralMetrics,
    UsageMetrics,
)
from mcp_server.experiments.domain.runtime import OutputContract


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


RECEIPT = EvaluationReceipt("aggregate-evaluator", "1", "a" * 64, "b" * 64)
PRICE_CARD = PriceCardReceipt("approved", "1", "c" * 64)


def _document(document_id: str, *, missing_candidate: bool = False, context: str = "same") -> DocumentEvaluationInput:
    contract = OutputContract(document_id, "text/markdown")
    baseline = ParitySide(
        artifact=ArtifactIdentity("baseline", "document", document_id, _hash("baseline-" + document_id), 10),
        semantic_prompt_sha256=_hash("prompt-" + document_id),
        context_manifest_sha256=_hash(context),
        source_snapshot_sha256=_hash("source"),
        output_contract_sha256=fingerprint_output_contract(contract),
        model_route_options_sha256=_hash("route"),
        provenance=ProvenanceMetrics(citation_count=2, resolved_citation_count=2),
    )
    candidate = ParitySide(
        artifact=None
        if missing_candidate
        else ArtifactIdentity("candidate", "document", document_id, _hash("candidate-" + document_id), 10),
        semantic_prompt_sha256=_hash("prompt-" + document_id),
        context_manifest_sha256=_hash(context),
        source_snapshot_sha256=_hash("source"),
        output_contract_sha256=fingerprint_output_contract(contract),
        model_route_options_sha256=_hash("route"),
        provenance=ProvenanceMetrics(citation_count=2, resolved_citation_count=2),
    )
    return DocumentEvaluationInput(
        parity=SingleDocumentParityRequest(document_id, contract, baseline, candidate),
        baseline_structure=StructuralMetrics(1, 2, 2, 20, 8, 0),
        candidate_structure=StructuralMetrics(0 if missing_candidate else 1, 2, 2, 20, 8, 0),
    )


def _attempt(
    branch: str,
    task_id: str,
    attempt_id: str,
    state: str = "accepted",
    *,
    dependencies: tuple[str, ...] = (),
    outputs: tuple[str, ...] = (),
    input_tokens: int | None = 10,
    provider_latency_ms: int | None = 10,
    started_ms: int | None = 0,
    completed_ms: int | None = 10,
    billed: Decimal | None = None,
    normalized: Decimal | None = None,
) -> TaskAttemptObservation:
    cost = CostMetrics(
        billed_cost_usd=billed,
        normalized_cost_usd=normalized,
        price_card=PRICE_CARD if normalized is not None else None,
    )
    return TaskAttemptObservation(
        branch=branch,  # type: ignore[arg-type]
        task_id=task_id,
        attempt_id=attempt_id,
        state=state,  # type: ignore[arg-type]
        dependency_task_ids=dependencies,
        accepted_output_document_ids=outputs,
        started_ms=started_ms,
        completed_ms=completed_ms,
        provider_latency_ms=provider_latency_ms,
        usage=UsageMetrics(
            input_tokens=input_tokens,
            output_tokens=1,
            cache_read_tokens=0,
            cache_write_tokens=0,
            provider_calls=1,
            tool_calls=1,
            tool_bytes=1,
            repairs=0,
        ),
        cost=cost,
    )


def _request(
    documents: tuple[DocumentEvaluationInput, ...],
    *,
    baseline_attempts: tuple[TaskAttemptObservation, ...] = (),
    candidate_attempts: tuple[TaskAttemptObservation, ...] = (),
) -> ExperimentEvaluationRequest:
    return ExperimentEvaluationRequest(
        receipt=RECEIPT,
        required_document_ids=tuple(document.document_id for document in documents),
        documents=documents,
        baseline_run=BranchRunObservation(
            baseline_attempts,
            experiment_wall_ms=500 if baseline_attempts else None,
            queue_ms=10 if baseline_attempts else None,
            snapshot_ms=20 if baseline_attempts else None,
            validation_ms=30 if baseline_attempts else None,
        ),
        candidate_run=BranchRunObservation(
            candidate_attempts,
            experiment_wall_ms=400 if candidate_attempts else None,
            queue_ms=5 if candidate_attempts else None,
            snapshot_ms=15 if candidate_attempts else None,
            validation_ms=25 if candidate_attempts else None,
        ),
    )


def test_evaluates_ten_required_documents_in_deterministic_alignment_order() -> None:
    documents = tuple(_document(f"{index:02d}_document.md") for index in range(10, 0, -1))
    candidate_attempts = tuple(
        _attempt("candidate", f"writer-{index}", f"writer-{index}-1", outputs=(document.document_id,))
        for index, document in enumerate(documents)
    )

    result = evaluate_experiment(_request(documents, candidate_attempts=candidate_attempts))

    assert [pair.document_id for pair in result.alignment.pairs] == sorted(document.document_id for document in documents)
    assert len(result.documents) == 10
    assert result.baseline_structure.document_count == 10
    assert result.candidate_provenance.citation_count == 20
    assert result.comparability.semantic_parity_confirmed is True
    assert result.candidate_output_claims.status == "passed"


def test_missing_required_candidate_document_is_invalid_not_imputed() -> None:
    result = evaluate_experiment(
        _request(
            (_document("required.md", missing_candidate=True),),
            candidate_attempts=(_attempt("candidate", "writer", "writer-1", outputs=("required.md",)),),
        )
    )

    assert result.alignment.pairs[0].status == "missing_candidate"
    assert result.documents[0].parity.candidate_contract.status == "failed"
    assert result.verdict.candidate_verdict == "invalid"
    assert result.verdict.blocking_failures == ("candidate required output invalid: required.md",)


def test_failed_and_retried_attempts_are_included_without_double_counting_document_output() -> None:
    document = _document("report.md")
    candidate_attempts = (
        _attempt("candidate", "writer", "writer-1", "retried", input_tokens=3, provider_latency_ms=7, billed=Decimal("0.10"), normalized=Decimal("0.08")),
        _attempt("candidate", "writer", "writer-2", input_tokens=5, provider_latency_ms=11, billed=Decimal("0.20"), normalized=Decimal("0.16"), outputs=("report.md",)),
    )
    result = evaluate_experiment(_request((document,), candidate_attempts=candidate_attempts))

    assert result.candidate_attempts.total == 2
    assert result.candidate_attempts.accepted == 1
    assert result.candidate_attempts.retried == 1
    assert result.candidate_usage.input_tokens == 8
    assert result.candidate_timing.provider_latency_sum_ms == 18
    assert result.candidate_cost.billed_cost_usd == Decimal("0.30")
    assert result.candidate_cost.normalized_cost_usd == Decimal("0.24")
    assert result.candidate_structure.document_count == 1


def test_parallel_accepted_tasks_have_dag_critical_path_but_all_attempt_latency() -> None:
    document = _document("report.md")
    attempts = (
        _attempt("candidate", "a", "a-1", started_ms=0, completed_ms=100, provider_latency_ms=10),
        _attempt("candidate", "b", "b-1", started_ms=0, completed_ms=100, provider_latency_ms=20),
        _attempt("candidate", "c", "c-1", dependencies=("a", "b"), started_ms=100, completed_ms=200, provider_latency_ms=30, outputs=("report.md",)),
        _attempt("candidate", "noise", "noise-1", "failed", started_ms=0, completed_ms=25, provider_latency_ms=40),
    )
    result = evaluate_experiment(_request((document,), candidate_attempts=attempts))

    assert result.candidate_timing.generation_wall_ms == 200
    assert result.candidate_timing.critical_path_ms == 200
    assert result.candidate_timing.critical_path_task_ids == ("b", "c")
    assert result.candidate_timing.provider_latency_sum_ms == 100
    assert result.candidate_timing.provider_latency_p50_ms == 20
    assert result.candidate_timing.provider_latency_p95_ms == 40


def test_historical_unknown_costs_stay_unknown_and_are_not_conflated_with_provider_billing() -> None:
    document = _document("report.md")
    candidate = (_attempt("candidate", "writer", "a", billed=Decimal("1.20"), normalized=Decimal("1.00"), outputs=("report.md",)),)
    result = evaluate_experiment(_request((document,), candidate_attempts=candidate))
    report = render_markdown_report(result)

    assert result.baseline_cost.billed_cost_usd is None
    assert result.baseline_cost.normalized_cost_usd is None
    assert result.candidate_cost.billed_cost_usd == Decimal("1.20")
    assert result.candidate_cost.normalized_cost_usd == Decimal("1.00")
    assert result.verdict.candidate_verdict == "not_evaluated"
    assert "Provider-reported cost (USD)" in report
    assert "Normalized cost (USD)" in report


def test_mixed_dimensions_can_never_be_reported_as_better() -> None:
    document = _document("report.md")
    baseline = (_attempt("baseline", "writer", "baseline-1", outputs=("report.md",)),)
    candidate = (_attempt("candidate", "writer", "candidate-1", outputs=("report.md",)),)
    result = evaluate_experiment(_request((document,), baseline_attempts=baseline, candidate_attempts=candidate))

    assert result.comparability.semantic_parity_confirmed is True
    assert result.verdict.candidate_verdict == "mixed"
    assert {comparison.verdict for comparison in result.verdict.dimensions} >= {"better", "mixed"}


def test_semantic_mismatch_is_not_evaluated_even_with_valid_documents() -> None:
    document = _document("report.md")
    changed_candidate = replace(
        document.parity.candidate,
        context_manifest_sha256=_hash("different-context"),
    )
    changed = replace(document, parity=replace(document.parity, candidate=changed_candidate))

    result = evaluate_experiment(
        _request(
            (changed,),
            candidate_attempts=(_attempt("candidate", "writer", "writer-1", outputs=("report.md",)),),
        )
    )

    assert result.comparability.semantic_status == "different"
    assert result.verdict.candidate_verdict == "not_evaluated"


def test_candidate_accepted_output_gate_rejects_missing_and_undeclared_claims() -> None:
    document = _document("report.md")
    missing = evaluate_experiment(
        _request(
            (document,),
            candidate_attempts=(_attempt("candidate", "writer", "writer-1"),),
        )
    )
    undeclared = evaluate_experiment(
        _request(
            (document,),
            candidate_attempts=(
                _attempt("candidate", "writer", "writer-1", outputs=("report.md", "not-declared.md")),
            ),
        )
    )

    assert missing.candidate_output_claims.status == "failed"
    assert missing.candidate_output_claims.missing_document_ids == ("report.md",)
    assert missing.verdict.candidate_verdict == "invalid"
    assert undeclared.candidate_output_claims.status == "failed"
    assert undeclared.candidate_output_claims.undeclared_document_ids == ("not-declared.md",)
    assert undeclared.verdict.candidate_verdict == "invalid"


def test_attempt_runtime_values_reject_invalid_literal_and_bool_or_float_timing() -> None:
    invalid_branch = None
    invalid_state = None
    invalid_bool_timing = None
    invalid_float_timing = None
    try:
        _attempt("other", "writer", "writer-1")
    except ValueError as error:
        invalid_branch = str(error)
    try:
        _attempt("candidate", "writer", "writer-1", "other")
    except ValueError as error:
        invalid_state = str(error)
    try:
        _attempt("candidate", "writer", "writer-1", started_ms=True)  # type: ignore[arg-type]
    except ValueError as error:
        invalid_bool_timing = str(error)
    try:
        _attempt("candidate", "writer", "writer-1", completed_ms=1.5)  # type: ignore[arg-type]
    except ValueError as error:
        invalid_float_timing = str(error)

    assert invalid_branch == "branch must be baseline or candidate"
    assert invalid_state == "state is invalid"
    assert invalid_bool_timing == "started_ms must be a non-negative integer"
    assert invalid_float_timing == "completed_ms must be a non-negative integer"


def test_order_invariance_and_reports_never_expose_transient_prompt_or_runtime_envelope() -> None:
    first = _document("a.md")
    second = _document("b.md")
    secret_prompt = "private prompt body must never leave the evaluator"
    secret_envelope = {"runtime_secret": "never serialize me"}
    baseline_with_transient = replace(first.parity.baseline, semantic_prompt_sha256=_hash(secret_prompt))
    candidate_with_transient = replace(first.parity.candidate, semantic_prompt_sha256=_hash(secret_prompt))
    first = replace(
        first,
        parity=replace(
            first.parity,
            baseline=baseline_with_transient,
            candidate=candidate_with_transient,
            baseline_transient=TransientParityMaterial(secret_prompt, secret_envelope),
            candidate_transient=TransientParityMaterial(secret_prompt, secret_envelope),
        ),
    )
    attempts = (
        _attempt("candidate", "b", "b-1", started_ms=10, completed_ms=20, outputs=("b.md",)),
        _attempt("candidate", "a", "a-1", started_ms=0, completed_ms=10, outputs=("a.md",)),
    )
    forward = evaluate_experiment(_request((first, second), candidate_attempts=attempts))
    reverse = evaluate_experiment(_request((second, first), candidate_attempts=tuple(reversed(attempts))))
    json_report = render_canonical_json_report(forward)
    markdown_report = render_markdown_report(forward)

    assert json_report == render_canonical_json_report(reverse)
    assert markdown_report == render_markdown_report(reverse)
    assert secret_prompt not in json_report
    assert "runtime_secret" not in json_report
    assert "never serialize me" not in markdown_report
