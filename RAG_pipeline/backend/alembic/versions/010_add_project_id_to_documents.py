"""add project_id column to documents table

Revision ID: 010
Revises: 009
Create Date: 2026-03-12
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "010"
down_revision: Union[str, None] = "009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("project_id", sa.String(255), nullable=True))
    op.create_index("ix_documents_project_id", "documents", ["project_id"], if_not_exists=True)


def downgrade() -> None:
    op.drop_index("ix_documents_project_id", table_name="documents")
    op.drop_column("documents", "project_id")
