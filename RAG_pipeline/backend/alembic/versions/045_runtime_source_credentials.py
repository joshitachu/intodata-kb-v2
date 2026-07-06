"""runtime source credentials and source config columns"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "045_runtime_sources"
down_revision: Union[str, None] = "044"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("runtime_sources", sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.add_column("runtime_sources", sa.Column("sync_interval_seconds", sa.Integer(), nullable=False, server_default="60"))
    op.add_column("runtime_sources", sa.Column("auto_disable_threshold", sa.Integer(), nullable=False, server_default="3"))
    op.add_column("runtime_sources", sa.Column("disabled_reason", sa.String(120), nullable=True))

    op.create_table(
        "runtime_source_credentials",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source_id", sa.Integer(), sa.ForeignKey("runtime_sources.id", ondelete="CASCADE"), nullable=False),
        sa.Column("credential_type", sa.String(100), nullable=False),
        sa.Column("encrypted_payload", sa.LargeBinary(), nullable=False),
        sa.Column("encryption_key_id", sa.String(40), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("rotated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rotated_by", sa.String(255), nullable=True),
        sa.UniqueConstraint("source_id", name="uq_runtime_source_credentials_source"),
    )
    op.create_index(
        "ix_runtime_source_credentials_source_id",
        "runtime_source_credentials",
        ["source_id"],
    )

    op.execute(
        "INSERT INTO permissions (id, codename, description) VALUES "
        "(11, 'runtime_sources:read', 'Read runtime source configuration and health'), "
        "(12, 'runtime_sources:write', 'Manage runtime source configuration and credentials') "
        "ON CONFLICT DO NOTHING"
    )
    op.execute(
        "INSERT INTO role_permissions (role_id, permission_id) VALUES "
        "(1, 11), (1, 12) ON CONFLICT DO NOTHING"
    )


def downgrade() -> None:
    op.execute("DELETE FROM role_permissions WHERE permission_id IN (11, 12)")
    op.execute("DELETE FROM permissions WHERE id IN (11, 12)")
    op.drop_index("ix_runtime_source_credentials_source_id", table_name="runtime_source_credentials")
    op.drop_table("runtime_source_credentials")
    op.drop_column("runtime_sources", "disabled_reason")
    op.drop_column("runtime_sources", "auto_disable_threshold")
    op.drop_column("runtime_sources", "sync_interval_seconds")
    op.drop_column("runtime_sources", "enabled")
