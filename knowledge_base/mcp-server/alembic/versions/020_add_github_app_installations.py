"""Add github_app_installations ownership-binding table.

Multi-tenant guard: records which user connected a GitHub App installation so the
repos / add-repo / callback endpoints can authorize against ownership rather than
trusting a client-supplied (enumerable) installation id.

Revision ID: 020_add_github_app_installations
Revises: 019_add_github_app_installation
Create Date: 2026-07-06

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "020_add_github_app_installations"
down_revision: Union[str, None] = "019_add_github_app_installation"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "github_app_installations",
        sa.Column("installation_id", sa.Integer(), primary_key=True, autoincrement=False, nullable=False),
        sa.Column("owner_user_id", sa.Integer(), nullable=False),
        sa.Column("account_login", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index(
        "ix_github_app_installations_owner_user_id",
        "github_app_installations",
        ["owner_user_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_github_app_installations_owner_user_id",
        table_name="github_app_installations",
    )
    op.drop_table("github_app_installations")
