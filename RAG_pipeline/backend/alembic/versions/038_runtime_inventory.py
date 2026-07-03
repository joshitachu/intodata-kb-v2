"""Add generic runtime inventory tables.

Revision ID: 038
Revises: 037
Create Date: 2026-07-02
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "038"
down_revision: Union[str, None] = "037"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "runtime_sources",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.String(255), nullable=False),
        sa.Column("source_key", sa.String(100), nullable=False),
        sa.Column("source_type", sa.String(100), nullable=False),
        sa.Column("label", sa.String(255), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("is_demo", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("project_id", "source_key", name="uq_runtime_sources_project_source"),
    )
    op.create_index("ix_runtime_sources_project_id", "runtime_sources", ["project_id"])
    op.create_index("ix_runtime_sources_source_key", "runtime_sources", ["source_key"])

    op.create_table(
        "runtime_environments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.String(255), nullable=False),
        sa.Column("source_key", sa.String(100), nullable=False),
        sa.Column("environment_id", sa.String(255), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "project_id",
            "source_key",
            "environment_id",
            name="uq_runtime_environments_project_source_environment",
        ),
    )
    op.create_index("ix_runtime_environments_project_id", "runtime_environments", ["project_id"])
    op.create_index("ix_runtime_environments_source_key", "runtime_environments", ["source_key"])

    op.create_table(
        "runtime_workspaces",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.String(255), nullable=False),
        sa.Column("source_key", sa.String(100), nullable=False),
        sa.Column("workspace_id", sa.String(255), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("environment_id", sa.String(255), nullable=True),
        sa.Column("environment_name", sa.String(255), nullable=True),
        sa.Column("owner", sa.String(255), nullable=True),
        sa.Column("workspace_type", sa.String(80), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "project_id",
            "source_key",
            "workspace_id",
            name="uq_runtime_workspaces_project_source_workspace",
        ),
    )
    op.create_index("ix_runtime_workspaces_project_id", "runtime_workspaces", ["project_id"])
    op.create_index("ix_runtime_workspaces_source_key", "runtime_workspaces", ["source_key"])
    op.create_index("ix_runtime_workspaces_environment_id", "runtime_workspaces", ["environment_id"])

    op.create_table(
        "runtime_engines",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.String(255), nullable=False),
        sa.Column("source_key", sa.String(100), nullable=False),
        sa.Column("engine_id", sa.String(255), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("raw_status", sa.String(120), nullable=True),
        sa.Column("availability", sa.String(120), nullable=True),
        sa.Column("version", sa.String(120), nullable=True),
        sa.Column("environment_id", sa.String(255), nullable=True),
        sa.Column("environment_name", sa.String(255), nullable=True),
        sa.Column("workspace_id", sa.String(255), nullable=True),
        sa.Column("workspace_name", sa.String(255), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "project_id",
            "source_key",
            "engine_id",
            name="uq_runtime_engines_project_source_engine",
        ),
    )
    op.create_index("ix_runtime_engines_project_id", "runtime_engines", ["project_id"])
    op.create_index("ix_runtime_engines_source_key", "runtime_engines", ["source_key"])
    op.create_index("ix_runtime_engines_status", "runtime_engines", ["status"])
    op.create_index("ix_runtime_engines_environment_id", "runtime_engines", ["environment_id"])


def downgrade() -> None:
    op.drop_index("ix_runtime_engines_environment_id", table_name="runtime_engines")
    op.drop_index("ix_runtime_engines_status", table_name="runtime_engines")
    op.drop_index("ix_runtime_engines_source_key", table_name="runtime_engines")
    op.drop_index("ix_runtime_engines_project_id", table_name="runtime_engines")
    op.drop_table("runtime_engines")
    op.drop_index("ix_runtime_workspaces_environment_id", table_name="runtime_workspaces")
    op.drop_index("ix_runtime_workspaces_source_key", table_name="runtime_workspaces")
    op.drop_index("ix_runtime_workspaces_project_id", table_name="runtime_workspaces")
    op.drop_table("runtime_workspaces")
    op.drop_index("ix_runtime_environments_source_key", table_name="runtime_environments")
    op.drop_index("ix_runtime_environments_project_id", table_name="runtime_environments")
    op.drop_table("runtime_environments")
    op.drop_index("ix_runtime_sources_source_key", table_name="runtime_sources")
    op.drop_index("ix_runtime_sources_project_id", table_name="runtime_sources")
    op.drop_table("runtime_sources")
