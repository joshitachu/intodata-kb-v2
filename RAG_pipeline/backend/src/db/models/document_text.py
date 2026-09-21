"""The parsed text of a document, kept so it can be read rather than searched.

Ingestion parses a file to text, chunks it, embeds the chunks and used to throw
the text away. Similarity retrieval was then the only way back in, and that is
the wrong access path for any question whose answer is a complete set: an agent
asked how many fields a mapping has can only ever see the chunks that scored
well, and has no way to know what it did not see. Measured consequence: a
31-row mapping table reported to a client as 30 fields.

So this table is now the whole-document store, and the policy that decides what
lands in it is deliberately NOT the policy that decides what is mounted into the
agent's workspace. The two were the same rule once and that is why the table was
effectively empty -- three rows against 551 documents:

* **Stored here**: every document that parses to text, up to
  :data:`DOCUMENT_TEXT_MAX_CHARS`. Role is not consulted. The source corpus is
  exactly where the completeness questions are asked, so excluding it defeated
  the purpose of keeping text at all.
* **Mounted into the workspace**: only what
  :func:`src.harness.guidance.material.should_materialize` still admits -- small,
  non-evidence documents. A mounted file costs context on the turn it is read
  and sits in front of the agent as a constraint; that is a much narrower thing
  than "is readable when asked for".

:mod:`src.harness.guidance.material_repository` is where the two meet: a row
existing here is necessary but no longer sufficient for ``materialized``.

Why a side table instead of a column on ``documents``:

* ``documents`` is read on every page load and the text is read by one
  agent-facing capability. A ``text`` column would drag hundreds of kilobytes
  through every document listing for the benefit of a caller that is not there.
* "Is this document's text stored?" becomes a row that exists or does not, which
  a join or an ``EXISTS`` answers directly. As a nullable column the same
  question is only answerable by inspecting the value, and "present but empty"
  and "absent" become two spellings of the same state.

The row is keyed by ``document_id`` rather than carrying a surrogate key: a
document has at most one stored text, and making that a primary key is the
cheapest way to say so. ``ON DELETE CASCADE`` ties its lifetime to the document,
so re-ingesting a filename -- which deletes and recreates the document row --
cannot leave the previous version's text behind.
"""

from __future__ import annotations

from typing import Final

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base

#: Hard ceiling on one stored document, in characters.
#:
#: This bounds the TABLE, not what an agent may read in one call -- that is
#: bounded separately and much lower by the read capability's outline/section
#: modes. Two megabytes is well above anything observed in the corpus and still
#: small enough that a pathological upload (a generated log dump landing under a
#: documentation role) cannot put a gigabyte of text into a row that every
#: backup then carries.
#:
#: A document over the ceiling stores nothing rather than a truncated prefix. A
#: prefix would be indistinguishable from the whole document to the one caller
#: that exists, which is the exact failure this table was created to fix.
DOCUMENT_TEXT_MAX_CHARS: Final = 2_000_000

#: Text taken from the parser during ingestion. Byte-identical to what was
#: chunked, and the only provenance a reader may treat as the document itself.
DOCUMENT_TEXT_SOURCE_PARSED: Final = "parsed"

#: Text reassembled from the document's indexed chunks by the backfill.
#:
#: The original upload bytes are not retained anywhere, so a document ingested
#: before this table was populated can only be recovered from Qdrant. Chunking
#: is lossy in both directions -- the markdown chunker drops heading lines and
#: prepends a synthetic heading path, the semantic chunker overlaps consecutive
#: chunks -- so a reassembled text is a faithful-enough reconstruction and not a
#: copy. It is marked so the read capability can say which it is handing over,
#: because "here is the document" and "here is the document as far as the index
#: preserved it" are different claims and only one of them is true here.
DOCUMENT_TEXT_SOURCE_RECONSTRUCTED: Final = "reconstructed"


class DocumentTextRow(Base):
    """Full parsed text of one document, stored so it can be read in document order."""

    __tablename__ = "document_texts"

    document_id: Mapped[int] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True
    )
    text: Mapped[str] = mapped_column(Text, nullable=False)
    #: Denormalized ``len(text)``, so the manifest can report size and the
    #: materialization threshold can be re-evaluated without loading the body.
    char_count: Mapped[int] = mapped_column(Integer, nullable=False)
    #: Digest of the stored text. Lets a reader tell "the same document, again"
    #: from "the document changed" across a re-ingest, which allocates a new
    #: document_id and would otherwise look like an unrelated row.
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    #: ``parsed`` or ``reconstructed`` -- see the constants above. Defaulted at
    #: the database so the rows written before this column existed, which all
    #: came from the parser, read correctly without a data migration.
    source: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=DOCUMENT_TEXT_SOURCE_PARSED,
        server_default=DOCUMENT_TEXT_SOURCE_PARSED,
    )
