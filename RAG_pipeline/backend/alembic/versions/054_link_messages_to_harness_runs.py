"""link assistant messages to durable harness workflow runs"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "054_message_workflow_link"
down_revision: Union[str, None] = "053_harness_evidence"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "conversation_messages",
        sa.Column("workflow_run_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_conversation_messages_workflow_run_id",
        "conversation_messages",
        "harness_workflow_runs",
        ["workflow_run_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_conversation_messages_workflow_run_id",
        "conversation_messages",
        ["workflow_run_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_conversation_messages_workflow_run_id",
        table_name="conversation_messages",
    )
    op.drop_constraint(
        "fk_conversation_messages_workflow_run_id",
        "conversation_messages",
        type_="foreignkey",
    )
    op.drop_column("conversation_messages", "workflow_run_id")
