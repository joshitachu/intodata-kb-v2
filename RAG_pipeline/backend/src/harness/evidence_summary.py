"""Deterministic current-turn evidence provenance for persisted answers."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from src.harness.capability_runtime import (
    CapabilityObservation,
    CapabilityObservationStatus,
)


EvidenceFamily = Literal[
    "topology", "documentation", "runtime", "metadata", "conversation", "connector", "other"
]
EvidenceRole = Literal[
    "structure",
    "explanation",
    "runtime_verification",
    "metadata",
    "conversation_context",
    "external_evidence",
    "supporting",
]
FailedSourceStatus = Literal["failed", "unavailable", "denied", "needs_input"]
ProviderAttemptStatus = Literal["failed", "succeeded"]


class SuccessfulEvidenceSource(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    family: EvidenceFamily
    role: EvidenceRole
    evidence_count: int = Field(ge=1)


class FailedEvidenceSource(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    family: EvidenceFamily
    status: FailedSourceStatus
    reason_code: str | None = Field(default=None, max_length=120)
    message: str | None = Field(default=None, max_length=500)


class DocumentationProviderAttempt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: str = Field(min_length=1, max_length=80)
    status: ProviderAttemptStatus


class DocumentationEvidenceMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider_used: str | None = Field(default=None, max_length=80)
    fallback_used: bool = False
    attempts: list[DocumentationProviderAttempt] = Field(default_factory=list, max_length=10)


class EvidenceSummary(BaseModel):
    """Versioned provenance derived only from the current turn's observations."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal["1.0"] = "1.0"
    evidence_step_count: int = Field(default=0, ge=0)
    successful_sources: list[SuccessfulEvidenceSource] = Field(default_factory=list)
    failed_sources: list[FailedEvidenceSource] = Field(default_factory=list)
    zero_evidence: bool = True
    documentation: DocumentationEvidenceMetadata = Field(
        default_factory=DocumentationEvidenceMetadata
    )


def empty_evidence_summary() -> EvidenceSummary:
    return EvidenceSummary()


def build_evidence_summary(
    observations: Sequence[CapabilityObservation],
    *,
    documentation_trace: Mapping[str, Any] | None = None,
    document_source_count: int = 0,
) -> EvidenceSummary:
    """Build one answer's provenance without consulting conversation history.

    Attempts and successful calls with zero references never inflate the evidence
    count. Repeated reads are counted because they are distinct validated evidence
    steps in this turn; source-family rows are aggregated for compact persistence.

    ``document_source_count`` covers evidence that never appears in an
    observation. A trusted documentation answer hands its content and document
    sources straight to the turn loop (see ``harness.trusted_answer``), which by
    design bypasses the self-reported grounding block -- so its sources are
    absent from ``evidence_refs`` and, counted from observations alone, a
    correctly sourced answer reported ``zero_evidence``. Pass the count of
    document sources actually delivered with the answer.
    """

    counts: dict[tuple[EvidenceFamily, EvidenceRole], int] = defaultdict(int)
    failures: dict[tuple[EvidenceFamily, FailedSourceStatus, str | None], FailedEvidenceSource] = {}
    for observation in observations:
        family, role = _family_and_role(observation)
        if observation.succeeded:
            count = len([item for item in observation.evidence_refs if isinstance(item, dict)])
            if count:
                counts[(family, role)] += count
            continue
        if not _is_evidence_observation(observation):
            continue
        status = _failed_status(observation)
        reason = _clean(observation.error_code, 120)
        # Observation text is normally sanitized by the semantic gateway, but the
        # persisted provenance contract must remain safe even when called directly
        # by tests or another runtime. Persist a stable server phrase, not provider
        # exception prose that may contain credentials or request fragments.
        message = "The evidence source could not answer this request."
        failures[(family, status, reason)] = FailedEvidenceSource(
            family=family,
            status=status,
            reason_code=reason,
            message=message,
        )

    # Only when the observations carried no documentation evidence of their own:
    # if the capability did report refs, those are the authoritative count and
    # adding the delivered sources on top would double-count one lookup.
    if document_source_count > 0 and not counts[("documentation", "explanation")]:
        counts[("documentation", "explanation")] = document_source_count

    successful = [
        SuccessfulEvidenceSource(family=family, role=role, evidence_count=count)
        for (family, role), count in sorted(counts.items())
        if count
    ]
    provider_metadata = _documentation_metadata(documentation_trace)
    if provider_metadata.attempts and provider_metadata.provider_used is None:
        failures = {
            key: value for key, value in failures.items() if key[0] != "documentation"
        }
        failures.setdefault(
            ("documentation", "unavailable", "providers_exhausted"),
            FailedEvidenceSource(
                family="documentation",
                status="unavailable",
                reason_code="providers_exhausted",
                message="Documentation is temporarily unavailable.",
            ),
        )
    total = sum(item.evidence_count for item in successful)
    return EvidenceSummary(
        evidence_step_count=total,
        successful_sources=successful,
        failed_sources=list(failures.values()),
        zero_evidence=total == 0,
        documentation=provider_metadata,
    )


