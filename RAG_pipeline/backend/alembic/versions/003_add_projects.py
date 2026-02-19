"""add projects and scan_runs tables for MCP server

Revision ID: 003
Revises: 002
Create Date: 2026-02-18
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Projects table
    op.create_table(
        "projects",
        sa.Column("id", sa.String(100), primary_key=True),
        sa.Column("display_name", sa.String(255), nullable=False),
        sa.Column("repo_url", sa.String(500), nullable=True),
        sa.Column("local_path", sa.String(1000), nullable=False),
        sa.Column("platform", sa.String(50), server_default="talend", nullable=False),
        sa.Column("source_type", sa.String(20), server_default="local", nullable=False),
        sa.Column("status", sa.String(20), server_default="registered", nullable=False, index=True),
        sa.Column("last_scan_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_generate_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_rag_push_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("scan_result_path", sa.String(1000), nullable=True),
        sa.Column("output_dir", sa.String(1000), nullable=True),
        sa.Column("job_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("doc_count", sa.Integer(), server_default="0", nullable=False),
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
    )

    # Scan runs table
    op.create_table(
        "scan_runs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "project_id",
            sa.String(100),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("triggered_by", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), server_default="running", nullable=False, index=True),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("job_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("log_file", sa.String(500), nullable=True),
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
    )


def downgrade() -> None:
    op.drop_table("scan_runs")
    op.drop_table("projects")
