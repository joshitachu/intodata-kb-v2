"""Framework- and ORM-free value objects for experiment persistence ports."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping
from uuid import UUID, uuid4

from mcp_server.experiments.domain.statuses import ExperimentStatus, require_transition


def utc_now() -> datetime:
    """Return an aware UTC timestamp without coupling domain contracts to an ORM."""

    return datetime.now(timezone.utc)


def new_experiment_id() -> str:
    """Create the canonical string UUID used for experiment identity."""

    return str(uuid4())


def require_uuid(value: str) -> str:
    """Validate and canonicalize an experiment UUID string."""

    try:
        return str(UUID(value))
    except (TypeError, ValueError) as exc:
        raise ValueError("experiment_id must be a UUID string") from exc


def require_safe_identifier(value: str, *, field_name: str) -> str:
    """Allow simple path-segment identifiers only.

    Project and logical artifact identifiers become filesystem paths downstream;
    validating them in the domain avoids a second, surprising interpretation later.
    """

    cleaned = value.strip()
    if not cleaned or cleaned in {".", ".."} or "/" in cleaned or "\\" in cleaned:
        raise ValueError(f"{field_name} must be a non-empty path segment")
    if "\x00" in cleaned:
        raise ValueError(f"{field_name} must not contain a NUL byte")
    return cleaned


@dataclass(frozen=True)
class ExperimentArtifact:
    """An immutable artifact index entry; content remains on the filesystem."""

    experiment_id: str
    branch: str
    logical_name: str
    relative_path: str
    sha256: str
    size_bytes: int
    media_type: str
    kind: str = "document"
    task_id: str | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        require_uuid(self.experiment_id)
        for field_name, value in (
            ("branch", self.branch),
            ("logical_name", self.logical_name),
            ("relative_path", self.relative_path),
            ("kind", self.kind),
            ("media_type", self.media_type),
        ):
            if not value or "\x00" in value:
                raise ValueError(f"{field_name} must be non-empty and NUL-free")
        if len(self.sha256) != 64 or any(char not in "0123456789abcdef" for char in self.sha256):
            raise ValueError("sha256 must be a lowercase SHA-256 hex digest")
        if self.size_bytes < 0:
            raise ValueError("size_bytes must not be negative")


@dataclass(frozen=True)
class BaselineSupportingArtifact:
    """A repository-approved legacy receipt eligible for baseline copying."""

    logical_name: str
    source_path: Path
    kind: str
    media_type: str = "application/json"

    def __post_init__(self) -> None:
        if self.kind not in {"metrics", "manifest", "receipt"}:
            raise ValueError("supporting artifact kind must be metrics, manifest, or receipt")
        if not self.logical_name or "\x00" in self.logical_name:
            raise ValueError("supporting artifact logical_name must be non-empty and NUL-free")


@dataclass(frozen=True)
class LegacyBaselineRun:
    """Read-only legacy baseline selected through an experiment repository port."""

    id: int
    project_id: str
    completed_at: datetime
    documents_directory: Path
    supporting_artifacts: tuple[BaselineSupportingArtifact, ...] = ()
    metrics: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.id <= 0:
            raise ValueError("legacy baseline run id must be positive")
        require_safe_identifier(self.project_id, field_name="project_id")


@dataclass(frozen=True)
class GenerationExperiment:
    """Experiment lifecycle root, independent from legacy ``GenerateRun`` rows."""

    id: str
    project_id: str
    baseline_run_id: int
    idempotency_key: str
    status: ExperimentStatus = ExperimentStatus.QUEUED
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)
    baseline_manifest_sha256: str | None = None
    baseline_artifact_root: str | None = None
    baseline_document_count: int = 0
    error_message: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", require_uuid(self.id))
        object.__setattr__(
            self, "project_id", require_safe_identifier(self.project_id, field_name="project_id")
        )
        if self.baseline_run_id <= 0:
            raise ValueError("baseline_run_id must be positive")
        if not self.idempotency_key.strip():
            raise ValueError("idempotency_key must not be blank")
        if self.baseline_manifest_sha256 is not None and (
            len(self.baseline_manifest_sha256) != 64
            or any(char not in "0123456789abcdef" for char in self.baseline_manifest_sha256)
        ):
            raise ValueError("baseline_manifest_sha256 must be a lowercase SHA-256 digest")
        if self.baseline_document_count < 0:
            raise ValueError("baseline_document_count must not be negative")

    @classmethod
    def new(
        cls,
        *,
        project_id: str,
        baseline_run_id: int,
        idempotency_key: str,
        experiment_id: str | None = None,
    ) -> "GenerationExperiment":
        return cls(
            id=experiment_id or new_experiment_id(),
            project_id=project_id,
            baseline_run_id=baseline_run_id,
            idempotency_key=idempotency_key,
        )

    def with_frozen_baseline(
        self,
        *,
        manifest_sha256: str,
        artifact_root: str,
        document_count: int,
    ) -> "GenerationExperiment":
        """Attach immutable S1 receipt metadata without starting a candidate."""

        if self.baseline_manifest_sha256 is not None:
            if self.baseline_manifest_sha256 != manifest_sha256:
                raise ValueError("a baseline manifest cannot be replaced")
            return self
        return replace(
            self,
            baseline_manifest_sha256=manifest_sha256,
            baseline_artifact_root=artifact_root,
            baseline_document_count=document_count,
            updated_at=utc_now(),
        )

    def transition_to(self, target: ExperimentStatus, *, error_message: str | None = None) -> "GenerationExperiment":
        require_transition(self.status, target)
        return replace(
            self,
            status=target,
            error_message=error_message,
            updated_at=utc_now(),
        )
