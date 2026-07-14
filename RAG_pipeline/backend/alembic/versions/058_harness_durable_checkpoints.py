"""persist sanitized LangGraph checkpoints for Nexus workflows"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "058_harness_durable_checkpoints"
down_revision: Union[str, None] = "057_conversation_continuations"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "harness_workflow_checkpoints",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("thread_id", sa.String(255), nullable=False),
        sa.Column("checkpoint_ns", sa.String(255), nullable=False, server_default=""),
        sa.Column("checkpoint_id", sa.String(255), nullable=False),
        sa.Column("parent_checkpoint_id", sa.String(255)),
        sa.Column("checkpoint_type", sa.String(24), nullable=False),
        sa.Column("checkpoint_data", sa.LargeBinary(), nullable=False),
        sa.Column("metadata_type", sa.String(24), nullable=False),
        sa.Column("metadata_data", sa.LargeBinary(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("thread_id", "checkpoint_ns", "checkpoint_id", name="uq_harness_workflow_checkpoints_thread_checkpoint"),
    )
    op.create_index("ix_harness_workflow_checkpoints_thread_ns", "harness_workflow_checkpoints", ["thread_id", "checkpoint_ns"])
    op.create_table(
        "harness_workflow_checkpoint_blobs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("thread_id", sa.String(255), nullable=False),
        sa.Column("checkpoint_ns", sa.String(255), nullable=False, server_default=""),
        sa.Column("channel", sa.String(255), nullable=False),
        sa.Column("channel_version", sa.String(255), nullable=False),
        sa.Column("value_type", sa.String(24), nullable=False),
        sa.Column("value_data", sa.LargeBinary(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("thread_id", "checkpoint_ns", "channel", "channel_version", name="uq_harness_workflow_checkpoint_blobs_channel_version"),
    )
    op.create_index("ix_harness_workflow_checkpoint_blobs_thread_ns", "harness_workflow_checkpoint_blobs", ["thread_id", "checkpoint_ns"])
    op.create_table(
        "harness_workflow_checkpoint_writes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("thread_id", sa.String(255), nullable=False),
        sa.Column("checkpoint_ns", sa.String(255), nullable=False, server_default=""),
        sa.Column("checkpoint_id", sa.String(255), nullable=False),
        sa.Column("task_id", sa.String(255), nullable=False),
        sa.Column("write_index", sa.Integer(), nullable=False),
        sa.Column("channel", sa.String(255), nullable=False),
        sa.Column("value_type", sa.String(24), nullable=False),
        sa.Column("value_data", sa.LargeBinary(), nullable=False),
        sa.Column("task_path", sa.String(512), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("thread_id", "checkpoint_ns", "checkpoint_id", "task_id", "write_index", name="uq_harness_workflow_checkpoint_writes_task_index"),
    )
    op.create_index("ix_harness_workflow_checkpoint_writes_checkpoint", "harness_workflow_checkpoint_writes", ["thread_id", "checkpoint_ns", "checkpoint_id"])


def downgrade() -> None:
    op.drop_index("ix_harness_workflow_checkpoint_writes_checkpoint", table_name="harness_workflow_checkpoint_writes")
    op.drop_table("harness_workflow_checkpoint_writes")
    op.drop_index("ix_harness_workflow_checkpoint_blobs_thread_ns", table_name="harness_workflow_checkpoint_blobs")
    op.drop_table("harness_workflow_checkpoint_blobs")
    op.drop_index("ix_harness_workflow_checkpoints_thread_ns", table_name="harness_workflow_checkpoints")
    op.drop_table("harness_workflow_checkpoints")
