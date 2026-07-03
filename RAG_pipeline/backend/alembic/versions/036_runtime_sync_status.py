"""Add runtime sync status fields.

Revision ID: 036
Revises: 035
Create Date: 2026-07-02
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "036"
down_revision: Union[str, None] = "035"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "runtime_sync_state",
        sa.Column("status", sa.String(20), server_default="success", nullable=False),
    )
    op.add_column(
        "runtime_sync_state",
        sa.Column("failure_reason_code", sa.String(80), nullable=True),
    )
    op.add_column(
        "runtime_sync_state",
        sa.Column("error_summary", sa.Text(), nullable=True),
    )
    op.alter_column("runtime_sync_state", "last_synced_at", nullable=True)


def downgrade() -> None:
    op.execute(
        "UPDATE runtime_sync_state SET last_synced_at = updated_at WHERE last_synced_at IS NULL"
    )
    op.alter_column("runtime_sync_state", "last_synced_at", nullable=False)
    op.drop_column("runtime_sync_state", "error_summary")
    op.drop_column("runtime_sync_state", "failure_reason_code")
    op.drop_column("runtime_sync_state", "status")
