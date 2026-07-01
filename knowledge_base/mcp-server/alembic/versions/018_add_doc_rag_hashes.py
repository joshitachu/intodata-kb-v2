"""Add project_doc_rag_hashes table for content-hash-based RAG push skip.

Revision ID: 018_add_doc_rag_hashes
Revises: 017_add_project_group_scan_hashes
Create Date: 2026-06-30

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "018_add_doc_rag_hashes"
down_revision: Union[str, None] = "017_add_project_group_scan_hashes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "project_doc_rag_hashes",
        sa.Column(
            "project_id",
            sa.String(100),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            primary_key=True,
            nullable=False,
        ),
        sa.Column("doc_filename", sa.String(500), primary_key=True, nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column(
            "pushed_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_project_doc_rag_hashes_project_id",
        "project_doc_rag_hashes",
        ["project_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_project_doc_rag_hashes_project_id",
        table_name="project_doc_rag_hashes",
    )
    op.drop_table("project_doc_rag_hashes")
