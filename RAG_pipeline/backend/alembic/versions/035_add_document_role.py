"""Add document_role to documents.

Revision ID: 035
Revises: 034
Create Date: 2026-06-25
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "035"
down_revision: Union[str, None] = "034"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("documents") as batch:
        batch.add_column(
            sa.Column(
                "document_role",
                sa.String(20),
                nullable=False,
                server_default="source",
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("documents") as batch:
        batch.drop_column("document_role")
