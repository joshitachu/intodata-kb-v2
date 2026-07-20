"""Add exact provider-child workflow ledger receipts and observations.

Revision ID: 066_workflow_child_ledger
Revises: 065_detached_pack_drafts
Create Date: 2026-07-18
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "066_workflow_child_ledger"
down_revision = "065_detached_pack_drafts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "harness_workflow_child_receipts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "workflow_run_id",
            sa.Integer(),
            sa.ForeignKey("harness_workflow_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("provider_id", sa.String(160), nullable=False),
        sa.Column("child_kind", sa.String(80), nullable=False),
        sa.Column("external_run_id", sa.String(255), nullable=False),
        sa.Column("project_id", sa.String(255), nullable=True),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("provider_status", sa.String(80), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("provider_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("provider_finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("provider_timestamp", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_observation_source", sa.String(24), nullable=False),
        sa.Column("error_message", sa.String(500), nullable=True),
        sa.Column("cancellation_state", sa.String(30), nullable=False, server_default="none"),
        sa.Column("cancellation_idempotency_key", sa.String(255), nullable=True),
        sa.Column("cancellation_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancellation_provider_status", sa.String(80), nullable=True),
        sa.Column("cancellation_recorded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint(
            "workflow_run_id",
            "provider_id",
            "child_kind",
            name="uq_harness_workflow_child_parent_provider_kind",
        ),
        sa.UniqueConstraint(
            "provider_id",
            "external_run_id",
            name="uq_harness_workflow_child_provider_external_run",
        ),
        sa.UniqueConstraint(
            "cancellation_idempotency_key",
            name="uq_harness_workflow_child_cancellation_idempotency",
        ),
    )
    op.create_index(
        "ix_harness_workflow_child_receipts_workflow_run_id",
        "harness_workflow_child_receipts",
        ["workflow_run_id"],
    )
    op.create_index(
        "ix_harness_workflow_child_receipts_status",
        "harness_workflow_child_receipts",
        ["status"],
    )
    op.create_table(
        "harness_workflow_child_observations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "child_receipt_id",
            sa.Integer(),
            sa.ForeignKey("harness_workflow_child_receipts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("idempotency_key", sa.String(255), nullable=False),
        sa.Column("source", sa.String(24), nullable=False),
        sa.Column("provider_status", sa.String(80), nullable=False),
        sa.Column("normalized_status", sa.String(30), nullable=False),
        sa.Column("provider_timestamp", sa.DateTime(timezone=True), nullable=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted", sa.Boolean(), nullable=False),
        sa.Column("decision_reason", sa.String(80), nullable=False),
        sa.Column("error_message", sa.String(500), nullable=True),
        sa.UniqueConstraint(
            "idempotency_key",
            name="uq_harness_workflow_child_observation_idempotency",
        ),
    )
    op.create_index(
        "ix_harness_workflow_child_observations_receipt_id",
        "harness_workflow_child_observations",
        ["child_receipt_id"],
    )

    # Backfill only unambiguous typed legacy receipts. A duplicate external ID,
    # missing/empty run ID, or malformed JSON is intentionally left unbound.
    op.execute(
        """
        WITH legacy_candidates AS (
            SELECT
                id AS workflow_run_id,
                project_id,
                provider_id,
                runtime_metadata -> 'external_generation' ->> 'run_id' AS external_run_id,
                lower(coalesce(runtime_metadata -> 'external_generation' ->> 'status', 'unknown')) AS provider_status,
                count(*) OVER (
                    PARTITION BY provider_id,
                    runtime_metadata -> 'external_generation' ->> 'run_id'
                ) AS external_reference_count,
                started_at,
                finished_at
            FROM harness_workflow_runs
            WHERE provider_id = 'mcp.docs'
              AND runtime_metadata IS NOT NULL
              AND jsonb_typeof((runtime_metadata::jsonb) -> 'external_generation') = 'object'
              AND nullif(runtime_metadata -> 'external_generation' ->> 'run_id', '') IS NOT NULL
        )
        INSERT INTO harness_workflow_child_receipts (
            workflow_run_id, provider_id, child_kind, external_run_id, project_id,
            status, provider_status, version, provider_started_at,
            provider_finished_at, provider_timestamp, last_observed_at,
            last_observation_source, cancellation_state, created_at, updated_at
        )
        SELECT
            workflow_run_id,
            provider_id,
            'documentation_generation',
            external_run_id,
            project_id,
            CASE
                WHEN provider_status IN ('queued', 'pending', 'triggered', 'generation_triggered') THEN 'queued'
                WHEN provider_status IN ('running', 'generating') THEN 'running'
                WHEN provider_status IN ('completed', 'completed_warnings', 'succeeded', 'ready') THEN 'succeeded'
                WHEN provider_status IN ('failed', 'error') THEN 'failed'
                WHEN provider_status IN ('cancelled', 'canceled') THEN 'cancelled'
                ELSE 'unknown'
            END,
            provider_status,
            1,
            started_at,
            finished_at,
            coalesce(finished_at, started_at),
            coalesce(finished_at, started_at, now()),
            'backfill',
            'none',
            now(),
            now()
        FROM legacy_candidates
        WHERE external_reference_count = 1
        ON CONFLICT DO NOTHING
        """
    )


def downgrade() -> None:
    op.drop_index(
        "ix_harness_workflow_child_observations_receipt_id",
        table_name="harness_workflow_child_observations",
    )
    op.drop_table("harness_workflow_child_observations")
    op.drop_index(
        "ix_harness_workflow_child_receipts_status",
        table_name="harness_workflow_child_receipts",
    )
    op.drop_index(
        "ix_harness_workflow_child_receipts_workflow_run_id",
        table_name="harness_workflow_child_receipts",
    )
    op.drop_table("harness_workflow_child_receipts")
