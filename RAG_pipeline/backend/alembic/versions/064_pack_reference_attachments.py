"""Persist owner-private Pack reference blobs and bindings.

Revision ID: 064_pack_reference_attachments
Revises: 063_pack_builder_timestamps
Create Date: 2026-07-17
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "064_pack_reference_attachments"
down_revision = "063_pack_builder_timestamps"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("harness_pack_drafts") as batch:
        batch.add_column(sa.Column("target_private_pack_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("base_private_pack_version_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_harness_pack_drafts_target_private_pack",
            "harness_private_analysis_packs",
            ["target_private_pack_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch.create_foreign_key(
            "fk_harness_pack_drafts_base_private_pack_version",
            "harness_private_analysis_pack_versions",
            ["base_private_pack_version_id"],
            ["id"],
            ondelete="RESTRICT",
        )

    op.create_table(
        "harness_pack_attachment_blobs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("owner_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("safe_filename", sa.String(255), nullable=False),
        sa.Column("content_type", sa.String(120), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("content", sa.LargeBinary(), nullable=False),
        sa.Column("extracted_text", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("owner_user_id", "sha256", name="uq_harness_pack_attachment_blob_owner_sha256"),
    )
    op.create_index("ix_harness_pack_attachment_blobs_owner", "harness_pack_attachment_blobs", ["owner_user_id"])
    op.create_table(
        "harness_pack_attachment_bindings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("blob_id", sa.Integer(), sa.ForeignKey("harness_pack_attachment_blobs.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("draft_id", sa.Integer(), sa.ForeignKey("harness_pack_drafts.id", ondelete="CASCADE"), nullable=True),
        sa.Column("private_pack_version_id", sa.Integer(), sa.ForeignKey("harness_private_analysis_pack_versions.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("purpose", sa.String(500), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "(draft_id IS NOT NULL AND private_pack_version_id IS NULL) OR "
            "(draft_id IS NULL AND private_pack_version_id IS NOT NULL)",
            name="ck_harness_pack_attachment_binding_one_target",
        ),
        sa.UniqueConstraint("draft_id", "blob_id", "purpose", name="uq_harness_pack_attachment_draft_blob_purpose"),
        sa.UniqueConstraint("private_pack_version_id", "blob_id", "purpose", name="uq_harness_pack_attachment_version_blob_purpose"),
    )
    op.create_index("ix_harness_pack_attachment_bindings_draft", "harness_pack_attachment_bindings", ["draft_id"])
    op.create_index("ix_harness_pack_attachment_bindings_version", "harness_pack_attachment_bindings", ["private_pack_version_id"])


def downgrade() -> None:
    op.drop_index("ix_harness_pack_attachment_bindings_version", table_name="harness_pack_attachment_bindings")
    op.drop_index("ix_harness_pack_attachment_bindings_draft", table_name="harness_pack_attachment_bindings")
    op.drop_table("harness_pack_attachment_bindings")
    op.drop_index("ix_harness_pack_attachment_blobs_owner", table_name="harness_pack_attachment_blobs")
    op.drop_table("harness_pack_attachment_blobs")
    with op.batch_alter_table("harness_pack_drafts") as batch:
        batch.drop_constraint("fk_harness_pack_drafts_base_private_pack_version", type_="foreignkey")
        batch.drop_constraint("fk_harness_pack_drafts_target_private_pack", type_="foreignkey")
        batch.drop_column("base_private_pack_version_id")
        batch.drop_column("target_private_pack_id")
