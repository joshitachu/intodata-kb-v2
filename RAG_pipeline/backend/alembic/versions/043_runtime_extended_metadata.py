"""Runtime extended metadata.

Adds optional artifact, connection, and job-dependency facts to the runtime mirror.
Connection parameters and credentials are intentionally not stored.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "043"
down_revision: Union[str, None] = "042"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "runtime_artifacts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.String(255), nullable=False),
        sa.Column("source_key", sa.String(100), nullable=False),
        sa.Column("artifact_id", sa.String(255), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("artifact_type", sa.String(120), nullable=True),
        sa.Column("version", sa.String(120), nullable=True),
        sa.Column("workspace_id", sa.String(255), nullable=True),
        sa.Column("workspace_name", sa.String(255), nullable=True),
        sa.Column("owner", sa.String(255), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "project_id",
            "source_key",
            "artifact_id",
            name="uq_runtime_artifacts_project_source_artifact",
        ),
    )
    op.create_index("ix_runtime_artifacts_project_id", "runtime_artifacts", ["project_id"])
    op.create_index("ix_runtime_artifacts_source_key", "runtime_artifacts", ["source_key"])
    op.create_index("ix_runtime_artifacts_workspace_id", "runtime_artifacts", ["workspace_id"])
    op.create_index("ix_runtime_artifacts_type", "runtime_artifacts", ["artifact_type"])

    op.create_table(
        "runtime_connections",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.String(255), nullable=False),
        sa.Column("source_key", sa.String(100), nullable=False),
        sa.Column("connection_id", sa.String(255), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("connection_type", sa.String(120), nullable=True),
        sa.Column("workspace_id", sa.String(255), nullable=True),
        sa.Column("workspace_name", sa.String(255), nullable=True),
        sa.Column("owner", sa.String(255), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "project_id",
            "source_key",
            "connection_id",
            name="uq_runtime_connections_project_source_connection",
        ),
    )
    op.create_index("ix_runtime_connections_project_id", "runtime_connections", ["project_id"])
    op.create_index("ix_runtime_connections_source_key", "runtime_connections", ["source_key"])
    op.create_index("ix_runtime_connections_workspace_id", "runtime_connections", ["workspace_id"])
    op.create_index("ix_runtime_connections_type", "runtime_connections", ["connection_type"])

    op.create_table(
        "runtime_job_dependencies",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.String(255), nullable=False),
        sa.Column("source_key", sa.String(100), nullable=False),
        sa.Column("job_id", sa.String(255), nullable=False),
        sa.Column("job_name", sa.String(255), nullable=False),
        sa.Column("dependency_type", sa.String(40), nullable=False),
        sa.Column("dependency_id", sa.String(255), nullable=False),
        sa.Column("dependency_name", sa.String(255), nullable=True),
        sa.Column("dependency_kind", sa.String(120), nullable=True),
        sa.Column("workspace_id", sa.String(255), nullable=True),
        sa.Column("workspace_name", sa.String(255), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "project_id",
            "source_key",
            "job_id",
            "dependency_type",
            "dependency_id",
            name="uq_runtime_job_dependencies_project_source_job_dep",
        ),
    )
    op.create_index("ix_runtime_job_dependencies_project_id", "runtime_job_dependencies", ["project_id"])
    op.create_index("ix_runtime_job_dependencies_source_key", "runtime_job_dependencies", ["source_key"])
    op.create_index("ix_runtime_job_dependencies_job_id", "runtime_job_dependencies", ["job_id"])
    op.create_index(
        "ix_runtime_job_dependencies_dependency",
        "runtime_job_dependencies",
        ["dependency_type", "dependency_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_runtime_job_dependencies_dependency", table_name="runtime_job_dependencies")
    op.drop_index("ix_runtime_job_dependencies_job_id", table_name="runtime_job_dependencies")
    op.drop_index("ix_runtime_job_dependencies_source_key", table_name="runtime_job_dependencies")
    op.drop_index("ix_runtime_job_dependencies_project_id", table_name="runtime_job_dependencies")
    op.drop_table("runtime_job_dependencies")

    op.drop_index("ix_runtime_connections_type", table_name="runtime_connections")
    op.drop_index("ix_runtime_connections_workspace_id", table_name="runtime_connections")
    op.drop_index("ix_runtime_connections_source_key", table_name="runtime_connections")
    op.drop_index("ix_runtime_connections_project_id", table_name="runtime_connections")
    op.drop_table("runtime_connections")

    op.drop_index("ix_runtime_artifacts_type", table_name="runtime_artifacts")
    op.drop_index("ix_runtime_artifacts_workspace_id", table_name="runtime_artifacts")
    op.drop_index("ix_runtime_artifacts_source_key", table_name="runtime_artifacts")
    op.drop_index("ix_runtime_artifacts_project_id", table_name="runtime_artifacts")
    op.drop_table("runtime_artifacts")
