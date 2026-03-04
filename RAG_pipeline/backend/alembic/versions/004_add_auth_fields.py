"""add auth fields to users and refresh_tokens table

Revision ID: 004
Revises: 003
Create Date: 2026-03-04
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "004"
down_revision: Union[str, None] = "003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_columns = {c["name"] for c in inspector.get_columns("users")}
    existing_tables = set(inspector.get_table_names())

    # Add auth columns to users table (skip if already present from old migration)
    if "password_hash" not in existing_columns:
        op.add_column("users", sa.Column("password_hash", sa.String(255), nullable=True))
    if "is_active" not in existing_columns:
        op.add_column(
            "users",
            sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        )
    if "is_superadmin" not in existing_columns:
        op.add_column(
            "users",
            sa.Column("is_superadmin", sa.Boolean(), server_default="false", nullable=False),
        )
    if "last_login_at" not in existing_columns:
        op.add_column(
            "users",
            sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        )

    # Drop old column from previous (deleted) migration if present
    if "must_change_password" in existing_columns:
        op.drop_column("users", "must_change_password")

    # Create refresh_tokens table
    if "refresh_tokens" not in existing_tables:
        op.create_table(
            "refresh_tokens",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("token_hash", sa.String(255), nullable=False, unique=True, index=True),
            sa.Column(
                "user_id",
                sa.Integer(),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False,
                index=True,
            ),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("revoked", sa.Boolean(), server_default="false", nullable=False),
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

    # Set existing admin user (id=1) as superadmin
    op.execute("UPDATE users SET is_superadmin = true WHERE id = 1")


def downgrade() -> None:
    op.drop_table("refresh_tokens")
    op.drop_column("users", "last_login_at")
    op.drop_column("users", "is_superadmin")
    op.drop_column("users", "is_active")
    op.drop_column("users", "password_hash")
