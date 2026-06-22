"""Add cause_origin + pattern_id to runtime_incidents.

Slice 3 changes the deterministic signature algorithm (causal resolution + an
unknown-failure disambiguator) and adds two columns to runtime_incidents:

  * cause_origin  (String(20), NOT NULL, server_default "deterministic") —
    "deterministic" is the only value the builder writes; "llm_fallback" is
    reserved for the Slice 4 LLM fallback.
  * pattern_id    (String(64), nullable) — the matched catalog pattern id.

A separate data backfill recomputes existing insight rows against the new
algorithm. The rebuild must not run inside this migration: alembic holds a
transaction with locks on runtime_incidents, and the backfill writes to the
same table, so an inline rebuild deadlocks against itself. The
``rebuild_runtime_insights`` helper in ``src.runtime.insights.builder`` is
the single entry point for that backfill and is idempotent.

Revision ID: 034
Revises: 033
Create Date: 2026-06-18
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "034"
down_revision: Union[str, None] = "033"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("runtime_incidents") as batch:
        batch.add_column(
            sa.Column(
                "cause_origin",
                sa.String(20),
                nullable=False,
                server_default="deterministic",
            )
        )
        batch.add_column(sa.Column("pattern_id", sa.String(64), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("runtime_incidents") as batch:
        batch.drop_column("pattern_id")
        batch.drop_column("cause_origin")
