"""Persist the terminal component separately from the originating fault.

Revision ID: 092_incident_termination
Revises: 091_one_tmc_source_per_project
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "092_incident_termination"
down_revision: str | Sequence[str] | None = "091_one_tmc_source_per_project"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "runtime_incidents",
        sa.Column("termination_component", sa.String(length=255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("runtime_incidents", "termination_component")
