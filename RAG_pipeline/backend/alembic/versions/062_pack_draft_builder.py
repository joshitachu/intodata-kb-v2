"""Persist private V3 Analysis Pack drafts, versions, shares, and receipts.

Revision ID: 062_pack_draft_builder
Revises: 061_pack_governance
Create Date: 2026-07-17
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "062_pack_draft_builder"
down_revision = "061_pack_governance"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "harness_pack_drafts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("conversation_id", sa.Integer(), sa.ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("owner_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("artifact", sa.JSON(), nullable=False),
        sa.Column("validation", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_harness_pack_drafts_conversation_owner", "harness_pack_drafts", ["conversation_id", "owner_user_id"])
    op.create_index("ix_harness_pack_drafts_owner_status", "harness_pack_drafts", ["owner_user_id", "status"])
    op.create_table(
        "harness_private_analysis_packs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("pack_id", sa.String(80), nullable=False),
        sa.Column("owner_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("owner_user_id", "pack_id", name="uq_harness_private_analysis_pack_owner_id"),
    )
    op.create_index("ix_harness_private_analysis_packs_owner", "harness_private_analysis_packs", ["owner_user_id"])
    op.create_table(
        "harness_private_analysis_pack_versions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("private_pack_id", sa.Integer(), sa.ForeignKey("harness_private_analysis_packs.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("version", sa.String(64), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("artifact", sa.JSON(), nullable=False),
        sa.Column("receipt", sa.JSON(), nullable=False),
        sa.Column("created_by_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("private_pack_id", "version", name="uq_harness_private_analysis_pack_version"),
    )
    op.create_index("ix_harness_private_analysis_pack_versions_pack", "harness_private_analysis_pack_versions", ["private_pack_id"])
    op.create_table(
        "harness_private_analysis_pack_shares",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("private_pack_version_id", sa.Integer(), sa.ForeignKey("harness_private_analysis_pack_versions.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("project_id", sa.String(255), nullable=False),
        sa.Column("shared_by_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("private_pack_version_id", "project_id", name="uq_harness_private_analysis_pack_share"),
    )
    op.create_table(
        "harness_pack_preview_receipts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("private_pack_version_id", sa.Integer(), sa.ForeignKey("harness_private_analysis_pack_versions.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("project_id", sa.String(255), nullable=False),
        sa.Column("receipt", sa.JSON(), nullable=False),
        sa.Column("created_by_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        "harness_pack_promotion_proposals",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("private_pack_version_id", sa.Integer(), sa.ForeignKey("harness_private_analysis_pack_versions.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("detail", sa.JSON(), nullable=False),
        sa.Column("proposed_by_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    with op.batch_alter_table("harness_pack_activation_snapshots") as batch:
        batch.add_column(sa.Column("private_version_receipts", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("harness_pack_activation_snapshots") as batch:
        batch.drop_column("private_version_receipts")
    op.drop_table("harness_pack_promotion_proposals")
    op.drop_table("harness_pack_preview_receipts")
    op.drop_table("harness_private_analysis_pack_shares")
    op.drop_index("ix_harness_private_analysis_pack_versions_pack", table_name="harness_private_analysis_pack_versions")
    op.drop_table("harness_private_analysis_pack_versions")
    op.drop_index("ix_harness_private_analysis_packs_owner", table_name="harness_private_analysis_packs")
    op.drop_table("harness_private_analysis_packs")
    op.drop_index("ix_harness_pack_drafts_owner_status", table_name="harness_pack_drafts")
    op.drop_index("ix_harness_pack_drafts_conversation_owner", table_name="harness_pack_drafts")
    op.drop_table("harness_pack_drafts")
