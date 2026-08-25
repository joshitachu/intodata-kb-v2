"""Add inherited base timestamps to runtime topology tables.

Revision ID: 093_runtime_topology_timestamps
Revises: 092_incident_termination
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "093_runtime_topology_timestamps"
down_revision = "092_incident_termination"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "runtime_static_topology_snapshots",
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.add_column(
        "runtime_static_topology_snapshots",
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.add_column(
        "runtime_job_scan_maps",
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("runtime_job_scan_maps", "updated_at")
    op.drop_column("runtime_static_topology_snapshots", "updated_at")
    op.drop_column("runtime_static_topology_snapshots", "created_at")
