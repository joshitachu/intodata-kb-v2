"""Add template management tables and GenerateRun columns.

Revision ID: 001_templates
Revises: None
Create Date: 2026-03-24

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "001_templates"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- templates ---
    op.create_table(
        "templates",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("slug", sa.String(120), nullable=False),
        sa.Column("category", sa.String(40), nullable=False),
        sa.Column("platform", sa.String(50), nullable=True),
        sa.Column("display_name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_templates_slug", "templates", ["slug"], unique=True)
    op.create_index("ix_templates_category", "templates", ["category"])

    # --- template_versions ---
    op.create_table(
        "template_versions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("template_id", sa.Integer(), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("change_summary", sa.String(500), nullable=False),
        sa.Column("created_by", sa.String(100), nullable=True),
        sa.Column("content_hash", sa.String(64), nullable=True),
        sa.Column("byte_size", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["template_id"], ["templates.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("template_id", "version_number", name="uq_template_version"),
    )
    op.create_index("ix_template_versions_template_id", "template_versions", ["template_id"])

    # --- template_profiles ---
    op.create_table(
        "template_profiles",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("platform", sa.String(50), nullable=True),
        sa.Column("scope", sa.String(20), nullable=False, server_default=sa.text("'global'")),
        sa.Column("scope_ref", sa.String(100), nullable=True),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_by", sa.String(100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )

    # --- profile_assignments ---
    op.create_table(
        "profile_assignments",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("profile_id", sa.Integer(), nullable=False),
        sa.Column("template_id", sa.Integer(), nullable=False),
        sa.Column("version_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["profile_id"], ["template_profiles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["template_id"], ["templates.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["version_id"], ["template_versions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("profile_id", "template_id", name="uq_profile_template"),
    )
    op.create_index("ix_profile_assignments_profile_id", "profile_assignments", ["profile_id"])

    # --- Add columns to generate_runs ---
    op.add_column(
        "generate_runs",
        sa.Column("template_profile_id", sa.Integer(), nullable=True),
    )
    op.add_column(
        "generate_runs",
        sa.Column("template_snapshot", postgresql.JSONB(), nullable=True),
    )
    op.create_foreign_key(
        "fk_generate_runs_template_profile",
        "generate_runs",
        "template_profiles",
        ["template_profile_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_generate_runs_template_profile", "generate_runs", type_="foreignkey")
    op.drop_column("generate_runs", "template_snapshot")
    op.drop_column("generate_runs", "template_profile_id")
    op.drop_table("profile_assignments")
    op.drop_table("template_profiles")
    op.drop_table("template_versions")
    op.drop_table("templates")
