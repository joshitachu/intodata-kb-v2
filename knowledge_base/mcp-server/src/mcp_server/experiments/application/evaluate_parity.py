"""Pure single-document parity evaluation with explicit comparability boundaries.

This use case neither executes a runtime nor reads artifacts.  It compares
frozen observations only, retains unavailable historical values as unknown, and
keeps transient prompt/runtime data out of the durable result.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, field, replace
import hashlib
from typing import Any, Literal

from mcp_server.experiments.domain.evaluation import (
    ArtifactIdentity,
    ComparisonVerdict,
    CostMetrics,
    DimensionComparison,
    ProvenanceMetrics,
    TimingMetrics,
    UsageMetrics,
)
from mcp_server.experiments.domain.runtime import OutputContract
from mcp_server.experiments.infrastructure.storage.canonical_json import canonical_json_bytes
from mcp_server.experiments.infrastructure.validation.report import compare_numeric_dimension


ComparisonStatus = Literal["exact", "matched", "different", "unknown"]
ContractStatus = Literal["passed", "failed", "not_applicable"]


def _sha256(value: str, field_name: str) -> None:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError(f"{field_name} must be a lowercase SHA-256 hex digest")


def _fingerprint_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _fingerprint_envelope(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json_bytes(dict(value))).hexdigest()


def fingerprint_output_contract(contract: OutputContract) -> str:
    """Hash the exact declared contract; its value is never inferred from output."""
    return hashlib.sha256(canonical_json_bytes(asdict(contract))).hexdigest()


@dataclass(frozen=True, slots=True)
class ContractCheck:
    """One explicit condition from a declared runtime output contract."""

    field: str
    status: ContractStatus
    detail: str


@dataclass(frozen=True, slots=True)
class OutputContractValidation:
    """Validation result for one branch; optional outputs remain explicit."""

    status: ContractStatus
    checks: tuple[ContractCheck, ...]


@dataclass(frozen=True, slots=True)
class ComparabilityField:
    """A non-sensitive comparison of one frozen execution identity."""

    name: str
    status: ComparisonStatus
    baseline_fingerprint: str | None = None
    candidate_fingerprint: str | None = None


@dataclass(frozen=True, slots=True)
class PromptRuntimeComparabilityReceipt:
    """Semantic parity excludes runtime-envelope drift but records it explicitly."""

    semantic_prompt_sha256: ComparabilityField
    context_manifest_sha256: ComparabilityField
    source_snapshot_sha256: ComparabilityField
    output_contract_sha256: ComparabilityField
    model_route_options_sha256: ComparabilityField
    runtime_envelope_sha256: ComparabilityField
    semantic_status: ComparisonStatus
    runtime_status: ComparisonStatus
    semantic_parity_confirmed: bool
    limitations: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ParitySide:
    """Durable, non-sensitive observations for one branch of a document output."""

    artifact: ArtifactIdentity | None
    semantic_prompt_sha256: str | None = None
    context_manifest_sha256: str | None = None
    source_snapshot_sha256: str | None = None
    output_contract_sha256: str | None = None
    model_route_options_sha256: str | None = None
    runtime_envelope_sha256: str | None = None
    provenance: ProvenanceMetrics = field(default_factory=ProvenanceMetrics)
    timing: TimingMetrics = field(default_factory=TimingMetrics)
    usage: UsageMetrics = field(default_factory=UsageMetrics)
    cost: CostMetrics = field(default_factory=CostMetrics)

    def __post_init__(self) -> None:
        for field_name in (
            "semantic_prompt_sha256",
            "context_manifest_sha256",
            "source_snapshot_sha256",
            "output_contract_sha256",
            "model_route_options_sha256",
            "runtime_envelope_sha256",
        ):
            value = getattr(self, field_name)
            if value is not None:
                _sha256(value, field_name)


@dataclass(frozen=True, slots=True)
class TransientParityMaterial:
    """Optional raw data used only to validate supplied fingerprints during evaluation."""

    semantic_prompt: str | None = None
    runtime_envelope: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        if self.semantic_prompt is not None and not self.semantic_prompt.strip():
            raise ValueError("semantic_prompt must not be blank when supplied")


@dataclass(frozen=True, slots=True)
class SingleDocumentParityRequest:
    """Inputs for one declared output; raw material is transient and never returned."""

    document_id: str
    output_contract: OutputContract
    baseline: ParitySide
    candidate: ParitySide
    baseline_transient: TransientParityMaterial = field(default_factory=TransientParityMaterial)
    candidate_transient: TransientParityMaterial = field(default_factory=TransientParityMaterial)

    def __post_init__(self) -> None:
        if not self.document_id.strip():
            raise ValueError("document_id must not be blank")
        if not self.output_contract.artifact_name.strip():
            raise ValueError("output contract artifact_name must not be blank")
        if not self.output_contract.media_type.strip():
            raise ValueError("output contract media_type must not be blank")
        if self.output_contract.max_bytes is not None and self.output_contract.max_bytes < 0:
            raise ValueError("output contract max_bytes must be non-negative")


@dataclass(frozen=True, slots=True)
class SingleDocumentParityResult:
    """Durable deterministic payload; it contains fingerprints and metrics, never raw inputs."""

    document_id: str
    output_contract: OutputContract
    baseline_contract: OutputContractValidation
    candidate_contract: OutputContractValidation
    comparability: PromptRuntimeComparabilityReceipt
    baseline: ParitySide
    candidate: ParitySide
    comparisons: tuple[DimensionComparison, ...]
    verdict: ComparisonVerdict
    limitations: tuple[str, ...]


def validate_output_contract(
    contract: OutputContract,
    artifact: ArtifactIdentity | None,
    *,
    expected_branch: Literal["baseline", "candidate"] | None = None,
) -> OutputContractValidation:
    """Validate output facts and branch identity without replacing absent values."""
    if not contract.artifact_name.strip() or not contract.media_type.strip():
        raise ValueError("declared output contract must have non-blank name and media type")
    if contract.max_bytes is not None and contract.max_bytes < 0:
        raise ValueError("declared output contract max_bytes must be non-negative")
    if artifact is None:
        if contract.required:
            return OutputContractValidation(
                "failed", (ContractCheck("artifact_present", "failed", "required artifact is absent"),)
            )
        return OutputContractValidation(
            "not_applicable",
            (ContractCheck("artifact_present", "not_applicable", "optional artifact is absent"),),
        )

    checks = [ContractCheck("artifact_present", "passed", "artifact is present")]
    if expected_branch is not None:
        checks.append(
            ContractCheck(
                "artifact_branch",
                "passed" if artifact.branch == expected_branch else "failed",
                "matches expected branch"
                if artifact.branch == expected_branch
                else f"must be on the {expected_branch} branch",
            )
        )
    checks.extend(
        (
            ContractCheck(
                "artifact_name",
                "passed" if artifact.logical_name == contract.artifact_name else "failed",
                "matches declared artifact name"
                if artifact.logical_name == contract.artifact_name
                else "does not match declared artifact name",
            ),
            ContractCheck(
                "media_type",
                "passed" if artifact.media_type == contract.media_type else "failed",
                "matches declared media type"
                if artifact.media_type == contract.media_type
                else "does not match declared media type",
            ),
        )
    )
    if contract.max_bytes is not None:
        checks.append(
            ContractCheck(
                "max_bytes",
                "passed" if artifact.size_bytes <= contract.max_bytes else "failed",
                "within declared maximum size"
                if artifact.size_bytes <= contract.max_bytes
                else "exceeds declared maximum size",
            )
        )
    return OutputContractValidation(
        "failed" if any(check.status == "failed" for check in checks) else "passed", tuple(checks)
    )


def _validate_transient(side: ParitySide, transient: TransientParityMaterial) -> ParitySide:
    """Derive missing fingerprints transiently and reject any forged claimed hash."""
    prompt_hash = None if transient.semantic_prompt is None else _fingerprint_text(transient.semantic_prompt)
    envelope_hash = (
        None if transient.runtime_envelope is None else _fingerprint_envelope(transient.runtime_envelope)
    )
    if side.semantic_prompt_sha256 is not None and prompt_hash is not None and side.semantic_prompt_sha256 != prompt_hash:
        raise ValueError("semantic_prompt_sha256 does not match transient semantic_prompt")
    if side.runtime_envelope_sha256 is not None and envelope_hash is not None and side.runtime_envelope_sha256 != envelope_hash:
        raise ValueError("runtime_envelope_sha256 does not match transient runtime_envelope")
    return replace(
        side,
        semantic_prompt_sha256=side.semantic_prompt_sha256 or prompt_hash,
        runtime_envelope_sha256=side.runtime_envelope_sha256 or envelope_hash,
    )


def _compare_fingerprint(
    name: str, baseline: str | None, candidate: str | None
) -> ComparabilityField:
    if baseline is None or candidate is None:
        return ComparabilityField(name, "unknown", baseline, candidate)
    return ComparabilityField(name, "matched" if baseline == candidate else "different", baseline, candidate)


def _comparison_receipt(baseline: ParitySide, candidate: ParitySide) -> PromptRuntimeComparabilityReceipt:
    prompt = _compare_fingerprint(
        "semantic_prompt_sha256", baseline.semantic_prompt_sha256, candidate.semantic_prompt_sha256
    )
    context = _compare_fingerprint(
        "context_manifest_sha256", baseline.context_manifest_sha256, candidate.context_manifest_sha256
    )
    source = _compare_fingerprint(
        "source_snapshot_sha256", baseline.source_snapshot_sha256, candidate.source_snapshot_sha256
    )
    contract = _compare_fingerprint(
        "output_contract_sha256", baseline.output_contract_sha256, candidate.output_contract_sha256
    )
    route_options = _compare_fingerprint(
        "model_route_options_sha256",
        baseline.model_route_options_sha256,
        candidate.model_route_options_sha256,
    )
    runtime = _compare_fingerprint(
        "runtime_envelope_sha256", baseline.runtime_envelope_sha256, candidate.runtime_envelope_sha256
    )
    semantic_fields = (prompt, context, source, contract, route_options)
    if all(field.status == "matched" for field in semantic_fields):
        semantic_status: ComparisonStatus = "matched"
    elif any(field.status == "different" for field in semantic_fields):
        semantic_status = "different"
    else:
        semantic_status = "unknown"
    limitations = tuple(
        f"{field.name} is {field.status}"
        for field in (*semantic_fields, runtime)
        if field.status in {"different", "unknown"}
    )
    return PromptRuntimeComparabilityReceipt(
        semantic_prompt_sha256=prompt,
        context_manifest_sha256=context,
        source_snapshot_sha256=source,
        output_contract_sha256=contract,
        model_route_options_sha256=route_options,
        runtime_envelope_sha256=runtime,
        semantic_status=semantic_status,
        runtime_status=runtime.status,
        semantic_parity_confirmed=semantic_status in {"exact", "matched"},
        limitations=limitations,
    )


def _metric_comparisons(baseline: ParitySide, candidate: ParitySide) -> tuple[DimensionComparison, ...]:
    return (
        compare_numeric_dimension("input_tokens", baseline.usage.input_tokens, candidate.usage.input_tokens, lower_is_better=True),
        compare_numeric_dimension("output_tokens", baseline.usage.output_tokens, candidate.usage.output_tokens, lower_is_better=True),
        compare_numeric_dimension("wall_ms", baseline.timing.experiment_wall_ms, candidate.timing.experiment_wall_ms, lower_is_better=True),
        compare_numeric_dimension("provider_latency_sum_ms", baseline.timing.provider_latency_sum_ms, candidate.timing.provider_latency_sum_ms, lower_is_better=True),
        compare_numeric_dimension("tool_calls", baseline.usage.tool_calls, candidate.usage.tool_calls, lower_is_better=True),
        compare_numeric_dimension("normalized_cost_usd", baseline.cost.normalized_cost_usd, candidate.cost.normalized_cost_usd, lower_is_better=True),
        compare_numeric_dimension("provider_reported_cost_usd", baseline.cost.billed_cost_usd, candidate.cost.billed_cost_usd, lower_is_better=True),
    )


def _verdict(
    baseline_contract: OutputContractValidation,
    candidate_contract: OutputContractValidation,
    comparisons: tuple[DimensionComparison, ...],
    comparability: PromptRuntimeComparabilityReceipt,
) -> ComparisonVerdict:
    failures = tuple(
        branch + " output contract validation failed"
        for branch, validation in (("baseline", baseline_contract), ("candidate", candidate_contract))
        if validation.status == "failed"
    )
    if failures:
        return ComparisonVerdict("invalid", comparisons, blocking_failures=failures, limitations=comparability.limitations)
    if not comparability.semantic_parity_confirmed:
        return ComparisonVerdict(
            "not_evaluated",
            comparisons,
            limitations=(
                "overall candidate verdict requires confirmed semantic parity",
                *comparability.limitations,
            ),
        )
    observed = [comparison.verdict for comparison in comparisons if comparison.verdict != "incomparable"]
    if not observed:
        candidate_verdict = "not_evaluated"
    elif "better" in observed and "worse" in observed:
        candidate_verdict = "mixed"
    elif "worse" in observed:
        candidate_verdict = "worse"
    elif "better" in observed:
        candidate_verdict = "better"
    else:
        candidate_verdict = "mixed"
    return ComparisonVerdict(candidate_verdict, comparisons, limitations=comparability.limitations)


def evaluate_single_document_parity(request: SingleDocumentParityRequest) -> SingleDocumentParityResult:
    """Evaluate one output; only validated fingerprints are retained in the result."""
    baseline = _validate_transient(request.baseline, request.baseline_transient)
    candidate = _validate_transient(request.candidate, request.candidate_transient)
    baseline_contract = validate_output_contract(request.output_contract, baseline.artifact, expected_branch="baseline")
    candidate_contract = validate_output_contract(request.output_contract, candidate.artifact, expected_branch="candidate")
    comparability = _comparison_receipt(baseline, candidate)
    comparisons = _metric_comparisons(baseline, candidate)
    verdict = _verdict(baseline_contract, candidate_contract, comparisons, comparability)
    limitations = tuple(dict.fromkeys((*comparability.limitations, *verdict.limitations, *verdict.blocking_failures)))
    return SingleDocumentParityResult(
        document_id=request.document_id,
        output_contract=request.output_contract,
        baseline_contract=baseline_contract,
        candidate_contract=candidate_contract,
        comparability=comparability,
        baseline=baseline,
        candidate=candidate,
        comparisons=comparisons,
        verdict=verdict,
        limitations=limitations,
    )
