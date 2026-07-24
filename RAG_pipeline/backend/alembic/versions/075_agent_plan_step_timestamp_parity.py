"""Add inherited timestamps omitted from AgentPlan steps.

Revision ID: 075_plan_step_timestamp_parity
Revises: 074_pack_capability_permission
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "075_plan_step_timestamp_parity"
down_revision = "074_pack_capability_permission"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "harness_agent_plan_steps",
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.add_column(
        "harness_agent_plan_steps",
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )


def downgrade() -> None:
    op.drop_column("harness_agent_plan_steps", "updated_at")
    op.drop_column("harness_agent_plan_steps", "created_at")
