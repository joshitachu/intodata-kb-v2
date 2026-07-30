"""Deterministic, offline evaluation of a complete documentation experiment.

This module deliberately works on frozen observations.  It does not read an
artifact store, call a model, infer prices, or publish documentation.  The
runner/scheduler turns its durable task records into these value objects; this
evaluator then gives an A/B experiment one reproducible, non-sensitive result.

Attempts are operationally inclusive: failed and retried calls contribute to
usage, provider latency and billed cost.  A document itself is represented once
by its accepted artifact, so retries can never double-count an output.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from decimal import Decimal
from math import ceil
from typing import Literal

from mcp_server.experiments.application.evaluate_parity import (
    SingleDocumentParityRequest,
    SingleDocumentParityResult,
    evaluate_single_document_parity,
)
from mcp_server.experiments.domain.evaluation import (
    ComparisonVerdict,
    CostMetrics,
    DimensionComparison,
    DocumentAlignment,
    EvaluationReceipt,
    ProvenanceMetrics,
    QualityMetrics,
    StructuralMetrics,
    TimingMetrics,
    UsageMetrics,
)
from mcp_server.experiments.infrastructure.validation.alignment import align_required_documents
from mcp_server.experiments.infrastructure.validation.report import compare_numeric_dimension
from mcp_server.experiments.infrastructure.validation.timing import (
    TaskTimingObservation,
    aggregate_timing,
)


AttemptState = Literal["accepted", "failed", "retried", "cancelled", "skipped"]
Branch = Literal["baseline", "candidate"]
SemanticStatus = Literal["matched", "different", "unknown"]


def _non_negative(value: int | None, field_name: str) -> None:
    if value is not None and (type(value) is not int or value < 0):
        raise ValueError(f"{field_name} must be a non-negative integer")


def _unique_non_blank(values: tuple[str, ...], field_name: str) -> None:
    if any(not value.strip() for value in values):
        raise ValueError(f"{field_name} must not contain blank values")
    if len(set(values)) != len(values):
        raise ValueError(f"{field_name} must be unique")


@dataclass(frozen=True, slots=True)
class TaskAttemptObservation:
    """Sanitized operational facts for one task attempt.

    ``accepted_output_document_ids`` is intentionally only an identity list;
    output bytes, prompt text, tool arguments, and runtime envelopes are not
    evaluator inputs.
    """

    branch: Branch
    task_id: str
    attempt_id: str
    state: AttemptState
    dependency_task_ids: tuple[str, ...] = ()
    accepted_output_document_ids: tuple[str, ...] = ()
    started_ms: int | None = None
    completed_ms: int | None = None
    provider_latency_ms: int | None = None
    usage: UsageMetrics = field(default_factory=UsageMetrics)
    cost: CostMetrics = field(default_factory=CostMetrics)

    def __post_init__(self) -> None:
        if self.branch not in {"baseline", "candidate"}:
            raise ValueError("branch must be baseline or candidate")
        if self.state not in {"accepted", "failed", "retried", "cancelled", "skipped"}:
            raise ValueError("state is invalid")
        if not self.task_id.strip() or not self.attempt_id.strip():
            raise ValueError("task_id and attempt_id must not be blank")
        _unique_non_blank(self.dependency_task_ids, "dependency_task_ids")
        _unique_non_blank(self.accepted_output_document_ids, "accepted_output_document_ids")
        for value, field_name in (
            (self.started_ms, "started_ms"),
            (self.completed_ms, "completed_ms"),
            (self.provider_latency_ms, "provider_latency_ms"),
        ):
            _non_negative(value, field_name)
        if self.started_ms is not None and self.completed_ms is not None and self.completed_ms < self.started_ms:
            raise ValueError("completed_ms cannot precede started_ms")
        if self.state != "accepted" and self.accepted_output_document_ids:
            raise ValueError("only an accepted attempt may declare accepted outputs")


@dataclass(frozen=True, slots=True)
class BranchRunObservation:
    """Run-level timing phases plus all attempts for one experiment branch."""

    attempts: tuple[TaskAttemptObservation, ...] = ()
    experiment_wall_ms: int | None = None
    queue_ms: int | None = None
    snapshot_ms: int | None = None
    validation_ms: int | None = None
    quality: QualityMetrics = field(default_factory=QualityMetrics)

    def __post_init__(self) -> None:
        for value, field_name in (
            (self.experiment_wall_ms, "experiment_wall_ms"),
            (self.queue_ms, "queue_ms"),
            (self.snapshot_ms, "snapshot_ms"),
            (self.validation_ms, "validation_ms"),
        ):
            _non_negative(value, field_name)
        attempt_ids = tuple(attempt.attempt_id for attempt in self.attempts)
        _unique_non_blank(attempt_ids, "attempt_ids")
        accepted_tasks = tuple(attempt.task_id for attempt in self.attempts if attempt.state == "accepted")
        if len(set(accepted_tasks)) != len(accepted_tasks):
            raise ValueError("a task may have at most one accepted attempt")


@dataclass(frozen=True, slots=True)
class AcceptedOutputClaimValidation:
    """Auditable output-acceptance gate, independent from an artifact's presence."""

    branch: Branch
    status: Literal["passed", "failed", "not_applicable"]
    claimed_document_ids: tuple[str, ...] = ()
    missing_document_ids: tuple[str, ...] = ()
    undeclared_document_ids: tuple[str, ...] = ()
    duplicate_document_ids: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class DocumentEvaluationInput:
    """One declared document output and its pre-measured structural facts."""

    parity: SingleDocumentParityRequest
    baseline_structure: StructuralMetrics
    candidate_structure: StructuralMetrics

    @property
    def document_id(self) -> str:
        return self.parity.document_id


