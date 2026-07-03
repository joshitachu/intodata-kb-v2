"""Add Base timestamp columns to runtime inventory tables.

Revision ID: 039
Revises: 038
Create Date: 2026-07-02
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "039"
down_revision: Union[str, None] = "038"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLES = (
    "runtime_sources",
    "runtime_environments",
    "runtime_workspaces",
    "runtime_engines",
)


def upgrade() -> None:
    for table in _TABLES:
        op.add_column(
            table,
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )
        op.add_column(
            table,
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )


def downgrade() -> None:
    for table in reversed(_TABLES):
        op.drop_column(table, "updated_at")
        op.drop_column(table, "created_at")
