"""Persistence rows for generic Nexus harness workflow receipts."""

from __future__ import annotations

import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.base import Base


class HarnessWorkflowRunRow(Base):
    """One user-triggered harness workflow run.

    The row stores only sanitized summaries and artifact references. Provider raw
    payloads, prompts, logs, credentials, and hidden reasoning do not belong here.
    """

    __tablename__ = "harness_workflow_runs"
    __table_args__ = (
        Index("ix_harness_workflow_runs_conversation_id", "conversation_id"),
        Index("ix_harness_workflow_runs_user_id", "user_id"),
        Index("ix_harness_workflow_runs_project_id", "project_id"),
        Index("ix_harness_workflow_runs_status", "status"),
        Index("ix_harness_workflow_runs_capability_id", "capability_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    project_id: Mapped[str] = mapped_column(String(255), nullable=False)
    intent_mode: Mapped[str] = mapped_column(String(80), nullable=False)
    capability_id: Mapped[str] = mapped_column(String(160), nullable=False)
    provider_id: Mapped[str] = mapped_column(String(160), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    input_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    output_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    artifact_links: Mapped[list | None] = mapped_column(JSON, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    runtime_provider_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    runtime_thread_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    runtime_run_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    current_node: Mapped[str | None] = mapped_column(String(120), nullable=True)
    resume_token: Mapped[str | None] = mapped_column(String(255), nullable=True)
    budget: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    evidence_refs: Mapped[list | None] = mapped_column(JSON, nullable=True)
    artifact_receipts: Mapped[list | None] = mapped_column(JSON, nullable=True)
    runtime_metadata: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    started_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    activity_events: Mapped[list["HarnessActivityEventRow"]] = relationship(
        back_populates="workflow_run",
        cascade="all, delete-orphan",
        order_by="HarnessActivityEventRow.sequence",
        passive_deletes=True,
    )


class HarnessActivityEventRow(Base):
    """Append-only, user-safe workflow activity event."""

    __tablename__ = "harness_activity_events"
    __table_args__ = (
        UniqueConstraint(
            "workflow_run_id",
            "sequence",
            name="uq_harness_activity_events_run_sequence",
        ),
        Index("ix_harness_activity_events_workflow_run_id", "workflow_run_id"),
        Index("ix_harness_activity_events_event_type", "event_type"),
        Index("ix_harness_activity_events_status", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    workflow_run_id: Mapped[int] = mapped_column(
        ForeignKey("harness_workflow_runs.id", ondelete="CASCADE"), nullable=False
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    label: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    provider_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    capability_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    safe_details: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    runtime_node: Mapped[str | None] = mapped_column(String(120), nullable=True)
    tool_call_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    evidence_refs: Mapped[list | None] = mapped_column(JSON, nullable=True)
    artifact_receipts: Mapped[list | None] = mapped_column(JSON, nullable=True)
    occurred_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    workflow_run: Mapped[HarnessWorkflowRunRow] = relationship(back_populates="activity_events")