@dataclass(frozen=True, slots=True)
class ExperimentEvaluationRequest:
    """A complete frozen A/B input, independent of a scanner or provider."""

    receipt: EvaluationReceipt
    required_document_ids: tuple[str, ...]
    documents: tuple[DocumentEvaluationInput, ...]
    baseline_run: BranchRunObservation = field(default_factory=BranchRunObservation)
    candidate_run: BranchRunObservation = field(default_factory=BranchRunObservation)

    def __post_init__(self) -> None:
        _unique_non_blank(self.required_document_ids, "required_document_ids")
        document_ids = tuple(document.document_id for document in self.documents)
        _unique_non_blank(document_ids, "document IDs")
        if set(document_ids) != set(self.required_document_ids):
            raise ValueError("documents must declare exactly the required_document_ids")
        for branch, run in (("baseline", self.baseline_run), ("candidate", self.candidate_run)):
            if any(attempt.branch != branch for attempt in run.attempts):
                raise ValueError(f"{branch}_run contains an attempt for another branch")


@dataclass(frozen=True, slots=True)
class AttemptSummary:
    """Counts make inclusion of failed/retried work auditable without raw logs."""

    total: int
    accepted: int
    failed: int
    retried: int
    cancelled: int
    skipped: int


@dataclass(frozen=True, slots=True)
class DocumentEvaluationResult:
    document_id: str
    parity: SingleDocumentParityResult
    baseline_structure: StructuralMetrics
    candidate_structure: StructuralMetrics


@dataclass(frozen=True, slots=True)
class ExperimentComparability:
    """Summary of document-level semantic comparability, never prompt bytes."""

    semantic_status: SemanticStatus
    semantic_parity_confirmed: bool
    limitations: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ExperimentEvaluationResult:
    """Durable aggregate evaluation with per-document results and no side effects."""

    receipt: EvaluationReceipt
    alignment: DocumentAlignment
    documents: tuple[DocumentEvaluationResult, ...]
    baseline_output_claims: AcceptedOutputClaimValidation
    candidate_output_claims: AcceptedOutputClaimValidation
    baseline_attempts: AttemptSummary
    candidate_attempts: AttemptSummary
    baseline_structure: StructuralMetrics
    candidate_structure: StructuralMetrics
    baseline_provenance: ProvenanceMetrics
    candidate_provenance: ProvenanceMetrics
    baseline_timing: TimingMetrics
    candidate_timing: TimingMetrics
    baseline_usage: UsageMetrics
    candidate_usage: UsageMetrics
    baseline_cost: CostMetrics
    candidate_cost: CostMetrics
    baseline_quality: QualityMetrics
    candidate_quality: QualityMetrics
    comparability: ExperimentComparability
    verdict: ComparisonVerdict
    limitations: tuple[str, ...] = ()


