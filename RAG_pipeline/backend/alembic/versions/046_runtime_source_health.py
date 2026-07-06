"""runtime source health"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "046_source_health"
down_revision: Union[str, None] = "045_runtime_sources"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "runtime_source_health",
        sa.Column("source_id", sa.Integer(), sa.ForeignKey("runtime_sources.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_failure_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consecutive_failures", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_failure_sanitized", sa.Text(), nullable=True),
        sa.Column("total_cycles", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_records_ingested", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("runtime_source_health")
