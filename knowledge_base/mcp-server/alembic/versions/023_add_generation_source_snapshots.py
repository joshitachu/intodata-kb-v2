"""Add immutable generic source snapshot and item indexes.

Revision ID: 023_generation_source_snapshots
Revises: 022_generation_experiment_core
Create Date: 2026-07-27
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "023_generation_source_snapshots"
down_revision: Union[str, None] = "022_generation_experiment_core"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "generation_source_snapshots",
        sa.Column("id", sa.String(64), primary_key=True, nullable=False),
        sa.Column(
            "experiment_id",
            sa.String(36),
            sa.ForeignKey("generation_experiments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("manifest_sha256", sa.String(64), nullable=False),
        sa.Column("security_state", sa.String(32), nullable=False),
        sa.Column("manifest_relative_path", sa.String(1000), nullable=False),
        sa.Column("metadata_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("finalized_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "experiment_id",
            name="uq_generation_source_snapshots_experiment",
        ),
    )
    op.create_index(
        "ix_generation_source_snapshots_experiment_id",
        "generation_source_snapshots",
        ["experiment_id"],
    )
    op.create_index(
        "ix_generation_source_snapshots_security",
        "generation_source_snapshots",
        ["security_state", "created_at"],
    )

    op.create_table(
        "generation_source_snapshot_items",
        sa.Column("id", sa.String(64), primary_key=True, nullable=False),
        sa.Column(
            "snapshot_id",
            sa.String(64),
            sa.ForeignKey("generation_source_snapshots.id", ondelete="CASCADE"),
            primary_key=True,
            nullable=False,
        ),
        sa.Column("source_descriptor_id", sa.String(200), nullable=False),
        sa.Column("logical_identity", sa.String(1000), nullable=False),
        sa.Column("source_kind", sa.String(200), nullable=False),
        sa.Column("source_role", sa.String(200), nullable=False),
        sa.Column("capabilities_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("locator", sa.Text(), nullable=False),
        sa.Column("raw_sha256", sa.String(64), nullable=False),
        sa.Column("extracted_sha256", sa.String(64), nullable=False),
        sa.Column("media_type", sa.String(200), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("extracted_size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("content_relative_path", sa.String(1000), nullable=False),
        sa.Column("trust_level", sa.String(100), nullable=False),
        sa.Column("redaction_state", sa.String(32), nullable=False),
        sa.Column("adapter_id", sa.String(200), nullable=False),
        sa.Column("adapter_version", sa.String(100), nullable=False),
        sa.Column("metadata_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "snapshot_id",
            "logical_identity",
            name="uq_generation_source_snapshot_item_identity",
        ),
    )
    op.create_index(
        "ix_generation_source_snapshot_items_snapshot_id",
        "generation_source_snapshot_items",
        ["snapshot_id"],
    )
    op.create_index(
        "ix_generation_source_snapshot_items_role_kind",
        "generation_source_snapshot_items",
        ["snapshot_id", "source_role", "source_kind"],
    )

    op.add_column(
        "generation_experiments",
        sa.Column("source_snapshot_id", sa.String(64), nullable=True),
    )
    op.create_index(
        "ix_generation_experiments_source_snapshot_id",
        "generation_experiments",
        ["source_snapshot_id"],
    )
    op.create_foreign_key(
        "fk_generation_experiments_source_snapshot",
        "generation_experiments",
        "generation_source_snapshots",
        ["source_snapshot_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_generation_experiments_source_snapshot",
        "generation_experiments",
        type_="foreignkey",
    )
    op.drop_index(
        "ix_generation_experiments_source_snapshot_id",
        table_name="generation_experiments",
    )
    op.drop_column("generation_experiments", "source_snapshot_id")
    op.drop_index(
        "ix_generation_source_snapshot_items_role_kind",
        table_name="generation_source_snapshot_items",
    )
    op.drop_index(
        "ix_generation_source_snapshot_items_snapshot_id",
        table_name="generation_source_snapshot_items",
    )
    op.drop_table("generation_source_snapshot_items")
    op.drop_index(
        "ix_generation_source_snapshots_security",
        table_name="generation_source_snapshots",
    )
    op.drop_index(
        "ix_generation_source_snapshots_experiment_id",
        table_name="generation_source_snapshots",
    )
    op.drop_table("generation_source_snapshots")
