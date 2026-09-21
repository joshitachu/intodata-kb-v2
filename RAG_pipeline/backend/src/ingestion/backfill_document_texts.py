"""Recover a readable copy of documents ingested before their text was kept.

Ingestion now stores every document's parsed text in ``document_texts``, so an
agent can read a document in document order instead of retrieving ranked
fragments of it. Documents ingested before that change have no row -- measured
on a live instance: 3 rows against 551 documents -- and there is no way to
re-derive one from the original file, because the upload bytes are not retained
anywhere. What IS retained is every chunk of the document, with its text and its
position, in Qdrant.

So this reassembles the document from its own search index. That is a
reconstruction, not a copy, and the rows it writes are marked
``source='reconstructed'`` so the read capability can say which it is handing
over. Three known differences from the original:

* The markdown chunker prepends a synthetic ``[heading path]`` line to each
  chunk and drops the heading line itself. The prefix is stripped here and a
  heading is re-emitted from the stored ``section``/``unit`` payload, so the
  structure returns -- with ``##`` levels inferred from the path depth rather
  than read from the source.
* The semantic chunker overlaps consecutive chunks. The overlap is detected and
  removed by matching the tail of what is already assembled against the head of
  the next chunk; where it cannot be matched exactly, a few duplicated lines
  survive.
* Anything the parser produced but the chunker discarded -- pathological
  whitespace, content past a chunk cap -- is not recoverable.

None of that stops it being worth doing: the failure it fixes is an agent
answering "30 fields" for a 31-row table because it only ever saw the chunks
that scored well, and a reconstruction with an intact row set answers that
correctly.

This is an operator-run command and not something a deployment does to itself.
It writes one row per document across the whole corpus, and a mass write that
nobody asked for is exactly the kind of thing that should not happen quietly on
a boot.

Usage (inside the backend container, or any environment with DATABASE_URL and
QDRANT_URL set):

    # What would be written, and nothing else. This is the default.
    python -m src.ingestion.backfill_document_texts

    # Actually write.
    python -m src.ingestion.backfill_document_texts --apply

    # One project at a time, which is the safer way to start.
    python -m src.ingestion.backfill_document_texts --project-id acme --apply

    # Replace reconstructions written by an earlier run of this tool.
    python -m src.ingestion.backfill_document_texts --apply --redo-reconstructed

Safe to interrupt and safe to re-run. A document that already has a stored text
is skipped, so a second pass does nothing -- and a row written by INGESTION
(``source='parsed'``) is never replaced by a reconstruction, even with
``--redo-reconstructed``, because the parsed copy is the better one.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import logging
import sys
import time
from dataclasses import dataclass

from qdrant_client import AsyncQdrantClient
from qdrant_client.models import FieldCondition, Filter, MatchValue
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.db.models.document import Document
from src.db.models.document_text import (
    DOCUMENT_TEXT_MAX_CHARS,
    DOCUMENT_TEXT_SOURCE_PARSED,
    DOCUMENT_TEXT_SOURCE_RECONSTRUCTED,
    DocumentTextRow,
)
from src.db.session import async_session_factory
from src.retrieval.vector_store import COLLECTION_NAME

logger = logging.getLogger(__name__)

#: Chunks fetched per scroll round trip, per document.
_SCROLL_BATCH = 256

#: How far back into the assembled text to look for a chunk overlap, in
#: characters. The semantic chunker overlaps by at most 128 tokens; 2,000
#: characters is comfortably above that and bounds the search so a long document
#: does not turn the scan quadratic.
_OVERLAP_WINDOW = 2_000

#: Shortest run that may be treated as an overlap. Below this, "the next chunk
#: starts with the same twenty characters the last one ended with" is as likely
#: to be a repeated table header as a real overlap, and deleting a real line is
#: worse than keeping a duplicated one.
_MIN_OVERLAP = 40

#: The synthetic context line `chunk_markdown` prepends to every chunk. Removed
#: rather than kept: it is an embedding aid, never part of the document, and
#: leaving it in would put a bracketed path between every pair of paragraphs.
_HEADING_PREFIX = "["


@dataclass(frozen=True, slots=True)
class BackfillReport:
    """What one run did, in the terms an operator has to report upward."""

    considered: int = 0
    already_stored: int = 0
    no_chunks: int = 0
    too_large: int = 0
    written: int = 0

    def merged(self, **changes: int) -> BackfillReport:
        counts = {
            "considered": self.considered,
            "already_stored": self.already_stored,
            "no_chunks": self.no_chunks,
            "too_large": self.too_large,
            "written": self.written,
        }
        for key, delta in changes.items():
            counts[key] += delta
        return BackfillReport(**counts)


def _strip_heading_prefix(text: str) -> str:
    """Remove `chunk_markdown`'s `[doc > H2 > H3]` context line, if present.

    Matched structurally rather than with a regex over the path: a heading can
    contain almost anything, including brackets, so the reliable test is that
    the FIRST line starts with `[`, ends with `]` and is followed by a newline.
    A chunk whose real first line happens to look like that loses one line; a
    chunk that keeps the prefix gains a spurious heading in every section. The
    first is rarer and less damaging.
    """

    if not text.startswith(_HEADING_PREFIX):
        return text
    newline = text.find("\n")
    if newline == -1:
        return text
    if not text[:newline].rstrip().endswith("]"):
        return text
    return text[newline + 1 :]


def _heading_for(section: str | None, unit: str | None) -> str | None:
    """Rebuild the heading line the chunker consumed, from its own metadata.

    ``section`` is the full path (``doc_name > H2 > H3``) and ``unit`` is the
    deepest heading. The document name is the path root, so the heading's markdown
    level is the number of path elements minus one. A chunk whose section is just
    the document name covers pre-heading content and gets no heading at all.
    """

    if not section or not unit:
        return None
    depth = len([part for part in section.split(">") if part.strip()]) - 1
    if depth < 1:
        return None
    return f"{'#' * min(depth, 6)} {unit}"


def _append_without_overlap(assembled: str, addition: str) -> str:
    """Append ``addition``, dropping any prefix of it already at the tail.

    Anchored search rather than a suffix/prefix sweep: take the first
    ``_MIN_OVERLAP`` characters of the addition, find each place they occur in
    the recent tail, and accept the first one where the whole remaining tail is
    a prefix of the addition. That is linear in the window instead of quadratic
    in it, which matters across tens of thousands of chunks.
    """

    if not assembled:
        return addition
    if not addition:
        return assembled

    window = assembled[-_OVERLAP_WINDOW:]
    anchor = addition[:_MIN_OVERLAP]
    if len(anchor) == _MIN_OVERLAP:
        index = window.find(anchor)
        while index != -1:
            candidate = window[index:]
            if addition.startswith(candidate):
                return assembled + addition[len(candidate) :]
            index = window.find(anchor, index + 1)

    separator = "" if assembled.endswith("\n") else "\n"
    return assembled + separator + addition


async def reconstruct_document_text(
    client: AsyncQdrantClient, document_id: int
) -> str | None:
    """Reassemble one document from its indexed chunks, in chunk order.

    Returns None when the document has no chunks at all, which is a different
    state from "reassembled to nothing" and has a different remedy -- the
    document needs re-ingesting, not backfilling.
    """

    payloads: list[dict] = []
    offset = None
    while True:
        points, offset = await client.scroll(
            collection_name=COLLECTION_NAME,
            scroll_filter=Filter(
                must=[
                    FieldCondition(
                        key="document_id", match=MatchValue(value=document_id)
                    )
                ]
            ),
            limit=_SCROLL_BATCH,
            offset=offset,
            with_payload=["text", "chunk_index", "section", "unit"],
            with_vectors=False,
        )
        payloads.extend(point.payload or {} for point in points)
        if offset is None or not points:
            break

    if not payloads:
        return None

    # Chunk order is the document's order, and the scroll does not preserve it.
    payloads.sort(key=lambda payload: int(payload.get("chunk_index") or 0))

    assembled = ""
    last_section: str | None = None
    for payload in payloads:
        text = payload.get("text")
        if not isinstance(text, str) or not text:
            continue
        section = payload.get("section")
        body = _strip_heading_prefix(text)
        if section != last_section:
            heading = _heading_for(section, payload.get("unit"))
            if heading is not None:
                separator = "" if not assembled or assembled.endswith("\n") else "\n"
                assembled = f"{assembled}{separator}\n{heading}\n\n"
            last_section = section
        assembled = _append_without_overlap(assembled, body)

    return assembled


async def backfill_document_texts(
    db: AsyncSession,
    client: AsyncQdrantClient,
    *,
    project_id: str | None = None,
    limit: int | None = None,
    apply_changes: bool = False,
    redo_reconstructed: bool = False,
) -> BackfillReport:
    """Write a reconstructed text for every document that has none.

    Commits per document rather than at the end: a corpus-wide backfill is long
    enough that being interrupted halfway is a realistic outcome, and the useful
    behaviour there is "the documents processed so far are done", not "start
    again".
    """

    statement = (
        select(Document.id, Document.filename, DocumentTextRow.source)
        .outerjoin(DocumentTextRow, DocumentTextRow.document_id == Document.id)
        .where(Document.deleted_at.is_(None))
        .order_by(Document.id)
    )
    if project_id is not None:
        statement = statement.where(Document.project_id == project_id)
    if limit is not None:
        statement = statement.limit(limit)

    rows = (await db.execute(statement)).all()
    report = BackfillReport()
    started = time.perf_counter()

    for document_id, filename, stored_source in rows:
        report = report.merged(considered=1)
        if stored_source == DOCUMENT_TEXT_SOURCE_PARSED or (
            stored_source is not None and not redo_reconstructed
        ):
            # A parsed copy is never replaced: it came from the file itself and
            # is strictly better than anything reassembled from the index.
            report = report.merged(already_stored=1)
            continue

        text = await reconstruct_document_text(client, document_id)
        if not text or not text.strip():
            logger.warning(
                "Document %d (%s) has no usable chunks in Qdrant; it cannot be "
                "reconstructed and needs re-ingesting",
                document_id,
                filename,
            )
            report = report.merged(no_chunks=1)
            continue
        if len(text) > DOCUMENT_TEXT_MAX_CHARS:
            logger.warning(
                "Document %d (%s) reconstructs to %d chars, above the %d-char "
                "ceiling; skipped",
                document_id,
                filename,
                len(text),
                DOCUMENT_TEXT_MAX_CHARS,
            )
            report = report.merged(too_large=1)
            continue

        logger.info(
            "%s document %d (%s): %d chars from %s",
            "Writing" if apply_changes else "Would write",
            document_id,
            filename,
            len(text),
            "reconstruction",
        )
        if apply_changes:
            existing = await db.get(DocumentTextRow, document_id)
            if existing is not None:
                await db.delete(existing)
                await db.flush()
            db.add(
                DocumentTextRow(
                    document_id=document_id,
                    text=text,
                    char_count=len(text),
                    sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
                    source=DOCUMENT_TEXT_SOURCE_RECONSTRUCTED,
                )
            )
            await db.commit()
        report = report.merged(written=1)

    logger.info(
        "Backfill %s in %.1fs: considered=%d already_stored=%d no_chunks=%d "
        "too_large=%d written=%d",
        "applied" if apply_changes else "planned (dry run, nothing written)",
        time.perf_counter() - started,
        report.considered,
        report.already_stored,
        report.no_chunks,
        report.too_large,
        report.written,
    )
    return report


async def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m src.ingestion.backfill_document_texts",
        description=(
            "Reassemble the text of documents ingested before whole-document "
            "storage, from their indexed chunks. Dry run unless --apply is given."
        ),
    )
    parser.add_argument("--project-id", default=None, help="Restrict to one project.")
    parser.add_argument(
        "--limit", type=int, default=None, help="Process at most this many documents."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write the reconstructed texts. Without it nothing is written.",
    )
    parser.add_argument(
        "--redo-reconstructed",
        action="store_true",
        help=(
            "Replace texts written by an earlier run of this tool. Texts written "
            "by ingestion are never replaced."
        ),
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )

    # Passed through exactly as configured, `or None` deliberately absent. Every
    # other client in the codebase (api/dependencies.py, onboarding_service.py,
    # scripts/) hands the setting straight to the client, and a blank key is the
    # normal local configuration. Collapsing "" to None changes the request the
    # client builds and Qdrant answers it with 401, so this script failed against
    # the very deployment it was written for while the app beside it worked.
    client = AsyncQdrantClient(url=settings.qdrant_url, api_key=settings.qdrant_api_key)
    try:
        async with async_session_factory() as db:
            await backfill_document_texts(
                db,
                client,
                project_id=args.project_id,
                limit=args.limit,
                apply_changes=args.apply,
                redo_reconstructed=args.redo_reconstructed,
            )
    finally:
        await client.close()
    return 0


if __name__ == "__main__":  # pragma: no cover - module entry point
    sys.exit(asyncio.run(_main()))
