"""Record how a runtime-to-static job mapping came to exist.

Revision ID: 106_job_scan_map_provenance
Revises: 105_runtime_source_estate_key

``runtime_job_scan_maps`` used to hold exactly one kind of row: one a human
created through ``POST /runtime/job-scan-maps``. It is about to hold a second
kind, written automatically by ``src/runtime/topology/binding.py`` from the
static reference each provider declares (``RuntimeSourceProvider.
static_ref_strategy``).

Those two are not interchangeable, and the distinction is a rule, not a label:
the resolver runs again on every sync cycle, so without a provenance column it
cannot tell its own previous output from a correction an operator made by hand,
and re-running would silently overwrite the correction. ``resolved_by`` is what
lets the resolver skip ``manual`` rows and rewrite only its own.

``server_default='manual'`` is what makes this safe on a populated table: every
row that exists when this runs was created through the human write path, so the
default is the true value for all of them. In practice the table is empty in
production (0 rows), but the backfill is correct either way.

Reversible. Upgrade adds one non-null column with a default; downgrade drops it,
losing only the provenance distinction. No mapping is read, rewritten or
deleted in either direction.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "106_job_scan_map_provenance"
down_revision: str | None = "105_runtime_source_estate_key"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _has_column(table: str, column: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return any(item["name"] == column for item in inspector.get_columns(table))


def upgrade() -> None:
    if _has_column("runtime_job_scan_maps", "resolved_by"):
        return
    op.add_column(
        "runtime_job_scan_maps",
        sa.Column(
            "resolved_by",
            sa.String(64),
            nullable=False,
            server_default="manual",
        ),
    )
    # Explicit, even though the server default already covers every existing
    # row. The column is read by a predicate that decides whether a row may be
    # overwritten, so leaving a NULL reachable here would make that predicate
    # depend on how the rows happened to be inserted.
    op.execute(
        sa.text(
            "UPDATE runtime_job_scan_maps SET resolved_by = 'manual' "
            "WHERE resolved_by IS NULL"
        )
    )


def downgrade() -> None:
    if _has_column("runtime_job_scan_maps", "resolved_by"):
        op.drop_column("runtime_job_scan_maps", "resolved_by")
