"""The unified Pack registry: one pack table, one version table, one activation table.

Program spec docs/one-pack-program.md section 11 item 1. Additive ONLY: this
revision creates `packs`, `pack_versions` and `pack_activations` and touches
nothing else. The thirteen pack tables from 059-074 keep working for the whole
migration window; collapsing them is a later, orchestrator-owned stage.

`downgrade()` drops exactly the three tables and their indexes, in reverse
dependency order, so the revision is reversible on a populated database.

Revision ID: 084_unified_pack_registry
Revises: 083_conversation_page_scope
Create Date: 2026-08-20
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "084_unified_pack_registry"
down_revision: Union[str, None] = "083_conversation_page_scope"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # `packs` -- one logical Pack identity. `scope` is the AUTHORING scope;
    # `visibility` is the ladder rung (user -> project -> organization ->
    # certified). Every FK is RESTRICT: delete means DELIST, not purge.
    op.create_table(
        "packs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("pack_id", sa.String(200), nullable=False),
        sa.Column("layer", sa.String(20), nullable=False),
        sa.Column("owner_kind", sa.String(20), nullable=False),
        sa.Column("scope", sa.String(20), nullable=False),
        sa.Column("visibility", sa.String(20), nullable=False, server_default="user"),
        sa.Column(
            "owner_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("project_id", sa.String(255), nullable=True),
        sa.Column("organization_id", sa.String(80), nullable=True),
        sa.Column("visibility_project_id", sa.String(255), nullable=True),
        sa.Column("visibility_organization_id", sa.String(80), nullable=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column(
            "created_by_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
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
        sa.UniqueConstraint("pack_id", name="uq_packs_pack_id"),
        sa.CheckConstraint(
            "scope <> 'user' OR owner_user_id IS NOT NULL",
            name="ck_packs_user_scope_requires_owner",
        ),
        sa.CheckConstraint(
            "scope <> 'project' OR project_id IS NOT NULL",
            name="ck_packs_project_scope_requires_project",
        ),
        sa.CheckConstraint(
            "scope <> 'organization' OR organization_id IS NOT NULL",
            name="ck_packs_org_scope_requires_organization",
        ),
    )
    op.create_index("ix_packs_scope_status", "packs", ["scope", "status"])
    op.create_index("ix_packs_owner_user", "packs", ["owner_user_id"])
    op.create_index("ix_packs_project", "packs", ["project_id"])

    # `pack_versions` -- immutable content. `preview_receipt` / `preview_gate`
    # are the activation gate's only input (spec section 9: preview-and-score
    # before activation).
    op.create_table(
        "pack_versions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "pack_row_id",
            sa.Integer(),
            sa.ForeignKey("packs.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("version", sa.String(64), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False, server_default="2"),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("manifest", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="draft"),
        sa.Column("quality_score", sa.Float(), nullable=True),
        sa.Column("estimated_calls", sa.Integer(), nullable=True),
        sa.Column("estimated_input_tokens", sa.Integer(), nullable=True),
        sa.Column("estimated_output_tokens", sa.Integer(), nullable=True),
        sa.Column("preview_receipt", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("preview_gate", sa.String(10), nullable=True),
        sa.Column(
            "created_by_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("pack_row_id", "version", name="uq_pack_versions_pack_version"),
        sa.UniqueConstraint("content_hash", name="uq_pack_versions_content_hash"),
        sa.CheckConstraint(
            "quality_score IS NULL OR (quality_score >= 0 AND quality_score <= 1)",
            name="ck_pack_versions_quality_score",
        ),
    )
    op.create_index("ix_pack_versions_pack_row", "pack_versions", ["pack_row_id", "status"])

    # `pack_activations` -- append-only history. The partial unique index is
    # what makes "exactly one current activation" a database invariant rather
    # than an application convention; the literal predicate form mirrors
    # 061_pack_governance.py and works on PostgreSQL and SQLite alike.
    op.create_table(
        "pack_activations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.String(255), nullable=False),
        sa.Column(
            "workflow_key",
            sa.String(80),
            nullable=False,
            server_default="documentation.generate",
        ),
        sa.Column(
            "pack_version_id",
            sa.Integer(),
            sa.ForeignKey("pack_versions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column(
            "activated_by_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "activated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("deactivated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("preview_receipt", sa.JSON(), nullable=False, server_default="{}"),
    )
    op.create_index(
        "ix_pack_activations_project", "pack_activations", ["project_id", "workflow_key"]
    )
    op.create_index("ix_pack_activations_version", "pack_activations", ["pack_version_id"])
    op.create_index(
        "uq_pack_activations_current",
        "pack_activations",
        ["project_id", "workflow_key"],
        unique=True,
        postgresql_where=sa.text("deactivated_at IS NULL"),
        sqlite_where=sa.text("deactivated_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_pack_activations_current", table_name="pack_activations")
    op.drop_index("ix_pack_activations_version", table_name="pack_activations")
    op.drop_index("ix_pack_activations_project", table_name="pack_activations")
    op.drop_table("pack_activations")
    op.drop_index("ix_pack_versions_pack_row", table_name="pack_versions")
    op.drop_table("pack_versions")
    op.drop_index("ix_packs_project", table_name="packs")
    op.drop_index("ix_packs_owner_user", table_name="packs")
    op.drop_index("ix_packs_scope_status", table_name="packs")
    op.drop_table("packs")
