"""add generic harness workflow receipts"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "049_harness_runs"
down_revision: Union[str, None] = "048_runtime_meta_ts"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "harness_workflow_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "conversation_id",
            sa.Integer(),
            sa.ForeignKey("conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", sa.String(255), nullable=False),
        sa.Column("intent_mode", sa.String(80), nullable=False),
        sa.Column("capability_id", sa.String(160), nullable=False),
        sa.Column("provider_id", sa.String(160), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("input_summary", sa.Text(), nullable=True),
        sa.Column("output_summary", sa.Text(), nullable=True),
        sa.Column("artifact_links", sa.JSON(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_harness_workflow_runs_conversation_id", "harness_workflow_runs", ["conversation_id"])
    op.create_index("ix_harness_workflow_runs_user_id", "harness_workflow_runs", ["user_id"])
    op.create_index("ix_harness_workflow_runs_project_id", "harness_workflow_runs", ["project_id"])
    op.create_index("ix_harness_workflow_runs_status", "harness_workflow_runs", ["status"])
    op.create_index("ix_harness_workflow_runs_capability_id", "harness_workflow_runs", ["capability_id"])

    op.create_table(
        "harness_activity_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "workflow_run_id",
            sa.Integer(),
            sa.ForeignKey("harness_workflow_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(80), nullable=False),
        sa.Column("label", sa.String(255), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("provider_id", sa.String(160), nullable=True),
        sa.Column("capability_id", sa.String(160), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("safe_details", sa.JSON(), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint(
            "workflow_run_id",
            "sequence",
            name="uq_harness_activity_events_run_sequence",
        ),
    )
    op.create_index(
        "ix_harness_activity_events_workflow_run_id",
        "harness_activity_events",
        ["workflow_run_id"],
    )
    op.create_index("ix_harness_activity_events_event_type", "harness_activity_events", ["event_type"])
    op.create_index("ix_harness_activity_events_status", "harness_activity_events", ["status"])


def downgrade() -> None:
    op.drop_index("ix_harness_activity_events_status", table_name="harness_activity_events")
    op.drop_index("ix_harness_activity_events_event_type", table_name="harness_activity_events")
    op.drop_index(
        "ix_harness_activity_events_workflow_run_id",
        table_name="harness_activity_events",
    )
    op.drop_table("harness_activity_events")

    op.drop_index("ix_harness_workflow_runs_capability_id", table_name="harness_workflow_runs")
    op.drop_index("ix_harness_workflow_runs_status", table_name="harness_workflow_runs")
    op.drop_index("ix_harness_workflow_runs_project_id", table_name="harness_workflow_runs")
    op.drop_index("ix_harness_workflow_runs_user_id", table_name="harness_workflow_runs")
    op.drop_index("ix_harness_workflow_runs_conversation_id", table_name="harness_workflow_runs")
    op.drop_table("harness_workflow_runs")
