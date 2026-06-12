"""Add doc_gap_events table and ladder metadata on conversation_messages.

Revision ID: 029
Revises: 028
Create Date: 2026-06-12
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "029"
down_revision: Union[str, None] = "028"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_JSON = sa.JSON().with_variant(JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "doc_gap_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.String(255), nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=True),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("conversation_id", sa.Integer(), nullable=True),
        sa.Column("message_id", sa.Integer(), nullable=True),
        sa.Column("query_text", sa.Text(), nullable=False),
        sa.Column("detected_entities", _JSON, nullable=True),
        sa.Column("insufficiency_reason", sa.String(50), nullable=False),
        sa.Column("primary_source_layer", sa.String(50), nullable=True),
        sa.Column("layers_used", _JSON, nullable=True),
        sa.Column("supporting_artifacts", _JSON, nullable=True),
        sa.Column("supporting_chunk_ids", _JSON, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_doc_gap_events_project_id", "doc_gap_events", ["project_id"])

    op.add_column(
        "conversation_messages",
        sa.Column("primary_source_layer", sa.String(50), nullable=True),
    )
    op.add_column(
        "conversation_messages",
        sa.Column("fallback_used", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column(
        "conversation_messages",
        sa.Column("doc_gap", sa.Boolean(), nullable=False, server_default="false"),
    )


def downgrade() -> None:
    op.drop_column("conversation_messages", "doc_gap")
    op.drop_column("conversation_messages", "fallback_used")
    op.drop_column("conversation_messages", "primary_source_layer")
    op.drop_index("ix_doc_gap_events_project_id", table_name="doc_gap_events")
    op.drop_table("doc_gap_events")
