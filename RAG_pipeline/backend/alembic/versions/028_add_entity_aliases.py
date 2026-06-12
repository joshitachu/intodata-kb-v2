"""Add entity_aliases table for per-project entity alias lookup.

Revision ID: 028
Revises: 027
Create Date: 2026-06-12
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "028"
down_revision: Union[str, None] = "027"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "entity_aliases",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.String(255), nullable=False),
        sa.Column("alias", sa.String(500), nullable=False),
        sa.Column("alias_normalized", sa.String(500), nullable=False),
        sa.Column("canonical_name", sa.String(500), nullable=False),
        sa.Column("entity_type", sa.String(100), nullable=False),
        sa.Column("unit_name", sa.String(500), nullable=True),
        sa.Column("source_path", sa.String(1000), nullable=True),
        sa.Column("source_artifact", sa.String(100), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="1.0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_entity_aliases_project_id", "entity_aliases", ["project_id"])
    op.create_index(
        "ix_entity_aliases_project_alias",
        "entity_aliases",
        ["project_id", "alias_normalized"],
    )


def downgrade() -> None:
    op.drop_index("ix_entity_aliases_project_alias", table_name="entity_aliases")
    op.drop_index("ix_entity_aliases_project_id", table_name="entity_aliases")
    op.drop_table("entity_aliases")
