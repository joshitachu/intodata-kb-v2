"""Runtime log storage, source identity, and legacy mirror consolidation.

The one-off project names in this migration are deliberately not part of the
runtime domain model. They identify a verified historical mirror only. Every
affected row is copied into an archival table before deletion, so downgrade
restores the original mirror even when its source configuration was already
removed.

Revision ID: 060_runtime_logs_storage
Revises: 059_harness_project_packs
Create Date: 2026-07-15
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "060_runtime_logs_storage"
down_revision: Union[str, None] = "059_harness_project_packs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_LEGACY_PROJECT = "IntodataStreams"
_SURVIVOR_PROJECT = "cdc"
_MIRROR_TABLES = (
    "runtime_executions", "runtime_failures", "runtime_log_events", "runtime_sync_state",
    "runtime_sources", "runtime_environments", "runtime_workspaces", "runtime_engines",
    "runtime_jobs", "runtime_artifacts", "runtime_connections", "runtime_job_dependencies",
    "runtime_job_runs", "runtime_plans", "runtime_plan_runs", "runtime_component_metrics",
    "runtime_enriched_events", "runtime_error_signatures", "runtime_execution_summaries",
    "runtime_incidents", "runtime_incident_evidence",
)


def _archive_name(table: str) -> str:
    return f"runtime_mirror_backup_{table.removeprefix('runtime_')}"


def upgrade() -> None:
    # Source identity is provider-owned and contains no credential material.
    with op.batch_alter_table("runtime_sources") as batch:
        batch.add_column(sa.Column("physical_identity", sa.String(512), nullable=True))
    op.execute("UPDATE runtime_sources SET physical_identity = 'legacy:' || source_type || ':' || id WHERE physical_identity IS NULL")
    with op.batch_alter_table("runtime_sources") as batch:
        batch.alter_column("physical_identity", nullable=False)
    op.create_index("ix_runtime_sources_physical_identity", "runtime_sources", ["physical_identity"], unique=True)

    # Ordinal identity preserves two identical lines at the same timestamp.
    with op.batch_alter_table("runtime_log_events") as batch:
        batch.add_column(sa.Column("line_ordinal", sa.Integer(), nullable=True))
    op.execute("""
        WITH numbered AS (
            SELECT id, ROW_NUMBER() OVER (
                PARTITION BY project_id, execution_id ORDER BY timestamp, id
            ) - 1 AS ordinal
            FROM runtime_log_events
        )
        UPDATE runtime_log_events AS logs SET line_ordinal = numbered.ordinal
        FROM numbered WHERE logs.id = numbered.id
    """)
    with op.batch_alter_table("runtime_log_events") as batch:
        batch.alter_column("line_ordinal", nullable=False)
        batch.drop_constraint("uq_runtime_log_events_project_exec_fp", type_="unique")
        batch.create_unique_constraint(
            "uq_runtime_log_events_project_exec_ordinal_fp",
            ["project_id", "execution_id", "line_ordinal", "event_fingerprint"],
        )
    op.add_column("runtime_failures", sa.Column("error_detail", sa.Text(), nullable=True))
    op.create_index("ix_runtime_log_events_timestamp", "runtime_log_events", ["timestamp"])

    # Archive first. The production mirror was verified byte-identical; retaining
    # snapshots makes the consolidation auditable and fully reversible.
    for table in _MIRROR_TABLES:
        backup = _archive_name(table)
        op.execute(f"CREATE TABLE {backup} AS TABLE {table} WITH NO DATA")
        op.execute(f"INSERT INTO {backup} SELECT * FROM {table} WHERE project_id = '{_LEGACY_PROJECT}'")
    for table in reversed(_MIRROR_TABLES):
        op.execute(f"DELETE FROM {table} WHERE project_id = '{_LEGACY_PROJECT}'")

    # PostgreSQL search primitives. Alembic migrations run against Postgres in
    # production; SQLite unit tests exercise the repository fallback path.
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute("CREATE INDEX ix_runtime_log_events_message_fts ON runtime_log_events USING GIN (to_tsvector('simple', message))")
    op.execute("CREATE INDEX ix_runtime_log_events_message_trgm ON runtime_log_events USING GIN (message gin_trgm_ops)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_runtime_log_events_message_trgm")
    op.execute("DROP INDEX IF EXISTS ix_runtime_log_events_message_fts")
    for table in _MIRROR_TABLES:
        backup = _archive_name(table)
        op.execute(f"INSERT INTO {table} SELECT * FROM {backup}")
        op.execute(f"DROP TABLE {backup}")
    op.drop_index("ix_runtime_log_events_timestamp", table_name="runtime_log_events")
    op.drop_column("runtime_failures", "error_detail")
    with op.batch_alter_table("runtime_log_events") as batch:
        batch.drop_constraint("uq_runtime_log_events_project_exec_ordinal_fp", type_="unique")
        batch.create_unique_constraint("uq_runtime_log_events_project_exec_fp", ["project_id", "execution_id", "event_fingerprint"])
        batch.drop_column("line_ordinal")
    op.drop_index("ix_runtime_sources_physical_identity", table_name="runtime_sources")
    op.drop_column("runtime_sources", "physical_identity")
