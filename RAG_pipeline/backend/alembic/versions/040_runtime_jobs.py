"""Add generic runtime job inventory and run facts.

Revision ID: 040
Revises: 039
Create Date: 2026-07-02
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "040"
down_revision: Union[str, None] = "039"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _base_timestamps() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "runtime_jobs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.String(255), nullable=False),
        sa.Column("source_key", sa.String(100), nullable=False),
        sa.Column("job_id", sa.String(255), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("display_name", sa.String(255), nullable=True),
        sa.Column("job_type", sa.String(80), nullable=True),
        sa.Column("status", sa.String(80), nullable=True),
        sa.Column("environment_id", sa.String(255), nullable=True),
        sa.Column("environment_name", sa.String(255), nullable=True),
        sa.Column("workspace_id", sa.String(255), nullable=True),
        sa.Column("workspace_name", sa.String(255), nullable=True),
        sa.Column("engine_id", sa.String(255), nullable=True),
        sa.Column("engine_name", sa.String(255), nullable=True),
        sa.Column("owner", sa.String(255), nullable=True),
        sa.Column("artifact_name", sa.String(255), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=False),
        *_base_timestamps(),
        sa.UniqueConstraint(
            "project_id",
            "source_key",
            "job_id",
            name="uq_runtime_jobs_project_source_job",
        ),
    )
    op.create_index("ix_runtime_jobs_project_id", "runtime_jobs", ["project_id"])
    op.create_index("ix_runtime_jobs_source_key", "runtime_jobs", ["source_key"])
    op.create_index("ix_runtime_jobs_environment_id", "runtime_jobs", ["environment_id"])
    op.create_index("ix_runtime_jobs_workspace_id", "runtime_jobs", ["workspace_id"])
    op.create_index("ix_runtime_jobs_engine_id", "runtime_jobs", ["engine_id"])
    op.create_index("ix_runtime_jobs_status", "runtime_jobs", ["status"])

    op.create_table(
        "runtime_job_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.String(255), nullable=False),
        sa.Column("source_key", sa.String(100), nullable=False),
        sa.Column("run_id", sa.String(255), nullable=False),
        sa.Column("job_id", sa.String(255), nullable=False),
        sa.Column("job_name", sa.String(255), nullable=False),
        sa.Column("execution_id", sa.String(255), nullable=True),
        sa.Column("state", sa.String(20), nullable=False),
        sa.Column("environment_id", sa.String(255), nullable=True),
        sa.Column("environment_name", sa.String(255), nullable=True),
        sa.Column("workspace_id", sa.String(255), nullable=True),
        sa.Column("workspace_name", sa.String(255), nullable=True),
        sa.Column("engine_id", sa.String(255), nullable=True),
        sa.Column("engine_name", sa.String(255), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=False),
        *_base_timestamps(),
        sa.UniqueConstraint(
            "project_id",
            "source_key",
            "run_id",
            name="uq_runtime_job_runs_project_source_run",
        ),
    )
    op.create_index("ix_runtime_job_runs_project_id", "runtime_job_runs", ["project_id"])
    op.create_index("ix_runtime_job_runs_source_key", "runtime_job_runs", ["source_key"])
    op.create_index("ix_runtime_job_runs_job_id", "runtime_job_runs", ["job_id"])
    op.create_index("ix_runtime_job_runs_engine_id", "runtime_job_runs", ["engine_id"])
    op.create_index("ix_runtime_job_runs_state", "runtime_job_runs", ["state"])
    op.create_index("ix_runtime_job_runs_started_at", "runtime_job_runs", ["started_at"])
    op.create_index("ix_runtime_job_runs_execution_id", "runtime_job_runs", ["execution_id"])


def downgrade() -> None:
    op.drop_index("ix_runtime_job_runs_execution_id", table_name="runtime_job_runs")
    op.drop_index("ix_runtime_job_runs_started_at", table_name="runtime_job_runs")
    op.drop_index("ix_runtime_job_runs_state", table_name="runtime_job_runs")
    op.drop_index("ix_runtime_job_runs_engine_id", table_name="runtime_job_runs")
    op.drop_index("ix_runtime_job_runs_job_id", table_name="runtime_job_runs")
    op.drop_index("ix_runtime_job_runs_source_key", table_name="runtime_job_runs")
    op.drop_index("ix_runtime_job_runs_project_id", table_name="runtime_job_runs")
    op.drop_table("runtime_job_runs")
    op.drop_index("ix_runtime_jobs_status", table_name="runtime_jobs")
    op.drop_index("ix_runtime_jobs_engine_id", table_name="runtime_jobs")
    op.drop_index("ix_runtime_jobs_workspace_id", table_name="runtime_jobs")
    op.drop_index("ix_runtime_jobs_environment_id", table_name="runtime_jobs")
    op.drop_index("ix_runtime_jobs_source_key", table_name="runtime_jobs")
    op.drop_index("ix_runtime_jobs_project_id", table_name="runtime_jobs")
    op.drop_table("runtime_jobs")
