"""superadmin/admin split: project ownership + retire manager + create_projects perm

Reshapes RBAC into a 3-tier model — superadmin / admin / user:

- Adds ``project_assignments.is_owner``. An admin "owns" the projects they create;
  ownership is just an assignment flag, so the existing ``get_user_project_ids``
  scoping (assigned-only for non view_all users) already limits an admin to their
  own projects.
- Adds the ``create_projects`` permission (id=10).
- Retires the ``manager`` role: migrates manager users to admin, then drops the
  manager grants + role row.
- Rebalances the admin role: gains ``assign_projects``, ``manage_projects``,
  ``create_projects`` (all enforced as owner-scoped at the endpoints); loses
  ``view_all_projects`` so admins see only owned/assigned projects.

Revision ID: 044_rbac
Revises: 043
Create Date: 2026-07-03

NB: rebased to sit after the runtime feature's migrations (head 043).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "044"
down_revision: Union[str, None] = "043"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

ADMIN_ROLE_ID = 1
MANAGER_ROLE_ID = 2

# Permission ids seeded in migration 006
ASSIGN_PROJECTS = 3
VIEW_ALL_PROJECTS = 4
MANAGE_PROJECTS = 5
CREATE_PROJECTS = 10


def upgrade() -> None:
    # 1. Project ownership flag (default false → existing assignments are members)
    op.add_column(
        "project_assignments",
        sa.Column(
            "is_owner", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )

    # 2. New permission: create_projects
    op.execute(
        "INSERT INTO permissions (id, codename, description) "
        "VALUES (10, 'create_projects', "
        "'Create/register new projects (owned by the creator)') "
        "ON CONFLICT DO NOTHING"
    )
    # Inserting an explicit id does not advance the sequence, so a later
    # migration that omits the id would draw a value that already exists and
    # fail on permissions_pkey. Resync before that can happen.
    op.execute(
        "SELECT setval(pg_get_serial_sequence('permissions', 'id'), "
        "COALESCE((SELECT MAX(id) FROM permissions), 0) + 1, false)"
    )

    # 3. Retire the `manager` role.
    #    a) migrate manager users to admin (dedup via ON CONFLICT)
    op.execute(
        "INSERT INTO user_roles (user_id, role_id) "
        "SELECT user_id, 1 FROM user_roles WHERE role_id = 2 "
        "ON CONFLICT DO NOTHING"
    )
    #    b) drop manager assignments, grants, and the role row
    op.execute("DELETE FROM user_roles WHERE role_id = 2")
    op.execute("DELETE FROM role_permissions WHERE role_id = 2")
    op.execute("DELETE FROM roles WHERE id = 2")

    # 4. Rebalance the admin role: owner-scoped project control, no view-all.
    op.execute(
        "INSERT INTO role_permissions (role_id, permission_id) "
        "VALUES (1, 3), (1, 5), (1, 10) "  # assign_projects, manage_projects, create_projects
        "ON CONFLICT DO NOTHING"
    )
    op.execute(
        "DELETE FROM role_permissions WHERE role_id = 1 AND permission_id = 4"  # view_all_projects
    )


def downgrade() -> None:
    # Revert the admin permission rebalance
    op.execute(
        "INSERT INTO role_permissions (role_id, permission_id) VALUES (1, 4) "
        "ON CONFLICT DO NOTHING"
    )
    op.execute(
        "DELETE FROM role_permissions WHERE role_id = 1 AND permission_id IN (3, 5, 10)"
    )

    # Recreate the manager role with its original grants (from migration 006).
    # NB: users demoted manager→admin are not un-migrated (lossy, acceptable).
    op.execute(
        "INSERT INTO roles (id, name, description) "
        "VALUES (2, 'manager', 'Manager: full project access and user assignment') "
        "ON CONFLICT DO NOTHING"
    )
    op.execute(
        "INSERT INTO role_permissions (role_id, permission_id) "
        "VALUES (2, 1), (2, 2), (2, 3), (2, 4), (2, 5), (2, 7), (2, 8), (2, 9) "
        "ON CONFLICT DO NOTHING"
    )

    # Drop create_projects and the ownership column
    op.execute("DELETE FROM role_permissions WHERE permission_id = 10")
    op.execute("DELETE FROM permissions WHERE id = 10")
    op.drop_column("project_assignments", "is_owner")