def _attempt_summary(attempts: Iterable[TaskAttemptObservation]) -> AttemptSummary:
    states = tuple(attempt.state for attempt in attempts)
    return AttemptSummary(
        total=len(states),
        accepted=states.count("accepted"),
        failed=states.count("failed"),
        retried=states.count("retried"),
        cancelled=states.count("cancelled"),
        skipped=states.count("skipped"),
    )


def _validate_accepted_output_claims(
    branch: Branch,
    attempts: tuple[TaskAttemptObservation, ...],
    required_document_ids: tuple[str, ...],
) -> AcceptedOutputClaimValidation:
    """Require candidate output acceptance; tolerate a ledger-less legacy baseline.

    A baseline with no task ledger is the frozen historical case and cannot be
    retroactively made to claim scheduler outputs.  If it does declare output
    claims, those claims are checked just like candidate claims.
    """
    claimed = tuple(
        document_id
        for attempt in attempts
        if attempt.state == "accepted"
        for document_id in attempt.accepted_output_document_ids
    )
    if branch == "baseline" and not attempts:
        return AcceptedOutputClaimValidation("baseline", "not_applicable")
    required = set(required_document_ids)
    claimed_set = set(claimed)
    duplicates = tuple(sorted(document_id for document_id in claimed_set if claimed.count(document_id) > 1))
    undeclared = tuple(sorted(claimed_set - required))
    # A ledger containing only non-output baseline tasks is insufficient to
    # establish a historical acceptance chain, but is not a fabricated claim.
    require_complete = branch == "candidate" or bool(claimed)
    missing = tuple(sorted(required - claimed_set)) if require_complete else ()
    limitations: list[str] = []
    if missing:
        limitations.append(
            f"{branch} accepted output claims missing required documents: {', '.join(missing)}"
        )
    if undeclared:
        limitations.append(
            f"{branch} accepted output claims include undeclared documents: {', '.join(undeclared)}"
        )
    if duplicates:
        limitations.append(
            f"{branch} accepted output claims duplicate documents: {', '.join(duplicates)}"
        )
    status: Literal["passed", "failed", "not_applicable"] = "failed" if limitations else "passed"
    return AcceptedOutputClaimValidation(
        branch=branch,
        status=status,
        claimed_document_ids=tuple(sorted(claimed)),
        missing_document_ids=missing,
        undeclared_document_ids=undeclared,
        duplicate_document_ids=duplicates,
        limitations=tuple(limitations),
    )


def _sum_known(values: Iterable[int | None]) -> int | None:
    values_tuple = tuple(values)
    if not values_tuple or any(value is None for value in values_tuple):
        return None
    return sum(int(value) for value in values_tuple)


def _aggregate_usage(attempts: tuple[TaskAttemptObservation, ...]) -> UsageMetrics:
    """Aggregate each field only when every operational attempt reported it."""
    fields = (
        "input_tokens",
        "output_tokens",
        "cache_read_tokens",
        "cache_write_tokens",
        "provider_calls",
        "tool_calls",
        "tool_bytes",
        "repairs",
    )
    values = {field: _sum_known(getattr(attempt.usage, field) for attempt in attempts) for field in fields}
    return UsageMetrics(
        **values,
        retries=sum(attempt.state == "retried" for attempt in attempts),
    )


def _aggregate_cost(attempts: tuple[TaskAttemptObservation, ...]) -> CostMetrics:
    """Keep provider billing and normalized price-card values separate and nullable."""
    if not attempts:
        return CostMetrics()
    billed = tuple(attempt.cost.billed_cost_usd for attempt in attempts)
    normalized = tuple(attempt.cost.normalized_cost_usd for attempt in attempts)
    billed_total = None if any(value is None for value in billed) else sum(billed, Decimal("0"))
    cards = tuple(attempt.cost.price_card for attempt in attempts)
    known_card = cards[0] if cards and cards[0] is not None else None
    same_card = known_card is not None and all(card == known_card for card in cards)
    normalized_total = (
        None
        if any(value is None for value in normalized) or not same_card
        else sum(normalized, Decimal("0"))
    )
    return CostMetrics(
        billed_cost_usd=billed_total,
        normalized_cost_usd=normalized_total,
        price_card=known_card if same_card else None,
    )


