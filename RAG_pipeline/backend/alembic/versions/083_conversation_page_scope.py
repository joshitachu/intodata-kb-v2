"""Persist the client's page scope on each conversation turn.

Additive only: one nullable JSON column holding the validated `schemas.PageScope`
snapshot of what the client was viewing when it sent the turn. No data migration --
existing rows stay NULL, which reads as "no page context supplied".

The stored value is client-supplied and untrusted. It exists for prompt
disambiguation and turn replay; nothing reads it back as an authorization input.

Revision ID: 083_conversation_page_scope
Revises: 082_runtime_log_search_index
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "083_conversation_page_scope"
down_revision: str | Sequence[str] | None = "082_runtime_log_search_index"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "conversation_messages",
        sa.Column("page_scope", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("conversation_messages", "page_scope")
