"""Add the inherited `updated_at` that two tables were created without.

`Base` (src/db/base.py) contributes `created_at` AND `updated_at` to every
mapped class. A migration that hand-writes its `create_table` and forgets one of
them produces a table the ORM cannot even SELECT: the mapper emits the declared
column list, and PostgreSQL answers `UndefinedColumnError`.

That is exactly what happened here. `runtime_source_read_grants` (095) declared
`created_at` explicitly and inherited `updated_at`, but its `create_table` listed
only `created_at`, so every read of a project's read grants failed:

    column runtime_source_read_grants.updated_at does not exist

`wizard_events` carries the same omission and had simply not been read through
the ORM yet.

This is the THIRD instance of the same class of bug -- 093 backfilled
`runtime_static_topology_snapshots` / `runtime_job_scan_maps`, 094 backfilled
`instance_settings`. A data fix alone would invite a fourth, so it is paired with
a drift-guard test (`tests/test_db/test_model_schema_drift.py`) that compares
every mapped table's declared columns against the database and fails naming the
offender.

Idempotent: each column is added only when absent, so this is safe on a database
that has already been repaired by hand.

Revision ID: 096_base_timestamp_drift
Revises: 095_runtime_source_read_grants
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "096_base_timestamp_drift"
down_revision = "095_runtime_source_read_grants"
branch_labels = None
depends_on = None

# (table, column) pairs the declarative Base contributes but the creating
# migration omitted.
_MISSING: tuple[tuple[str, str], ...] = (
    ("runtime_source_read_grants", "updated_at"),
    ("wizard_events", "updated_at"),
)


def _columns(bind, table: str) -> set[str]:
    return {row["name"] for row in sa.inspect(bind).get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    for table, column in _MISSING:
        if not inspector.has_table(table):
            continue
        if column in _columns(bind, table):
            continue
        # NOT NULL with a server default so existing rows are backfilled to now()
        # in one statement; these are audit timestamps, and "when the column was
        # added" is the only honest value available for a pre-existing row.
        op.add_column(
            table,
            sa.Column(
                column,
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    for table, column in _MISSING:
        if not inspector.has_table(table):
            continue
        if column in _columns(bind, table):
            op.drop_column(table, column)
