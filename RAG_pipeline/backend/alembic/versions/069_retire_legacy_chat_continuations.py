"""Fail closed before retiring legacy chat continuation storage.

Revision ID: 069_retire_legacy_chat_paths
Revises: 068_agent_chat_cutover_audit
Create Date: 2026-07-19
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "069_retire_legacy_chat_paths"
down_revision = "068_agent_chat_cutover_audit"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Refuse retirement if an active envelope is malformed or its objective and
    # requested inputs were not projected into ConversationState by migration 067.
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM harness_conversation_continuations c
                WHERE c.status = 'active'
                  AND (
                    jsonb_typeof(c.envelope::jsonb) <> 'object'
                    OR jsonb_typeof(c.envelope::jsonb -> 'plan') <> 'object'
                    OR nullif(c.envelope::jsonb -> 'plan' ->> 'objective', '') IS NULL
                    OR length(c.envelope::jsonb -> 'plan' ->> 'objective') > 2000
                    OR jsonb_typeof(coalesce(c.envelope::jsonb -> 'missing_slots', '[]'::jsonb)) <> 'array'
                    OR jsonb_typeof(coalesce(c.envelope::jsonb -> 'slots', '[]'::jsonb)) <> 'array'
                  )
            ) THEN
                RAISE EXCEPTION 'legacy continuation retirement blocked: malformed active envelope';
            END IF;

            IF EXISTS (
                SELECT 1
                FROM harness_conversation_continuations c
                LEFT JOIN harness_conversation_states s
                  ON s.conversation_id = c.conversation_id
                 AND s.owner_user_id = c.owner_user_id
                WHERE c.status = 'active'
                  AND (
                    s.id IS NULL
                    OR s.objective IS DISTINCT FROM left(c.envelope::jsonb -> 'plan' ->> 'objective', 2000)
                    OR (c.last_message_id IS NOT NULL AND s.last_applied_message_id IS DISTINCT FROM c.last_message_id)
                    OR EXISTS (
                        SELECT 1
                        FROM jsonb_array_elements_text(c.envelope::jsonb -> 'missing_slots') missing(field)
                        WHERE NOT EXISTS (
                            SELECT 1
                            FROM jsonb_array_elements(coalesce(s.requested_inputs::jsonb, '[]'::jsonb)) requested(item)
                            WHERE requested.item ->> 'field' = missing.field
                        )
                    )
                  )
            ) THEN
                RAISE EXCEPTION 'legacy continuation retirement blocked: ConversationState parity mismatch';
            END IF;
        END $$
        """
    )

    # Task parity is required only when the legacy envelope explicitly named a
    # task/workflow/draft authority. Envelopes containing only objective and
    # requested-input state need no synthetic task.
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM harness_conversation_continuations c
                JOIN harness_conversation_states s
                  ON s.conversation_id = c.conversation_id
                 AND s.owner_user_id = c.owner_user_id
                WHERE c.status = 'active'
                  AND (
                    (
                      nullif(c.envelope::jsonb #>> '{plan,continuation,task_ref_id}', '') IS NOT NULL
                      AND NOT EXISTS (
                        SELECT 1 FROM jsonb_array_elements(s.active_tasks::jsonb) task(item)
                        WHERE task.item ->> 'task_ref_id' = c.envelope::jsonb #>> '{plan,continuation,task_ref_id}'
                      )
                    ) OR (
                      nullif(c.envelope::jsonb #>> '{plan,continuation,workflow_run_id}', '') IS NOT NULL
                      AND NOT EXISTS (
                        SELECT 1 FROM jsonb_array_elements(s.active_tasks::jsonb) task(item)
                        WHERE task.item ->> 'workflow_run_id' = c.envelope::jsonb #>> '{plan,continuation,workflow_run_id}'
                      )
                    ) OR (
                      nullif(c.envelope::jsonb #>> '{plan,continuation,draft_id}', '') IS NOT NULL
                      AND NOT EXISTS (
                        SELECT 1 FROM jsonb_array_elements(s.active_tasks::jsonb) task(item)
                        WHERE task.item ->> 'draft_id' = c.envelope::jsonb #>> '{plan,continuation,draft_id}'
                      )
                    )
                  )
            ) THEN
                RAISE EXCEPTION 'legacy continuation retirement blocked: referenced task authority missing';
            END IF;
        END $$
        """
    )
    op.drop_table("harness_conversation_continuations")


def downgrade() -> None:
    op.create_table(
        "harness_conversation_continuations",
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
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("envelope", sa.JSON(), nullable=False),
        sa.Column(
            "last_message_id",
            sa.Integer(),
            sa.ForeignKey("conversation_messages.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index(
        "uq_harness_conversation_continuations_conversation_owner",
        "harness_conversation_continuations",
        ["conversation_id", "owner_user_id"],
        unique=True,
    )
    op.create_index(
        "ix_harness_conversation_continuations_status",
        "harness_conversation_continuations",
        ["status"],
    )
