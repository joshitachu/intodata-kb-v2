"""Add model_name column to generate_runs table.

Revision ID: 003_model_name
Revises: 002_template_tier
Create Date: 2026-04-03

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "003_model_name"
down_revision: Union[str, None] = "002_template_tier"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "generate_runs",
        sa.Column("model_name", sa.String(100), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("generate_runs", "model_name")
