"""add feedback table for chat thumbs-up/down persistence

Captures user reactions to assistant messages. Append-only log (latest row per
user+message wins) so we keep the full audit trail. Feeds the Week-3
knowledge-gaps admin view and the chat->docs learning loop.

Revision ID: 016
Revises: 015
Create Date: 2026-04-20
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "016"
down_revision: Union[str, None] = "015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "feedback",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "conversation_id",
            sa.Integer(),
            sa.ForeignKey("conversations.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "message_id",
            sa.Integer(),
            sa.ForeignKey("conversation_messages.id", ondelete="SET NULL"),
            nullable=True,
        ),
        # 'up', 'down', 'correction', 'cleared'
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("text", sa.Text(), nullable=True),
        sa.Column(
            "citations_snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
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
            onupdate=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_feedback_user_id", "feedback", ["user_id"])
    op.create_index("ix_feedback_message_id", "feedback", ["message_id"])
    op.create_index("ix_feedback_kind_created_at", "feedback", ["kind", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_feedback_kind_created_at", table_name="feedback")
    op.drop_index("ix_feedback_message_id", table_name="feedback")
    op.drop_index("ix_feedback_user_id", table_name="feedback")
    op.drop_table("feedback")
