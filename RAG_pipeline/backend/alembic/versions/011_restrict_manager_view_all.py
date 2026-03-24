"""Remove view_all_projects permission from manager role

Only admin role and superadmin flag should see all projects.
Managers should only see projects they are assigned to.

Revision ID: 011
Revises: 010
Create Date: 2026-03-24
"""
from typing import Sequence, Union

from alembic import op

revision: str = "011"
down_revision: Union[str, None] = "010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Remove view_all_projects (permission_id=4) from manager (role_id=2)
    op.execute(
        "DELETE FROM role_permissions WHERE role_id = 2 AND permission_id = 4"
    )


def downgrade() -> None:
    # Re-grant view_all_projects to manager
    op.execute(
        "INSERT INTO role_permissions (role_id, permission_id) VALUES (2, 4)"
    )
