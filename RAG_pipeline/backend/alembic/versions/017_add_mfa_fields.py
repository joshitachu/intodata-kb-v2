"""add MFA / TOTP fields to users table

Adds mfa_enabled flag, totp_secret (plaintext base32 secret, readable by the
app when verifying codes — same threat model as password_hash, a DB breach is
game over either way), and backup_codes_hashed (JSONB list of
`{hash, used}` entries using bcrypt hashes).

Revision ID: 017
Revises: 016
Create Date: 2026-04-21
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "017"
down_revision: Union[str, None] = "016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = {c["name"] for c in inspector.get_columns("users")}

    if "mfa_enabled" not in existing:
        op.add_column(
            "users",
            sa.Column(
                "mfa_enabled",
                sa.Boolean(),
                server_default="false",
                nullable=False,
            ),
        )
    if "totp_secret" not in existing:
        op.add_column(
            "users",
            sa.Column("totp_secret", sa.String(64), nullable=True),
        )
    if "backup_codes_hashed" not in existing:
        op.add_column(
            "users",
            sa.Column(
                "backup_codes_hashed",
                postgresql.JSONB(astext_type=sa.Text()),
                nullable=True,
            ),
        )


def downgrade() -> None:
    op.drop_column("users", "backup_codes_hashed")
    op.drop_column("users", "totp_secret")
    op.drop_column("users", "mfa_enabled")
