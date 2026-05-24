"""Add enterprise generation verdict fields.

Revision ID: 013_enterprise_verdict
Revises: 012_drop_template_management
Create Date: 2026-05-20

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "013_enterprise_verdict"
down_revision: Union[str, None] = "012_drop_template_management"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("generate_runs", sa.Column("run_verdict", sa.String(40), nullable=True))
    bind = op.get_bind()
    json_type = postgresql.JSONB(astext_type=sa.Text()) if bind.dialect.name == "postgresql" else sa.JSON()
    op.add_column("generate_runs", sa.Column("gate_results", json_type, nullable=True))


def downgrade() -> None:
    op.drop_column("generate_runs", "gate_results")
    op.drop_column("generate_runs", "run_verdict")
