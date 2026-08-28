"""Separate batch Jobs from long-running Routes and Data Services.

Revision ID: 098_runtime_route_observability
Revises: 097_invite_role_and_tour_variant
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "098_runtime_route_observability"
down_revision: str | None = "097_invite_role_and_tour_variant"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "runtime_jobs",
        sa.Column("executable_kind", sa.String(32), nullable=False, server_default="job"),
    )
    op.add_column("runtime_jobs", sa.Column("artifact_id", sa.String(255), nullable=True))
    op.add_column("runtime_jobs", sa.Column("artifact_version", sa.String(120), nullable=True))
    op.add_column("runtime_jobs", sa.Column("metric_path", sa.String(512), nullable=True))
    op.create_index("ix_runtime_jobs_executable_kind", "runtime_jobs", ["executable_kind"])

    op.add_column(
        "runtime_job_runs",
        sa.Column("executable_kind", sa.String(32), nullable=False, server_default="job"),
    )
    op.add_column("runtime_job_runs", sa.Column("completed_exchanges", sa.BigInteger(), nullable=True))
    op.add_column("runtime_job_runs", sa.Column("failed_exchanges", sa.BigInteger(), nullable=True))
    op.add_column("runtime_job_runs", sa.Column("total_exchanges", sa.BigInteger(), nullable=True))
    op.add_column("runtime_job_runs", sa.Column("last_process_time_ms", sa.BigInteger(), nullable=True))
    op.add_column("runtime_job_runs", sa.Column("mean_process_time_ms", sa.BigInteger(), nullable=True))
    op.add_column(
        "runtime_job_runs",
        sa.Column("metrics_updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column("runtime_job_runs", sa.Column("metric_path", sa.String(512), nullable=True))
    op.add_column("runtime_job_runs", sa.Column("service_call_count", sa.BigInteger(), nullable=True))
    op.add_column("runtime_job_runs", sa.Column("one_minute_rate", sa.Float(), nullable=True))
    op.add_column("runtime_job_runs", sa.Column("five_minute_rate", sa.Float(), nullable=True))
    op.add_column("runtime_job_runs", sa.Column("fifteen_minute_rate", sa.Float(), nullable=True))
    op.create_index(
        "ix_runtime_job_runs_executable_kind",
        "runtime_job_runs",
        ["executable_kind"],
    )

    op.add_column(
        "runtime_executions",
        sa.Column("executable_kind", sa.String(32), nullable=False, server_default="job"),
    )
    op.add_column(
        "runtime_log_events",
        sa.Column("scope", sa.String(20), nullable=False, server_default="execution"),
    )
    op.add_column(
        "runtime_log_events",
        sa.Column("attribution_safe", sa.Boolean(), nullable=False, server_default=sa.true()),
    )

    # Existing TMC rows stored the artifact id in artifact_name. Preserve it and
    # classify against the already mirrored artifact inventory.
    op.execute("UPDATE runtime_jobs SET artifact_id = artifact_name WHERE artifact_id IS NULL")
    op.execute(
        """
        UPDATE runtime_jobs AS jobs
        SET executable_kind = CASE
            WHEN LOWER(COALESCE(artifacts.artifact_type, '')) = 'route' THEN 'route'
            WHEN REPLACE(LOWER(COALESCE(artifacts.artifact_type, '')), ' ', '_')
                 IN ('data_service', 'dataservice') THEN 'data_service'
            WHEN LOWER(COALESCE(artifacts.artifact_type, ''))
                 IN ('standard', 'job', 'batch') THEN 'job'
            ELSE 'job'
        END,
        artifact_name = COALESCE(artifacts.name, jobs.artifact_name),
        artifact_version = artifacts.version
        FROM runtime_artifacts AS artifacts
        WHERE artifacts.project_id = jobs.project_id
          AND artifacts.source_key = jobs.source_key
          AND artifacts.artifact_id = jobs.artifact_id
        """
    )
    op.execute(
        """
        UPDATE runtime_job_runs AS runs
        SET executable_kind = jobs.executable_kind
        FROM runtime_jobs AS jobs
        WHERE jobs.project_id = runs.project_id
          AND jobs.source_key = runs.source_key
          AND jobs.job_id = runs.job_id
        """
    )
    op.execute(
        """
        UPDATE runtime_executions AS executions
        SET executable_kind = jobs.executable_kind
        FROM runtime_jobs AS jobs
        WHERE jobs.project_id = executions.project_id
          AND jobs.source_key = executions.source_key
          AND jobs.name = executions.job_name
        """
    )
    op.execute(
        """
        UPDATE runtime_log_events AS logs
        SET scope = 'engine', attribution_safe = FALSE
        FROM runtime_executions AS executions
        WHERE executions.project_id = logs.project_id
          AND executions.execution_id = logs.execution_id
          AND executions.executable_kind IN ('route', 'data_service')
        """
    )
    # Derived per-execution diagnoses created before executable kinds existed
    # are unsafe for shared OSGi Route/Data Service logs. The raw logs remain;
    # only rebuildable, falsely attributed rows and batch-style failures go.
    op.execute(
        """
        DELETE FROM runtime_incident_evidence AS evidence
        USING runtime_incidents AS incidents, runtime_executions AS executions
        WHERE incidents.project_id = executions.project_id
          AND incidents.execution_id = executions.execution_id
          AND evidence.project_id = incidents.project_id
          AND evidence.incident_id = incidents.incident_id
          AND executions.executable_kind IN ('route', 'data_service')
        """
    )
    for table in (
        "runtime_incidents",
        "runtime_execution_summaries",
        "runtime_enriched_events",
        "runtime_failures",
    ):
        op.execute(
            f"""
            DELETE FROM {table} AS derived
            USING runtime_executions AS executions
            WHERE derived.project_id = executions.project_id
              AND derived.execution_id = executions.execution_id
              AND executions.executable_kind IN ('route', 'data_service')
            """
        )
    op.execute(
        """
        DELETE FROM runtime_error_signatures AS signatures
        WHERE NOT EXISTS (
            SELECT 1 FROM runtime_incidents AS incidents
            WHERE incidents.project_id = signatures.project_id
              AND incidents.signature_hash = signatures.signature_hash
        )
        """
    )


def downgrade() -> None:
    op.drop_column("runtime_log_events", "attribution_safe")
    op.drop_column("runtime_log_events", "scope")
    op.drop_column("runtime_executions", "executable_kind")
    op.drop_index("ix_runtime_job_runs_executable_kind", table_name="runtime_job_runs")
    op.drop_column("runtime_job_runs", "fifteen_minute_rate")
    op.drop_column("runtime_job_runs", "five_minute_rate")
    op.drop_column("runtime_job_runs", "one_minute_rate")
    op.drop_column("runtime_job_runs", "service_call_count")
    op.drop_column("runtime_job_runs", "metric_path")
    op.drop_column("runtime_job_runs", "metrics_updated_at")
    op.drop_column("runtime_job_runs", "mean_process_time_ms")
    op.drop_column("runtime_job_runs", "last_process_time_ms")
    op.drop_column("runtime_job_runs", "total_exchanges")
    op.drop_column("runtime_job_runs", "failed_exchanges")
    op.drop_column("runtime_job_runs", "completed_exchanges")
    op.drop_column("runtime_job_runs", "executable_kind")
    op.drop_index("ix_runtime_jobs_executable_kind", table_name="runtime_jobs")
    op.drop_column("runtime_jobs", "metric_path")
    op.drop_column("runtime_jobs", "artifact_version")
    op.drop_column("runtime_jobs", "artifact_id")
    op.drop_column("runtime_jobs", "executable_kind")