def _aggregate_structure(values: Iterable[StructuralMetrics]) -> StructuralMetrics:
    values_tuple = tuple(values)
    fields = ("document_count", "heading_count", "section_count", "word_count", "line_count", "table_count")
    totals = {field: sum(getattr(value, field) for value in values_tuple) for field in fields}
    required_count = _sum_known(value.required_section_count for value in values_tuple)
    required_covered = _sum_known(value.required_section_covered_count for value in values_tuple)
    return StructuralMetrics(
        **totals,
        required_section_count=required_count,
        required_section_covered_count=required_covered,
    )


def _aggregate_provenance(values: Iterable[ProvenanceMetrics]) -> ProvenanceMetrics:
    values_tuple = tuple(values)
    fields = (
        "citation_count",
        "resolved_citation_count",
        "evidence_item_count",
        "cited_evidence_item_count",
        "unsupported_claim_count",
        "extracted_fact_count",
        "matched_extracted_fact_count",
    )
    return ProvenanceMetrics(**{field: _sum_known(getattr(value, field) for value in values_tuple) for field in fields})


def _percentile(values: tuple[int, ...], fraction: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(1, ceil(fraction * len(ordered))) - 1]


def _aggregate_timing(run: BranchRunObservation) -> TimingMetrics:
    """Use accepted tasks for DAG duration and every attempt for provider work."""
    accepted = tuple(attempt for attempt in run.attempts if attempt.state == "accepted")
    accepted_timing = tuple(
        TaskTimingObservation(
            node_id=attempt.task_id,
            dependency_node_ids=attempt.dependency_task_ids,
            started_ms=attempt.started_ms,
            completed_ms=attempt.completed_ms,
            provider_latency_ms=attempt.provider_latency_ms,
        )
        for attempt in accepted
    )
    accepted_metrics = aggregate_timing(
        accepted_timing,
        experiment_wall_ms=run.experiment_wall_ms,
        queue_ms=run.queue_ms,
        snapshot_ms=run.snapshot_ms,
        validation_ms=run.validation_ms,
    )
    raw_latencies = tuple(attempt.provider_latency_ms for attempt in run.attempts)
    all_latency_known = bool(run.attempts) and all(value is not None for value in raw_latencies)
    latencies = tuple(int(value) for value in raw_latencies) if all_latency_known else ()
    return TimingMetrics(
        experiment_wall_ms=accepted_metrics.experiment_wall_ms,
        generation_wall_ms=accepted_metrics.generation_wall_ms,
        queue_ms=accepted_metrics.queue_ms,
        snapshot_ms=accepted_metrics.snapshot_ms,
        validation_ms=accepted_metrics.validation_ms,
        provider_latency_sum_ms=sum(latencies) if all_latency_known else None,
        provider_latency_p50_ms=_percentile(latencies, 0.50) if all_latency_known else None,
        provider_latency_p95_ms=_percentile(latencies, 0.95) if all_latency_known else None,
        critical_path_ms=accepted_metrics.critical_path_ms,
        critical_path_task_ids=accepted_metrics.critical_path_task_ids,
    )


