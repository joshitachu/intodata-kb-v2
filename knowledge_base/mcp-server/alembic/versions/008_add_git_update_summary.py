"""Add latest Git update summary to projects.

Revision ID: 008_git_update_summary
Revises: 007_auto_reingest
Create Date: 2026-05-07

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "008_git_update_summary"
down_revision: Union[str, None] = "007_auto_reingest"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    json_type = postgresql.JSONB() if bind.dialect.name == "postgresql" else sa.JSON()
    op.add_column(
        "projects",
        sa.Column("git_update_summary", json_type, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("projects", "git_update_summary")
