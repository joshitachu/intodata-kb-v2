"""Add github_token_enc and github_webhook_id to projects.

Revision ID: 009_github_token_webhook
Revises: 008_git_update_summary
Create Date: 2026-05-08

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "009_github_token_webhook"
down_revision: Union[str, None] = "008_git_update_summary"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("projects", sa.Column("github_token_enc", sa.String(500), nullable=True))
    op.add_column("projects", sa.Column("github_webhook_id", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("projects", "github_webhook_id")
    op.drop_column("projects", "github_token_enc")
