"""runtime workspace project map"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "047_workspace_map"
down_revision: Union[str, None] = "046_source_health"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "runtime_workspace_project_map",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source_id", sa.Integer(), sa.ForeignKey("runtime_sources.id", ondelete="CASCADE"), nullable=False),
        sa.Column("container_key", sa.String(255), nullable=False),
        sa.Column("container_name", sa.String(255), nullable=True),
        sa.Column("project_id", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("created_by", sa.String(255), nullable=True),
        sa.UniqueConstraint(
            "source_id",
            "container_key",
            name="uq_runtime_workspace_project_map_source_container",
        ),
    )
    op.create_index("ix_runtime_workspace_project_map_source_id", "runtime_workspace_project_map", ["source_id"])
    op.create_index("ix_runtime_workspace_project_map_project_id", "runtime_workspace_project_map", ["project_id"])


def downgrade() -> None:
    op.drop_index("ix_runtime_workspace_project_map_project_id", table_name="runtime_workspace_project_map")
    op.drop_index("ix_runtime_workspace_project_map_source_id", table_name="runtime_workspace_project_map")
    op.drop_table("runtime_workspace_project_map")
