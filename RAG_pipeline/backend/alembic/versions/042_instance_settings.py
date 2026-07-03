"""Per-instance model settings.

A single-row-per-key table that lets superadmins override the env-var-derived
default LLM/embedder models at runtime without restarting services. The DB
value takes precedence; falls back to env / pydantic-settings default when
unset.

Scope: each VPS owns its own Postgres, so a row in this table is naturally
per-VPS. No replication layer.

Keys are registered constants in src.services.instance_settings_service so
callers cannot spell them inconsistently.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "042"
down_revision: Union[str, None] = "041"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "instance_settings",
        sa.Column("key", sa.String(100), primary_key=True),
        sa.Column("value", sa.String(500), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_by_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("updated_by_username", sa.String(100), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("instance_settings")