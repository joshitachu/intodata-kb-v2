"""SQLAlchemy models for the deterministic runtime insight layer.

Derived tables: populated by the insight builder from raw runtime store data.
All rows carry project_id for RBAC scoping. Tables are rebuildable — the raw
runtime store is the source of truth.
"""

from __future__ import annotations

import datetime

from sqlalchemy import DateTime, Index, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base


class RuntimeEnrichedEventRow(Base):
    """Parsed signals extracted from one raw log event. Unique per (project_id, log_event_id)."""

    __tablename__ = "runtime_enriched_events"
    __table_args__ = (
        UniqueConstraint("project_id", "log_event_id", name="uq_runtime_enriched_events_project_log"),
        Index("ix_runtime_enriched_events_project_id", "project_id"),
        Index("ix_runtime_enriched_events_project_exec", "project_id", "execution_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[str] = mapped_column(String(255), nullable=False)
    log_event_id: Mapped[int] = mapped_column(Integer, nullable=False)
    execution_id: Mapped[str] = mapped_column(String(255), nullable=False)
    component_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    exception_class: Mapped[str | None] = mapped_column(String(255), nullable=True)
    retry_attempt: Mapped[int | None] = mapped_column(Integer, nullable=True)
    retry_max: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    timeout_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    endpoint: Mapped[str | None] = mapped_column(Text, nullable=True)
    host: Mapped[str | None] = mapped_column(String(255), nullable=True)
    database_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    table_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    queue_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    message_family: Mapped[str | None] = mapped_column(String(50), nullable=True)
    confidence: Mapped[str | None] = mapped_column(String(20), nullable=True)


class RuntimeErrorSignatureRow(Base):
    """Normalized error pattern observed across executions. Unique per (project_id, signature_hash)."""

    __tablename__ = "runtime_error_signatures"
    __table_args__ = (
        UniqueConstraint(
            "project_id", "signature_hash", name="uq_runtime_error_signatures_project_hash"
        ),
        Index("ix_runtime_error_signatures_project_id", "project_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[str] = mapped_column(String(255), nullable=False)
    signature_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    exception_class: Mapped[str | None] = mapped_column(String(255), nullable=True)
    component_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    summary_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    first_seen_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_seen_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    occurrence_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class RuntimeExecutionSummaryRow(Base):
    """Aggregated diagnostic summary for one execution. Unique per (project_id, execution_id)."""

    __tablename__ = "runtime_execution_summaries"
    __table_args__ = (
        UniqueConstraint(
            "project_id", "execution_id", name="uq_runtime_execution_summaries_project_exec"
        ),
        Index("ix_runtime_execution_summaries_project_id", "project_id"),
        Index("ix_runtime_execution_summaries_project_exec", "project_id", "execution_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[str] = mapped_column(String(255), nullable=False)
    execution_id: Mapped[str] = mapped_column(String(255), nullable=False)
    job_name: Mapped[str] = mapped_column(String(255), nullable=False)
    warning_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    first_warning_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    first_error_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    final_error_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failed_component: Mapped[str | None] = mapped_column(String(255), nullable=True)
    primary_error_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    primary_exception_class: Mapped[str | None] = mapped_column(String(255), nullable=True)
    summary_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence_refs: Mapped[list | None] = mapped_column(JSON, nullable=True)


class RuntimeIncidentRow(Base):
    """One operational incident derived from a failed execution. Unique per (project_id, execution_id)."""

    __tablename__ = "runtime_incidents"
    __table_args__ = (
        UniqueConstraint("project_id", "execution_id", name="uq_runtime_incidents_project_exec"),
        Index("ix_runtime_incidents_project_id", "project_id"),
        Index("ix_runtime_incidents_project_incident", "project_id", "incident_id"),
        Index("ix_runtime_incidents_project_signature_bad_at", "project_id", "signature_hash", "first_bad_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[str] = mapped_column(String(255), nullable=False)
    incident_id: Mapped[str] = mapped_column(String(64), nullable=False)
    execution_id: Mapped[str] = mapped_column(String(255), nullable=False)
    signature_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    symptom_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    root_cause_candidate: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence: Mapped[str | None] = mapped_column(String(20), nullable=True)
    first_bad_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_healthy_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_checks: Mapped[list | None] = mapped_column(JSON, nullable=True)


class RuntimeIncidentEvidenceRow(Base):
    """Bounded evidence excerpt for one incident event. Unique per (project_id, incident_id, log_event_id)."""

    __tablename__ = "runtime_incident_evidence"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "incident_id",
            "log_event_id",
            name="uq_runtime_incident_evidence_project_incident_log",
        ),
        Index("ix_runtime_incident_evidence_project_id", "project_id"),
        Index("ix_runtime_incident_evidence_project_incident", "project_id", "incident_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[str] = mapped_column(String(255), nullable=False)
    incident_id: Mapped[str] = mapped_column(String(64), nullable=False)
    log_event_id: Mapped[int] = mapped_column(Integer, nullable=False)
    evidence_role: Mapped[str | None] = mapped_column(String(50), nullable=True)
    excerpt: Mapped[str | None] = mapped_column(Text, nullable=True)
