"""Allow prefixed SHA-256 discovery digests for connector tools.

Revision ID: 034_connector_digest
Revises: 033_remote_mcp_credentials
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "034_connector_digest"
down_revision = "033_remote_mcp_credentials"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("connector_sources") as batch:
        batch.alter_column(
            "discovery_digest",
            existing_type=sa.String(length=64),
            type_=sa.String(length=128),
            existing_nullable=True,
        )


def downgrade() -> None:
    connection = op.get_bind()
    long_digests = connection.execute(
        sa.text(
            "SELECT COUNT(*) FROM connector_sources "
            "WHERE length(discovery_digest) > 64"
        )
    ).scalar_one()
    if long_digests:
        raise RuntimeError(
            "Cannot narrow connector discovery digests while prefixed digests exist"
        )
    with op.batch_alter_table("connector_sources") as batch:
        batch.alter_column(
            "discovery_digest",
            existing_type=sa.String(length=128),
            type_=sa.String(length=64),
            existing_nullable=True,
        )
