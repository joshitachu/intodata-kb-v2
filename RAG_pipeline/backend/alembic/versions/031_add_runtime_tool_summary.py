"""Add runtime_tool_summary to conversation_messages (runtime tool memory).

Additive only: a nullable JSON column holding a compact, sanitized summary of the
runtime executions a turn surfaced, so later turns can resolve references like "that
execution". No existing column is altered.

Revision ID: 031
Revises: 030
Create Date: 2026-06-16
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "031"
down_revision: Union[str, None] = "030"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "conversation_messages",
        sa.Column("runtime_tool_summary", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("conversation_messages", "runtime_tool_summary")
