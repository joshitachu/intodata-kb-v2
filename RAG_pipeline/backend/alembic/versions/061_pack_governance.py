"""Persist organization Pack governance and presentation-only Style Packs.

Revision ID: 061_pack_governance
Revises: 060_runtime_logs_storage
Create Date: 2026-07-16
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "061_pack_governance"
down_revision: Union[str, None] = "060_runtime_logs_storage"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_DEFAULT_POLICY = {
    "id": "org.default",
    "required_certified_pack_ids": ["nexus.documentation"],
    "allowed_functional_pack_ids": ["nexus.data-integration"],
    "permitted_style": {
        "permitted_fields": [
            "audience", "tone", "technical_depth", "output_organization", "formatting", "communication"
        ],
        "allowed_audiences": ["executive", "business", "engineering", "operations"],
        "allowed_tones": ["neutral", "concise", "explanatory"],
        "allowed_technical_depths": ["summary", "standard", "deep"],
        "allowed_output_organizations": ["direct_answer", "sectioned", "incident_report"],
        "allowed_formatting": ["markdown", "bulleted_lists", "tables", "code_blocks"],
        "allowed_communication": ["qualify_uncertainty", "action_oriented", "include_sources"],
    },
    "minimum_quality": {"quality_score": 0.8, "evaluation_score": 0.8},
    "protected_invariants": [
        "evidence_policy", "findings", "entity_semantics", "required_analysis", "security_redaction",
        "permissions", "risk_tiers", "quality_thresholds", "capabilities",
    ],
}


def upgrade() -> None:
    # The old table remains intact but is explicitly quarantined: its broad manifests
    # are not reinterpreted as presentation-only Style Packs.
    with op.batch_alter_table("harness_custom_pack_versions") as batch:
        batch.add_column(
            sa.Column("governance_state", sa.String(24), nullable=False, server_default="legacy")
        )

    op.create_table(
        "harness_organization_governance_policies",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("organization_id", sa.String(80), nullable=False, server_default="default"),
        sa.Column("policy", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("organization_id", name="uq_harness_org_governance_policy"),
    )
    op.bulk_insert(
        sa.table(
            "harness_organization_governance_policies",
            sa.column("organization_id", sa.String()),
            sa.column("policy", sa.JSON()),
        ),
        [{"organization_id": "default", "policy": _DEFAULT_POLICY}],
    )

    op.create_table(
        "harness_style_pack_versions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("pack_id", sa.String(80), nullable=False),
        sa.Column("version", sa.String(64), nullable=False),
        sa.Column("scope", sa.String(16), nullable=False),
        sa.Column("project_id", sa.String(255), nullable=True),
        sa.Column("owner_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("presentation", sa.JSON(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("quality_score", sa.Float(), nullable=True),
        sa.Column("evaluation_score", sa.Float(), nullable=True),
        sa.Column("evaluation_metadata", sa.JSON(), nullable=True),
        sa.Column("evaluated_by_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("review_status", sa.String(32), nullable=False, server_default="draft"),
        sa.Column("promotion_eligible", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("quality_score IS NULL OR (quality_score >= 0 AND quality_score <= 1)", name="ck_harness_style_pack_quality_score"),
        sa.CheckConstraint("evaluation_score IS NULL OR (evaluation_score >= 0 AND evaluation_score <= 1)", name="ck_harness_style_pack_evaluation_score"),
    )
    op.create_index("ix_harness_style_pack_versions_scope_owner", "harness_style_pack_versions", ["scope", "owner_user_id"])
    op.create_index("ix_harness_style_pack_versions_project", "harness_style_pack_versions", ["project_id", "pack_id"])
    op.create_index(
        "uq_harness_style_user_pack_version",
        "harness_style_pack_versions",
        ["owner_user_id", "pack_id", "version"],
        unique=True,
        postgresql_where=sa.text("scope = 'user'"),
    )
    op.create_index(
        "uq_harness_style_project_pack_version",
        "harness_style_pack_versions",
        ["project_id", "pack_id", "version"],
        unique=True,
        postgresql_where=sa.text("scope = 'project'"),
    )

    with op.batch_alter_table("harness_pack_activation_snapshots") as batch:
        batch.add_column(sa.Column("stack_selection", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("compiled_stack_snapshot", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("compiled_stack_hash", sa.String(64), nullable=True))
        batch.add_column(sa.Column("compiled_presentation", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("harness_pack_activation_snapshots") as batch:
        batch.drop_column("compiled_presentation")
        batch.drop_column("compiled_stack_hash")
        batch.drop_column("compiled_stack_snapshot")
        batch.drop_column("stack_selection")
    op.drop_index("uq_harness_style_project_pack_version", table_name="harness_style_pack_versions")
    op.drop_index("uq_harness_style_user_pack_version", table_name="harness_style_pack_versions")
    op.drop_index("ix_harness_style_pack_versions_project", table_name="harness_style_pack_versions")
    op.drop_index("ix_harness_style_pack_versions_scope_owner", table_name="harness_style_pack_versions")
    op.drop_table("harness_style_pack_versions")
    op.drop_table("harness_organization_governance_policies")
    with op.batch_alter_table("harness_custom_pack_versions") as batch:
        batch.drop_column("governance_state")
