"""add query_usage table for token/cost tracking

Revision ID: 007
Revises: 006
Create Date: 2026-03-10
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "007"
down_revision: Union[str, None] = "006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "query_usage",
        sa.Column("id", sa.Integer(), primary_key=True),
        # Who / what
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("interface", sa.String(50), nullable=False),
        # Query info
        sa.Column("query_text", sa.Text(), nullable=True),
        sa.Column("model_name", sa.String(100), nullable=True),
        sa.Column("provider", sa.String(50), nullable=True),
        # Token counts
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("embedding_tokens", sa.Integer(), nullable=True),
        # Cost
        sa.Column("cost_usd", sa.Float(), nullable=True),
        # Performance
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("embedding_ms", sa.Integer(), nullable=True),
        sa.Column("search_ms", sa.Integer(), nullable=True),
        sa.Column("llm_ms", sa.Integer(), nullable=True),
        # Context
        sa.Column("project_id", sa.String(255), nullable=True),
        sa.Column("conversation_id", sa.Integer(), sa.ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True),
        # Timestamps
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_query_usage_user_id", "query_usage", ["user_id"])
    op.create_index("ix_query_usage_interface", "query_usage", ["interface"])
    op.create_index("ix_query_usage_project_id", "query_usage", ["project_id"])
    op.create_index("ix_query_usage_created_at", "query_usage", ["created_at"])


def downgrade() -> None:
    op.drop_table("query_usage")
