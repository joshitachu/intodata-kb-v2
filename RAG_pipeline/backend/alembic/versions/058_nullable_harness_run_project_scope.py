"""allow global and multi-project harness workflow receipts"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "058_nullable_harness_project"
down_revision: Union[str, None] = "057_conversation_continuations"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "harness_workflow_runs",
        "project_id",
        existing_type=sa.String(length=255),
        nullable=True,
    )


def downgrade() -> None:
    op.execute("DELETE FROM harness_workflow_runs WHERE project_id IS NULL")
    op.alter_column(
        "harness_workflow_runs",
        "project_id",
        existing_type=sa.String(length=255),
        nullable=False,
    )
