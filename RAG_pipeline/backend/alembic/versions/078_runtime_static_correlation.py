"""Persist bounded static topology snapshots and explicit runtime job mappings.

Revision ID: 078_runtime_static_correlation
Revises: 077_prepared_actions
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "078_runtime_static_correlation"
down_revision = "077_prepared_actions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "runtime_static_topology_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.String(length=255), nullable=False),
        sa.Column("scan_revision", sa.String(length=128), nullable=False),
        sa.Column("source_sequence", sa.Integer(), nullable=False),
        sa.Column("platform", sa.String(length=80), nullable=False),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "project_id", "scan_revision",
            name="uq_runtime_static_topology_project_revision",
        ),
        sa.UniqueConstraint(
            "project_id", "source_sequence",
            name="uq_runtime_static_topology_project_source_sequence",
        ),
    )
    op.create_index(
        "ix_runtime_static_topology_project_sequence",
        "runtime_static_topology_snapshots",
        ["project_id", "source_sequence"],
    )
    op.create_table(
        "runtime_job_scan_maps",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.String(length=255), nullable=False),
        sa.Column("source_key", sa.String(length=100), nullable=False),
        sa.Column("runtime_job_id", sa.String(length=255), nullable=False),
        sa.Column("static_job_id", sa.String(length=255), nullable=False),
        sa.Column("scan_revision", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.String(length=255), nullable=True),
        sa.UniqueConstraint(
            "project_id", "source_key", "runtime_job_id",
            name="uq_runtime_job_scan_map_project_source_runtime_job",
        ),
    )
    op.create_index("ix_runtime_job_scan_maps_project", "runtime_job_scan_maps", ["project_id"])
    op.create_index(
        "ix_runtime_job_scan_maps_static_job",
        "runtime_job_scan_maps",
        ["project_id", "static_job_id"],
    )
    with op.batch_alter_table("runtime_enriched_events") as batch:
        batch.add_column(sa.Column("field_name", sa.String(length=255), nullable=True))
        batch.add_column(sa.Column("field_violation_kind", sa.String(length=50), nullable=True))
        batch.add_column(sa.Column("expected_type", sa.String(length=120), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("runtime_enriched_events") as batch:
        batch.drop_column("expected_type")
        batch.drop_column("field_violation_kind")
        batch.drop_column("field_name")
    op.drop_index("ix_runtime_job_scan_maps_static_job", table_name="runtime_job_scan_maps")
    op.drop_index("ix_runtime_job_scan_maps_project", table_name="runtime_job_scan_maps")
    op.drop_table("runtime_job_scan_maps")
    op.drop_index(
        "ix_runtime_static_topology_project_sequence",
        table_name="runtime_static_topology_snapshots",
    )
    op.drop_table("runtime_static_topology_snapshots")