def _is_evidence_observation(observation: CapabilityObservation) -> bool:
    capability_id = observation.capability_id
    return any(
        marker in capability_id
        for marker in ("topology", "rag.docs", "runtime.", "metadata", "conversation", "connector")
    ) or bool(observation.evidence_refs)


def _family_and_role(
    observation: CapabilityObservation,
) -> tuple[EvidenceFamily, EvidenceRole]:
    capability_id = observation.capability_id
    source_types = {
        str(item.get("source_type", ""))
        for item in observation.evidence_refs
        if isinstance(item, dict)
    }
    if "topology" in capability_id or "topology_structure" in source_types:
        return "topology", "structure"
    if capability_id.startswith("rag.docs") or any("document" in item for item in source_types):
        return "documentation", "explanation"
    if capability_id.startswith("runtime.") or any("runtime" in item or "log" in item for item in source_types):
        return "runtime", "runtime_verification"
    if "metadata" in capability_id or "structured_metadata" in source_types:
        return "metadata", "metadata"
    if "conversation" in capability_id or "conversation_message" in source_types:
        return "conversation", "conversation_context"
    if "connector" in capability_id or "connector" in source_types:
        return "connector", "external_evidence"
    return "other", "supporting"


def _failed_status(observation: CapabilityObservation) -> FailedSourceStatus:
    if observation.status is CapabilityObservationStatus.DENIED:
        return "denied"
    if observation.status is CapabilityObservationStatus.NEEDS_INPUT:
        return "needs_input"
    if observation.error_code == "unavailable":
        return "unavailable"
    return "failed"


def _documentation_metadata(
    trace: Mapping[str, Any] | None,
) -> DocumentationEvidenceMetadata:
    if not isinstance(trace, Mapping):
        return DocumentationEvidenceMetadata()
    raw_attempts = trace.get("attempts")
    attempts: list[DocumentationProviderAttempt] = []
    if isinstance(raw_attempts, list):
        for item in raw_attempts[:10]:
            if not isinstance(item, Mapping):
                continue
            provider = _clean(item.get("provider"), 80)
            status = item.get("status")
            if provider and status in {"failed", "succeeded"}:
                attempts.append(DocumentationProviderAttempt(provider=provider, status=status))
    provider_used = _clean(trace.get("provider_used"), 80)
    return DocumentationEvidenceMetadata(
        provider_used=provider_used,
        fallback_used=bool(trace.get("fallback_used")),
        attempts=attempts,
    )


def _clean(value: Any, limit: int) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return " ".join(value.split())[:limit]


__all__ = [
    "DocumentationEvidenceMetadata",
    "DocumentationProviderAttempt",
    "EvidenceSummary",
    "FailedEvidenceSource",
    "SuccessfulEvidenceSource",
    "build_evidence_summary",
    "empty_evidence_summary",
]
