"""Add tier column to templates table.

Revision ID: 002_template_tier
Revises: 001_templates
Create Date: 2026-03-26

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "002_template_tier"
down_revision: Union[str, None] = "001_templates"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "templates",
        sa.Column("tier", sa.String(20), nullable=False, server_default="configurable"),
    )
    op.create_index("ix_templates_tier", "templates", ["tier"])

    # Set tiers based on category
    op.execute("UPDATE templates SET tier = 'policy' WHERE category IN ('analysis', 'orchestration', 'output')")
    op.execute("UPDATE templates SET tier = 'addon' WHERE category = 'pattern'")
    # assessment stays 'configurable' (the default)


def downgrade() -> None:
    op.drop_index("ix_templates_tier", table_name="templates")
    op.drop_column("templates", "tier")
