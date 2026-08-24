"""Add the pack audit record to generate_runs.

One Pack Across Nexus (docs/one-pack-program.md section 9): mandatory GitHub PR
review is dropped for user-authored packs, so every client deliverable must be
bindable to the exact pack version that produced it. All five columns are
nullable -- runs with no active pack leave them NULL. ``pack_activated_by`` is
an actor identity string supplied by the RAG side; this database has no
``users`` table, so it is deliberately not a foreign key.

Mirrored by the idempotent startup migration in
``mcp_server.api.app._apply_column_migrations`` for deployments that never ran
alembic. The two mechanisms must stay in agreement.

Revision ID: 026_generate_run_pack_audit
Revises: 025_generation_task_attempts
Create Date: 2026-08-20
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "026_generate_run_pack_audit"
down_revision: Union[str, None] = "025_generation_task_attempts"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("generate_runs", sa.Column("pack_id", sa.String(200), nullable=True))
    op.add_column("generate_runs", sa.Column("pack_version", sa.String(64), nullable=True))
    op.add_column(
        "generate_runs", sa.Column("pack_content_hash", sa.String(64), nullable=True)
    )
    op.add_column(
        "generate_runs", sa.Column("pack_activated_by", sa.String(200), nullable=True)
    )
    op.add_column(
        "generate_runs",
        sa.Column("pack_activated_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("generate_runs", "pack_activated_at")
    op.drop_column("generate_runs", "pack_activated_by")
    op.drop_column("generate_runs", "pack_content_hash")
    op.drop_column("generate_runs", "pack_version")
    op.drop_column("generate_runs", "pack_id")
