"""Add durable Agent Run, public events, and safe step telemetry for Slice 6.

Revision ID: 073_agent_run_streaming
Revises: 072_agent_effect_authority
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "073_agent_run_streaming"
down_revision = "072_agent_effect_authority"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "harness_agent_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("run_id", sa.String(80), nullable=False),
        sa.Column("conversation_id", sa.Integer(), sa.ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("owner_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_message_id", sa.Integer(), sa.ForeignKey("conversation_messages.id", ondelete="CASCADE"), nullable=False),
        sa.Column("assistant_message_id", sa.Integer(), sa.ForeignKey("conversation_messages.id", ondelete="SET NULL")),
        sa.Column("runtime_cohort", sa.String(40), nullable=False),
        sa.Column("transport", sa.String(16), nullable=False),
        sa.Column("manifest_snapshot_hash", sa.String(64)),
        sa.Column("status", sa.String(24), nullable=False, server_default="running"),
        sa.Column("stop_reason", sa.String(64)),
        sa.Column("objective_summary", sa.String(500)),
        sa.Column("budget", sa.JSON(), nullable=False),
        sa.Column("usage", sa.JSON(), nullable=False),
        sa.Column("timing", sa.JSON(), nullable=False),
        sa.Column("next_event_sequence", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("run_id", name="uq_harness_agent_runs_public_id"),
        sa.UniqueConstraint("user_message_id", name="uq_harness_agent_runs_user_message"),
        sa.CheckConstraint("next_event_sequence >= 1", name="ck_harness_agent_runs_next_sequence"),
        sa.CheckConstraint("status IN ('running','waiting','completed','failed','interrupted')", name="ck_harness_agent_runs_status"),
    )
    op.create_index("ix_harness_agent_runs_owner_conversation", "harness_agent_runs", ["owner_user_id", "conversation_id", "id"])
    op.create_index("ix_harness_agent_runs_status", "harness_agent_runs", ["status", "updated_at"])

    op.create_table(
        "harness_agent_public_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("event_id", sa.String(120), nullable=False),
        sa.Column("agent_run_id", sa.Integer(), sa.ForeignKey("harness_agent_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(48), nullable=False),
        sa.Column("public_payload", sa.JSON(), nullable=False),
        sa.Column("replayable", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("event_id", name="uq_harness_agent_public_events_id"),
        sa.UniqueConstraint("agent_run_id", "sequence", name="uq_harness_agent_public_events_sequence"),
        sa.CheckConstraint("sequence >= 1", name="ck_harness_agent_public_events_sequence"),
    )
    op.create_index("ix_harness_agent_public_events_run", "harness_agent_public_events", ["agent_run_id", "sequence"])

    op.create_table(
        "harness_agent_steps",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("agent_run_id", sa.Integer(), sa.ForeignKey("harness_agent_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("capability_id", sa.String(160)),
        sa.Column("policy_status", sa.String(48)),
        sa.Column("observation_status", sa.String(48)),
        sa.Column("stop_reason", sa.String(64)),
        sa.Column("latency_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cost_usd", sa.Float(), nullable=False, server_default="0"),
        sa.Column("evidence_refs", sa.JSON(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("agent_run_id", "sequence", name="uq_harness_agent_steps_sequence"),
        sa.CheckConstraint("sequence >= 1", name="ck_harness_agent_steps_sequence"),
        sa.CheckConstraint("latency_ms >= 0", name="ck_harness_agent_steps_latency"),
    )
    op.create_index("ix_harness_agent_steps_run", "harness_agent_steps", ["agent_run_id", "sequence"])


def downgrade() -> None:
    op.drop_index("ix_harness_agent_steps_run", table_name="harness_agent_steps")
    op.drop_table("harness_agent_steps")
    op.drop_index("ix_harness_agent_public_events_run", table_name="harness_agent_public_events")
    op.drop_table("harness_agent_public_events")
    op.drop_index("ix_harness_agent_runs_status", table_name="harness_agent_runs")
    op.drop_index("ix_harness_agent_runs_owner_conversation", table_name="harness_agent_runs")
    op.drop_table("harness_agent_runs")
