"""Add project_group_scan_hashes table for fingerprint-based dirty detection.

Revision ID: 017_add_project_group_scan_hashes
Revises: 016_plugin_pack_branch_metadata
Create Date: 2026-06-30

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "017_add_project_group_scan_hashes"
down_revision: Union[str, None] = "016_plugin_pack_branch_metadata"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "project_group_scan_hashes",
        sa.Column(
            "project_id",
            sa.String(100),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            primary_key=True,
            nullable=False,
        ),
        sa.Column("group_name", sa.String(255), primary_key=True, nullable=False),
        sa.Column("scan_hash", sa.String(64), nullable=False),
        sa.Column("scanned_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_project_group_scan_hashes_project_id",
        "project_group_scan_hashes",
        ["project_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_project_group_scan_hashes_project_id",
        table_name="project_group_scan_hashes",
    )
    op.drop_table("project_group_scan_hashes")
