"""Planner-neutral contracts for evidence access and answer synthesis."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class EvidenceSource(StrEnum):
    INDEXED_DOCUMENTATION = "indexed_documentation"
    GENERATED_DOCUMENTATION = "generated_documentation"
    RUNTIME_EVIDENCE = "runtime_evidence"
    CONNECTOR_EVIDENCE = "connector_evidence"


class EvidenceAccessPolicy(BaseModel):
    """Closed evidence boundary supplied by policy-aware callers."""

    model_config = ConfigDict(extra="forbid")

    allowed: list[EvidenceSource] = Field(default_factory=list)
    denied: list[EvidenceSource] = Field(default_factory=list)
    preferred: list[EvidenceSource] = Field(default_factory=list)
    required: list[EvidenceSource] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_sets(self) -> EvidenceAccessPolicy:
        allowed = set(self.allowed)
        denied = set(self.denied)
        if allowed & denied:
            raise ValueError("evidence sources cannot be both allowed and denied")
        if any(source not in allowed for source in (*self.preferred, *self.required)):
            raise ValueError("preferred and required evidence must be allowed")
        return self

    def allows(self, source: str | EvidenceSource) -> bool:
        normalized = EvidenceSource(source)
        return normalized in self.allowed and normalized not in self.denied


class EvidenceSynthesisPlan(BaseModel):
    """Narrow input required to validate and render an evidence answer."""

    model_config = ConfigDict(extra="forbid")

    objective: str = Field(min_length=1, max_length=2_000)
    output_contract: str = Field(min_length=1, max_length=120)
    answer_kind: str = Field(default="answer", max_length=80)
    evidence_policy: EvidenceAccessPolicy = Field(default_factory=EvidenceAccessPolicy)

    def allows(self, source: str | EvidenceSource) -> bool:
        return self.evidence_policy.allows(source)
