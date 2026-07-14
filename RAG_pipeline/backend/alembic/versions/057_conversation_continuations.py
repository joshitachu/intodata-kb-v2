"""persist typed conversation continuation envelopes"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "057_conversation_continuations"
down_revision: Union[str, None] = "056_structured_answers"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "harness_conversation_continuations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("conversation_id", sa.Integer(), sa.ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("owner_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(30), nullable=False, server_default="active"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("envelope", sa.JSON(), nullable=False),
        sa.Column("last_message_id", sa.Integer(), sa.ForeignKey("conversation_messages.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("conversation_id", "owner_user_id", name="uq_harness_conversation_continuations_conversation_owner"),
    )
    op.create_index("ix_harness_conversation_continuations_status", "harness_conversation_continuations", ["status"])


def downgrade() -> None:
    op.drop_index("ix_harness_conversation_continuations_status", table_name="harness_conversation_continuations")
    op.drop_table("harness_conversation_continuations")
