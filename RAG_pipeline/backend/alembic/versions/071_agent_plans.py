"""Add durable non-executable AgentPlan aggregates.

Revision ID: 071_agent_plans
Revises: 070_agent_timestamp_parity
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "071_agent_plans"
down_revision = "070_agent_timestamp_parity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("harness_agent_plans",
        sa.Column("id", sa.Integer(), primary_key=True), sa.Column("plan_id", sa.String(64), nullable=False),
        sa.Column("conversation_id", sa.Integer(), sa.ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("owner_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("goal", sa.Text(), nullable=False), sa.Column("success_criteria", sa.JSON(), nullable=False),
        sa.Column("constraints", sa.JSON(), nullable=False), sa.Column("scoped_entities", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"), sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_by_message_id", sa.Integer(), sa.ForeignKey("conversation_messages.id", ondelete="SET NULL")),
        sa.Column("last_modified_message_id", sa.Integer(), sa.ForeignKey("conversation_messages.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("plan_id", name="uq_harness_agent_plans_public_id"))
    op.create_index("ix_harness_agent_plans_conversation_owner", "harness_agent_plans", ["conversation_id", "owner_user_id"])
    op.create_index("uq_harness_agent_plans_one_live_per_conversation_owner", "harness_agent_plans", ["conversation_id", "owner_user_id"], unique=True, postgresql_where=sa.text("status IN ('active', 'paused')"), sqlite_where=sa.text("status IN ('active', 'paused')"))
    op.create_table("harness_agent_plan_steps", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("plan_id", sa.Integer(), sa.ForeignKey("harness_agent_plans.id", ondelete="CASCADE"), nullable=False), sa.Column("step_key", sa.String(80), nullable=False), sa.Column("ordinal", sa.Integer(), nullable=False), sa.Column("objective", sa.Text(), nullable=False), sa.Column("success_criteria", sa.JSON(), nullable=False), sa.Column("capability_id", sa.String(160)), sa.Column("topology_kind", sa.String(20), nullable=False), sa.Column("requires_wait", sa.Boolean(), nullable=False, server_default=sa.false()), sa.Column("restart_sensitive", sa.Boolean(), nullable=False, server_default=sa.false()), sa.Column("depends_on", sa.JSON(), nullable=False), sa.Column("status", sa.String(20), nullable=False, server_default="pending"), sa.UniqueConstraint("plan_id", "step_key", name="uq_harness_agent_plan_steps_key"), sa.UniqueConstraint("plan_id", "ordinal", name="uq_harness_agent_plan_steps_ordinal"))
    op.create_table("harness_agent_plan_events", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("plan_id", sa.Integer(), sa.ForeignKey("harness_agent_plans.id", ondelete="CASCADE"), nullable=False), sa.Column("action", sa.String(30), nullable=False), sa.Column("from_version", sa.Integer()), sa.Column("to_version", sa.Integer(), nullable=False), sa.Column("reason_code", sa.String(80), nullable=False), sa.Column("change_summary", sa.JSON(), nullable=False), sa.Column("message_id", sa.Integer(), sa.ForeignKey("conversation_messages.id", ondelete="SET NULL")), sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()))
    op.create_index("ix_harness_agent_plan_events_plan", "harness_agent_plan_events", ["plan_id", "id"])


def downgrade() -> None:
    op.drop_index("ix_harness_agent_plan_events_plan", table_name="harness_agent_plan_events"); op.drop_table("harness_agent_plan_events"); op.drop_table("harness_agent_plan_steps"); op.drop_index("uq_harness_agent_plans_one_live_per_conversation_owner", table_name="harness_agent_plans"); op.drop_index("ix_harness_agent_plans_conversation_owner", table_name="harness_agent_plans"); op.drop_table("harness_agent_plans")
