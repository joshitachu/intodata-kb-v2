"""fix auto-increment sequences for users and roles tables

After seeding rows with explicit IDs, PostgreSQL sequences are not
advanced, causing duplicate-key errors on the next INSERT.

Revision ID: 005
Revises: 004
Create Date: 2026-03-04
"""
from typing import Sequence, Union

from alembic import op

revision: str = "005"
down_revision: Union[str, None] = "004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("SELECT setval('users_id_seq', COALESCE((SELECT MAX(id) FROM users), 0))")
    op.execute("SELECT setval('roles_id_seq', COALESCE((SELECT MAX(id) FROM roles), 0))")


def downgrade() -> None:
    # Sequences will naturally be correct; nothing to revert.
    pass
