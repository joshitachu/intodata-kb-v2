"""Add plugin_packs table and plugin_pack_id FK on projects.

Revision ID: 014_add_plugin_packs
Revises: 013_enterprise_verdict
Create Date: 2026-05-24

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "014_add_plugin_packs"
down_revision: Union[str, None] = "013_enterprise_verdict"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "plugin_packs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(100), nullable=False, unique=True),
        sa.Column("version", sa.String(50), nullable=False),
        sa.Column("path", sa.String(1000), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("repo_url", sa.String(500), nullable=True),
        sa.Column("last_commit_sha", sa.String(40), nullable=True),
        sa.Column("auto_update", sa.Boolean(), nullable=False, server_default="true"),
    )
    op.add_column(
        "projects",
        sa.Column(
            "plugin_pack_id",
            sa.Integer(),
            sa.ForeignKey("plugin_packs.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("projects", "plugin_pack_id")
    op.drop_table("plugin_packs")
