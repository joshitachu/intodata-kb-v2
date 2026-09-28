"""Allow unauthenticated remote MCP connector instances.

Revision ID: 033_remote_mcp_credentials
Revises: 032_generation_profile
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "033_remote_mcp_credentials"
down_revision = "032_generation_profile"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("connector_instances") as batch:
        batch.alter_column(
            "credential_ciphertext",
            existing_type=sa.Text(),
            nullable=True,
        )


def downgrade() -> None:
    connection = op.get_bind()
    missing = connection.execute(
        sa.text(
            "SELECT COUNT(*) FROM connector_instances "
            "WHERE credential_ciphertext IS NULL"
        )
    ).scalar_one()
    if missing:
        raise RuntimeError(
            "Cannot downgrade while unauthenticated connector instances exist"
        )
    with op.batch_alter_table("connector_instances") as batch:
        batch.alter_column(
            "credential_ciphertext",
            existing_type=sa.Text(),
            nullable=False,
        )
