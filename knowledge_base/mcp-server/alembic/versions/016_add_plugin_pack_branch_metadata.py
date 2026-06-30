"""Add branch metadata to plugin packs.

Revision ID: 016_plugin_pack_branch_metadata
Revises: 015_add_generate_run_parent_id
Create Date: 2026-06-24

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "016_plugin_pack_branch_metadata"
down_revision: Union[str, None] = "015_add_generate_run_parent_id"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "plugin_packs",
        sa.Column("manifest_name", sa.String(100), nullable=True),
    )
    op.add_column(
        "plugin_packs",
        sa.Column("repo_branch", sa.String(255), nullable=True),
    )
    op.execute("UPDATE plugin_packs SET manifest_name = name WHERE manifest_name IS NULL")


def downgrade() -> None:
    op.drop_column("plugin_packs", "repo_branch")
    op.drop_column("plugin_packs", "manifest_name")
