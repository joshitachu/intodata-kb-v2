"""add totp replay-protection columns to users

Revision ID: 022
Revises: 021
Create Date: 2026-05-11
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "022"
down_revision: Union[str, None] = "021"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("totp_last_code", sa.String(6), nullable=True))
    op.add_column(
        "users",
        sa.Column("totp_last_code_used_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("users", "totp_last_code_used_at")
    op.drop_column("users", "totp_last_code")
