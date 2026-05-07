"""Add doc_generation_provider column to projects table.

Revision ID: 004_doc_generation_provider
Revises: 003_model_name
Create Date: 2026-04-21

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "004_doc_generation_provider"
down_revision: Union[str, None] = "003_model_name"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column("doc_generation_provider", sa.String(100), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("projects", "doc_generation_provider")