def _numeric_dimensions(
    baseline_structure: StructuralMetrics,
    candidate_structure: StructuralMetrics,
    baseline_provenance: ProvenanceMetrics,
    candidate_provenance: ProvenanceMetrics,
    baseline_timing: TimingMetrics,
    candidate_timing: TimingMetrics,
    baseline_usage: UsageMetrics,
    candidate_usage: UsageMetrics,
    baseline_cost: CostMetrics,
    candidate_cost: CostMetrics,
    baseline_quality: QualityMetrics,
    candidate_quality: QualityMetrics,
) -> tuple[DimensionComparison, ...]:
    groups: tuple[tuple[object, object, tuple[tuple[str, bool], ...]], ...] = (
        (baseline_structure, candidate_structure, (
            ("document_count", False), ("heading_count", False), ("section_count", False),
            ("word_count", False), ("line_count", False), ("table_count", False),
            ("required_section_count", False), ("required_section_covered_count", False),
        )),
        (baseline_provenance, candidate_provenance, (
            ("citation_count", False), ("resolved_citation_count", False),
            ("evidence_item_count", False), ("cited_evidence_item_count", False),
            ("unsupported_claim_count", True), ("extracted_fact_count", False),
            ("matched_extracted_fact_count", False),
        )),
        (baseline_timing, candidate_timing, (
            ("experiment_wall_ms", True), ("generation_wall_ms", True), ("queue_ms", True),
            ("snapshot_ms", True), ("validation_ms", True), ("provider_latency_sum_ms", True),
            ("provider_latency_p50_ms", True), ("provider_latency_p95_ms", True),
            ("critical_path_ms", True),
        )),
        (baseline_usage, candidate_usage, (
            ("input_tokens", True), ("output_tokens", True), ("cache_read_tokens", True),
            ("cache_write_tokens", True), ("provider_calls", True), ("tool_calls", True),
            ("tool_bytes", True), ("retries", True), ("repairs", True),
        )),
        (baseline_cost, candidate_cost, (("billed_cost_usd", True), ("normalized_cost_usd", True))),
        (baseline_quality, candidate_quality, (("historical_score", False), ("reevaluated_score", False))),
    )
    comparisons: list[DimensionComparison] = []
    for baseline, candidate, fields in groups:
        for name, lower_is_better in fields:
            baseline_value = getattr(baseline, name)
            candidate_value = getattr(candidate, name)
            # A value that neither branch measured is not a comparison dimension.
            if baseline_value is None and candidate_value is None:
                continue
            comparisons.append(compare_numeric_dimension(name, baseline_value, candidate_value, lower_is_better=lower_is_better))
    return tuple(comparisons)


def _comparability(results: tuple[DocumentEvaluationResult, ...]) -> ExperimentComparability:
    statuses = tuple(result.parity.comparability.semantic_status for result in results)
    limitations = tuple(
        dict.fromkeys(
            limitation for result in results for limitation in result.parity.comparability.limitations
        )
    )
    if statuses and all(status in {"exact", "matched"} for status in statuses):
        return ExperimentComparability("matched", True, limitations)
    if "different" in statuses:
        return ExperimentComparability("different", False, limitations)
    return ExperimentComparability("unknown", False, limitations)


def _verdict(
    *,
    document_results: tuple[DocumentEvaluationResult, ...],
    comparisons: tuple[DimensionComparison, ...],
    comparability: ExperimentComparability,
    baseline_output_claims: AcceptedOutputClaimValidation,
    candidate_output_claims: AcceptedOutputClaimValidation,
) -> ComparisonVerdict:
    failures: list[str] = []
    for result in document_results:
        if result.parity.baseline_contract.status == "failed":
            failures.append(f"baseline required output invalid: {result.document_id}")
        if result.parity.candidate_contract.status == "failed":
            failures.append(f"candidate required output invalid: {result.document_id}")
    for claims in (baseline_output_claims, candidate_output_claims):
        if claims.status == "failed":
            failures.extend(claims.limitations)
    if failures:
        return ComparisonVerdict("invalid", comparisons, tuple(failures), comparability.limitations)
    if not comparability.semantic_parity_confirmed:
        return ComparisonVerdict(
            "not_evaluated",
            comparisons,
            limitations=("overall candidate verdict requires confirmed semantic parity", *comparability.limitations),
        )
    incomparable = tuple(comparison.dimension for comparison in comparisons if comparison.verdict == "incomparable")
    if incomparable:
        return ComparisonVerdict(
            "not_evaluated",
            comparisons,
            limitations=("overall candidate verdict is unavailable while comparison dimensions are unknown", *incomparable),
        )
    observed = tuple(comparison.verdict for comparison in comparisons)
    if not observed:
        return ComparisonVerdict("not_evaluated", comparisons, limitations=("no measured comparison dimensions",))
    if all(verdict == "better" for verdict in observed):
        candidate_verdict = "better"
    elif all(verdict == "worse" for verdict in observed):
        candidate_verdict = "worse"
    else:
        # Equal measurements and a mix of directions are deliberately not "better".
        candidate_verdict = "mixed"
    return ComparisonVerdict(candidate_verdict, comparisons, limitations=comparability.limitations)


