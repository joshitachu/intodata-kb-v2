"""Add durable attempts, ordered event ledger, and candidate artifact provenance.

Revision ID: 025_generation_task_attempts
Revises: 024_generation_recipe_tasks
Create Date: 2026-07-27
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "025_generation_task_attempts"
down_revision: Union[str, None] = "024_generation_recipe_tasks"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "generation_task_attempts",
        sa.Column("id", sa.String(36), primary_key=True, nullable=False),
        sa.Column(
            "task_run_id",
            sa.String(64),
            sa.ForeignKey("generation_task_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("execution_idempotency_key", sa.String(200), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("lease_owner", sa.String(200), nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("wall_duration_ms", sa.BigInteger(), nullable=True),
        sa.Column("provider_latency_ms", sa.BigInteger(), nullable=True),
        sa.Column("input_tokens", sa.BigInteger(), nullable=True),
        sa.Column("output_tokens", sa.BigInteger(), nullable=True),
        sa.Column("cache_read_tokens", sa.BigInteger(), nullable=True),
        sa.Column("provider_cost_usd", sa.String(50), nullable=True),
        sa.Column("runtime_session_id", sa.String(500), nullable=True),
        sa.Column("runtime_message_id", sa.String(500), nullable=True),
        sa.Column("result_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("metrics_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "task_run_id",
            "attempt_number",
            name="uq_generation_task_attempts_task_number",
        ),
        sa.UniqueConstraint(
            "execution_idempotency_key",
            name="uq_generation_task_attempts_execution_key",
        ),
    )
    op.create_index(
        "ix_generation_task_attempts_task_run_id",
        "generation_task_attempts",
        ["task_run_id"],
    )
    op.create_index(
        "ix_generation_task_attempts_task_status",
        "generation_task_attempts",
        ["task_run_id", "status"],
    )

    op.create_table(
        "generation_task_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True, nullable=False),
        sa.Column(
            "task_run_id",
            sa.String(64),
            sa.ForeignKey("generation_task_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "attempt_id",
            sa.String(36),
            sa.ForeignKey("generation_task_attempts.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("sequence_number", sa.BigInteger(), nullable=False),
        sa.Column("idempotency_key", sa.String(200), nullable=False),
        sa.Column("event_type", sa.String(100), nullable=False),
        sa.Column("payload_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "task_run_id",
            "sequence_number",
            name="uq_generation_task_events_task_sequence",
        ),
        sa.UniqueConstraint(
            "task_run_id",
            "idempotency_key",
            name="uq_generation_task_events_task_idempotency",
        ),
    )
    op.create_index(
        "ix_generation_task_events_task_run_id",
        "generation_task_events",
        ["task_run_id"],
    )
    op.create_index(
        "ix_generation_task_events_attempt_id",
        "generation_task_events",
        ["attempt_id"],
    )
    op.create_index(
        "ix_generation_task_events_task_occurred",
        "generation_task_events",
        ["task_run_id", "occurred_at"],
    )

    op.add_column(
        "generation_experiment_artifacts",
        sa.Column("task_run_id", sa.String(64), nullable=True),
    )
    op.add_column(
        "generation_experiment_artifacts",
        sa.Column("attempt_id", sa.String(36), nullable=True),
    )
    op.create_index(
        "ix_generation_experiment_artifacts_task_run_id",
        "generation_experiment_artifacts",
        ["task_run_id"],
    )
    op.create_index(
        "ix_generation_experiment_artifacts_attempt_id",
        "generation_experiment_artifacts",
        ["attempt_id"],
    )
    op.create_foreign_key(
        "fk_generation_experiment_artifacts_task_run",
        "generation_experiment_artifacts",
        "generation_task_runs",
        ["task_run_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_generation_experiment_artifacts_attempt",
        "generation_experiment_artifacts",
        "generation_task_attempts",
        ["attempt_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_generation_experiment_artifacts_attempt",
        "generation_experiment_artifacts",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_generation_experiment_artifacts_task_run",
        "generation_experiment_artifacts",
        type_="foreignkey",
    )
    op.drop_index(
        "ix_generation_experiment_artifacts_attempt_id",
        table_name="generation_experiment_artifacts",
    )
    op.drop_index(
        "ix_generation_experiment_artifacts_task_run_id",
        table_name="generation_experiment_artifacts",
    )
    op.drop_column("generation_experiment_artifacts", "attempt_id")
    op.drop_column("generation_experiment_artifacts", "task_run_id")

    op.drop_index(
        "ix_generation_task_events_task_occurred",
        table_name="generation_task_events",
    )
    op.drop_index(
        "ix_generation_task_events_attempt_id",
        table_name="generation_task_events",
    )
    op.drop_index(
        "ix_generation_task_events_task_run_id",
        table_name="generation_task_events",
    )
    op.drop_table("generation_task_events")

    op.drop_index(
        "ix_generation_task_attempts_task_status",
        table_name="generation_task_attempts",
    )
    op.drop_index(
        "ix_generation_task_attempts_task_run_id",
        table_name="generation_task_attempts",
    )
    op.drop_table("generation_task_attempts")
