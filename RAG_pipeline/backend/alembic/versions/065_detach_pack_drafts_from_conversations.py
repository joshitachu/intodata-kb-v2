"""Allow owner-private Pack drafts to exist without a chat conversation.

Revision ID: 065_detached_pack_drafts
Revises: 064_pack_reference_attachments
Create Date: 2026-07-17
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "065_detached_pack_drafts"
down_revision = "064_pack_reference_attachments"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("harness_pack_drafts") as batch:
        batch.alter_column(
            "conversation_id",
            existing_type=sa.Integer(),
            existing_nullable=False,
            nullable=True,
        )
        batch.add_column(
            sa.Column(
                "discovery_context",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'{}'"),
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("harness_pack_drafts") as batch:
        batch.drop_column("discovery_context")
        batch.alter_column(
            "conversation_id",
            existing_type=sa.Integer(),
            existing_nullable=True,
            nullable=False,
        )
