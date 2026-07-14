"""add durable normalized harness evidence snapshots"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "053_harness_evidence"
down_revision: Union[str, None] = "052_harness_connectors"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "harness_evidence",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "workflow_run_id",
            sa.Integer(),
            sa.ForeignKey("harness_workflow_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source_class", sa.String(40), nullable=False),
        sa.Column("source_id", sa.String(255), nullable=False),
        sa.Column("capability_id", sa.String(160)),
        sa.Column("project_ref", sa.String(255)),
        sa.Column("entity_ref", sa.String(255)),
        sa.Column("connector_ref", sa.String(255)),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("uri", sa.String(1000)),
        sa.Column("observed_at", sa.DateTime(timezone=True)),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("freshness", sa.Float(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("authority", sa.Float(), nullable=False),
        sa.Column("score", sa.Float()),
        sa.Column("claim_key", sa.String(255)),
        sa.Column("claim_value", sa.String(1000)),
        sa.Column("conflict_group", sa.String(80)),
        sa.Column("conflict_refs", sa.JSON()),
        sa.Column("collected_order", sa.Integer(), nullable=False),
        sa.Column("used_order", sa.Integer()),
        sa.Column("safe_metadata", sa.JSON()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint(
            "workflow_run_id", "collected_order", name="uq_harness_evidence_run_collected_order"
        ),
        sa.UniqueConstraint(
            "workflow_run_id", "used_order", name="uq_harness_evidence_run_used_order"
        ),
        sa.CheckConstraint(
            "freshness >= 0 AND freshness <= 1", name="ck_harness_evidence_freshness"
        ),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1", name="ck_harness_evidence_confidence"
        ),
        sa.CheckConstraint(
            "authority >= 0 AND authority <= 1", name="ck_harness_evidence_authority"
        ),
    )
    op.create_index("ix_harness_evidence_workflow_run_id", "harness_evidence", ["workflow_run_id"])
    op.create_index("ix_harness_evidence_claim_key", "harness_evidence", ["claim_key"])
    op.create_index("ix_harness_evidence_conflict_group", "harness_evidence", ["conflict_group"])


def downgrade() -> None:
    op.drop_index("ix_harness_evidence_conflict_group", table_name="harness_evidence")
    op.drop_index("ix_harness_evidence_claim_key", table_name="harness_evidence")
    op.drop_index("ix_harness_evidence_workflow_run_id", table_name="harness_evidence")
    op.drop_table("harness_evidence")
