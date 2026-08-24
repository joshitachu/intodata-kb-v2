"""Record when a refresh token was superseded by rotation.

Refresh-token rotation is single-use: `rotate_refresh_token` revoked the old
row and issued a new pair. Two concurrent POSTs to /auth/refresh -- two tabs
whose 10-minute timers align, a retry after a slow response, or React's
double-invoked effects in development -- meant the second caller presented an
already-rotated token and got a 401. The frontend read that as "logged out" and
redirected to /login while the session was in fact valid.

`rotated_at` lets rotation accept a token that was superseded moments ago and
issue a fresh pair, while still rejecting tokens revoked for real reasons
(logout, password change, admin revocation) which leave the column NULL.

Nullable with no backfill: existing rows are pre-grace-window and must keep
failing closed.

Revision ID: 087_refresh_token_rotation_grace
Revises: 086_pack_registry_timestamps
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "087_refresh_token_rotation_grace"
down_revision: Union[str, None] = "086_pack_registry_timestamps"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "refresh_tokens",
        sa.Column("rotated_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("refresh_tokens", "rotated_at")
