"""Add the timestamp columns 084 declared on the models but never created.

Verified defect, not a speculative tidy-up: opening the Packs workbench raised

    asyncpg.exceptions.UndefinedColumnError:
    column pack_activations.created_at does not exist

which escaped as a plain-text 500 ("Internal Server Error", not JSON), so the
frontend's `.json()` parse failed and the UI showed only the dead-end message
"Pack registry request failed".

084_unified_pack_registry created `created_at`/`updated_at` on `packs` and
`created_at` on `pack_versions`, but omitted `pack_versions.updated_at` and both
timestamps on `pack_activations` -- while `src/db/models/pack_registry.py`
declares all of them NOT NULL. Any SELECT over those tables therefore failed.
`packs` was already correct and is untouched here.

Column style (DateTime(timezone=True), server_default=now(), NOT NULL) mirrors
084 exactly so the three tables stay consistent. The server default also
backfills any rows that already exist.

Revision ID: 086_pack_registry_timestamps
Revises: 085_owner_admin_hierarchy
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "086_pack_registry_timestamps"
down_revision: Union[str, None] = "085_owner_admin_hierarchy"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _timestamp(name: str) -> sa.Column:
    return sa.Column(
        name,
        sa.DateTime(timezone=True),
        server_default=sa.func.now(),
        nullable=False,
    )


def upgrade() -> None:
    op.add_column("pack_versions", _timestamp("updated_at"))
    op.add_column("pack_activations", _timestamp("created_at"))
    op.add_column("pack_activations", _timestamp("updated_at"))


def downgrade() -> None:
    op.drop_column("pack_activations", "updated_at")
    op.drop_column("pack_activations", "created_at")
    op.drop_column("pack_versions", "updated_at")
