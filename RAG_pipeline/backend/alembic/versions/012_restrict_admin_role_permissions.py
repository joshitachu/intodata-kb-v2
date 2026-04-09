"""Restrict admin role: remove user/role/project management permissions

Admin role keeps read-only dashboard access (usage, audit, benchmark).
User/role/project management is superadmin-only.

Revision ID: 012
Revises: 011
Create Date: 2026-04-09
"""
from typing import Sequence, Union

from alembic import op

revision: str = "012"
down_revision: Union[str, None] = "011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Remove manage_users (id=1), manage_roles (id=2), assign_projects (id=3)
    # from admin role (id=1)
    op.execute(
        "DELETE FROM role_permissions WHERE role_id = 1 AND permission_id IN (1, 2, 3)"
    )


def downgrade() -> None:
    # Re-grant the three permissions to admin role
    op.execute(
        "INSERT INTO role_permissions (role_id, permission_id) VALUES (1, 1), (1, 2), (1, 3) "
        "ON CONFLICT DO NOTHING"
    )
