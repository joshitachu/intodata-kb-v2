"""Give the layered answering guidance somewhere durable to live.

Answering behaviour is governed by three durable levels -- organization,
project, person -- that until now existed only as prompt text assembled in
code. Nothing could be tuned per project or per person, and nothing a user
corrected survived the turn that corrected it.

One table, discriminated by ``layer``, with a CHECK constraint tying the scope
columns to that discriminator. The constraint is the reason this is not three
loose nullable columns: an org row carrying a ``project_id`` would render for
one project and silently vanish for every other, and no error would ever say so.

The three indexes match the three reads the compiler issues per turn (org rules,
this project's rules, this person's rules within the project); guidance is read
on every model call, so an unindexed scan here is a per-turn cost.

Reversible and purely additive: upgrade creates one table and its indexes,
downgrade drops them. No existing row is read, rewritten or deleted in either
direction, so a downgrade loses exactly the tuning captured since the upgrade.
The organization's invariants are code-owned (see
``src.harness.guidance.renderer``) and are unaffected by either direction.

Revision ID: 100_guidance_rules
Revises: 099_runtime_execution_evidence
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "100_guidance_rules"
down_revision: str | None = "099_runtime_execution_evidence"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Kept verbatim in sync with ``src.db.models.guidance._SCOPE_MATCHES_LAYER``.
_SCOPE_MATCHES_LAYER = (
    "(layer = 'org' AND project_id IS NULL AND user_id IS NULL)"
    " OR (layer = 'project' AND project_id IS NOT NULL AND user_id IS NULL)"
    " OR (layer = 'person' AND project_id IS NOT NULL AND user_id IS NOT NULL)"
)

_INDEXES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("ix_guidance_rules_layer_status", ("layer", "status")),
    ("ix_guidance_rules_project_status", ("project_id", "status")),
    ("ix_guidance_rules_user_project_status", ("user_id", "project_id", "status")),
)


def upgrade() -> None:
    op.create_table(
        "guidance_rules",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("layer", sa.String(16), nullable=False),
        sa.Column("project_id", sa.String(255), nullable=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column(
            "payload",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=True,
        ),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("source", sa.String(64), nullable=False),
        sa.Column(
            "supersedes_id",
            sa.Integer(),
            sa.ForeignKey("guidance_rules.id"),
            nullable=True,
        ),
        sa.Column("token_estimate", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_by",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        # Contributed by the declarative Base. Omitting either one produces a
        # table the ORM cannot SELECT at all -- the failure 093, 094 and 096 had
        # to repair after the fact.
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(_SCOPE_MATCHES_LAYER, name="ck_guidance_scope_matches_layer"),
    )
    for name, columns in _INDEXES:
        op.create_index(name, "guidance_rules", list(columns))


def downgrade() -> None:
    for name, _columns in reversed(_INDEXES):
        op.drop_index(name, table_name="guidance_rules")
    op.drop_table("guidance_rules")
