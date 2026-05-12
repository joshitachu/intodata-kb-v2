"""Add SSO/OIDC fields to users table

Revision ID: 026
Revises: 025
Create Date: 2026-05-12
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "026"
down_revision: Union[str, None] = "025"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("sso_provider", sa.String(50), nullable=True))
    op.add_column("users", sa.Column("sso_subject", sa.String(255), nullable=True))
    # Partial unique index: (provider, subject) pair must be unique, but only
    # for rows that actually have an SSO provider set.
    op.create_index(
        "ix_users_sso_subject",
        "users",
        ["sso_provider", "sso_subject"],
        postgresql_where=sa.text("sso_provider IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_users_sso_subject", "users")
    op.drop_column("users", "sso_subject")
    op.drop_column("users", "sso_provider")
