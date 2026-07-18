"""Align private Pack tables with the shared ORM timestamp contract.

Revision ID: 063_pack_builder_timestamps
Revises: 062_pack_draft_builder
Create Date: 2026-07-17
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "063_pack_builder_timestamps"
down_revision = "062_pack_draft_builder"
branch_labels = None
depends_on = None


_TABLES = (
    "harness_private_analysis_packs",
    "harness_private_analysis_pack_versions",
    "harness_private_analysis_pack_shares",
    "harness_pack_preview_receipts",
    "harness_pack_promotion_proposals",
)


def upgrade() -> None:
    for table_name in _TABLES:
        op.add_column(
            table_name,
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
        )


def downgrade() -> None:
    for table_name in reversed(_TABLES):
        op.drop_column(table_name, "updated_at")
