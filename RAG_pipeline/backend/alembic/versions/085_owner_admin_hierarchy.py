"""Grant Admin day-to-day member management under the Owner hierarchy.

The existing ``users.is_superadmin`` flag remains the deployment Owner
authority. This migration intentionally does not rewrite or deduplicate Owner
rows: installations may already contain more than one legacy superadmin, and
choosing an Owner automatically would be unsafe. Runtime hierarchy guards keep
all Owner rows outside member administration.

Revision ID: 085_owner_admin_hierarchy
Revises: 084_unified_pack_registry
Create Date: 2026-08-20
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op


revision: str = "085_owner_admin_hierarchy"
down_revision: str | Sequence[str] | None = "084_unified_pack_registry"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Permission 1 is ``manage_users`` from migration 006. It covers member
    # CRUD plus access-request/invite processing. Role definitions and global
    # settings remain Owner-only; project grants retain their existing
    # owner-scoped ``assign_projects`` checks.
    op.execute(
        "INSERT INTO role_permissions (role_id, permission_id) "
        "SELECT roles.id, permissions.id FROM roles, permissions "
        "WHERE roles.name = 'admin' AND permissions.codename = 'manage_users' "
        "ON CONFLICT DO NOTHING"
    )


def downgrade() -> None:
    op.execute(
        "DELETE FROM role_permissions "
        "WHERE role_id IN (SELECT id FROM roles WHERE name = 'admin') "
        "AND permission_id IN ("
        "SELECT id FROM permissions WHERE codename = 'manage_users'"
        ")"
    )
