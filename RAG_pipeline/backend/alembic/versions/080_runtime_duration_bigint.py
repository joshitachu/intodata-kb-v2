"""Use 64-bit storage for runtime durations.

Revision ID: 080_runtime_duration_bigint
Revises: 079_merge_rag_runtime_heads
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "080_runtime_duration_bigint"
down_revision: str | Sequence[str] | None = "079_merge_rag_runtime_heads"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DURATION_TABLES = (
    "runtime_executions",
    "runtime_enriched_events",
    "runtime_job_runs",
    "runtime_plan_runs",
    "runtime_component_metrics",
)


def upgrade() -> None:
    for table_name in _DURATION_TABLES:
        op.alter_column(
            table_name,
            "duration_ms",
            existing_type=sa.Integer(),
            type_=sa.BigInteger(),
            existing_nullable=True,
        )


def downgrade() -> None:
    for table_name in reversed(_DURATION_TABLES):
        op.alter_column(
            table_name,
            "duration_ms",
            existing_type=sa.BigInteger(),
            type_=sa.Integer(),
            existing_nullable=True,
        )
