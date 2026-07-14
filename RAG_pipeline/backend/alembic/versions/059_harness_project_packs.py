"""persist immutable custom Pack versions and project activations"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "059_harness_project_packs"
down_revision: Union[str, Sequence[str], None] = (
    "058_harness_durable_checkpoints",
    "058_nullable_harness_project",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "harness_custom_pack_versions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.String(255), nullable=False),
        sa.Column("pack_id", sa.String(80), nullable=False),
        sa.Column("version", sa.String(64), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("manifest", sa.JSON(), nullable=False),
        sa.Column("created_by_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("project_id", "pack_id", "version", name="uq_harness_custom_pack_version"),
        sa.UniqueConstraint("content_hash", name="uq_harness_custom_pack_content_hash"),
    )
    op.create_index("ix_harness_custom_pack_versions_project_pack", "harness_custom_pack_versions", ["project_id", "pack_id"])
    op.create_table(
        "harness_pack_activation_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.String(255), nullable=False),
        sa.Column("workflow_key", sa.String(80), nullable=False),
        sa.Column("active_pack_ids", sa.JSON(), nullable=False),
        sa.Column("resolved_snapshot", sa.JSON(), nullable=False),
        sa.Column("resolved_manifests", sa.JSON(), nullable=False),
        sa.Column("snapshot_hash", sa.String(64), nullable=False),
        sa.Column("created_by_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("snapshot_hash", name="uq_harness_pack_activation_snapshot_hash"),
    )
    op.create_index("ix_harness_pack_activation_snapshots_project", "harness_pack_activation_snapshots", ["project_id"])
    op.create_table(
        "harness_project_pack_activations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.String(255), nullable=False),
        sa.Column("workflow_key", sa.String(80), nullable=False),
        sa.Column("snapshot_id", sa.Integer(), sa.ForeignKey("harness_pack_activation_snapshots.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("updated_by_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("project_id", "workflow_key", name="uq_harness_project_pack_activation"),
    )
    op.create_index("ix_harness_project_pack_activations_project", "harness_project_pack_activations", ["project_id"])


def downgrade() -> None:
    op.drop_index("ix_harness_project_pack_activations_project", table_name="harness_project_pack_activations")
    op.drop_table("harness_project_pack_activations")
    op.drop_index("ix_harness_pack_activation_snapshots_project", table_name="harness_pack_activation_snapshots")
    op.drop_table("harness_pack_activation_snapshots")
    op.drop_index("ix_harness_custom_pack_versions_project_pack", table_name="harness_custom_pack_versions")
    op.drop_table("harness_custom_pack_versions")
