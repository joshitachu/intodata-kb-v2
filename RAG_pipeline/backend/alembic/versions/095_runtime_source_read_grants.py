"""Explicit read grants over a runtime source's mirror.

Revision ID: 095_runtime_source_read_grants
Revises: 094_instance_settings_created_at

One Talend tenant commonly backs several scanned projects, but registration
refuses the same credential twice (a second row means a second poll and two
mirrors of one estate that drift apart).  Sharing therefore belongs on the read
side, as an explicit grant, rather than as a duplicated source.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "095_runtime_source_read_grants"
down_revision: str | None = "094_instance_settings_created_at"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "runtime_source_read_grants",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "source_id",
            sa.Integer(),
            sa.ForeignKey("runtime_sources.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("project_id", sa.String(255), nullable=False),
        # NULL means every mirrored environment; an empty list is a deliberate
        # "nothing", the same distinction RuntimeDocReadScope draws.
        sa.Column("environment_ids", sa.JSON(), nullable=True),
        sa.Column("window_days", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.String(255), nullable=True),
        sa.UniqueConstraint(
            "source_id", "project_id", name="uq_runtime_source_read_grants_source_project"
        ),
    )
    op.create_index(
        "ix_runtime_source_read_grants_source_id",
        "runtime_source_read_grants",
        ["source_id"],
    )
    op.create_index(
        "ix_runtime_source_read_grants_project_id",
        "runtime_source_read_grants",
        ["project_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_runtime_source_read_grants_project_id", table_name="runtime_source_read_grants"
    )
    op.drop_index(
        "ix_runtime_source_read_grants_source_id", table_name="runtime_source_read_grants"
    )
    op.drop_table("runtime_source_read_grants")
