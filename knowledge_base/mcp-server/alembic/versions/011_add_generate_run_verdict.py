"""Add auto_verdict and auto_baseline_run_id to generate_runs.

Revision ID: 011_generate_run_verdict
Revises: 010_instruction_pack_dir
Create Date: 2026-05-11

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "011_generate_run_verdict"
down_revision: Union[str, None] = "010_instruction_pack_dir"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("generate_runs", sa.Column("auto_verdict", sa.String(10), nullable=True))
    op.add_column("generate_runs", sa.Column("auto_baseline_run_id", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("generate_runs", "auto_baseline_run_id")
    op.drop_column("generate_runs", "auto_verdict")
