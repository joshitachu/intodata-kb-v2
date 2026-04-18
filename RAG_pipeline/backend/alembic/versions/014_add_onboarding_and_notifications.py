"""add onboarding + notifications tables

Note: multi-tenancy uses a "one VPS per client" model — tenant config lives in
env vars per deployment, not in a shared DB table.

Revision ID: 014
Revises: 013
Create Date: 2026-04-17
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "014"
down_revision: Union[str, None] = "013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # user_onboarding
    op.create_table(
        "user_onboarding",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("workspace_name", sa.String(200), nullable=True),
        sa.Column("wizard_completed", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("wizard_completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("wizard_current_step", sa.Integer(), server_default="0", nullable=False),
        sa.Column("selected_platforms", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("selected_comm_tools", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("source_type", sa.String(20), nullable=True),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("source_filename", sa.String(500), nullable=True),
        sa.Column("setup_status", sa.String(20), server_default="pending", nullable=False),
        sa.Column("setup_error", sa.Text(), nullable=True),
        sa.Column("tour_completed", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("tour_completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("tour_current_step", sa.Integer(), server_default="0", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("user_id", name="uq_user_onboarding_user_id"),
    )
    op.create_index("ix_user_onboarding_user_id", "user_onboarding", ["user_id"])

    # notifications
    op.create_table(
        "notifications",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("type", sa.String(50), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("is_read", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("meta", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_notifications_user_id", "notifications", ["user_id"])
    op.create_index("ix_notifications_is_read", "notifications", ["is_read"])
    op.create_index("ix_notifications_user_unread", "notifications", ["user_id", "is_read"])


def downgrade() -> None:
    op.drop_index("ix_notifications_user_unread", table_name="notifications")
    op.drop_index("ix_notifications_is_read", table_name="notifications")
    op.drop_index("ix_notifications_user_id", table_name="notifications")
    op.drop_table("notifications")

    op.drop_index("ix_user_onboarding_user_id", table_name="user_onboarding")
    op.drop_table("user_onboarding")
