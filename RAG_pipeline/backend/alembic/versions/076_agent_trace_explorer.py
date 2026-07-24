"""Add safe diagnostic fields to Agent Run steps.

Revision ID: 076_agent_trace_explorer
Revises: 075_plan_step_timestamp_parity
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "076_agent_trace_explorer"
down_revision = "075_plan_step_timestamp_parity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    table = "harness_agent_steps"
    for name, type_ in (
        ("semantic_name", sa.String(120)), ("call_ref", sa.String(160)),
        ("parent_call_ref", sa.String(160)), ("attempt", sa.Integer()),
        ("phase", sa.String(48)), ("reason_code", sa.String(80)),
        ("task_type", sa.String(80)), ("trigger", sa.String(80)),
        ("recovery_from", sa.String(160)), ("arguments_hash", sa.String(64)),
    ):
        op.add_column(table, sa.Column(name, type_, nullable=True))
    for name, default in (
        ("argument_shape", "{}"), ("validation_errors", "[]"), ("details", "{}"),
    ):
        op.add_column(table, sa.Column(name, sa.JSON(), nullable=False, server_default=default))
    op.add_column(table, sa.Column("workflow_ref", sa.JSON(), nullable=True))
    op.add_column(table, sa.Column("receipt_ref", sa.JSON(), nullable=True))
    op.create_index("ix_harness_agent_steps_call_ref", table, ["agent_run_id", "call_ref"])


def downgrade() -> None:
    table = "harness_agent_steps"
    op.drop_index("ix_harness_agent_steps_call_ref", table_name=table)
    for name in (
        "receipt_ref", "workflow_ref", "details", "validation_errors",
        "argument_shape", "arguments_hash", "recovery_from", "trigger",
        "task_type", "reason_code", "phase", "attempt", "parent_call_ref",
        "call_ref", "semantic_name",
    ):
        op.drop_column(table, name)
