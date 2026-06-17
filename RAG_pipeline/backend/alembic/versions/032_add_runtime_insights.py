"""Add deterministic runtime insight tables (Slice 1).

Additive only: five new tables for enriched log events, error signatures,
execution summaries, incidents, and incident evidence. No existing table
is altered.

Revision ID: 032
Revises: 031
Create Date: 2026-06-17
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "032"
down_revision: Union[str, None] = "031"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "runtime_enriched_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.String(255), nullable=False),
        sa.Column("log_event_id", sa.Integer(), nullable=False),
        sa.Column("execution_id", sa.String(255), nullable=False),
        sa.Column("component_name", sa.String(255), nullable=True),
        sa.Column("error_code", sa.String(50), nullable=True),
        sa.Column("exception_class", sa.String(255), nullable=True),
        sa.Column("retry_attempt", sa.Integer(), nullable=True),
        sa.Column("retry_max", sa.Integer(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("timeout_ms", sa.Integer(), nullable=True),
        sa.Column("endpoint", sa.Text(), nullable=True),
        sa.Column("host", sa.String(255), nullable=True),
        sa.Column("database_name", sa.String(255), nullable=True),
        sa.Column("table_name", sa.String(255), nullable=True),
        sa.Column("queue_name", sa.String(255), nullable=True),
        sa.Column("message_family", sa.String(50), nullable=True),
        sa.Column("confidence", sa.String(20), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("project_id", "log_event_id", name="uq_runtime_enriched_events_project_log"),
    )
    op.create_index("ix_runtime_enriched_events_project_id", "runtime_enriched_events", ["project_id"])
    op.create_index(
        "ix_runtime_enriched_events_project_exec",
        "runtime_enriched_events",
        ["project_id", "execution_id"],
    )

    op.create_table(
        "runtime_error_signatures",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.String(255), nullable=False),
        sa.Column("signature_hash", sa.String(64), nullable=False),
        sa.Column("error_code", sa.String(50), nullable=True),
        sa.Column("exception_class", sa.String(255), nullable=True),
        sa.Column("component_name", sa.String(255), nullable=True),
        sa.Column("summary_text", sa.Text(), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("occurrence_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("project_id", "signature_hash", name="uq_runtime_error_signatures_project_hash"),
    )
    op.create_index("ix_runtime_error_signatures_project_id", "runtime_error_signatures", ["project_id"])

    op.create_table(
        "runtime_execution_summaries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.String(255), nullable=False),
        sa.Column("execution_id", sa.String(255), nullable=False),
        sa.Column("job_name", sa.String(255), nullable=False),
        sa.Column("warning_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("first_warning_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("first_error_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("final_error_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failed_component", sa.String(255), nullable=True),
        sa.Column("primary_error_code", sa.String(50), nullable=True),
        sa.Column("primary_exception_class", sa.String(255), nullable=True),
        sa.Column("summary_text", sa.Text(), nullable=True),
        sa.Column("evidence_refs", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint(
            "project_id", "execution_id", name="uq_runtime_execution_summaries_project_exec"
        ),
    )
    op.create_index(
        "ix_runtime_execution_summaries_project_id", "runtime_execution_summaries", ["project_id"]
    )
    op.create_index(
        "ix_runtime_execution_summaries_project_exec",
        "runtime_execution_summaries",
        ["project_id", "execution_id"],
    )

    op.create_table(
        "runtime_incidents",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.String(255), nullable=False),
        sa.Column("incident_id", sa.String(64), nullable=False),
        sa.Column("execution_id", sa.String(255), nullable=False),
        sa.Column("signature_hash", sa.String(64), nullable=True),
        sa.Column("symptom_summary", sa.Text(), nullable=True),
        sa.Column("root_cause_candidate", sa.Text(), nullable=True),
        sa.Column("confidence", sa.String(20), nullable=True),
        sa.Column("first_bad_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_healthy_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_checks", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("project_id", "execution_id", name="uq_runtime_incidents_project_exec"),
    )
    op.create_index("ix_runtime_incidents_project_id", "runtime_incidents", ["project_id"])
    op.create_index(
        "ix_runtime_incidents_project_incident",
        "runtime_incidents",
        ["project_id", "incident_id"],
    )

    op.create_table(
        "runtime_incident_evidence",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.String(255), nullable=False),
        sa.Column("incident_id", sa.String(64), nullable=False),
        sa.Column("log_event_id", sa.Integer(), nullable=False),
        sa.Column("evidence_role", sa.String(50), nullable=True),
        sa.Column("excerpt", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint(
            "project_id",
            "incident_id",
            "log_event_id",
            name="uq_runtime_incident_evidence_project_incident_log",
        ),
    )
    op.create_index(
        "ix_runtime_incident_evidence_project_id", "runtime_incident_evidence", ["project_id"]
    )
    op.create_index(
        "ix_runtime_incident_evidence_project_incident",
        "runtime_incident_evidence",
        ["project_id", "incident_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_runtime_incident_evidence_project_incident", table_name="runtime_incident_evidence")
    op.drop_index("ix_runtime_incident_evidence_project_id", table_name="runtime_incident_evidence")
    op.drop_table("runtime_incident_evidence")

    op.drop_index("ix_runtime_incidents_project_incident", table_name="runtime_incidents")
    op.drop_index("ix_runtime_incidents_project_id", table_name="runtime_incidents")
    op.drop_table("runtime_incidents")

    op.drop_index("ix_runtime_execution_summaries_project_exec", table_name="runtime_execution_summaries")
    op.drop_index("ix_runtime_execution_summaries_project_id", table_name="runtime_execution_summaries")
    op.drop_table("runtime_execution_summaries")

    op.drop_index("ix_runtime_error_signatures_project_id", table_name="runtime_error_signatures")
    op.drop_table("runtime_error_signatures")

    op.drop_index("ix_runtime_enriched_events_project_exec", table_name="runtime_enriched_events")
    op.drop_index("ix_runtime_enriched_events_project_id", table_name="runtime_enriched_events")
    op.drop_table("runtime_enriched_events")
