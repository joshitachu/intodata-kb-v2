"""Add instruction_pack_dir to projects.

Revision ID: 010_instruction_pack_dir
Revises: 009_github_token_webhook
Create Date: 2026-05-11

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "010_instruction_pack_dir"
down_revision: Union[str, None] = "009_github_token_webhook"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("projects", sa.Column("instruction_pack_dir", sa.String(500), nullable=True))


def downgrade() -> None:
    op.drop_column("projects", "instruction_pack_dir")
