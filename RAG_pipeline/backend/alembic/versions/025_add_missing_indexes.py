"""Add missing indexes for document pagination, audit log time-range, and message loading

Revision ID: 025
Revises: 024
Create Date: 2026-05-12
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "025"
down_revision: Union[str, None] = "024"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        "ix_documents_project_created",
        "documents",
        ["project_id", sa.text("created_at DESC")],
        if_not_exists=True,
    )
    op.create_index("ix_audit_logs_created_at", "audit_logs", ["created_at"], if_not_exists=True)
    op.create_index(
        "ix_conversation_messages_conversation_id",
        "conversation_messages",
        ["conversation_id"],
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_index("ix_conversation_messages_conversation_id", "conversation_messages", if_exists=True)
    op.drop_index("ix_audit_logs_created_at", "audit_logs", if_exists=True)
    op.drop_index("ix_documents_project_created", "documents", if_exists=True)
