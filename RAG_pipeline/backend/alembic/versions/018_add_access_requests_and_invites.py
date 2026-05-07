"""add access requests and user invites

Revision ID: 018
Revises: 017
Create Date: 2026-05-06
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "018"
down_revision: Union[str, None] = "017"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "user_invites",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="pending", nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("accepted_user_id", sa.Integer(), nullable=True),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("full_name", sa.String(length=255), nullable=True),
        sa.Column("role_title", sa.String(length=255), nullable=True),
        sa.Column("intended_use", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["accepted_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_user_invites_email", "user_invites", ["email"])
    op.create_index("ix_user_invites_status", "user_invites", ["status"])
    op.create_index("ix_user_invites_token_hash", "user_invites", ["token_hash"], unique=True)

    op.create_table(
        "access_requests",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("full_name", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=20), server_default="pending", nullable=False),
        sa.Column("reviewed_by", sa.Integer(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejection_reason", sa.Text(), nullable=True),
        sa.Column("invite_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["invite_id"], ["user_invites.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["reviewed_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_access_requests_email", "access_requests", ["email"])
    op.create_index("ix_access_requests_status", "access_requests", ["status"])


def downgrade() -> None:
    op.drop_index("ix_access_requests_status", table_name="access_requests")
    op.drop_index("ix_access_requests_email", table_name="access_requests")
    op.drop_table("access_requests")
    op.drop_index("ix_user_invites_token_hash", table_name="user_invites")
    op.drop_index("ix_user_invites_status", table_name="user_invites")
    op.drop_index("ix_user_invites_email", table_name="user_invites")
    op.drop_table("user_invites")