def evaluate_experiment(request: ExperimentEvaluationRequest) -> ExperimentEvaluationResult:
    """Evaluate all declared documents in a stable order, entirely offline."""
    document_inputs = tuple(sorted(request.documents, key=lambda document: document.document_id))
    documents = tuple(
        DocumentEvaluationResult(
            document_id=document.document_id,
            parity=evaluate_single_document_parity(document.parity),
            baseline_structure=document.baseline_structure,
            candidate_structure=document.candidate_structure,
        )
        for document in document_inputs
    )
    alignment = align_required_documents(
        request.required_document_ids,
        (result.parity.baseline.artifact for result in documents if result.parity.baseline.artifact is not None),
        (result.parity.candidate.artifact for result in documents if result.parity.candidate.artifact is not None),
    )
    baseline_structure = _aggregate_structure(result.baseline_structure for result in documents)
    candidate_structure = _aggregate_structure(result.candidate_structure for result in documents)
    baseline_provenance = _aggregate_provenance(result.parity.baseline.provenance for result in documents)
    candidate_provenance = _aggregate_provenance(result.parity.candidate.provenance for result in documents)
    baseline_timing = _aggregate_timing(request.baseline_run)
    candidate_timing = _aggregate_timing(request.candidate_run)
    baseline_usage = _aggregate_usage(request.baseline_run.attempts)
    candidate_usage = _aggregate_usage(request.candidate_run.attempts)
    baseline_cost = _aggregate_cost(request.baseline_run.attempts)
    candidate_cost = _aggregate_cost(request.candidate_run.attempts)
    baseline_output_claims = _validate_accepted_output_claims(
        "baseline", request.baseline_run.attempts, request.required_document_ids
    )
    candidate_output_claims = _validate_accepted_output_claims(
        "candidate", request.candidate_run.attempts, request.required_document_ids
    )
    comparability = _comparability(documents)
    comparisons = _numeric_dimensions(
        baseline_structure,
        candidate_structure,
        baseline_provenance,
        candidate_provenance,
        baseline_timing,
        candidate_timing,
        baseline_usage,
        candidate_usage,
        baseline_cost,
        candidate_cost,
        request.baseline_run.quality,
        request.candidate_run.quality,
    )
    verdict = _verdict(
        document_results=documents,
        comparisons=comparisons,
        comparability=comparability,
        baseline_output_claims=baseline_output_claims,
        candidate_output_claims=candidate_output_claims,
    )
    limitations = tuple(
        dict.fromkeys(
            (*comparability.limitations, *verdict.limitations, *verdict.blocking_failures)
        )
    )
    return ExperimentEvaluationResult(
        receipt=request.receipt,
        alignment=alignment,
        documents=documents,
        baseline_output_claims=baseline_output_claims,
        candidate_output_claims=candidate_output_claims,
        baseline_attempts=_attempt_summary(request.baseline_run.attempts),
        candidate_attempts=_attempt_summary(request.candidate_run.attempts),
        baseline_structure=baseline_structure,
        candidate_structure=candidate_structure,
        baseline_provenance=baseline_provenance,
        candidate_provenance=candidate_provenance,
        baseline_timing=baseline_timing,
        candidate_timing=candidate_timing,
        baseline_usage=baseline_usage,
        candidate_usage=candidate_usage,
        baseline_cost=baseline_cost,
        candidate_cost=candidate_cost,
        baseline_quality=request.baseline_run.quality,
        candidate_quality=request.candidate_run.quality,
        comparability=comparability,
        verdict=verdict,
        limitations=limitations,
    )
