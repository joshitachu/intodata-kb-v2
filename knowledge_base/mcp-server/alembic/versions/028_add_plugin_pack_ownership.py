"""Give plugin packs an owner, so a user can have their own private packs.

Anyone may author a pack. An authored pack is stored against its author's user
account and is usable only by them; it becomes available to everyone by being
merged into the shared pack repository through a pull request. Both lanes are
the same format and the same directory layout on disk -- the only difference is
whether ``owner_user_id`` is set.

Uniqueness follows that split:

* shared packs (``owner_user_id IS NULL``) keep a globally unique name, so the
  shared catalog cannot contain two entries called ``main``;
* personal packs are unique per owner, so two users may each have a pack called
  ``sql-audit`` without colliding.

Postgres treats NULLs as distinct in a unique index, so the shared-name
guarantee needs its own partial index rather than relying on the composite one.

Revision ID: 028_add_plugin_pack_ownership
Revises: 027_live_connectors
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "028_add_plugin_pack_ownership"
down_revision: Union[str, None] = "027_live_connectors"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "plugin_packs",
        sa.Column("owner_user_id", sa.Integer(), nullable=True),
    )
    op.create_index(
        "ix_plugin_packs_owner_user_id", "plugin_packs", ["owner_user_id"]
    )

    # The old global unique constraint would stop two users from each owning a
    # pack of the same name. Replace it with the two-part rule above.
    op.drop_constraint("plugin_packs_name_key", "plugin_packs", type_="unique")
    op.create_index(
        "uq_plugin_packs_shared_name",
        "plugin_packs",
        ["name"],
        unique=True,
        postgresql_where=sa.text("owner_user_id IS NULL"),
    )
    op.create_index(
        "uq_plugin_packs_owner_name",
        "plugin_packs",
        ["owner_user_id", "name"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_plugin_packs_owner_name", table_name="plugin_packs")
    op.drop_index("uq_plugin_packs_shared_name", table_name="plugin_packs")
    # Restoring the global unique constraint requires the personal packs that
    # rely on per-owner uniqueness to be gone first.
    op.execute("DELETE FROM plugin_packs WHERE owner_user_id IS NOT NULL")
    op.create_unique_constraint("plugin_packs_name_key", "plugin_packs", ["name"])
    op.drop_index("ix_plugin_packs_owner_user_id", table_name="plugin_packs")
    op.drop_column("plugin_packs", "owner_user_id")
