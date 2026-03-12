"""add performance indexes for common queries

Revision ID: 009
Revises: 008
Create Date: 2026-03-11
"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "009"
down_revision: Union[str, None] = "008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # query_usage.created_at — used by admin usage dashboard queries
    op.create_index(
        "ix_query_usage_created_at",
        "query_usage",
        ["created_at"],
        if_not_exists=True,
    )

    # audit_logs.created_at — used by audit log queries (ORDER BY + filter)
    op.create_index(
        "ix_audit_logs_created_at",
        "audit_logs",
        ["created_at"],
        if_not_exists=True,
    )

    # documents.filename — used by replace-on-re-upload lookup
    op.create_index(
        "ix_documents_filename",
        "documents",
        ["filename"],
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_index("ix_documents_filename", table_name="documents")
    op.drop_index("ix_audit_logs_created_at", table_name="audit_logs")
    op.drop_index("ix_query_usage_created_at", table_name="query_usage")
