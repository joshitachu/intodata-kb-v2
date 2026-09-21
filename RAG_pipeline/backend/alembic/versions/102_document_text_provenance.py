"""Record where a stored document text came from.

``document_texts`` now holds two kinds of row. One is the text the parser
produced during ingestion, which is byte-identical to what was chunked. The
other is a text reassembled from the document's indexed chunks by
``python -m src.ingestion.backfill_document_texts``, which is the only recovery
available for the 500-odd documents ingested before the table was populated --
the original upload bytes are not retained anywhere.

Those two are not interchangeable. Chunking is lossy: the markdown chunker drops
heading lines and prepends a synthetic heading path, and the semantic chunker
overlaps consecutive chunks. A reconstruction is close enough to answer a
completeness question from and is not a copy of the document, and the read
capability has to be able to say which one it is handing over. Without this
column it cannot, and the only honest alternative would be to caveat every
whole-document read forever.

``server_default='parsed'`` is what makes this safe to apply to a populated
table: every row that exists when this runs was written by the ingestion path,
so the default is the true value for all of them and no data migration is
needed.

Reversible. Upgrade adds one non-null column with a default; downgrade drops it,
losing only the provenance distinction. No text is read, rewritten or deleted in
either direction.

Revision ID: 102_document_text_provenance
Revises: 101_document_texts
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "102_document_text_provenance"
down_revision: str | None = "101_document_texts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "document_texts",
        sa.Column(
            "source",
            sa.String(20),
            nullable=False,
            server_default="parsed",
        ),
    )


def downgrade() -> None:
    op.drop_column("document_texts", "source")
