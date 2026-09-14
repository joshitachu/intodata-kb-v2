"""Held runtime-data store models (Model 2 skeleton).

Source-agnostic persistence for runtime executions, failures, and log events, plus
a freshness watermark. No Talend-specific names: a future sync worker (or any
source) writes generic rows through the repository write-port, and the `stored`
runtime provider (a later slice) reads them back as the normalized runtime models in
src/runtime/models.py.

Every data row carries `project_id` (the RBAC scope key) and `ingested_at` (the
per-row freshness stamp). `RuntimeSyncStateRow` holds the per-source/per-project
`last_synced_at` watermark so chat can later say "as of HH:MM" truthfully.
"""

from __future__ import annotations

import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Float,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    JSON,
    Boolean,
    LargeBinary,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base


# Provider evidence that the normalized `state` column deliberately discards.
#
# `state` is our own small vocabulary (RuntimeExecutionState); a source such as
# TMC reports a wider one plus a machine reason for why a run never ran. Keeping
# the verbatim strings alongside the normalized value means a mapping gap shows
# up as `state='unknown'` NEXT TO the raw token that caused it, instead of being
# unrecoverable. All three are nullable: no provider is obliged to supply them,
# and every row written before this column existed legitimately has none.
#
# Widths are chosen against observed TMC values with headroom, and are the same
# on all three run-carrying tables so a value can move between them unchanged:
#   raw_state       e.g. "EXECUTION_MISFIRED"           -> 64
#   misfire_reason  e.g. "PARALLEL_EXECUTION_FORBIDDEN" -> 64
#   execution_type  MANUAL | SCHEDULED | PLAN           -> 32
_RAW_STATE_LEN = 64
_MISFIRE_REASON_LEN = 64
_EXECUTION_TYPE_LEN = 32


