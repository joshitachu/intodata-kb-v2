"""Allow only one registered TMC runtime source per project.

Revision ID: 091_one_tmc_source_per_project
Revises: 090_retire_governed_pack_stack
Create Date: 2026-08-25
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "091_one_tmc_source_per_project"
down_revision: Union[str, None] = "090_retire_governed_pack_stack"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Inventory mirrors use source_type="talend_tmc" and are deliberately not
    # covered. This rule applies only to credential-backed connector rows.
    op.create_index(
        "uq_runtime_sources_project_tmc",
        "runtime_sources",
        ["project_id"],
        unique=True,
        postgresql_where=sa.text("source_type = 'tmc'"),
    )


def downgrade() -> None:
    op.drop_index("uq_runtime_sources_project_tmc", table_name="runtime_sources")
