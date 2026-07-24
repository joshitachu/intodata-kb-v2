"""Strict, content-free evidence schemas for Slice 8 canary operations."""

from __future__ import annotations

import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.agent_runtime_canary import CanaryStage


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _verified_text(value: str, field: str) -> str:
    normalized = value.strip()
    if not normalized or normalized.casefold() in {
        "todo", "tbd", "template", "synthetic", "unverified", "placeholder",
    }:
        raise ValueError(f"{field} must contain verified environment evidence")
    return normalized


class CanaryReleaseIdentity(StrictModel):
    application_revision: str
    database_revision: str
    image_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    config_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    lockfile_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    sbom_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def verified_identity(self) -> "CanaryReleaseIdentity":
        for field in ("application_revision", "database_revision", "image_digest"):
            _verified_text(str(getattr(self, field)), field)
        return self


class CanaryTurnSample(StrictModel):
    audit_id: int = Field(gt=0)
    canary_stage: CanaryStage
    runtime_cohort: str
    transport: Literal["stream", "nonstream"]
    terminal_action: str | None = None
    stop_reason: str | None = None
    capability_ids: list[str] = Field(default_factory=list, max_length=12)
    observation_statuses: list[str] = Field(default_factory=list, max_length=12)
    repeated: bool = False
    clarified: bool = False
    denied: bool = False
    stale_workflow: bool = False
    state_conflict: bool = False
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    tool_calls: int = Field(ge=0)
    cost_usd: float = Field(ge=0)
    latency_ms: int = Field(ge=0)
    observed_at: datetime.datetime


class SafetyReview(StrictModel):
    unauthorized_effects: int = Field(ge=0)
    cross_scope_evidence: int = Field(ge=0)
    duplicate_effects: int = Field(ge=0)
    secret_exposures: int = Field(ge=0)
    unsupported_claims: int = Field(ge=0)
    reviewer: str
    reviewed_at: datetime.datetime
    evidence_uri: str
    signature_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def verified_review(self) -> "SafetyReview":
        _verified_text(self.reviewer, "reviewer")
        _verified_text(self.evidence_uri, "evidence_uri")
        return self


class QualityReview(StrictModel):
    reviewed_turns: int = Field(ge=0)
    passed_turns: int = Field(ge=0)
    denial_turns: int = Field(ge=0)
    correct_denials: int = Field(ge=0)
    no_evidence_turns: int = Field(ge=0)
    correct_no_evidence_turns: int = Field(ge=0)
    unexpected_clarifications: int = Field(ge=0)
    repeated_outputs: int = Field(ge=0)
    rubric_version: str
    reviewer: str
    reviewed_at: datetime.datetime
    evidence_uri: str
    signature_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def counts_are_bounded(self) -> "QualityReview":
        if self.passed_turns > self.reviewed_turns:
            raise ValueError("passed_turns exceeds reviewed_turns")
        if self.correct_denials > self.denial_turns:
            raise ValueError("correct_denials exceeds denial_turns")
        if self.correct_no_evidence_turns > self.no_evidence_turns:
            raise ValueError("correct_no_evidence_turns exceeds no_evidence_turns")
        _verified_text(self.rubric_version, "rubric_version")
        _verified_text(self.reviewer, "reviewer")
        _verified_text(self.evidence_uri, "evidence_uri")
        return self


class BackupParityRecord(StrictModel):
    backup_id: str
    environment: str
    application_revision: str
    database_revision: str
    created_at: datetime.datetime
    restore_verified_at: datetime.datetime
    parity_checks: list[str] = Field(min_length=1)
    signer: str
    signature_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def verified_backup(self) -> "BackupParityRecord":
        for field in ("backup_id", "environment", "application_revision", "database_revision", "signer"):
            _verified_text(str(getattr(self, field)), field)
        if self.restore_verified_at < self.created_at:
            raise ValueError("backup restore verification predates backup")
        if any(not item.strip() for item in self.parity_checks):
            raise ValueError("parity checks must be named")
        return self


class RollbackRehearsalRecord(StrictModel):
    rehearsal_id: str
    application_revision: str
    database_revision: str
    from_stage: CanaryStage
    to_stage: Literal["off"] = "off"
    started_at: datetime.datetime
    finished_at: datetime.datetime
    config_restored: bool
    health_check_passed: bool
    state_reconciled: bool
    duplicate_effects: int = Field(ge=0)
    signer: str
    evidence_uri: str
    signature_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def verified_rehearsal(self) -> "RollbackRehearsalRecord":
        for field in ("rehearsal_id", "application_revision", "database_revision", "signer", "evidence_uri"):
            _verified_text(str(getattr(self, field)), field)
        if self.finished_at <= self.started_at:
            raise ValueError("rollback rehearsal must have positive duration")
        return self


class CanaryWindowEvidence(StrictModel):
    schema_version: Literal["1.0.0"] = "1.0.0"
    identity: CanaryReleaseIdentity
    samples: list[CanaryTurnSample]
    safety: SafetyReview
    quality: QualityReview
    backup: BackupParityRecord | None = None
    rollback: RollbackRehearsalRecord | None = None
    restart_suite_passed: bool = False
    transport_parity_passed: bool = False
    full_cutover_owner: str | None = None
    full_cutover_decision: Literal["accept", "reject"] | None = None

    @model_validator(mode="after")
    def unique_audits_and_identity_parity(self) -> "CanaryWindowEvidence":
        ids = [item.audit_id for item in self.samples]
        if len(ids) != len(set(ids)):
            raise ValueError("canary audit IDs must be unique")
        for record in (self.backup, self.rollback):
            if record is None:
                continue
            if record.application_revision != self.identity.application_revision:
                raise ValueError("operator record application revision mismatch")
            if record.database_revision != self.identity.database_revision:
                raise ValueError("operator record database revision mismatch")
        return self


class CanaryEvidenceInputs(StrictModel):
    """Operator-owned facts combined with database-derived turn samples."""

    identity: CanaryReleaseIdentity
    safety: SafetyReview
    quality: QualityReview
    backup: BackupParityRecord | None = None
    rollback: RollbackRehearsalRecord | None = None
    restart_suite_passed: bool = False
    transport_parity_passed: bool = False
    full_cutover_owner: str | None = None
    full_cutover_decision: Literal["accept", "reject"] | None = None


class CanaryVerdict(StrictModel):
    verdict: Literal["GO", "NO_GO"]
    blockers: list[str]
    total_turns: int
    consecutive_hours: float
    stage_counts: dict[str, int]
    transport_counts: dict[str, int]
    quality_rate: float
    denial_correctness: float
    no_evidence_correctness: float
    unexpected_clarification_rate: float
    repeat_rate: float
    p95_latency_ms: float
    mean_cost_usd: float
    p95_cost_usd: float
    max_cost_usd: float
