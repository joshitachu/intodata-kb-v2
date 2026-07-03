"""Add generic runtime plan inventory and run facts.

Revision ID: 041
Revises: 040
Create Date: 2026-07-02
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "041"
down_revision: Union[str, None] = "040"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _base_timestamps() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "runtime_plans",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.String(255), nullable=False),
        sa.Column("source_key", sa.String(100), nullable=False),
        sa.Column("plan_id", sa.String(255), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("display_name", sa.String(255), nullable=True),
        sa.Column("status", sa.String(80), nullable=True),
        sa.Column("environment_id", sa.String(255), nullable=True),
        sa.Column("environment_name", sa.String(255), nullable=True),
        sa.Column("workspace_id", sa.String(255), nullable=True),
        sa.Column("workspace_name", sa.String(255), nullable=True),
        sa.Column("owner", sa.String(255), nullable=True),
        sa.Column("step_count", sa.Integer(), nullable=True),
        sa.Column("steps", sa.JSON(), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=False),
        *_base_timestamps(),
        sa.UniqueConstraint(
            "project_id",
            "source_key",
            "plan_id",
            name="uq_runtime_plans_project_source_plan",
        ),
    )
    op.create_index("ix_runtime_plans_project_id", "runtime_plans", ["project_id"])
    op.create_index("ix_runtime_plans_source_key", "runtime_plans", ["source_key"])
    op.create_index("ix_runtime_plans_environment_id", "runtime_plans", ["environment_id"])
    op.create_index("ix_runtime_plans_workspace_id", "runtime_plans", ["workspace_id"])
    op.create_index("ix_runtime_plans_status", "runtime_plans", ["status"])

    op.create_table(
        "runtime_plan_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.String(255), nullable=False),
        sa.Column("source_key", sa.String(100), nullable=False),
        sa.Column("run_id", sa.String(255), nullable=False),
        sa.Column("plan_id", sa.String(255), nullable=False),
        sa.Column("plan_name", sa.String(255), nullable=False),
        sa.Column("state", sa.String(20), nullable=False),
        sa.Column("environment_id", sa.String(255), nullable=True),
        sa.Column("environment_name", sa.String(255), nullable=True),
        sa.Column("workspace_id", sa.String(255), nullable=True),
        sa.Column("workspace_name", sa.String(255), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("step_results", sa.JSON(), nullable=True),
        sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=False),
        *_base_timestamps(),
        sa.UniqueConstraint(
            "project_id",
            "source_key",
            "run_id",
            name="uq_runtime_plan_runs_project_source_run",
        ),
    )
    op.create_index("ix_runtime_plan_runs_project_id", "runtime_plan_runs", ["project_id"])
    op.create_index("ix_runtime_plan_runs_source_key", "runtime_plan_runs", ["source_key"])
    op.create_index("ix_runtime_plan_runs_plan_id", "runtime_plan_runs", ["plan_id"])
    op.create_index("ix_runtime_plan_runs_state", "runtime_plan_runs", ["state"])
    op.create_index("ix_runtime_plan_runs_started_at", "runtime_plan_runs", ["started_at"])


def downgrade() -> None:
    op.drop_index("ix_runtime_plan_runs_started_at", table_name="runtime_plan_runs")
    op.drop_index("ix_runtime_plan_runs_state", table_name="runtime_plan_runs")
    op.drop_index("ix_runtime_plan_runs_plan_id", table_name="runtime_plan_runs")
    op.drop_index("ix_runtime_plan_runs_source_key", table_name="runtime_plan_runs")
    op.drop_index("ix_runtime_plan_runs_project_id", table_name="runtime_plan_runs")
    op.drop_table("runtime_plan_runs")
    op.drop_index("ix_runtime_plans_status", table_name="runtime_plans")
    op.drop_index("ix_runtime_plans_workspace_id", table_name="runtime_plans")
    op.drop_index("ix_runtime_plans_environment_id", table_name="runtime_plans")
    op.drop_index("ix_runtime_plans_source_key", table_name="runtime_plans")
    op.drop_index("ix_runtime_plans_project_id", table_name="runtime_plans")
    op.drop_table("runtime_plans")
