"""add benchmark_runs table

Revision ID: 008
Revises: 007
Create Date: 2026-03-10
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "008"
down_revision: Union[str, None] = "007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "benchmark_runs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tag", sa.String(100), nullable=False, index=True),
        sa.Column("corpus_name", sa.String(200), nullable=False),
        sa.Column("corpus_version", sa.String(50), nullable=False),
        sa.Column("llm_model", sa.String(100), nullable=False),
        sa.Column("embed_model", sa.String(100), nullable=False),
        sa.Column("reranker_model", sa.String(100), nullable=True),
        sa.Column("num_questions", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("avg_precision_at_5", sa.Float(), nullable=True),
        sa.Column("avg_relevance_score", sa.Float(), nullable=True),
        sa.Column("avg_latency_ms", sa.Float(), nullable=True),
        sa.Column("total_input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_output_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_cost_usd", sa.Float(), nullable=False, server_default="0"),
        sa.Column("results", postgresql.JSONB(), nullable=True),
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
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("benchmark_runs")
