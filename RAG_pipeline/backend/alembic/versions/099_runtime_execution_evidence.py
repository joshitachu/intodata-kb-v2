"""Keep the provider evidence behind a normalized run state.

TMC reports six execution statuses, a `misfiredExecutionReason` for every
misfire, and an `executionType` on every record. The mirror normalized all of
that down to a four-value `state` and threw the rest away, so a status we did
not map became an unattributable `unknown` row.

These three columns are additive and nullable on all three run-carrying tables
(`runtime_executions`, `runtime_job_runs`, `runtime_plan_runs`). They sit
alongside `state`, which keeps its String(20) type: the widest new member,
`terminated`, is 10 characters.

Reversible: upgrade only adds columns, downgrade only drops them. No row is
read, rewritten, or deleted in either direction, so a downgrade loses exactly
the captured evidence and nothing else.

Revision ID: 099_runtime_execution_evidence
Revises: 098_runtime_route_observability
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "099_runtime_execution_evidence"
down_revision: str | None = "098_runtime_route_observability"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# One definition drives both directions, so upgrade and downgrade cannot drift.
_TABLES: tuple[str, ...] = (
    "runtime_executions",
    "runtime_job_runs",
    "runtime_plan_runs",
)
_COLUMNS: tuple[tuple[str, int], ...] = (
    ("raw_state", 64),
    ("misfire_reason", 64),
    ("execution_type", 32),
)


def upgrade() -> None:
    for table in _TABLES:
        for name, length in _COLUMNS:
            op.add_column(table, sa.Column(name, sa.String(length), nullable=True))


def downgrade() -> None:
    for table in reversed(_TABLES):
        for name, _length in reversed(_COLUMNS):
            op.drop_column(table, name)
