"""Keep the parsed text of small non-evidence documents instead of discarding it.

Ingestion has always parsed a document to text, embedded the chunks and dropped
the text. That leaves similarity retrieval as the only way back in, which is the
right access path for the source corpus and the wrong one for a two-page policy:
fragments in relevance order lose the clause ordering, and nothing tells the
reader whether it saw the whole document. This table is where the text of the
documents worth reading whole now lives.

A side table rather than a column on ``documents``. The document table is
dominated by source rows that are never materialized; a nullable text column
would widen every one of them and be dragged through every document listing.
Keyed by ``document_id`` because a document has at most one stored text, and
``ON DELETE CASCADE`` because re-ingesting a filename deletes and recreates the
document row -- without the cascade the previous version's text would outlive
the document it came from.

Reversible and purely additive: upgrade creates one table, downgrade drops it.
No existing row is read, rewritten or deleted in either direction. A downgrade
loses the stored copies only; every document remains fully retrievable through
its chunks, which are held in Qdrant and are untouched here.

Revision ID: 101_document_texts
Revises: 100_guidance_rules
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "101_document_texts"
down_revision: str | None = "100_guidance_rules"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "document_texts",
        sa.Column(
            "document_id",
            sa.Integer(),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("char_count", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        # Contributed by the declarative Base. Omitting either one produces a
        # table the ORM cannot SELECT at all -- the failure 093, 094 and 096 had
        # to repair after the fact.
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )


def downgrade() -> None:
    op.drop_table("document_texts")
