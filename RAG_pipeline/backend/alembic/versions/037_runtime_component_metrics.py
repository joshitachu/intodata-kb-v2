"""Add generic runtime component metrics.

Revision ID: 037
Revises: 036
Create Date: 2026-07-02
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "037"
down_revision: Union[str, None] = "036"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "runtime_component_metrics",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.String(255), nullable=False),
        sa.Column("execution_id", sa.String(255), nullable=False),
        sa.Column("component_key", sa.String(255), nullable=False),
        sa.Column("component_type", sa.String(120), nullable=True),
        sa.Column("source_connector_type", sa.String(120), nullable=True),
        sa.Column("target_connector_type", sa.String(120), nullable=True),
        sa.Column("rows_total", sa.Integer(), nullable=True),
        sa.Column("rows_rejected", sa.Integer(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("source_key", sa.String(100), nullable=False),
        sa.Column("measured_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "project_id",
            "execution_id",
            "component_key",
            "source_key",
            name="uq_runtime_component_metrics_project_exec_component_source",
        ),
    )
    op.create_index(
        "ix_runtime_component_metrics_project_id",
        "runtime_component_metrics",
        ["project_id"],
    )
    op.create_index(
        "ix_runtime_component_metrics_execution_id",
        "runtime_component_metrics",
        ["execution_id"],
    )
    op.create_index(
        "ix_runtime_component_metrics_component_key",
        "runtime_component_metrics",
        ["component_key"],
    )


def downgrade() -> None:
    op.drop_index("ix_runtime_component_metrics_component_key", table_name="runtime_component_metrics")
    op.drop_index("ix_runtime_component_metrics_execution_id", table_name="runtime_component_metrics")
    op.drop_index("ix_runtime_component_metrics_project_id", table_name="runtime_component_metrics")
    op.drop_table("runtime_component_metrics")
