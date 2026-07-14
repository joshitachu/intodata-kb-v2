"""Persistence rows for generic Nexus harness workflow receipts."""

from __future__ import annotations

import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, JSON, LargeBinary, String, Text, UniqueConstraint
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
    project_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    intent_mode: Mapped[str] = mapped_column(String(80), nullable=False)
    capability_id: Mapped[str] = mapped_column(String(160), nullable=False)
    provider_id: Mapped[str] = mapped_column(String(160), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
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
    cancelled_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    cancelled_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    cancellation_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)

    activity_events: Mapped[list["HarnessActivityEventRow"]] = relationship(
        back_populates="workflow_run",
        cascade="all, delete-orphan",
        order_by="HarnessActivityEventRow.sequence",
        passive_deletes=True,
    )
    interrupts: Mapped[list["HarnessWorkflowInterruptRow"]] = relationship(
        back_populates="workflow_run",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class HarnessWorkflowInterruptRow(Base):
    """Owner-bound, expiring, single-use continuation record."""

    __tablename__ = "harness_workflow_interrupts"
    __table_args__ = (
        Index("ix_harness_workflow_interrupts_workflow_run_id", "workflow_run_id"),
        Index("ix_harness_workflow_interrupts_owner_user_id", "owner_user_id"),
        Index("ix_harness_workflow_interrupts_status", "status"),
        Index("ix_harness_workflow_interrupts_expires_at", "expires_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    workflow_run_id: Mapped[int] = mapped_column(
        ForeignKey("harness_workflow_runs.id", ondelete="CASCADE"), nullable=False
    )
    owner_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    interrupt_type: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    options: Mapped[list | None] = mapped_column(JSON, nullable=True)
    policy_snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    scope_snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    argument_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    response_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    expires_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    workflow_run: Mapped[HarnessWorkflowRunRow] = relationship(back_populates="interrupts")


class HarnessConversationContinuationRow(Base):
    """Durable, owner-bound continuation envelope for a conversational turn."""

    __tablename__ = "harness_conversation_continuations"
    __table_args__ = (
        Index("uq_harness_conversation_continuations_conversation_owner", "conversation_id", "owner_user_id", unique=True),
        Index("ix_harness_conversation_continuations_status", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    owner_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="active")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    envelope: Mapped[dict] = mapped_column(JSON, nullable=False)
    last_message_id: Mapped[int | None] = mapped_column(
        ForeignKey("conversation_messages.id", ondelete="SET NULL"), nullable=True
    )


class HarnessWorkflowCheckpointRow(Base):
    """Sanitized LangGraph checkpoint header for a workflow thread.

    Channel values and pending writes live in dedicated tables so checkpoints can
    resume after a process restart without storing opaque provider payloads on
    the workflow receipt itself.
    """

    __tablename__ = "harness_workflow_checkpoints"
    __table_args__ = (
        UniqueConstraint(
            "thread_id", "checkpoint_ns", "checkpoint_id",
            name="uq_harness_workflow_checkpoints_thread_checkpoint",
        ),
        Index("ix_harness_workflow_checkpoints_thread_ns", "thread_id", "checkpoint_ns"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    thread_id: Mapped[str] = mapped_column(String(255), nullable=False)
    checkpoint_ns: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    checkpoint_id: Mapped[str] = mapped_column(String(255), nullable=False)
    parent_checkpoint_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    checkpoint_type: Mapped[str] = mapped_column(String(24), nullable=False)
    checkpoint_data: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    metadata_type: Mapped[str] = mapped_column(String(24), nullable=False)
    metadata_data: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)


class HarnessWorkflowCheckpointBlobRow(Base):
    """One serialized LangGraph channel value for a checkpoint version."""

    __tablename__ = "harness_workflow_checkpoint_blobs"
    __table_args__ = (
        UniqueConstraint(
            "thread_id", "checkpoint_ns", "channel", "channel_version",
            name="uq_harness_workflow_checkpoint_blobs_channel_version",
        ),
        Index("ix_harness_workflow_checkpoint_blobs_thread_ns", "thread_id", "checkpoint_ns"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    thread_id: Mapped[str] = mapped_column(String(255), nullable=False)
    checkpoint_ns: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    channel: Mapped[str] = mapped_column(String(255), nullable=False)
    channel_version: Mapped[str] = mapped_column(String(255), nullable=False)
    value_type: Mapped[str] = mapped_column(String(24), nullable=False)
    value_data: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)


class HarnessWorkflowCheckpointWriteRow(Base):
    """Idempotent pending write recorded by LangGraph between checkpoints."""

    __tablename__ = "harness_workflow_checkpoint_writes"
    __table_args__ = (
        UniqueConstraint(
            "thread_id", "checkpoint_ns", "checkpoint_id", "task_id", "write_index",
            name="uq_harness_workflow_checkpoint_writes_task_index",
        ),
        Index("ix_harness_workflow_checkpoint_writes_checkpoint", "thread_id", "checkpoint_ns", "checkpoint_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    thread_id: Mapped[str] = mapped_column(String(255), nullable=False)
    checkpoint_ns: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    checkpoint_id: Mapped[str] = mapped_column(String(255), nullable=False)
    task_id: Mapped[str] = mapped_column(String(255), nullable=False)
    write_index: Mapped[int] = mapped_column(Integer, nullable=False)
    channel: Mapped[str] = mapped_column(String(255), nullable=False)
    value_type: Mapped[str] = mapped_column(String(24), nullable=False)
    value_data: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    task_path: Mapped[str] = mapped_column(String(512), nullable=False, default="")


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
