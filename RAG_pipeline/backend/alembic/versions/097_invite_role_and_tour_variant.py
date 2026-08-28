"""Decide a member's role when access is approved, and track which tour they saw.

Revision ID: 097_invite_role_and_tour_variant
Revises: 096_base_timestamp_drift

Approving an access request used to send an invite that carried no authority at
all, so ``accept_invite`` fell back to a hardcoded ``developer`` role. Role was
therefore decided *after* first-run onboarding had already happened, which made
a role-aware walkthrough impossible and forced an admin to re-open Members to
promote someone they had just approved.

The approving admin now records the intended role (and optional project scope
plus a note) on the invite itself. Acceptance honours it, so persona is known
before the first screen renders.

``tour_variant`` records which walkthrough a user was actually shown. Without it
a role change later on cannot tell "finished the developer tour" apart from
"finished the admin tour", so we could never re-offer the right one.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "097_invite_role_and_tour_variant"
down_revision: str | None = "096_base_timestamp_drift"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # SET NULL rather than CASCADE: deleting a role must not silently delete the
    # audit trail of invitations that were sent under it. Acceptance falls back
    # to the default member role when this is NULL.
    op.add_column(
        "user_invites",
        sa.Column(
            "assigned_role_id",
            sa.Integer(),
            sa.ForeignKey("roles.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    # Projects the approver wants this member to land in. Stored on the invite
    # because the user row does not exist yet at approval time.
    #
    # JSONB, matching the model's PostgreSQL variant and the existing JSONB
    # columns on these tables. A plain `json` column here would be exactly the
    # kind of type drift migration 096 had to clean up.
    op.add_column(
        "user_invites",
        sa.Column(
            "assigned_project_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    # Shown verbatim in the invitee's first-run welcome so onboarding can say
    # who approved them and why, instead of greeting them anonymously.
    op.add_column(
        "user_invites",
        sa.Column("welcome_note", sa.Text(), nullable=True),
    )
    op.create_index(
        "ix_user_invites_assigned_role_id",
        "user_invites",
        ["assigned_role_id"],
    )

    op.add_column(
        "user_onboarding",
        sa.Column("tour_variant", sa.String(32), nullable=True),
    )
    # Step ids rather than an index: a role-filtered tour has a different length
    # per persona, so a bare integer cannot be resumed reliably once steps are
    # added, removed, or hidden by a capability check.
    op.add_column(
        "user_onboarding",
        sa.Column(
            "tour_seen_steps",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("user_onboarding", "tour_seen_steps")
    op.drop_column("user_onboarding", "tour_variant")
    op.drop_index("ix_user_invites_assigned_role_id", table_name="user_invites")
    op.drop_column("user_invites", "welcome_note")
    op.drop_column("user_invites", "assigned_project_ids")
    op.drop_column("user_invites", "assigned_role_id")
