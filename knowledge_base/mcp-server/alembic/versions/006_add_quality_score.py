"""Add quality_score column to generate_runs table.

Revision ID: 006_quality_score
Revises: 005_write_model_name
Create Date: 2026-04-23

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "006_quality_score"
down_revision: Union[str, None] = "005_write_model_name"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "generate_runs",
        sa.Column("quality_score", sa.Float(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("generate_runs", "quality_score")
