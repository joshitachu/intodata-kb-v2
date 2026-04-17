"""add image_data column to conversation_messages

Stores base64-encoded images attached to user messages for AI vision support.

Revision ID: 013
Revises: 012
Create Date: 2026-04-14
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSON

# revision identifiers, used by Alembic.
revision: str = "013"
down_revision: Union[str, None] = "012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "conversation_messages",
        sa.Column("image_data", JSON, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("conversation_messages", "image_data")
