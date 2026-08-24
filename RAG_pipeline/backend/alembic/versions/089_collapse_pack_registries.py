"""Collapse the RAG-side pack registries: plugin.yaml is the only pack format.

Two registries are removed here.

1. The **V3 private Analysis Pack** registry (059-074). It stored a second,
   weaker pack format that could only append Phase 3 documents and could never
   change what the pipeline looked at. Everything it expressed is a subset of
   ``plugin.yaml``'s ``docs:`` block, and saved packs now live in MCP.
2. The **unified pack registry** (084/086, `packs` / `pack_versions` /
   `pack_activations`). Its schema authority
   (``mcp_server/services/pack_schema_v2.py``) was deleted before it was ever
   wired to a caller, leaving three orphan tables.

`harness_pack_drafts` SURVIVES: it is the chat/Workbench scratch space for a
manifest in progress. Its `artifact` column now holds
``{"manifest_yaml": ..., "files": {...}}`` instead of a V3 JSON artifact -- no
column change is needed, but existing rows hold the old shape and are stale
drafts, not deliverables.

Attachment bindings lose `private_pack_version_id`: a saved pack lives in MCP,
so a binding can only target a draft. The one-of-two-targets check constraint
had nothing left to choose between.

`downgrade()` recreates the structures but NOT the data. Row data was dumped to
`deploy/backups/pack_tables_pre_collapse_2026-08-23.sql` before this migration
was written; restore from there if the packs are still wanted.

Revision ID: 089_collapse_pack_registries
Revises: 088_conversation_visibility
Create Date: 2026-08-23
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "089_collapse_pack_registries"
down_revision: Union[str, None] = "088_conversation_visibility"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # -- 1. Detach the surviving tables from the V3 registry ------------------
    op.drop_constraint(
        "ck_harness_pack_attachment_binding_one_target",
        "harness_pack_attachment_bindings",
        type_="check",
    )
    op.drop_constraint(
        "uq_harness_pack_attachment_version_blob_purpose",
        "harness_pack_attachment_bindings",
        type_="unique",
    )
    op.drop_index(
        "ix_harness_pack_attachment_bindings_version",
        table_name="harness_pack_attachment_bindings",
    )
    # Any binding that targeted a saved version has no draft to belong to.
    op.execute(
        "DELETE FROM harness_pack_attachment_bindings WHERE draft_id IS NULL"
    )
    op.drop_column("harness_pack_attachment_bindings", "private_pack_version_id")
    op.alter_column(
        "harness_pack_attachment_bindings",
        "draft_id",
        existing_type=sa.Integer(),
        nullable=False,
    )

    op.drop_column("harness_pack_drafts", "base_private_pack_version_id")
    op.drop_column("harness_pack_drafts", "target_private_pack_id")

    # -- 2. Drop both registries, children before parents --------------------
    for table in (
        "harness_private_analysis_pack_shares",
        "harness_private_analysis_pack_versions",
        "harness_private_analysis_packs",
        "harness_pack_promotion_proposals",
        "harness_pack_preview_receipts",
        "harness_custom_pack_versions",
        "pack_activations",
        "pack_versions",
        "packs",
    ):
        op.execute(sa.text(f'DROP TABLE IF EXISTS "{table}" CASCADE'))


def downgrade() -> None:
    """Recreate the structures. Data is NOT restored -- see the module docstring."""
    op.create_table(
        "harness_private_analysis_packs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("pack_id", sa.String(length=80), nullable=False),
        sa.Column("owner_user_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "owner_user_id", "pack_id", name="uq_harness_private_analysis_pack_owner_id"
        ),
    )
    op.create_index(
        "ix_harness_private_analysis_packs_owner",
        "harness_private_analysis_packs",
        ["owner_user_id"],
    )
    op.create_table(
        "harness_private_analysis_pack_versions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "private_pack_id",
            sa.Integer(),
            sa.ForeignKey("harness_private_analysis_packs.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("version", sa.String(length=40), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("artifact", sa.JSON(), nullable=False),
        sa.Column("receipt", sa.JSON(), nullable=False),
        sa.Column("created_by_user_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "private_pack_id", "version", name="uq_harness_private_analysis_pack_version"
        ),
    )
    op.create_index(
        "ix_harness_private_analysis_pack_versions_pack",
        "harness_private_analysis_pack_versions",
        ["private_pack_id"],
    )
    op.create_table(
        "harness_private_analysis_pack_shares",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "private_pack_version_id",
            sa.Integer(),
            sa.ForeignKey(
                "harness_private_analysis_pack_versions.id", ondelete="RESTRICT"
            ),
            nullable=False,
        ),
        sa.Column("project_id", sa.String(length=255), nullable=False),
        sa.Column("shared_by_user_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "private_pack_version_id",
            "project_id",
            name="uq_harness_private_analysis_pack_share",
        ),
    )
    op.create_table(
        "harness_pack_preview_receipts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "private_pack_version_id",
            sa.Integer(),
            sa.ForeignKey(
                "harness_private_analysis_pack_versions.id", ondelete="RESTRICT"
            ),
            nullable=False,
        ),
        sa.Column("project_id", sa.String(length=255), nullable=False),
        sa.Column("receipt", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "harness_pack_promotion_proposals",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "private_pack_version_id",
            sa.Integer(),
            sa.ForeignKey(
                "harness_private_analysis_pack_versions.id", ondelete="RESTRICT"
            ),
            nullable=False,
        ),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "harness_custom_pack_versions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("pack_id", sa.String(length=80), nullable=False),
        sa.Column("version", sa.String(length=40), nullable=False),
        sa.Column("manifest", sa.JSON(), nullable=False),
        sa.Column("project_id", sa.String(length=255), nullable=True),
        sa.Column("created_by_user_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.add_column(
        "harness_pack_drafts", sa.Column("target_private_pack_id", sa.Integer(), nullable=True)
    )
    op.add_column(
        "harness_pack_drafts",
        sa.Column("base_private_pack_version_id", sa.Integer(), nullable=True),
    )
    op.alter_column(
        "harness_pack_attachment_bindings",
        "draft_id",
        existing_type=sa.Integer(),
        nullable=True,
    )
    op.add_column(
        "harness_pack_attachment_bindings",
        sa.Column("private_pack_version_id", sa.Integer(), nullable=True),
    )
    op.create_index(
        "ix_harness_pack_attachment_bindings_version",
        "harness_pack_attachment_bindings",
        ["private_pack_version_id"],
    )
    op.create_unique_constraint(
        "uq_harness_pack_attachment_version_blob_purpose",
        "harness_pack_attachment_bindings",
        ["private_pack_version_id", "blob_id", "purpose"],
    )
    op.create_check_constraint(
        "ck_harness_pack_attachment_binding_one_target",
        "harness_pack_attachment_bindings",
        "(draft_id IS NOT NULL AND private_pack_version_id IS NULL) OR "
        "(draft_id IS NULL AND private_pack_version_id IS NOT NULL)",
    )

    # The unified registry from 084/086 is intentionally NOT recreated here:
    # 084's own downgrade() owns those three tables.
