"""add generic harness connector instances and capability bindings"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "052_harness_connectors"
down_revision: Union[str, None] = "051_harness_interrupts"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "harness_connector_instances",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False, unique=True),
        sa.Column("provider_key", sa.String(64), nullable=False),
        sa.Column("config", sa.JSON(), nullable=False),
        sa.Column("auth_mode", sa.String(40), nullable=False),
        sa.Column("credential_reference", sa.String(255), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("health_status", sa.String(30), nullable=False, server_default="unknown"),
        sa.Column("health_summary", sa.Text(), nullable=True),
        sa.Column("health_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_harness_connector_instances_provider_key", "harness_connector_instances", ["provider_key"])
    op.create_table(
        "harness_connector_capability_bindings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("connector_instance_id", sa.Integer(), sa.ForeignKey("harness_connector_instances.id", ondelete="CASCADE"), nullable=False),
        sa.Column("capability_id", sa.String(160), nullable=False),
        sa.Column("project_ids", sa.JSON(), nullable=False),
        sa.Column("domains", sa.JSON(), nullable=False),
        sa.Column("risk_tier", sa.String(30), nullable=False, server_default="read_only"),
        sa.Column("redaction_fields", sa.JSON(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("connector_instance_id", "capability_id", name="uq_harness_connector_binding"),
    )
    op.create_index("ix_harness_connector_bindings_capability_id", "harness_connector_capability_bindings", ["capability_id"])


def downgrade() -> None:
    op.drop_index("ix_harness_connector_bindings_capability_id", table_name="harness_connector_capability_bindings")
    op.drop_table("harness_connector_capability_bindings")
    op.drop_index("ix_harness_connector_instances_provider_key", table_name="harness_connector_instances")
    op.drop_table("harness_connector_instances")