class RuntimeExecutionRow(Base):
    """One runtime job execution. State is stored as the RuntimeExecutionState value.

    `state` is String(20), which already fits every RuntimeExecutionState member
    including the widest new one (`terminated`, 10 chars). No widening needed.
    """

    __tablename__ = "runtime_executions"
    __table_args__ = (
        UniqueConstraint("project_id", "execution_id", name="uq_runtime_executions_project_exec"),
        Index("ix_runtime_executions_project_id", "project_id"),
        Index("ix_runtime_executions_job_name", "job_name"),
        Index("ix_runtime_executions_started_at", "started_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[str] = mapped_column(String(255), nullable=False)
    execution_id: Mapped[str] = mapped_column(String(255), nullable=False)
    job_name: Mapped[str] = mapped_column(String(255), nullable=False)
    project_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    state: Mapped[str] = mapped_column(String(20), nullable=False)
    raw_state: Mapped[str | None] = mapped_column(String(_RAW_STATE_LEN), nullable=True)
    misfire_reason: Mapped[str | None] = mapped_column(String(_MISFIRE_REASON_LEN), nullable=True)
    execution_type: Mapped[str | None] = mapped_column(String(_EXECUTION_TYPE_LEN), nullable=True)
    executable_kind: Mapped[str] = mapped_column(String(32), nullable=False, default="job")
    started_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    source_key: Mapped[str] = mapped_column(String(100), nullable=False)
    ingested_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class RuntimeFailureRow(Base):
    """One detected runtime failure, keyed by the failing execution."""

    __tablename__ = "runtime_failures"
    __table_args__ = (
        UniqueConstraint("project_id", "execution_id", name="uq_runtime_failures_project_exec"),
        Index("ix_runtime_failures_project_id", "project_id"),
        Index("ix_runtime_failures_failed_at", "failed_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[str] = mapped_column(String(255), nullable=False)
    execution_id: Mapped[str] = mapped_column(String(255), nullable=False)
    job_name: Mapped[str] = mapped_column(String(255), nullable=False)
    failed_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    error_summary: Mapped[str] = mapped_column(Text, nullable=False)
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_key: Mapped[str] = mapped_column(String(100), nullable=False)
    ingested_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class RuntimeLogEventRow(Base):
    """One runtime log line. `event_fingerprint` makes re-ingest idempotent for logs
    that have no natural unique id. Severity is stored as the RuntimeLogSeverity value."""

    __tablename__ = "runtime_log_events"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "execution_id",
            "line_ordinal",
            "event_fingerprint",
            name="uq_runtime_log_events_project_exec_ordinal_fp",
        ),
        Index("ix_runtime_log_events_project_id", "project_id"),
        Index("ix_runtime_log_events_execution_id", "execution_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[str] = mapped_column(String(255), nullable=False)
    execution_id: Mapped[str] = mapped_column(String(255), nullable=False)
    timestamp: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    severity: Mapped[str] = mapped_column(String(20), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    line_ordinal: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    event_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    source_key: Mapped[str] = mapped_column(String(100), nullable=False)
    scope: Mapped[str] = mapped_column(String(20), nullable=False, default="execution")
    attribution_safe: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    ingested_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class RuntimeSyncStateRow(Base):
    """Freshness/status watermark per (source_key, project_id).

    `last_synced_at` is the truthful "as of" timestamp user-facing surfaces can show.
    Failed or partial sync attempts also persist a sanitized status/reason without
    raw source payloads or credentials.
    """

    __tablename__ = "runtime_sync_state"
    __table_args__ = (
        UniqueConstraint("source_key", "project_id", name="uq_runtime_sync_state_source_project"),
        Index("ix_runtime_sync_state_source_project", "source_key", "project_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_key: Mapped[str] = mapped_column(String(100), nullable=False)
    project_id: Mapped[str] = mapped_column(String(255), nullable=False)
    last_synced_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="success")
    failure_reason_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class RuntimeSourceRow(Base):
    """Current-state inventory for one runtime source in one project."""

    __tablename__ = "runtime_sources"
    __table_args__ = (
        UniqueConstraint("project_id", "source_key", name="uq_runtime_sources_project_source"),
        Index("ix_runtime_sources_project_id", "project_id"),
        Index("ix_runtime_sources_source_key", "source_key"),
        Index(
            "uq_runtime_sources_project_tmc",
            "project_id",
            unique=True,
            postgresql_where=text("source_type = 'tmc'"),
            sqlite_where=text("source_type = 'tmc'"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[str] = mapped_column(String(255), nullable=False)
    source_key: Mapped[str] = mapped_column(String(100), nullable=False)
    source_type: Mapped[str] = mapped_column(String(100), nullable=False)
    physical_identity: Mapped[str] = mapped_column(String(512), nullable=False)
    label: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # 30 minutes. Backstop only -- the repository passes an explicit value; kept
    # in step with RuntimeSourceCreate so a source created any other way is not
    # silently put back on a core-pinning cadence.
    sync_interval_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=1800)
    auto_disable_threshold: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    disabled_reason: Mapped[str | None] = mapped_column(String(120), nullable=True)
    is_demo: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_synced_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_seen_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ingested_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class RuntimeSourceCredentialsRow(Base):
    """Encrypted scheduler-only credentials for one runtime source."""

    __tablename__ = "runtime_source_credentials"
    __table_args__ = (
        UniqueConstraint("source_id", name="uq_runtime_source_credentials_source"),
        Index("ix_runtime_source_credentials_source_id", "source_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[int] = mapped_column(
        ForeignKey("runtime_sources.id", ondelete="CASCADE"), nullable=False
    )
    credential_type: Mapped[str] = mapped_column(String(100), nullable=False)
    encrypted_payload: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    encryption_key_id: Mapped[str] = mapped_column(String(40), nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    rotated_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rotated_by: Mapped[str | None] = mapped_column(String(255), nullable=True)


class RuntimeSourceHealthRow(Base):
    """Per-source lifecycle health used by source management UI and scheduler."""

    __tablename__ = "runtime_source_health"

    source_id: Mapped[int] = mapped_column(
        ForeignKey("runtime_sources.id", ondelete="CASCADE"), primary_key=True
    )
    last_attempt_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_success_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_failure_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    consecutive_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_failure_sanitized: Mapped[str | None] = mapped_column(Text, nullable=True)
    total_cycles: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_records_ingested: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class RuntimeWorkspaceProjectMapRow(Base):
    """Maps one source container/workspace to a Nexus project for future writes."""

    __tablename__ = "runtime_workspace_project_map"
    __table_args__ = (
        UniqueConstraint("source_id", "container_key", name="uq_runtime_workspace_project_map_source_container"),
        Index("ix_runtime_workspace_project_map_source_id", "source_id"),
        Index("ix_runtime_workspace_project_map_project_id", "project_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[int] = mapped_column(
        ForeignKey("runtime_sources.id", ondelete="CASCADE"), nullable=False
    )
    container_key: Mapped[str] = mapped_column(String(255), nullable=False)
    container_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    project_id: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_by: Mapped[str | None] = mapped_column(String(255), nullable=True)


class RuntimeSourceReadGrantRow(Base):
    """Grants one project permission to READ another project's runtime mirror.

    `runtime_sources.project_id` conflates three relations: who holds the
    credential and pays the poll, which project a mirrored row is stamped with
    (`RuntimeWorkspaceProjectMapRow` already re-points that), and which projects
    may read the result. This table is the third one, made explicit.

    It exists because one Talend tenant commonly backs several scanned projects,
    and the registration guard -- correctly -- refuses to store the same
    credential twice: a second row would mean a second poll and two mirrors of
    one estate that drift apart. Sharing therefore has to happen on the read
    side.

    Scope lives on the grant, not on the source's credential blob, because the
    reason two projects share a tenant is usually that they care about different
    environments of it. `environment_ids` NULL means every mirrored environment;
    an empty list is a deliberate "nothing", the same distinction
    `RuntimeDocReadScope` draws.
    """

    __tablename__ = "runtime_source_read_grants"
    __table_args__ = (
        UniqueConstraint(
            "source_id", "project_id", name="uq_runtime_source_read_grants_source_project"
        ),
        Index("ix_runtime_source_read_grants_source_id", "source_id"),
        Index("ix_runtime_source_read_grants_project_id", "project_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[int] = mapped_column(
        ForeignKey("runtime_sources.id", ondelete="CASCADE"), nullable=False
    )
    # The READER. The source's own project_id is the grantor and is never a row
    # here -- ownership already implies read.
    project_id: Mapped[str] = mapped_column(String(255), nullable=False)
    environment_ids: Mapped[list | None] = mapped_column(JSON, nullable=True)
    window_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_by: Mapped[str | None] = mapped_column(String(255), nullable=True)


class RuntimeEnvironmentRow(Base):
    """Current-state runtime environment inventory."""

    __tablename__ = "runtime_environments"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "source_key",
            "environment_id",
            name="uq_runtime_environments_project_source_environment",
        ),
        Index("ix_runtime_environments_project_id", "project_id"),
        Index("ix_runtime_environments_source_key", "source_key"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[str] = mapped_column(String(255), nullable=False)
    source_key: Mapped[str] = mapped_column(String(100), nullable=False)
    environment_id: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_seen_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ingested_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class RuntimeWorkspaceRow(Base):
    """Current-state runtime workspace inventory."""

    __tablename__ = "runtime_workspaces"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "source_key",
            "workspace_id",
            name="uq_runtime_workspaces_project_source_workspace",
        ),
        Index("ix_runtime_workspaces_project_id", "project_id"),
        Index("ix_runtime_workspaces_source_key", "source_key"),
        Index("ix_runtime_workspaces_environment_id", "environment_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[str] = mapped_column(String(255), nullable=False)
    source_key: Mapped[str] = mapped_column(String(100), nullable=False)
    workspace_id: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    environment_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    environment_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    owner: Mapped[str | None] = mapped_column(String(255), nullable=True)
    workspace_type: Mapped[str | None] = mapped_column(String(80), nullable=True)
    last_seen_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ingested_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class RuntimeEngineRow(Base):
    """Current-state runtime engine/runner inventory."""

    __tablename__ = "runtime_engines"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "source_key",
            "engine_id",
            name="uq_runtime_engines_project_source_engine",
        ),
        Index("ix_runtime_engines_project_id", "project_id"),
        Index("ix_runtime_engines_source_key", "source_key"),
        Index("ix_runtime_engines_status", "status"),
        Index("ix_runtime_engines_environment_id", "environment_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[str] = mapped_column(String(255), nullable=False)
    source_key: Mapped[str] = mapped_column(String(100), nullable=False)
    engine_id: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    raw_status: Mapped[str | None] = mapped_column(String(120), nullable=True)
    availability: Mapped[str | None] = mapped_column(String(120), nullable=True)
    version: Mapped[str | None] = mapped_column(String(120), nullable=True)
    environment_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    environment_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    workspace_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    workspace_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_seen_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    source_updated_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ingested_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class RuntimeJobRow(Base):
    """Current-state runtime job/task inventory."""

    __tablename__ = "runtime_jobs"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "source_key",
            "job_id",
            name="uq_runtime_jobs_project_source_job",
        ),
        Index("ix_runtime_jobs_project_id", "project_id"),
        Index("ix_runtime_jobs_source_key", "source_key"),
        Index("ix_runtime_jobs_environment_id", "environment_id"),
        Index("ix_runtime_jobs_workspace_id", "workspace_id"),
        Index("ix_runtime_jobs_engine_id", "engine_id"),
        Index("ix_runtime_jobs_status", "status"),
        Index("ix_runtime_jobs_executable_kind", "executable_kind"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[str] = mapped_column(String(255), nullable=False)
    source_key: Mapped[str] = mapped_column(String(100), nullable=False)
    job_id: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    job_type: Mapped[str | None] = mapped_column(String(80), nullable=True)
    executable_kind: Mapped[str] = mapped_column(String(32), nullable=False, default="job")
    status: Mapped[str | None] = mapped_column(String(80), nullable=True)
    environment_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    environment_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    workspace_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    workspace_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    engine_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    engine_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    owner: Mapped[str | None] = mapped_column(String(255), nullable=True)
    artifact_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    artifact_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    artifact_version: Mapped[str | None] = mapped_column(String(120), nullable=True)
    metric_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    last_seen_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    source_updated_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ingested_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class RuntimeArtifactRow(Base):
    """Optional source artifact/package metadata used by runtime jobs."""

    __tablename__ = "runtime_artifacts"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "source_key",
            "artifact_id",
            name="uq_runtime_artifacts_project_source_artifact",
        ),
        Index("ix_runtime_artifacts_project_id", "project_id"),
        Index("ix_runtime_artifacts_source_key", "source_key"),
        Index("ix_runtime_artifacts_workspace_id", "workspace_id"),
        Index("ix_runtime_artifacts_type", "artifact_type"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[str] = mapped_column(String(255), nullable=False)
    source_key: Mapped[str] = mapped_column(String(100), nullable=False)
    artifact_id: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    artifact_type: Mapped[str | None] = mapped_column(String(120), nullable=True)
    version: Mapped[str | None] = mapped_column(String(120), nullable=True)
    workspace_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    workspace_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    owner: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_seen_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    source_updated_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ingested_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class RuntimeConnectionRow(Base):
    """Optional source connection metadata without credentials or raw parameters."""

    __tablename__ = "runtime_connections"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "source_key",
            "connection_id",
            name="uq_runtime_connections_project_source_connection",
        ),
        Index("ix_runtime_connections_project_id", "project_id"),
        Index("ix_runtime_connections_source_key", "source_key"),
        Index("ix_runtime_connections_workspace_id", "workspace_id"),
        Index("ix_runtime_connections_type", "connection_type"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[str] = mapped_column(String(255), nullable=False)
    source_key: Mapped[str] = mapped_column(String(100), nullable=False)
    connection_id: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    connection_type: Mapped[str | None] = mapped_column(String(120), nullable=True)
    workspace_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    workspace_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    owner: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_seen_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    source_updated_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ingested_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class RuntimeJobDependencyRow(Base):
    """Dependency edge from a runtime job/task to an artifact or connection."""

    __tablename__ = "runtime_job_dependencies"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "source_key",
            "job_id",
            "dependency_type",
            "dependency_id",
            name="uq_runtime_job_dependencies_project_source_job_dep",
        ),
        Index("ix_runtime_job_dependencies_project_id", "project_id"),
        Index("ix_runtime_job_dependencies_source_key", "source_key"),
        Index("ix_runtime_job_dependencies_job_id", "job_id"),
        Index("ix_runtime_job_dependencies_dependency", "dependency_type", "dependency_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[str] = mapped_column(String(255), nullable=False)
    source_key: Mapped[str] = mapped_column(String(100), nullable=False)
    job_id: Mapped[str] = mapped_column(String(255), nullable=False)
    job_name: Mapped[str] = mapped_column(String(255), nullable=False)
    dependency_type: Mapped[str] = mapped_column(String(40), nullable=False)
    dependency_id: Mapped[str] = mapped_column(String(255), nullable=False)
    dependency_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    dependency_kind: Mapped[str | None] = mapped_column(String(120), nullable=True)
    workspace_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    workspace_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_seen_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ingested_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class RuntimeJobRunRow(Base):
    """One stored runtime job/task run fact."""

    __tablename__ = "runtime_job_runs"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "source_key",
            "run_id",
            name="uq_runtime_job_runs_project_source_run",
        ),
        Index("ix_runtime_job_runs_project_id", "project_id"),
        Index("ix_runtime_job_runs_source_key", "source_key"),
        Index("ix_runtime_job_runs_job_id", "job_id"),
        Index("ix_runtime_job_runs_engine_id", "engine_id"),
        Index("ix_runtime_job_runs_state", "state"),
        Index("ix_runtime_job_runs_started_at", "started_at"),
        Index("ix_runtime_job_runs_execution_id", "execution_id"),
        Index("ix_runtime_job_runs_executable_kind", "executable_kind"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[str] = mapped_column(String(255), nullable=False)
    source_key: Mapped[str] = mapped_column(String(100), nullable=False)
    run_id: Mapped[str] = mapped_column(String(255), nullable=False)
    job_id: Mapped[str] = mapped_column(String(255), nullable=False)
    job_name: Mapped[str] = mapped_column(String(255), nullable=False)
    execution_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    state: Mapped[str] = mapped_column(String(20), nullable=False)
    raw_state: Mapped[str | None] = mapped_column(String(_RAW_STATE_LEN), nullable=True)
    misfire_reason: Mapped[str | None] = mapped_column(String(_MISFIRE_REASON_LEN), nullable=True)
    execution_type: Mapped[str | None] = mapped_column(String(_EXECUTION_TYPE_LEN), nullable=True)
    executable_kind: Mapped[str] = mapped_column(String(32), nullable=False, default="job")
    environment_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    environment_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    workspace_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    workspace_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    engine_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    engine_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    started_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    completed_exchanges: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    failed_exchanges: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    total_exchanges: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    last_process_time_ms: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    mean_process_time_ms: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    metrics_updated_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    metric_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    service_call_count: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    one_minute_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    five_minute_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    fifteen_minute_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    ingested_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class RuntimePlanRow(Base):
    """Current-state runtime orchestration plan inventory."""

    __tablename__ = "runtime_plans"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "source_key",
            "plan_id",
            name="uq_runtime_plans_project_source_plan",
        ),
        Index("ix_runtime_plans_project_id", "project_id"),
        Index("ix_runtime_plans_source_key", "source_key"),
        Index("ix_runtime_plans_environment_id", "environment_id"),
        Index("ix_runtime_plans_workspace_id", "workspace_id"),
        Index("ix_runtime_plans_status", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[str] = mapped_column(String(255), nullable=False)
    source_key: Mapped[str] = mapped_column(String(100), nullable=False)
    plan_id: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str | None] = mapped_column(String(80), nullable=True)
    environment_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    environment_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    workspace_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    workspace_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    owner: Mapped[str | None] = mapped_column(String(255), nullable=True)
    step_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    steps: Mapped[list[dict] | None] = mapped_column(JSON, nullable=True)
    last_seen_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    source_updated_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ingested_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class RuntimePlanRunRow(Base):
    """One stored runtime orchestration plan run fact."""

    __tablename__ = "runtime_plan_runs"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "source_key",
            "run_id",
            name="uq_runtime_plan_runs_project_source_run",
        ),
        Index("ix_runtime_plan_runs_project_id", "project_id"),
        Index("ix_runtime_plan_runs_source_key", "source_key"),
        Index("ix_runtime_plan_runs_plan_id", "plan_id"),
        Index("ix_runtime_plan_runs_state", "state"),
        Index("ix_runtime_plan_runs_started_at", "started_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[str] = mapped_column(String(255), nullable=False)
    source_key: Mapped[str] = mapped_column(String(100), nullable=False)
    run_id: Mapped[str] = mapped_column(String(255), nullable=False)
    plan_id: Mapped[str] = mapped_column(String(255), nullable=False)
    plan_name: Mapped[str] = mapped_column(String(255), nullable=False)
    state: Mapped[str] = mapped_column(String(20), nullable=False)
    raw_state: Mapped[str | None] = mapped_column(String(_RAW_STATE_LEN), nullable=True)
    misfire_reason: Mapped[str | None] = mapped_column(String(_MISFIRE_REASON_LEN), nullable=True)
    execution_type: Mapped[str | None] = mapped_column(String(_EXECUTION_TYPE_LEN), nullable=True)
    environment_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    environment_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    workspace_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    workspace_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    started_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    step_results: Mapped[list[dict] | None] = mapped_column(JSON, nullable=True)
    ingested_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class RuntimeComponentMetricRow(Base):
    """Generic component/source-target metric fact for one execution.

    Source adapters can provide partial data. Component identifiers and connector
    types are technical labels, not user-authored log bodies.
    """

    __tablename__ = "runtime_component_metrics"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "execution_id",
            "component_key",
            "source_key",
            name="uq_runtime_component_metrics_project_exec_component_source",
        ),
        Index("ix_runtime_component_metrics_project_id", "project_id"),
        Index("ix_runtime_component_metrics_execution_id", "execution_id"),
        Index("ix_runtime_component_metrics_component_key", "component_key"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[str] = mapped_column(String(255), nullable=False)
    execution_id: Mapped[str] = mapped_column(String(255), nullable=False)
    component_key: Mapped[str] = mapped_column(String(255), nullable=False)
    component_type: Mapped[str | None] = mapped_column(String(120), nullable=True)
    source_connector_type: Mapped[str | None] = mapped_column(String(120), nullable=True)
    target_connector_type: Mapped[str | None] = mapped_column(String(120), nullable=True)
    rows_total: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rows_rejected: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    source_key: Mapped[str] = mapped_column(String(100), nullable=False)
    measured_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ingested_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
