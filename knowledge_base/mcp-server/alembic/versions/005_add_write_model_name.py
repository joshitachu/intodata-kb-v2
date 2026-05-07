"""Add write_model_name column to generate_runs table.

Revision ID: 005_write_model_name
Revises: 004_doc_generation_provider
Create Date: 2026-04-23

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "005_write_model_name"
down_revision: Union[str, None] = "004_doc_generation_provider"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "generate_runs",
        sa.Column("write_model_name", sa.String(100), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("generate_runs", "write_model_name")
