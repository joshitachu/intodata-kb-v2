"""Add agent chat idempotency links and observational cutover audit.

Revision ID: 068_agent_chat_cutover_audit
Revises: 067_unified_conversation_state
Create Date: 2026-07-19
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "068_agent_chat_cutover_audit"
down_revision = "067_unified_conversation_state"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "conversation_messages",
        sa.Column("client_turn_id", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "conversation_messages",
        sa.Column("client_turn_fingerprint", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "conversation_messages",
        sa.Column("in_reply_to_message_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_conversation_messages_in_reply_to",
        "conversation_messages",
        "conversation_messages",
        ["in_reply_to_message_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_unique_constraint(
        "uq_conversation_messages_client_turn",
        "conversation_messages",
        ["conversation_id", "client_turn_id"],
    )
    op.create_unique_constraint(
        "uq_conversation_messages_in_reply_to",
        "conversation_messages",
        ["in_reply_to_message_id"],
    )
    op.create_table(
        "harness_agent_turn_audits",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "conversation_id",
            sa.Integer(),
            sa.ForeignKey("conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "owner_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_message_id",
            sa.Integer(),
            sa.ForeignKey("conversation_messages.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "assistant_message_id",
            sa.Integer(),
            sa.ForeignKey("conversation_messages.id", ondelete="SET NULL"),
            nullable=True,
            unique=True,
        ),
        sa.Column("route", sa.String(length=16), nullable=False),
        sa.Column("cohort_source", sa.String(length=32), nullable=False),
        sa.Column("cohort_bucket", sa.Integer(), nullable=False),
        sa.Column("transport", sa.String(length=16), nullable=False),
        sa.Column("state_version", sa.String(length=64), nullable=True),
        sa.Column("terminal_action", sa.String(length=32), nullable=True),
        sa.Column("stop_reason", sa.String(length=64), nullable=True),
        sa.Column("capability_ids", sa.JSON(), nullable=False),
        sa.Column("observation_statuses", sa.JSON(), nullable=False),
        sa.Column("response_fingerprint", sa.String(length=64), nullable=True),
        sa.Column("flags", sa.JSON(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tool_calls", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cost_usd", sa.Float(), nullable=False, server_default="0"),
        sa.Column("latency_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("shadow_result", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "cohort_bucket >= 0 AND cohort_bucket < 10000",
            name="ck_harness_agent_turn_audit_bucket",
        ),
    )
    op.create_index(
        "ix_harness_agent_turn_audits_created",
        "harness_agent_turn_audits",
        ["created_at"],
    )
    op.create_index(
        "ix_harness_agent_turn_audits_route",
        "harness_agent_turn_audits",
        ["route", "transport"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_harness_agent_turn_audits_route",
        table_name="harness_agent_turn_audits",
    )
    op.drop_index(
        "ix_harness_agent_turn_audits_created",
        table_name="harness_agent_turn_audits",
    )
    op.drop_table("harness_agent_turn_audits")
    op.drop_constraint(
        "uq_conversation_messages_in_reply_to",
        "conversation_messages",
        type_="unique",
    )
    op.drop_constraint(
        "uq_conversation_messages_client_turn",
        "conversation_messages",
        type_="unique",
    )
    op.drop_constraint(
        "fk_conversation_messages_in_reply_to",
        "conversation_messages",
        type_="foreignkey",
    )
    op.drop_column("conversation_messages", "in_reply_to_message_id")
    op.drop_column("conversation_messages", "client_turn_id")
    op.drop_column("conversation_messages", "client_turn_fingerprint")
