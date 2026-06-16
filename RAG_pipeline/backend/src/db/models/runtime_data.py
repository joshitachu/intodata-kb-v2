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
    DateTime,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base


class RuntimeExecutionRow(Base):
    """One runtime job execution. State is stored as the RuntimeExecutionState value."""

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
    started_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
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
            "event_fingerprint",
            name="uq_runtime_log_events_project_exec_fp",
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
    event_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    source_key: Mapped[str] = mapped_column(String(100), nullable=False)
    ingested_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class RuntimeSyncStateRow(Base):
    """Freshness watermark per (source_key, project_id). `last_synced_at` is the
    truthful "as of" timestamp a future chat surface can show."""

    __tablename__ = "runtime_sync_state"
    __table_args__ = (
        UniqueConstraint("source_key", "project_id", name="uq_runtime_sync_state_source_project"),
        Index("ix_runtime_sync_state_source_project", "source_key", "project_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_key: Mapped[str] = mapped_column(String(100), nullable=False)
    project_id: Mapped[str] = mapped_column(String(255), nullable=False)
    last_synced_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
