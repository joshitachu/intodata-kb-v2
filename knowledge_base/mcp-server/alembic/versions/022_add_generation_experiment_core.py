"""Add isolated generation experiment and artifact index tables.

Revision ID: 022_generation_experiment_core
Revises: 021_plugin_pack_governance
Create Date: 2026-07-27
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "022_generation_experiment_core"
down_revision: Union[str, None] = "021_plugin_pack_governance"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "generation_experiments",
        sa.Column("id", sa.String(36), primary_key=True, nullable=False),
        sa.Column(
            "project_id",
            sa.String(100),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "baseline_run_id",
            sa.Integer(),
            sa.ForeignKey("generate_runs.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("idempotency_key", sa.String(200), nullable=False),
        sa.Column("status", sa.String(32), server_default="queued", nullable=False),
        sa.Column("comparison_status", sa.String(32), server_default="pending", nullable=False),
        sa.Column(
            "candidate_verdict",
            sa.String(32),
            server_default="not_evaluated",
            nullable=False,
        ),
        sa.Column("runtime_id", sa.String(100), server_default="fake", nullable=False),
        sa.Column("runtime_version", sa.String(100), nullable=True),
        sa.Column("provider_id", sa.String(100), nullable=True),
        sa.Column("model_id", sa.String(200), nullable=True),
        sa.Column("publishes", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("baseline_artifact_root", sa.String(1000), nullable=True),
        sa.Column("candidate_artifact_root", sa.String(1000), nullable=True),
        sa.Column("baseline_manifest_sha256", sa.String(64), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("wall_duration_ms", sa.BigInteger(), nullable=True),
        sa.Column("critical_path_ms", sa.BigInteger(), nullable=True),
        sa.Column("settings_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("metrics_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("comparison_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
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
            "project_id",
            "idempotency_key",
            name="uq_generation_experiments_project_idempotency",
        ),
    )
    op.create_index(
        "ix_generation_experiments_project_id",
        "generation_experiments",
        ["project_id"],
    )
    op.create_index(
        "ix_generation_experiments_status",
        "generation_experiments",
        ["status"],
    )
    op.create_index(
        "ix_generation_experiments_project_created",
        "generation_experiments",
        ["project_id", "created_at"],
    )

    op.create_table(
        "generation_experiment_artifacts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True, nullable=False),
        sa.Column(
            "experiment_id",
            sa.String(36),
            sa.ForeignKey("generation_experiments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("branch", sa.String(32), nullable=False),
        sa.Column("kind", sa.String(100), nullable=False),
        sa.Column("logical_name", sa.String(500), nullable=False),
        sa.Column("relative_path", sa.String(1000), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("media_type", sa.String(200), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
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
            "branch",
            "kind",
            "logical_name",
            "sha256",
            name="uq_generation_experiment_artifact_identity",
        ),
        sa.UniqueConstraint(
            "experiment_id",
            "relative_path",
            name="uq_generation_experiment_artifact_path",
        ),
    )
    op.create_index(
        "ix_generation_experiment_artifacts_experiment_id",
        "generation_experiment_artifacts",
        ["experiment_id"],
    )
    op.create_index(
        "ix_generation_experiment_artifacts_lookup",
        "generation_experiment_artifacts",
        ["experiment_id", "branch", "kind"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_generation_experiment_artifacts_lookup",
        table_name="generation_experiment_artifacts",
    )
    op.drop_index(
        "ix_generation_experiment_artifacts_experiment_id",
        table_name="generation_experiment_artifacts",
    )
    op.drop_table("generation_experiment_artifacts")
    op.drop_index(
        "ix_generation_experiments_project_created",
        table_name="generation_experiments",
    )
    op.drop_index(
        "ix_generation_experiments_status",
        table_name="generation_experiments",
    )
    op.drop_index(
        "ix_generation_experiments_project_id",
        table_name="generation_experiments",
    )
    op.drop_table("generation_experiments")
