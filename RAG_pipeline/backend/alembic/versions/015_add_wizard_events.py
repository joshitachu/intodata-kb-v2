"""add wizard_events telemetry table

Captures raw onboarding events (step viewed, platform/tool picked, tour
started/skipped) so we can see what users actually choose and prioritise
which integration to wire end-to-end first.

Revision ID: 015
Revises: 014
Create Date: 2026-04-20
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "015"
down_revision: Union[str, None] = "014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "wizard_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("step", sa.Integer(), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_wizard_events_user_id", "wizard_events", ["user_id"])
    op.create_index("ix_wizard_events_event_type", "wizard_events", ["event_type"])
    op.create_index("ix_wizard_events_created_at", "wizard_events", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_wizard_events_created_at", table_name="wizard_events")
    op.drop_index("ix_wizard_events_event_type", table_name="wizard_events")
    op.drop_index("ix_wizard_events_user_id", table_name="wizard_events")
    op.drop_table("wizard_events")
