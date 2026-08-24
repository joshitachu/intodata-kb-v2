"""Separate embedded topology threads from the main chat list.

The conversation runtime remains shared. These two presentation fields only
control where a thread was created and whether the user has explicitly chosen
to continue it in main chat.

Revision ID: 088_conversation_visibility
Revises: 087_refresh_token_rotation_grace
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "088_conversation_visibility"
down_revision: str | Sequence[str] | None = "087_refresh_token_rotation_grace"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "conversations",
        sa.Column("origin_surface", sa.String(length=20), server_default="chat", nullable=False),
    )
    op.add_column(
        "conversations",
        sa.Column("visible_in_chat", sa.Boolean(), server_default=sa.true(), nullable=False),
    )
    # Existing embedded threads are identifiable by their validated turn scope.
    # Hide them from the ordinary chat list without deleting their history.
    op.execute(
        """
        UPDATE conversations AS c
        SET origin_surface = 'topology', visible_in_chat = false
        WHERE EXISTS (
            SELECT 1
            FROM conversation_messages AS m
            WHERE m.conversation_id = c.id
              AND m.page_scope ->> 'surface' = 'topology'
        )
        """
    )


def downgrade() -> None:
    op.drop_column("conversations", "visible_in_chat")
    op.drop_column("conversations", "origin_surface")
