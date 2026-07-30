"""Pure, immutable contracts for agentic experiment evaluation.

This module deliberately contains no persistence, filesystem, Markdown parsing, or
provider logic.  It is the stable value boundary shared by future evaluators,
reports, and API adapters.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Literal


ArtifactBranch = Literal["shared", "baseline", "candidate"]
AlignmentStatus = Literal["aligned", "missing_baseline", "missing_candidate", "unmatched"]
ValidatorStatus = Literal["passed", "failed", "warning", "skipped", "not_available"]
DimensionVerdict = Literal["better", "mixed", "worse", "incomparable", "not_evaluated"]
CandidateVerdict = Literal["not_evaluated", "better", "mixed", "worse", "invalid"]


def _required(value: str, field_name: str) -> None:
    if not value.strip():
        raise ValueError(f"{field_name} must not be blank")


def _sha256(value: str, field_name: str) -> None:
    if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise ValueError(f"{field_name} must be a lowercase SHA-256 hex digest")


def _non_negative(value: int | None, field_name: str) -> None:
    if value is not None and value < 0:
        raise ValueError(f"{field_name} must be non-negative")


@dataclass(frozen=True, slots=True)
class ArtifactIdentity:
    """Immutable identity for a frozen artifact; paths are intentionally absent."""

    branch: ArtifactBranch
    kind: str
    logical_name: str
    sha256: str
    size_bytes: int
    media_type: str = "text/markdown"

    def __post_init__(self) -> None:
        _required(self.kind, "kind")
        _required(self.logical_name, "logical_name")
        _required(self.media_type, "media_type")
        _sha256(self.sha256, "sha256")
        _non_negative(self.size_bytes, "size_bytes")


@dataclass(frozen=True, slots=True)
class DocumentPair:
    """One deterministic document alignment, including deliberate absence."""

    document_id: str
    baseline: ArtifactIdentity | None
    candidate: ArtifactIdentity | None
    status: AlignmentStatus
    limitations: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _required(self.document_id, "document_id")
        if self.status == "aligned" and (self.baseline is None or self.candidate is None):
            raise ValueError("aligned documents require baseline and candidate artifacts")
        if self.status == "missing_baseline" and self.baseline is not None:
            raise ValueError("missing_baseline documents must not have a baseline artifact")
        if self.status == "missing_candidate" and self.candidate is not None:
            raise ValueError("missing_candidate documents must not have a candidate artifact")


@dataclass(frozen=True, slots=True)
class DocumentAlignment:
    """The frozen pairing basis used by all comparison dimensions."""

    required_document_ids: tuple[str, ...]
    pairs: tuple[DocumentPair, ...]

    def __post_init__(self) -> None:
        required = set(self.required_document_ids)
        pair_ids = [pair.document_id for pair in self.pairs]
        if any(not document_id.strip() for document_id in self.required_document_ids):
            raise ValueError("required_document_ids must not contain blank values")
        if len(required) != len(self.required_document_ids):
            raise ValueError("required_document_ids must be unique")
        if len(set(pair_ids)) != len(pair_ids):
            raise ValueError("document pairs must be unique by document_id")
        if not set(pair_ids).issubset(required):
            raise ValueError("document pairs must refer to required document IDs")


@dataclass(frozen=True, slots=True)
class StructuralMetrics:
    """Deterministic Markdown observations, not a quality or promotion score."""

    document_count: int
    heading_count: int
    section_count: int
    word_count: int
    line_count: int
    table_count: int
    required_section_count: int | None = None
    required_section_covered_count: int | None = None

    def __post_init__(self) -> None:
        for field_name in (
            "document_count",
            "heading_count",
            "section_count",
            "word_count",
            "line_count",
            "table_count",
            "required_section_count",
            "required_section_covered_count",
        ):
            _non_negative(getattr(self, field_name), field_name)
        if (
            self.required_section_count is not None
            and self.required_section_covered_count is not None
            and self.required_section_covered_count > self.required_section_count
        ):
            raise ValueError("required_section_covered_count cannot exceed required_section_count")


@dataclass(frozen=True, slots=True)
class ProvenanceMetrics:
    """Evidence and citation coverage observations, with unknowns kept explicit."""

    citation_count: int | None = None
    resolved_citation_count: int | None = None
    evidence_item_count: int | None = None
    cited_evidence_item_count: int | None = None
    unsupported_claim_count: int | None = None
    extracted_fact_count: int | None = None
    matched_extracted_fact_count: int | None = None

    def __post_init__(self) -> None:
        for field_name in (
            "citation_count",
            "resolved_citation_count",
            "evidence_item_count",
            "cited_evidence_item_count",
            "unsupported_claim_count",
            "extracted_fact_count",
            "matched_extracted_fact_count",
        ):
            _non_negative(getattr(self, field_name), field_name)
        for total_field, covered_field in (
            ("citation_count", "resolved_citation_count"),
            ("evidence_item_count", "cited_evidence_item_count"),
            ("extracted_fact_count", "matched_extracted_fact_count"),
        ):
            total = getattr(self, total_field)
            covered = getattr(self, covered_field)
            if total is not None and covered is not None and covered > total:
                raise ValueError(f"{covered_field} cannot exceed {total_field}")


@dataclass(frozen=True, slots=True)
class TimingMetrics:
    """Durations are milliseconds; absent values are genuinely unavailable."""

    experiment_wall_ms: int | None = None
    generation_wall_ms: int | None = None
    queue_ms: int | None = None
    snapshot_ms: int | None = None
    validation_ms: int | None = None
    provider_latency_sum_ms: int | None = None
    provider_latency_p50_ms: int | None = None
    provider_latency_p95_ms: int | None = None
    critical_path_ms: int | None = None
    critical_path_task_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for field_name in (
            "experiment_wall_ms",
            "generation_wall_ms",
            "queue_ms",
            "snapshot_ms",
            "validation_ms",
            "provider_latency_sum_ms",
            "provider_latency_p50_ms",
            "provider_latency_p95_ms",
            "critical_path_ms",
        ):
            _non_negative(getattr(self, field_name), field_name)
        if self.provider_latency_p50_ms is not None and self.provider_latency_p95_ms is not None:
            if self.provider_latency_p50_ms > self.provider_latency_p95_ms:
                raise ValueError("provider_latency_p50_ms cannot exceed provider_latency_p95_ms")
        if self.critical_path_task_ids and self.critical_path_ms is None:
            raise ValueError("critical_path_task_ids require critical_path_ms")
        if any(not task_id.strip() for task_id in self.critical_path_task_ids):
            raise ValueError("critical_path_task_ids must not contain blank values")


@dataclass(frozen=True, slots=True)
class UsageMetrics:
    """Attempt-inclusive operational usage; accepted-only values can be added later."""

    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None
    provider_calls: int | None = None
    tool_calls: int | None = None
    tool_bytes: int | None = None
    retries: int | None = None
    repairs: int | None = None

    def __post_init__(self) -> None:
        for field_name in (
            "input_tokens",
            "output_tokens",
            "cache_read_tokens",
            "cache_write_tokens",
            "provider_calls",
            "tool_calls",
            "tool_bytes",
            "retries",
            "repairs",
        ):
            _non_negative(getattr(self, field_name), field_name)


@dataclass(frozen=True, slots=True)
class PriceCardReceipt:
    """Identity of the approved frozen price card, never an inferred price."""

    price_card_id: str
    version: str
    sha256: str
    currency: str = "USD"

    def __post_init__(self) -> None:
        _required(self.price_card_id, "price_card_id")
        _required(self.version, "version")
        _required(self.currency, "currency")
        _sha256(self.sha256, "sha256")


@dataclass(frozen=True, slots=True)
class CostMetrics:
    """Provider billing and normalized cost remain distinct and independently nullable."""

    billed_cost_usd: Decimal | None = None
    normalized_cost_usd: Decimal | None = None
    price_card: PriceCardReceipt | None = None

    def __post_init__(self) -> None:
        for field_name in ("billed_cost_usd", "normalized_cost_usd"):
            value = getattr(self, field_name)
            if value is not None and value < Decimal("0"):
                raise ValueError(f"{field_name} must be non-negative")
        if self.normalized_cost_usd is not None and self.price_card is None:
            raise ValueError("normalized_cost_usd requires a price_card receipt")


@dataclass(frozen=True, slots=True)
class ValidatorResult:
    validator_id: str
    version: str
    status: ValidatorStatus
    message: str | None = None
    artifact_logical_names: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _required(self.validator_id, "validator_id")
        _required(self.version, "version")
        if any(not name.strip() for name in self.artifact_logical_names):
            raise ValueError("artifact_logical_names must not contain blank values")


@dataclass(frozen=True, slots=True)
class QualityMetrics:
    """Historical and rerun quality measurements must never be conflated."""

    historical_score: Decimal | None = None
    reevaluated_score: Decimal | None = None
    scorer_id: str | None = None
    scorer_version: str | None = None
    validator_results: tuple[ValidatorResult, ...] = ()

    def __post_init__(self) -> None:
        for field_name in ("historical_score", "reevaluated_score"):
            value = getattr(self, field_name)
            if value is not None and value < Decimal("0"):
                raise ValueError(f"{field_name} must be non-negative")
        if (self.scorer_id is None) != (self.scorer_version is None):
            raise ValueError("scorer_id and scorer_version must be supplied together")


@dataclass(frozen=True, slots=True)
class ComparabilityReceipt:
    """Hashes establish what was shared and what necessarily differed."""

    semantic_prompt_sha256: str | None = None
    context_manifest_sha256: str | None = None
    source_snapshot_sha256: str | None = None
    output_contract_sha256: str | None = None
    baseline_runtime_envelope_sha256: str | None = None
    candidate_runtime_envelope_sha256: str | None = None
    baseline_model_route: str | None = None
    candidate_model_route: str | None = None
    limitations: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for field_name in (
            "semantic_prompt_sha256",
            "context_manifest_sha256",
            "source_snapshot_sha256",
            "output_contract_sha256",
            "baseline_runtime_envelope_sha256",
            "candidate_runtime_envelope_sha256",
        ):
            value = getattr(self, field_name)
            if value is not None:
                _sha256(value, field_name)


@dataclass(frozen=True, slots=True)
class DimensionComparison:
    dimension: str
    verdict: DimensionVerdict
    limitations: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _required(self.dimension, "dimension")


@dataclass(frozen=True, slots=True)
class ComparisonVerdict:
    """Informational result only; this type has no publication side effect."""

    candidate_verdict: CandidateVerdict
    dimensions: tuple[DimensionComparison, ...] = ()
    blocking_failures: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        dimensions = [dimension.dimension for dimension in self.dimensions]
        if len(set(dimensions)) != len(dimensions):
            raise ValueError("comparison dimensions must be unique")


@dataclass(frozen=True, slots=True)
class EvaluationReceipt:
    """Versioned evaluator identity used to make reports traceable and reproducible."""

    evaluator_id: str
    evaluator_version: str
    code_sha256: str
    configuration_sha256: str

    def __post_init__(self) -> None:
        _required(self.evaluator_id, "evaluator_id")
        _required(self.evaluator_version, "evaluator_version")
        _sha256(self.code_sha256, "code_sha256")
        _sha256(self.configuration_sha256, "configuration_sha256")


@dataclass(frozen=True, slots=True)
class EvaluationResult:
    """Complete pure result envelope; later layers serialize it without mutation."""

    receipt: EvaluationReceipt
    alignment: DocumentAlignment
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
    comparability: ComparabilityReceipt
    verdict: ComparisonVerdict
    limitations: tuple[str, ...] = field(default_factory=tuple)
