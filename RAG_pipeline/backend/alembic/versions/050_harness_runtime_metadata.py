"""add harness runtime metadata columns"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "050_harness_runtime_metadata"
down_revision: Union[str, None] = "049_harness_runs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("harness_workflow_runs", sa.Column("runtime_provider_id", sa.String(120), nullable=True))
    op.add_column("harness_workflow_runs", sa.Column("runtime_thread_id", sa.String(255), nullable=True))
    op.add_column("harness_workflow_runs", sa.Column("runtime_run_id", sa.String(255), nullable=True))
    op.add_column("harness_workflow_runs", sa.Column("current_node", sa.String(120), nullable=True))
    op.add_column("harness_workflow_runs", sa.Column("resume_token", sa.String(255), nullable=True))
    op.add_column("harness_workflow_runs", sa.Column("budget", sa.JSON(), nullable=True))
    op.add_column("harness_workflow_runs", sa.Column("evidence_refs", sa.JSON(), nullable=True))
    op.add_column("harness_workflow_runs", sa.Column("artifact_receipts", sa.JSON(), nullable=True))
    op.add_column("harness_workflow_runs", sa.Column("runtime_metadata", sa.JSON(), nullable=True))

    op.add_column("harness_activity_events", sa.Column("runtime_node", sa.String(120), nullable=True))
    op.add_column("harness_activity_events", sa.Column("tool_call_id", sa.String(255), nullable=True))
    op.add_column("harness_activity_events", sa.Column("evidence_refs", sa.JSON(), nullable=True))
    op.add_column("harness_activity_events", sa.Column("artifact_receipts", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("harness_activity_events", "artifact_receipts")
    op.drop_column("harness_activity_events", "evidence_refs")
    op.drop_column("harness_activity_events", "tool_call_id")
    op.drop_column("harness_activity_events", "runtime_node")

    op.drop_column("harness_workflow_runs", "runtime_metadata")
    op.drop_column("harness_workflow_runs", "artifact_receipts")
    op.drop_column("harness_workflow_runs", "evidence_refs")
    op.drop_column("harness_workflow_runs", "budget")
    op.drop_column("harness_workflow_runs", "resume_token")
    op.drop_column("harness_workflow_runs", "current_node")
    op.drop_column("harness_workflow_runs", "runtime_run_id")
    op.drop_column("harness_workflow_runs", "runtime_thread_id")
    op.drop_column("harness_workflow_runs", "runtime_provider_id")
