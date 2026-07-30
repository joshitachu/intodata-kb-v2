"""Add immutable recipe snapshots and generic task-run identities.

Revision ID: 024_generation_recipe_tasks
Revises: 023_generation_source_snapshots
Create Date: 2026-07-27
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "024_generation_recipe_tasks"
down_revision: Union[str, None] = "023_generation_source_snapshots"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "generation_recipe_snapshots",
        sa.Column("id", sa.String(64), primary_key=True, nullable=False),
        sa.Column(
            "experiment_id",
            sa.String(36),
            sa.ForeignKey("generation_experiments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("recipe_id", sa.String(200), nullable=False),
        sa.Column("recipe_version", sa.String(100), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("certification_id", sa.String(200), nullable=True),
        sa.Column("manifest_sha256", sa.String(64), nullable=False),
        sa.Column("source_requirements_sha256", sa.String(64), nullable=False),
        sa.Column("effective_scope_sha256", sa.String(64), nullable=False),
        sa.Column("semantic_prompts_sha256", sa.String(64), nullable=False),
        sa.Column("output_contracts_sha256", sa.String(64), nullable=False),
        sa.Column("graph_sha256", sa.String(64), nullable=False),
        sa.Column("manifest_relative_path", sa.String(1000), nullable=False),
        sa.Column("metadata_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("finalized_at", sa.DateTime(timezone=True), nullable=True),
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
            "experiment_id",
            name="uq_generation_recipe_snapshots_experiment",
        ),
    )
    op.create_index(
        "ix_generation_recipe_snapshots_experiment_id",
        "generation_recipe_snapshots",
        ["experiment_id"],
    )
    op.create_index(
        "ix_generation_recipe_snapshots_identity",
        "generation_recipe_snapshots",
        ["recipe_id", "recipe_version"],
    )

    op.create_table(
        "generation_task_runs",
        sa.Column("id", sa.String(64), primary_key=True, nullable=False),
        sa.Column(
            "experiment_id",
            sa.String(36),
            sa.ForeignKey("generation_experiments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "recipe_snapshot_id",
            sa.String(64),
            sa.ForeignKey("generation_recipe_snapshots.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("node_id", sa.String(500), nullable=False),
        sa.Column("task_type", sa.String(100), nullable=False),
        sa.Column("role", sa.String(100), nullable=False),
        sa.Column("dependencies_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "dependency_output_refs_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("output_ids_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(32), server_default="pending", nullable=False),
        sa.Column("attempt", sa.Integer(), server_default="0", nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("semantic_prompt_sha256", sa.String(64), nullable=False),
        sa.Column("graph_sha256", sa.String(64), nullable=False),
        sa.Column("runtime_id", sa.String(100), nullable=True),
        sa.Column("provider_id", sa.String(100), nullable=True),
        sa.Column("model_id", sa.String(200), nullable=True),
        sa.Column("runtime_session_id", sa.String(500), nullable=True),
        sa.Column("runtime_message_id", sa.String(500), nullable=True),
        sa.Column("lease_owner", sa.String(200), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("wall_duration_ms", sa.BigInteger(), nullable=True),
        sa.Column("provider_latency_ms", sa.BigInteger(), nullable=True),
        sa.Column("input_tokens", sa.BigInteger(), nullable=True),
        sa.Column("output_tokens", sa.BigInteger(), nullable=True),
        sa.Column("cache_read_tokens", sa.BigInteger(), nullable=True),
        sa.Column("provider_cost_usd", sa.String(50), nullable=True),
        sa.Column("request_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
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
            "experiment_id",
            "node_id",
            name="uq_generation_task_runs_experiment_node",
        ),
    )
    op.create_index(
        "ix_generation_task_runs_experiment_id",
        "generation_task_runs",
        ["experiment_id"],
    )
    op.create_index(
        "ix_generation_task_runs_recipe_snapshot_id",
        "generation_task_runs",
        ["recipe_snapshot_id"],
    )
    op.create_index(
        "ix_generation_task_runs_scheduler",
        "generation_task_runs",
        ["status", "lease_expires_at"],
    )

    op.add_column(
        "generation_experiments",
        sa.Column("recipe_snapshot_id", sa.String(64), nullable=True),
    )
    op.create_index(
        "ix_generation_experiments_recipe_snapshot_id",
        "generation_experiments",
        ["recipe_snapshot_id"],
    )
    op.create_foreign_key(
        "fk_generation_experiments_recipe_snapshot",
        "generation_experiments",
        "generation_recipe_snapshots",
        ["recipe_snapshot_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_generation_experiments_recipe_snapshot",
        "generation_experiments",
        type_="foreignkey",
    )
    op.drop_index(
        "ix_generation_experiments_recipe_snapshot_id",
        table_name="generation_experiments",
    )
    op.drop_column("generation_experiments", "recipe_snapshot_id")
    op.drop_index(
        "ix_generation_task_runs_scheduler",
        table_name="generation_task_runs",
    )
    op.drop_index(
        "ix_generation_task_runs_recipe_snapshot_id",
        table_name="generation_task_runs",
    )
    op.drop_index(
        "ix_generation_task_runs_experiment_id",
        table_name="generation_task_runs",
    )
    op.drop_table("generation_task_runs")
    op.drop_index(
        "ix_generation_recipe_snapshots_identity",
        table_name="generation_recipe_snapshots",
    )
    op.drop_index(
        "ix_generation_recipe_snapshots_experiment_id",
        table_name="generation_recipe_snapshots",
    )
    op.drop_table("generation_recipe_snapshots")
