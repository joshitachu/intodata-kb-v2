"""Retire the project-selectable governed/style Pack stack.

``plugin_packs`` in MCP is the sole documentation-pack registry.  The four
tables below held the retired RAG-side stack: organization policy, style-pack
versions, immutable stack snapshots, and project activation pointers.  Their
contents are no longer read after this revision.  The pre-collapse database
dump is retained at ``deploy/backups/pack_tables_pre_collapse_2026-08-23.sql``
for operators that need archival recovery.

Revision ID: 090_retire_governed_pack_stack
Revises: 089_collapse_pack_registries
Create Date: 2026-08-23
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "090_retire_governed_pack_stack"
down_revision: Union[str, None] = "089_collapse_pack_registries"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Dependants first.  These tables are retired data only; no live caller
    # remains once the MCP plugin-pack binding is enabled.
    for table in (
        "harness_project_pack_activations",
        "harness_pack_activation_snapshots",
        "harness_style_pack_versions",
        "harness_organization_governance_policies",
    ):
        op.execute(sa.text(f'DROP TABLE IF EXISTS "{table}" CASCADE'))


def downgrade() -> None:
    # The retired stack intentionally has no executable downgrade.  Restoring
    # these empty tables would suggest a second supported pack authority; use
    # the archival dump named in the module docstring only for data recovery.
    raise NotImplementedError("The retired governed Pack stack cannot be restored by migration")
