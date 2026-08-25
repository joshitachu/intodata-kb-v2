"""Restore the inherited timestamp on instance settings.

Revision ID: 094_instance_settings_created_at
Revises: 093_runtime_topology_timestamps

Migration 042 created ``instance_settings`` without the ``created_at`` column
inherited by its ORM model.  Databases created through a metadata bootstrap can
therefore differ from databases built solely through Alembic.  Make the schema
convergent without disturbing installations that already have the column.
"""

from collections.abc import Sequence

from alembic import op


revision: str = "094_instance_settings_created_at"
down_revision: str | None = "093_runtime_topology_timestamps"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE instance_settings
        ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE instance_settings DROP COLUMN IF EXISTS created_at")
