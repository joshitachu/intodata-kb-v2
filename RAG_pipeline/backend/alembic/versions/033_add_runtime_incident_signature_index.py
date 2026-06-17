"""Add composite index on runtime_incidents (project_id, signature_hash, first_bad_at).

Revision ID: 033
Revises: 032
Create Date: 2026-06-17
"""

from alembic import op

revision = "033"
down_revision = "032"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_runtime_incidents_project_signature_bad_at",
        "runtime_incidents",
        ["project_id", "signature_hash", "first_bad_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_runtime_incidents_project_signature_bad_at", table_name="runtime_incidents")
