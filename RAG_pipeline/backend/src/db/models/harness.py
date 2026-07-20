"""Persistence rows for generic Nexus harness workflow receipts."""

from __future__ import annotations

import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.base import Base


class HarnessAgentTurnAuditRow(Base):
    """Append-only bounded cutover telemetry; never an execution authority."""

    __tablename__ = "harness_agent_turn_audits"
    __table_args__ = (
        UniqueConstraint(
            "user_message_id",
            name="uq_harness_agent_turn_audit_user_message",
        ),
        UniqueConstraint(
            "assistant_message_id",
            name="uq_harness_agent_turn_audit_assistant_message",
        ),
        Index("ix_harness_agent_turn_audits_created", "created_at"),
        Index("ix_harness_agent_turn_audits_route", "route", "transport"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    owner_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    user_message_id: Mapped[int] = mapped_column(
        ForeignKey("conversation_messages.id", ondelete="CASCADE"), nullable=False
    )
    assistant_message_id: Mapped[int | None] = mapped_column(
        ForeignKey("conversation_messages.id", ondelete="SET NULL"), nullable=True
    )
    route: Mapped[str] = mapped_column(String(16), nullable=False)
    cohort_source: Mapped[str] = mapped_column(String(32), nullable=False)
    cohort_bucket: Mapped[int] = mapped_column(Integer, nullable=False)
    transport: Mapped[str] = mapped_column(String(16), nullable=False)
    state_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    terminal_action: Mapped[str | None] = mapped_column(String(32), nullable=True)
    stop_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    capability_ids: Mapped[list] = mapped_column(JSON, nullable=False)
    observation_statuses: Mapped[list] = mapped_column(JSON, nullable=False)
    response_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    flags: Mapped[dict] = mapped_column(JSON, nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tool_calls: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_usd: Mapped[float] = mapped_column(nullable=False, default=0.0)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    shadow_result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


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
    child_receipts: Mapped[list["HarnessWorkflowChildReceiptRow"]] = relationship(
        back_populates="workflow_run",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="selectin",
    )


class HarnessWorkflowChildReceiptRow(Base):
    """Validated exact provider-child identity and canonical provider state."""

    __tablename__ = "harness_workflow_child_receipts"
    __table_args__ = (
        UniqueConstraint(
            "workflow_run_id",
            "provider_id",
            "child_kind",
            name="uq_harness_workflow_child_parent_provider_kind",
        ),
        UniqueConstraint(
            "provider_id",
            "external_run_id",
            name="uq_harness_workflow_child_provider_external_run",
        ),
        Index("ix_harness_workflow_child_receipts_workflow_run_id", "workflow_run_id"),
        Index("ix_harness_workflow_child_receipts_status", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    workflow_run_id: Mapped[int] = mapped_column(
        ForeignKey("harness_workflow_runs.id", ondelete="CASCADE"), nullable=False
    )
    provider_id: Mapped[str] = mapped_column(String(160), nullable=False)
    child_kind: Mapped[str] = mapped_column(String(80), nullable=False)
    external_run_id: Mapped[str] = mapped_column(String(255), nullable=False)
    project_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    provider_status: Mapped[str] = mapped_column(String(80), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    provider_started_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))
    provider_finished_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))
    provider_timestamp: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))
    last_observed_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_observation_source: Mapped[str] = mapped_column(String(24), nullable=False)
    error_message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    cancellation_state: Mapped[str] = mapped_column(
        String(30), nullable=False, default="none", server_default="none"
    )
    cancellation_idempotency_key: Mapped[str | None] = mapped_column(String(255), unique=True)
    cancellation_requested_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))
    cancellation_provider_status: Mapped[str | None] = mapped_column(String(80))
    cancellation_recorded_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    workflow_run: Mapped[HarnessWorkflowRunRow] = relationship(back_populates="child_receipts")
    observations: Mapped[list["HarnessWorkflowChildObservationRow"]] = relationship(
        back_populates="child_receipt",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class HarnessWorkflowChildObservationRow(Base):
    """Append-only replay guard for provider poll/callback/recovery facts."""

    __tablename__ = "harness_workflow_child_observations"
    __table_args__ = (
        UniqueConstraint(
            "idempotency_key", name="uq_harness_workflow_child_observation_idempotency"
        ),
        Index("ix_harness_workflow_child_observations_receipt_id", "child_receipt_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    child_receipt_id: Mapped[int] = mapped_column(
        ForeignKey("harness_workflow_child_receipts.id", ondelete="CASCADE"), nullable=False
    )
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    source: Mapped[str] = mapped_column(String(24), nullable=False)
    provider_status: Mapped[str] = mapped_column(String(80), nullable=False)
    normalized_status: Mapped[str] = mapped_column(String(30), nullable=False)
    provider_timestamp: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))
    observed_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    accepted: Mapped[bool] = mapped_column(Boolean, nullable=False)
    decision_reason: Mapped[str] = mapped_column(String(80), nullable=False)
    error_message: Mapped[str | None] = mapped_column(String(500), nullable=True)

    child_receipt: Mapped[HarnessWorkflowChildReceiptRow] = relationship(
        back_populates="observations"
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


class HarnessConversationStateRow(Base):
    """One optimistic-versioned conversational focus aggregate.

    Pack drafts and workflow runs remain authoritative for lifecycle state.  The
    JSON fields below contain only validated, bounded references and dialogue
    focus projected by ``harness.conversation_state``.
    """

    __tablename__ = "harness_conversation_states"
    __table_args__ = (
        UniqueConstraint(
            "conversation_id",
            "owner_user_id",
            name="uq_harness_conversation_state_conversation_owner",
        ),
        CheckConstraint("version >= 1", name="ck_harness_conversation_state_version"),
        Index("ix_harness_conversation_states_owner", "owner_user_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    owner_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    objective: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    referenced_entities: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    requested_inputs: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    active_tasks: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    response_fingerprints: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    last_applied_message_id: Mapped[int | None] = mapped_column(
        ForeignKey("conversation_messages.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class HarnessConversationTurnClaimRow(Base):
    """Durable pre-effect lease and idempotency record for one user message."""

    __tablename__ = "harness_conversation_turn_claims"
    __table_args__ = (
        UniqueConstraint("message_id", name="uq_harness_conversation_turn_claim_message"),
        UniqueConstraint(
            "idempotency_key", name="uq_harness_conversation_turn_claim_idempotency"
        ),
        Index("ix_harness_conversation_turn_claims_state", "conversation_state_id"),
        Index("ix_harness_conversation_turn_claims_lease", "status", "lease_expires_at"),
        Index(
            "uq_harness_conversation_turn_claim_active_state",
            "conversation_state_id",
            unique=True,
            postgresql_where=text(
                "status IN ('claimed', 'effect_started', 'recovery_required')"
            ),
            sqlite_where=text(
                "status IN ('claimed', 'effect_started', 'recovery_required')"
            ),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    conversation_state_id: Mapped[int] = mapped_column(
        ForeignKey("harness_conversation_states.id", ondelete="CASCADE"), nullable=False
    )
    message_id: Mapped[int] = mapped_column(
        ForeignKey("conversation_messages.id", ondelete="CASCADE"), nullable=False
    )
    expected_version: Mapped[int] = mapped_column(Integer, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    lease_token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    lease_expires_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    effect_receipts: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    final_result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    applied_transition_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    claimed_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    effect_started_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finalized_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class HarnessConversationStateTransitionRow(Base):
    """Append-only state transition and replay guard."""

    __tablename__ = "harness_conversation_state_transitions"
    __table_args__ = (
        UniqueConstraint(
            "transition_key", name="uq_harness_conversation_state_transition_key"
        ),
        CheckConstraint("to_version >= from_version", name="ck_harness_state_transition_versions"),
        Index("ix_harness_conversation_state_transitions_state", "conversation_state_id"),
        Index("ix_harness_conversation_state_transitions_claim", "turn_claim_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    conversation_state_id: Mapped[int] = mapped_column(
        ForeignKey("harness_conversation_states.id", ondelete="CASCADE"), nullable=False
    )
    turn_claim_id: Mapped[int | None] = mapped_column(
        ForeignKey("harness_conversation_turn_claims.id", ondelete="SET NULL"), nullable=True
    )
    message_id: Mapped[int | None] = mapped_column(
        ForeignKey("conversation_messages.id", ondelete="SET NULL"), nullable=True
    )
    transition_key: Mapped[str] = mapped_column(String(255), nullable=False)
    from_version: Mapped[int] = mapped_column(Integer, nullable=False)
    to_version: Mapped[int] = mapped_column(Integer, nullable=False)
    source: Mapped[str] = mapped_column(String(40), nullable=False)
    action: Mapped[str] = mapped_column(String(80), nullable=False)
    change_summary: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)


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
