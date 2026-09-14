"""Persist generation contracts without reinterpreting historical runs."""
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from alembic import op

revision = "032_generation_profile"
down_revision = "031_add_project_repo_sources"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "generation_profile" not in {c["name"] for c in sa.inspect(op.get_bind()).get_columns("generate_runs")}:
        op.add_column("generate_runs", sa.Column(
            "generation_profile", JSONB().with_variant(sa.JSON(), "sqlite"), nullable=True,
        ))


def downgrade() -> None:
    if "generation_profile" in {c["name"] for c in sa.inspect(op.get_bind()).get_columns("generate_runs")}:
        op.drop_column("generate_runs", "generation_profile")
