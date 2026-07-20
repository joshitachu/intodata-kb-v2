"""Add unified optimistic conversation state and durable turn claims.

Revision ID: 067_unified_conversation_state
Revises: 066_workflow_child_ledger
Create Date: 2026-07-19
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "067_unified_conversation_state"
down_revision = "066_workflow_child_ledger"
branch_labels = None
depends_on = None


_ACTIVE_CLAIM_SQL = "status IN ('claimed', 'effect_started', 'recovery_required')"


def upgrade() -> None:
    op.create_table(
        "harness_conversation_states",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "conversation_id",
            sa.Integer(),
            sa.ForeignKey("conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "owner_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("objective", sa.String(2000), nullable=True),
        sa.Column("referenced_entities", sa.JSON(), nullable=False),
        sa.Column("requested_inputs", sa.JSON(), nullable=False),
        sa.Column("active_tasks", sa.JSON(), nullable=False),
        sa.Column("response_fingerprints", sa.JSON(), nullable=False),
        sa.Column(
            "last_applied_message_id",
            sa.Integer(),
            sa.ForeignKey("conversation_messages.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "conversation_id",
            "owner_user_id",
            name="uq_harness_conversation_state_conversation_owner",
        ),
        sa.CheckConstraint(
            "version >= 1", name="ck_harness_conversation_state_version"
        ),
    )
    op.create_index(
        "ix_harness_conversation_states_owner",
        "harness_conversation_states",
        ["owner_user_id"],
    )

    op.create_table(
        "harness_conversation_turn_claims",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "conversation_state_id",
            sa.Integer(),
            sa.ForeignKey("harness_conversation_states.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "message_id",
            sa.Integer(),
            sa.ForeignKey("conversation_messages.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("expected_version", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(255), nullable=False),
        sa.Column("lease_token_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("effect_receipts", sa.JSON(), nullable=False),
        sa.Column("final_result", sa.JSON(), nullable=True),
        sa.Column("applied_transition_id", sa.Integer(), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("effect_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finalized_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "message_id", name="uq_harness_conversation_turn_claim_message"
        ),
        sa.UniqueConstraint(
            "idempotency_key", name="uq_harness_conversation_turn_claim_idempotency"
        ),
    )
    op.create_index(
        "ix_harness_conversation_turn_claims_state",
        "harness_conversation_turn_claims",
        ["conversation_state_id"],
    )
    op.create_index(
        "ix_harness_conversation_turn_claims_lease",
        "harness_conversation_turn_claims",
        ["status", "lease_expires_at"],
    )
    op.create_index(
        "uq_harness_conversation_turn_claim_active_state",
        "harness_conversation_turn_claims",
        ["conversation_state_id"],
        unique=True,
        postgresql_where=sa.text(_ACTIVE_CLAIM_SQL),
        sqlite_where=sa.text(_ACTIVE_CLAIM_SQL),
    )

    op.create_table(
        "harness_conversation_state_transitions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "conversation_state_id",
            sa.Integer(),
            sa.ForeignKey("harness_conversation_states.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "turn_claim_id",
            sa.Integer(),
            sa.ForeignKey("harness_conversation_turn_claims.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "message_id",
            sa.Integer(),
            sa.ForeignKey("conversation_messages.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("transition_key", sa.String(255), nullable=False),
        sa.Column("from_version", sa.Integer(), nullable=False),
        sa.Column("to_version", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(40), nullable=False),
        sa.Column("action", sa.String(80), nullable=False),
        sa.Column("change_summary", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "transition_key", name="uq_harness_conversation_state_transition_key"
        ),
        sa.CheckConstraint(
            "to_version >= from_version", name="ck_harness_state_transition_versions"
        ),
    )
    op.create_index(
        "ix_harness_conversation_state_transitions_state",
        "harness_conversation_state_transitions",
        ["conversation_state_id"],
    )
    op.create_index(
        "ix_harness_conversation_state_transitions_claim",
        "harness_conversation_state_transitions",
        ["turn_claim_id"],
    )

    # Backfill only exact owner/conversation references.  Lifecycle state is
    # deliberately not copied; Pack rows and Workflow Ledger rows stay the
    # authorities after migration.
    op.execute(
        """
        WITH task_candidates AS (
            SELECT
                d.conversation_id,
                d.owner_user_id,
                'pack:' || d.id::text AS task_ref_id,
                'pack_draft' AS task_kind,
                'nexus.packs.drafts.mutate' AS capability_id,
                d.id AS draft_id,
                NULL::integer AS workflow_run_id,
                NULL::text AS project_id,
                d.updated_at AS occurred_at
            FROM harness_pack_drafts d
            WHERE d.conversation_id IS NOT NULL AND d.status = 'active'
            UNION ALL
            SELECT
                w.conversation_id,
                w.user_id,
                'workflow:' || w.id::text,
                CASE
                    WHEN w.capability_id = 'nexus.workflow.onboard_project'
                        THEN 'onboarding_workflow'
                    ELSE 'documentation_workflow'
                END,
                w.capability_id,
                NULL::integer,
                w.id,
                w.project_id,
                w.started_at
            FROM harness_workflow_runs w
            WHERE w.capability_id IN (
                'nexus.workflow.onboard_project',
                'mcp.docs.generate_project_documentation'
            )
              AND w.status IN ('pending', 'running', 'triggered', 'interrupted', 'indeterminate')
        ), ranked_tasks AS (
            SELECT *, row_number() OVER (
                PARTITION BY conversation_id, owner_user_id
                ORDER BY occurred_at DESC, task_ref_id DESC
            ) AS focus_rank
            FROM task_candidates
        ), bounded_tasks AS (
            SELECT * FROM ranked_tasks WHERE focus_rank <= 30
        ), task_arrays AS (
            SELECT
                conversation_id,
                owner_user_id,
                jsonb_agg(
                    jsonb_strip_nulls(jsonb_build_object(
                        'task_ref_id', task_ref_id,
                        'kind', task_kind,
                        'capability_id', capability_id,
                        'draft_id', draft_id,
                        'workflow_run_id', workflow_run_id,
                        'project_id', project_id,
                        'focused', focus_rank = 1
                    )) ORDER BY focus_rank
                ) AS active_tasks
            FROM bounded_tasks
            GROUP BY conversation_id, owner_user_id
        ), active_continuations AS (
            SELECT DISTINCT ON (conversation_id, owner_user_id)
                conversation_id,
                owner_user_id,
                left(nullif(envelope::jsonb -> 'plan' ->> 'objective', ''), 2000) AS objective,
                coalesce(requested.requested_inputs, '[]'::jsonb) AS requested_inputs,
                last_message_id
            FROM harness_conversation_continuations
            LEFT JOIN LATERAL (
                SELECT jsonb_agg(
                    jsonb_build_object(
                        'task_ref_id', NULL,
                        'field', missing.field,
                        'prompt', left(
                            'Provide ' || replace(missing.field, '_', ' ')
                            || ' to continue the current objective.',
                            500
                        ),
                        'options', coalesce(slot.options, '[]'::jsonb)
                    )
                    ORDER BY missing.ordinality
                ) AS requested_inputs
                FROM jsonb_array_elements_text(
                    CASE
                        WHEN jsonb_typeof(envelope::jsonb -> 'missing_slots') = 'array'
                            THEN envelope::jsonb -> 'missing_slots'
                        ELSE '[]'::jsonb
                    END
                ) WITH ORDINALITY AS missing(field, ordinality)
                LEFT JOIN LATERAL (
                    SELECT slot_value AS slot_item
                    FROM jsonb_array_elements(
                        CASE
                            WHEN jsonb_typeof(envelope::jsonb -> 'slots') = 'array'
                                THEN envelope::jsonb -> 'slots'
                            ELSE '[]'::jsonb
                        END
                    ) slot_value
                    WHERE slot_value ->> 'name' = missing.field
                    LIMIT 1
                ) matched_slot ON true
                LEFT JOIN LATERAL (
                    SELECT coalesce(
                        jsonb_agg(left(coalesce(
                            option ->> 'value', option ->> 'id', option ->> 'label'
                        ), 120)) FILTER (
                            WHERE coalesce(
                                option ->> 'value', option ->> 'id', option ->> 'label'
                            ) IS NOT NULL
                        ),
                        '[]'::jsonb
                    ) AS options
                    FROM (
                        SELECT option
                        FROM jsonb_array_elements(
                            CASE
                                WHEN jsonb_typeof(matched_slot.slot_item -> 'options') = 'array'
                                    THEN matched_slot.slot_item -> 'options'
                                ELSE '[]'::jsonb
                            END
                        ) option
                        LIMIT 20
                    ) bounded_options
                ) slot ON true
                WHERE length(missing.field) BETWEEN 1 AND 80
                  AND missing.ordinality <= 30
            ) requested ON true
            WHERE status = 'active'
            ORDER BY conversation_id, owner_user_id, id DESC
        )
        INSERT INTO harness_conversation_states (
            conversation_id, owner_user_id, version, objective,
            referenced_entities, requested_inputs, active_tasks,
            response_fingerprints, last_applied_message_id, created_at, updated_at
        )
        SELECT
            c.id,
            c.user_id,
            1,
            ac.objective,
            '[]'::jsonb,
            coalesce(ac.requested_inputs, '[]'::jsonb),
            coalesce(ta.active_tasks, '[]'::jsonb),
            '[]'::jsonb,
            ac.last_message_id,
            now(),
            now()
        FROM conversations c
        LEFT JOIN active_continuations ac
          ON ac.conversation_id = c.id AND ac.owner_user_id = c.user_id
        LEFT JOIN task_arrays ta
          ON ta.conversation_id = c.id AND ta.owner_user_id = c.user_id
        ON CONFLICT (conversation_id, owner_user_id) DO NOTHING
        """
    )
    op.execute(
        """
        INSERT INTO harness_conversation_state_transitions (
            conversation_state_id, turn_claim_id, message_id, transition_key,
            from_version, to_version, source, action, change_summary, created_at
        )
        SELECT
            id, NULL, last_applied_message_id, 'migration:067:state:' || id::text,
            0, 1, 'migration_backfill', 'initialize', '{}'::jsonb, created_at
        FROM harness_conversation_states
        ON CONFLICT (transition_key) DO NOTHING
        """
    )


def downgrade() -> None:
    op.drop_index(
        "ix_harness_conversation_state_transitions_claim",
        table_name="harness_conversation_state_transitions",
    )
    op.drop_index(
        "ix_harness_conversation_state_transitions_state",
        table_name="harness_conversation_state_transitions",
    )
    op.drop_table("harness_conversation_state_transitions")
    op.drop_index(
        "uq_harness_conversation_turn_claim_active_state",
        table_name="harness_conversation_turn_claims",
    )
    op.drop_index(
        "ix_harness_conversation_turn_claims_lease",
        table_name="harness_conversation_turn_claims",
    )
    op.drop_index(
        "ix_harness_conversation_turn_claims_state",
        table_name="harness_conversation_turn_claims",
    )
    op.drop_table("harness_conversation_turn_claims")
    op.drop_index(
        "ix_harness_conversation_states_owner",
        table_name="harness_conversation_states",
    )
    op.drop_table("harness_conversation_states")
