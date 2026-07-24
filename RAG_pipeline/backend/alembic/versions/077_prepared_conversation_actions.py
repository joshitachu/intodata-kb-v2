"""Add bounded prepared action state to conversations.

Revision ID: 077_prepared_actions
Revises: 076_agent_trace_explorer
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "077_prepared_actions"
down_revision = "076_agent_trace_explorer"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "harness_conversation_states",
        sa.Column("prepared_action", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("harness_conversation_states", "prepared_action")
