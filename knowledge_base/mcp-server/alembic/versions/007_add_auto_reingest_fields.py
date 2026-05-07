"""Add Git auto re-ingestion tracking fields.

Revision ID: 007_auto_reingest
Revises: 006_quality_score
Create Date: 2026-05-07

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "007_auto_reingest"
down_revision: Union[str, None] = "006_quality_score"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column("last_ingested_commit_sha", sa.String(length=40), nullable=True),
    )
    op.add_column(
        "projects",
        sa.Column(
            "auto_reingest_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
    )
    op.add_column(
        "projects",
        sa.Column("auto_reingest_last_checked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "projects",
        sa.Column("auto_reingest_error", sa.Text(), nullable=True),
    )
    op.alter_column("projects", "auto_reingest_enabled", server_default=None)


def downgrade() -> None:
    op.drop_column("projects", "auto_reingest_error")
    op.drop_column("projects", "auto_reingest_last_checked_at")
    op.drop_column("projects", "auto_reingest_enabled")
    op.drop_column("projects", "last_ingested_commit_sha")
