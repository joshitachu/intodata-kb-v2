"""Add deleted_at soft-delete column to documents, conversations, conversation_messages

Revision ID: 024
Revises: 023
Create Date: 2026-05-12
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "024"
down_revision: Union[str, None] = "023"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    for table in ("documents", "conversations", "conversation_messages"):
        op.add_column(
            table,
            sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        )


def downgrade() -> None:
    for table in ("documents", "conversations", "conversation_messages"):
        op.drop_column(table, "deleted_at")
