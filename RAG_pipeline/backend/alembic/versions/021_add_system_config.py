"""add system_config table for company-level onboarding

Revision ID: 021
Revises: 020
Create Date: 2026-05-08
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "021"
down_revision: Union[str, None] = "020"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "system_config",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("workspace_name", sa.String(200), nullable=True),
        sa.Column("selected_platforms", JSONB(), nullable=True),
        sa.Column("selected_comm_tools", JSONB(), nullable=True),
        sa.Column("source_type", sa.String(20), nullable=True),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("source_filename", sa.String(500), nullable=True),
        sa.Column(
            "company_wizard_completed",
            sa.Boolean(),
            server_default="false",
            nullable=False,
        ),
        sa.Column("company_wizard_completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "wizard_current_step",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
        sa.Column(
            "setup_status",
            sa.String(20),
            server_default="pending",
            nullable=False,
        ),
        sa.Column("setup_error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("system_config")
