"""add created_at/updated_at to runtime metadata tables

Migrations 037 and 043 created runtime_component_metrics, runtime_artifacts,
runtime_connections, and runtime_job_dependencies without the created_at/updated_at
columns that the shared declarative Base (src/db/base.py) declares on every model.
The resulting schema drift made every SELECT on these tables raise
UndefinedColumnError (500), breaking the /runtime/health, /artifacts, /connections,
/dependencies, and /dependency-summary endpoints. This migration aligns the schema
with the model, matching the timestamp definition used by the sibling runtime tables.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "048_runtime_meta_ts"
down_revision: Union[str, None] = "047_workspace_map"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLES = (
    "runtime_component_metrics",
    "runtime_artifacts",
    "runtime_connections",
    "runtime_job_dependencies",
)


def upgrade() -> None:
    for table in _TABLES:
        op.add_column(
            table,
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
        )
        op.add_column(
            table,
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
        )


def downgrade() -> None:
    for table in reversed(_TABLES):
        op.drop_column(table, "updated_at")
        op.drop_column(table, "created_at")
