"""Add status metadata to conversation messages.

Revision ID: 027
Revises: 026
Create Date: 2026-05-21
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "027"
down_revision: Union[str, None] = "026"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "conversation_messages",
        sa.Column(
            "status",
            sa.String(20),
            nullable=False,
            server_default="completed",
        ),
    )
    op.add_column(
        "conversation_messages",
        sa.Column("error_message", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("conversation_messages", "error_message")
    op.drop_column("conversation_messages", "status")
