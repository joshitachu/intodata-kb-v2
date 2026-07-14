"""add durable harness interrupts and optimistic lifecycle fields"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "051_harness_interrupts"
down_revision: Union[str, None] = "050_harness_runtime_metadata"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "harness_workflow_runs",
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
    )
    op.add_column("harness_workflow_runs", sa.Column("cancelled_at", sa.DateTime(timezone=True)))
    op.add_column("harness_workflow_runs", sa.Column("cancelled_by_user_id", sa.Integer()))
    op.add_column("harness_workflow_runs", sa.Column("cancellation_reason", sa.String(500)))
    op.create_foreign_key(
        "fk_harness_workflow_runs_cancelled_by_user_id",
        "harness_workflow_runs",
        "users",
        ["cancelled_by_user_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.create_table(
        "harness_workflow_interrupts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "workflow_run_id",
            sa.Integer(),
            sa.ForeignKey("harness_workflow_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "owner_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("interrupt_type", sa.String(40), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("options", sa.JSON()),
        sa.Column("policy_snapshot", sa.JSON()),
        sa.Column("scope_snapshot", sa.JSON()),
        sa.Column("argument_hash", sa.String(128), nullable=False),
        sa.Column("response_summary", sa.Text()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    for column in ("workflow_run_id", "owner_user_id", "status", "expires_at"):
        op.create_index(
            f"ix_harness_workflow_interrupts_{column}",
            "harness_workflow_interrupts",
            [column],
        )


def downgrade() -> None:
    op.drop_table("harness_workflow_interrupts")
    op.drop_constraint(
        "fk_harness_workflow_runs_cancelled_by_user_id",
        "harness_workflow_runs",
        type_="foreignkey",
    )
    op.drop_column("harness_workflow_runs", "cancellation_reason")
    op.drop_column("harness_workflow_runs", "cancelled_by_user_id")
    op.drop_column("harness_workflow_runs", "cancelled_at")
    op.drop_column("harness_workflow_runs", "version")
