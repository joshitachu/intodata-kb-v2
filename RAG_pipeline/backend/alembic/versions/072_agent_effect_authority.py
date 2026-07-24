"""Add exact approval and effect-operation authority for Slice 5.

Revision ID: 072_agent_effect_authority
Revises: 071_agent_plans
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "072_agent_effect_authority"
down_revision = "071_agent_plans"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "harness_approval_proposals",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("proposal_id", sa.String(64), nullable=False),
        sa.Column("conversation_id", sa.Integer(), sa.ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("owner_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("message_id", sa.Integer(), sa.ForeignKey("conversation_messages.id", ondelete="CASCADE"), nullable=False),
        sa.Column("turn_claim_id", sa.Integer(), sa.ForeignKey("harness_conversation_turn_claims.id", ondelete="CASCADE"), nullable=False),
        sa.Column("operation_key", sa.String(255), nullable=False),
        sa.Column("capability_id", sa.String(160), nullable=False),
        sa.Column("semantic_name", sa.String(64), nullable=False),
        sa.Column("arguments", sa.JSON(), nullable=False),
        sa.Column("arguments_hash", sa.String(64), nullable=False),
        sa.Column("project_scope", sa.JSON(), nullable=False),
        sa.Column("required_permissions", sa.JSON(), nullable=False),
        sa.Column("risk_tier", sa.String(40), nullable=False),
        sa.Column("target_version", sa.Integer()),
        sa.Column("allowed_decisions", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="pending"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("decision", sa.String(16)),
        sa.Column("decided_by_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("proposal_id", name="uq_harness_approval_proposals_public_id"),
        sa.UniqueConstraint("operation_key", name="uq_harness_approval_proposals_operation"),
    )
    op.create_index("ix_harness_approval_proposals_owner_status", "harness_approval_proposals", ["owner_user_id", "status"])
    op.create_index("ix_harness_approval_proposals_conversation", "harness_approval_proposals", ["conversation_id", "id"])

    op.create_table(
        "harness_effect_operations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("operation_id", sa.String(80), nullable=False),
        sa.Column("proposal_id", sa.Integer(), sa.ForeignKey("harness_approval_proposals.id", ondelete="SET NULL")),
        sa.Column("turn_claim_id", sa.Integer(), sa.ForeignKey("harness_conversation_turn_claims.id", ondelete="CASCADE"), nullable=False),
        sa.Column("call_id", sa.String(160), nullable=False),
        sa.Column("capability_id", sa.String(160), nullable=False),
        sa.Column("arguments_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="prepared"),
        sa.Column("observation", sa.JSON()),
        sa.Column("receipt_ref", sa.JSON()),
        sa.Column("workflow_ref", sa.JSON()),
        sa.Column("prepared_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("committed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("operation_id", name="uq_harness_effect_operations_public_id"),
        sa.UniqueConstraint("turn_claim_id", "call_id", name="uq_harness_effect_operations_claim_call"),
        sa.UniqueConstraint("proposal_id", name="uq_harness_effect_operations_proposal"),
    )
    op.create_index("ix_harness_effect_operations_status", "harness_effect_operations", ["status"])

    op.add_column("harness_agent_plan_steps", sa.Column("effect_operation_id", sa.String(80)))
    op.add_column("harness_agent_plan_steps", sa.Column("capability_receipt_ref", sa.JSON()))
    op.add_column("harness_agent_plan_steps", sa.Column("workflow_run_id", sa.Integer(), sa.ForeignKey("harness_workflow_runs.id", ondelete="SET NULL")))
    op.add_column("harness_agent_plan_steps", sa.Column("started_at", sa.DateTime(timezone=True)))
    op.add_column("harness_agent_plan_steps", sa.Column("finished_at", sa.DateTime(timezone=True)))


def downgrade() -> None:
    op.drop_column("harness_agent_plan_steps", "finished_at")
    op.drop_column("harness_agent_plan_steps", "started_at")
    op.drop_column("harness_agent_plan_steps", "workflow_run_id")
    op.drop_column("harness_agent_plan_steps", "capability_receipt_ref")
    op.drop_column("harness_agent_plan_steps", "effect_operation_id")
    op.drop_index("ix_harness_effect_operations_status", table_name="harness_effect_operations")
    op.drop_table("harness_effect_operations")
    op.drop_index("ix_harness_approval_proposals_conversation", table_name="harness_approval_proposals")
    op.drop_index("ix_harness_approval_proposals_owner_status", table_name="harness_approval_proposals")
    op.drop_table("harness_approval_proposals")
