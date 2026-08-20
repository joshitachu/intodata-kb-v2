"""Indexes for the runtime aggregate rollup access paths.

Revision ID: 082_runtime_log_search_index
Revises: 081_runtime_causal_meaning

Scope note: this revision was originally scoped to add a full-text/trigram index on
``runtime_log_events.message`` for ``search_log_events``. That index already exists --
migration 060 creates ``ix_runtime_log_events_message_fts`` (GIN over
``to_tsvector('simple', message)``) and ``ix_runtime_log_events_message_trgm`` (GIN
``gin_trgm_ops``), and ``EXPLAIN ANALYZE`` on the live search query confirms both are
used via BitmapOr index scans, not a sequential scan. Adding them again would create
duplicate indexes, so this revision instead covers the access paths the new SQL
aggregate rollups introduced, which measurably lacked support:

  * ``runtime_component_metrics`` grouped by (project_id, source_key, component_key)
    for the component-hotspot rollup planned as ``Seq Scan`` + ``Sort`` over every
    metric row (22,942 rows / ~34 ms on the reference dataset, growing with retention).
    Only single-column indexes on project_id / execution_id / component_key existed.
  * ``runtime_executions`` filtered by project and a ``started_at`` window for the
    outcome-trend and job-reliability rollups planned as an index scan on project_id
    with ``started_at`` applied as a post-scan Filter. A composite turns the retention
    window into an index range scan.

Both indexes are plain B-tree composites created non-concurrently: they are small and
build in well under a second at current volumes, and CREATE INDEX CONCURRENTLY cannot
run inside Alembic's migration transaction.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "082_runtime_log_search_index"
down_revision: str | Sequence[str] | None = "081_runtime_causal_meaning"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INDEXES: tuple[tuple[str, str, list[str]], ...] = (
    (
        "ix_runtime_component_metrics_project_source_component",
        "runtime_component_metrics",
        ["project_id", "source_key", "component_key"],
    ),
    (
        "ix_runtime_executions_project_started_at",
        "runtime_executions",
        ["project_id", "started_at"],
    ),
)


def upgrade() -> None:
    for index_name, table_name, columns in _INDEXES:
        op.create_index(index_name, table_name, columns)


def downgrade() -> None:
    for index_name, table_name, _columns in reversed(_INDEXES):
        op.drop_index(index_name, table_name=table_name)
