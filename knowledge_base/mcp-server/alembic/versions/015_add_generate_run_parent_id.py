"""Add parent_run_id to generate_runs for single-phase reruns.

Revision ID: 015_add_generate_run_parent_id
Revises: 014_add_plugin_packs
Create Date: 2026-05-29

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "015_add_generate_run_parent_id"
down_revision: Union[str, None] = "014_add_plugin_packs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "generate_runs",
        sa.Column("parent_run_id", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("generate_runs", "parent_run_id")
