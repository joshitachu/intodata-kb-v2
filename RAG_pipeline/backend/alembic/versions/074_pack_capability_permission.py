"""Require explicit Pack management authority for private Pack capabilities.

Revision ID: 074_pack_capability_permission
Revises: 073_agent_run_streaming
"""

from __future__ import annotations

from alembic import op

revision = "074_pack_capability_permission"
down_revision = "073_agent_run_streaming"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "INSERT INTO permissions (codename, description) VALUES "
        "('manage_packs', 'Create, modify, validate, save, and abandon private Packs') "
        "ON CONFLICT (codename) DO NOTHING"
    )
    op.execute(
        "INSERT INTO role_permissions (role_id, permission_id) "
        "SELECT roles.id, permissions.id FROM roles CROSS JOIN permissions "
        "WHERE roles.name = 'admin' AND permissions.codename = 'manage_packs' "
        "ON CONFLICT DO NOTHING"
    )


def downgrade() -> None:
    op.execute(
        "DELETE FROM role_permissions WHERE permission_id IN "
        "(SELECT id FROM permissions WHERE codename = 'manage_packs')"
    )
    op.execute("DELETE FROM permissions WHERE codename = 'manage_packs'")
