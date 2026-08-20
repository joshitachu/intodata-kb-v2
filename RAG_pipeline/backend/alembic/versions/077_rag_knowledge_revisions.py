"""Add durable active RAG knowledge revision authority.

Revision ID: 077_rag_knowledge_revisions
Revises: 076_agent_trace_explorer
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "077_rag_knowledge_revisions"
down_revision = "076_agent_trace_explorer"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "rag_knowledge_revisions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.String(255), nullable=False),
        sa.Column("knowledge_revision_id", sa.String(255), nullable=False),
        sa.Column("base_knowledge_revision_id", sa.String(255), nullable=True),
        sa.Column("state", sa.String(20), nullable=False, server_default="prepared"),
        sa.Column("membership_hashes", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("deleted_logical_ids", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("aliases", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("project_id", "knowledge_revision_id", name="uq_rag_knowledge_revision"),
    )
    op.create_index("ix_rag_knowledge_revisions_project_id", "rag_knowledge_revisions", ["project_id"])
    op.create_table(
        "active_rag_knowledge_revisions",
        sa.Column("project_id", sa.String(255), primary_key=True),
        sa.Column("knowledge_revision_id", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("active_rag_knowledge_revisions")
    op.drop_index("ix_rag_knowledge_revisions_project_id", table_name="rag_knowledge_revisions")
    op.drop_table("rag_knowledge_revisions")
