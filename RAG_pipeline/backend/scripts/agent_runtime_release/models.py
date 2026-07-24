"""Versioned, fail-closed schemas for Slice 7 release evidence."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AttemptEvidence(StrictModel):
    attempt_id: str
    scenario_id: str
    prompt_id: str
    family: str
    authenticated: bool
    target: Literal["authenticated-public-api", "scripted", "recorded"]
    passed: bool
    deterministic_failures: list[str] = Field(default_factory=list)
    taxonomy: list[str] = Field(default_factory=list)
    assistant_text: str
    review_rationale: str
    tool_trajectory: list[str] = Field(default_factory=list)
    safe_arguments: list[dict] = Field(default_factory=list)
    latency_ms: float = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0
    clarification_expected: bool = False
    clarified: bool = False
    unsupported_claims: int = 0


class HumanRating(StrictModel):
    attempt_id: str
    rater_id: str
    rubric_version: str
    score: float = Field(ge=1, le=5)


class DependencyEvidence(StrictModel):
    lockfile_sha256: str
    sbom_sha256: str
    scanner: str
    critical: int = 0
    high: int = 0
    accepted_finding_ids: list[str] = Field(default_factory=list)


class ReleaseEvidence(StrictModel):
    schema_version: Literal["1.0.0"] = "1.0.0"
    gate_profile: Literal["slice7-bounded-50-v1", "production-1000-v1"]
    corpus_version: str
    application_revision: str
    runtime_version: str
    model_profile: str
    prompt_version: str
    manifest_sha256: str
    corpus_sha256: str
    checkpoint_serialization_review: Literal["pass", "fail", "missing"]
    attempts: list[AttemptEvidence]
    ratings: list[HumanRating]
    dependency: DependencyEvidence | None = None
    release_owner: str | None = None
    release_owner_decision: Literal["accept", "reject"] | None = None
    reliability: float | None = None

    @model_validator(mode="after")
    def unique_attempts(self) -> "ReleaseEvidence":
        ids = [item.attempt_id for item in self.attempts]
        if len(ids) != len(set(ids)):
            raise ValueError("attempt IDs must be unique")
        return self


class ReleaseVerdict(StrictModel):
    verdict: Literal["GO", "NO_GO"]
    blockers: list[str]
    total_attempts: int
    passed_attempts: int
    point_accuracy: float
    wilson_lower_95: float
    family_accuracy: dict[str, float]
    clarification_precision: float | None
    clarification_recall: float | None
    rated_answers: int
    mean_rating: float | None
    family_mean_rating: dict[str, float]
    failed_attempt_ids: list[str]
    failures_by_taxonomy: dict[str, int]
    latency_ms: dict[str, float]
    tokens: dict[str, int]
    cost_usd: float


class CaptureReview(StrictModel):
    """Deterministic review of a sanitized authenticated capture checkpoint."""

    schema_version: Literal["1.0.0"] = "1.0.0"
    attempts: list[AttemptEvidence]
    total_attempts: int
    passed_attempts: int
    failed_attempts: int

    @model_validator(mode="after")
    def totals_match_attempts(self) -> "CaptureReview":
        passed = sum(item.passed for item in self.attempts)
        if self.total_attempts != len(self.attempts):
            raise ValueError("capture review total does not match attempts")
        if self.passed_attempts != passed or self.failed_attempts != len(self.attempts) - passed:
            raise ValueError("capture review pass/fail totals do not match attempts")
        return self
