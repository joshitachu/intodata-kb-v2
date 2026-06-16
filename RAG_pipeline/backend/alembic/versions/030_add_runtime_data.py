"""Add held runtime-data store tables (Model 2 skeleton).

Additive only: four new source-agnostic tables for runtime executions, failures,
log events, and the freshness watermark. No existing table is altered.

Revision ID: 030
Revises: 029
Create Date: 2026-06-16
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "030"
down_revision: Union[str, None] = "029"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "runtime_executions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.String(255), nullable=False),
        sa.Column("execution_id", sa.String(255), nullable=False),
        sa.Column("job_name", sa.String(255), nullable=False),
        sa.Column("project_name", sa.String(255), nullable=True),
        sa.Column("state", sa.String(20), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("source_key", sa.String(100), nullable=False),
        sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("project_id", "execution_id", name="uq_runtime_executions_project_exec"),
    )
    op.create_index("ix_runtime_executions_project_id", "runtime_executions", ["project_id"])
    op.create_index("ix_runtime_executions_job_name", "runtime_executions", ["job_name"])
    op.create_index("ix_runtime_executions_started_at", "runtime_executions", ["started_at"])

    op.create_table(
        "runtime_failures",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.String(255), nullable=False),
        sa.Column("execution_id", sa.String(255), nullable=False),
        sa.Column("job_name", sa.String(255), nullable=False),
        sa.Column("failed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("error_summary", sa.Text(), nullable=False),
        sa.Column("source_key", sa.String(100), nullable=False),
        sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("project_id", "execution_id", name="uq_runtime_failures_project_exec"),
    )
    op.create_index("ix_runtime_failures_project_id", "runtime_failures", ["project_id"])
    op.create_index("ix_runtime_failures_failed_at", "runtime_failures", ["failed_at"])

    op.create_table(
        "runtime_log_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.String(255), nullable=False),
        sa.Column("execution_id", sa.String(255), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("severity", sa.String(20), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("event_fingerprint", sa.String(64), nullable=False),
        sa.Column("source_key", sa.String(100), nullable=False),
        sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint(
            "project_id",
            "execution_id",
            "event_fingerprint",
            name="uq_runtime_log_events_project_exec_fp",
        ),
    )
    op.create_index("ix_runtime_log_events_project_id", "runtime_log_events", ["project_id"])
    op.create_index("ix_runtime_log_events_execution_id", "runtime_log_events", ["execution_id"])

    op.create_table(
        "runtime_sync_state",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source_key", sa.String(100), nullable=False),
        sa.Column("project_id", sa.String(255), nullable=False),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("source_key", "project_id", name="uq_runtime_sync_state_source_project"),
    )
    op.create_index(
        "ix_runtime_sync_state_source_project",
        "runtime_sync_state",
        ["source_key", "project_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_runtime_sync_state_source_project", table_name="runtime_sync_state")
    op.drop_table("runtime_sync_state")

    op.drop_index("ix_runtime_log_events_execution_id", table_name="runtime_log_events")
    op.drop_index("ix_runtime_log_events_project_id", table_name="runtime_log_events")
    op.drop_table("runtime_log_events")

    op.drop_index("ix_runtime_failures_failed_at", table_name="runtime_failures")
    op.drop_index("ix_runtime_failures_project_id", table_name="runtime_failures")
    op.drop_table("runtime_failures")

    op.drop_index("ix_runtime_executions_started_at", table_name="runtime_executions")
    op.drop_index("ix_runtime_executions_job_name", table_name="runtime_executions")
    op.drop_index("ix_runtime_executions_project_id", table_name="runtime_executions")
    op.drop_table("runtime_executions")
