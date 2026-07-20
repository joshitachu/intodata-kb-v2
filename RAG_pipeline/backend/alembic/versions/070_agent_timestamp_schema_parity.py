"""Reconcile inherited ORM timestamps on agent runtime tables.

Revision ID: 070_agent_timestamp_parity
Revises: 069_retire_legacy_chat_paths
Create Date: 2026-07-19
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "070_agent_timestamp_parity"
down_revision = "069_retire_legacy_chat_paths"
branch_labels = None
depends_on = None


def _timestamp_column(name: str) -> sa.Column:
    return sa.Column(
        name,
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    )


def upgrade() -> None:
    # All ORM rows inherit created_at/updated_at from src.db.base.Base. These
    # tables predated an executable metadata-parity check and omitted some of
    # those inherited columns from migrations 066/067.
    op.add_column(
        "harness_conversation_turn_claims",
        _timestamp_column("created_at"),
    )
    op.add_column(
        "harness_conversation_turn_claims",
        _timestamp_column("updated_at"),
    )
    op.add_column(
        "harness_conversation_state_transitions",
        _timestamp_column("updated_at"),
    )
    op.add_column(
        "harness_workflow_child_observations",
        _timestamp_column("created_at"),
    )
    op.add_column(
        "harness_workflow_child_observations",
        _timestamp_column("updated_at"),
    )


def downgrade() -> None:
    op.drop_column("harness_workflow_child_observations", "updated_at")
    op.drop_column("harness_workflow_child_observations", "created_at")
    op.drop_column("harness_conversation_state_transitions", "updated_at")
    op.drop_column("harness_conversation_turn_claims", "updated_at")
    op.drop_column("harness_conversation_turn_claims", "created_at")
