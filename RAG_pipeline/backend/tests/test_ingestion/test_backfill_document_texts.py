"""Recovering a readable document from its own search index.

Ingestion now keeps every document's text, but the 500-odd documents ingested
before that have none, and the upload bytes are not retained anywhere. The only
remaining copy of their content is the chunks in Qdrant, so the backfill
reassembles from those.

The reconstruction is lossy in known ways -- the markdown chunker prepends a
synthetic heading path and drops the heading line, the semantic chunker overlaps
consecutive chunks -- and these tests pin the handling of each, plus the two
properties an operator depends on: nothing is written without `--apply`, and a
text written by ingestion is never overwritten by a reconstruction.
"""

from __future__ import annotations

import hashlib
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.document import Document
from src.db.models.document_text import (
    DOCUMENT_TEXT_SOURCE_PARSED,
    DOCUMENT_TEXT_SOURCE_RECONSTRUCTED,
    DocumentTextRow,
)
from src.ingestion.backfill_document_texts import (
    _append_without_overlap,
    _heading_for,
    _strip_heading_prefix,
    backfill_document_texts,
    reconstruct_document_text,
)


class FakeQdrant:
    """Scroll over a fixed set of payloads, keyed by document id.

    Deliberately returns the chunks out of order: the payload's `chunk_index`
    is the document's order and the scroll's is not, and reassembling in scroll
    order would produce a plausible document with its paragraphs shuffled.
    """

    def __init__(self, chunks: dict[int, list[dict]]) -> None:
        self._chunks = chunks

    async def scroll(self, *, scroll_filter, **_kwargs):  # noqa: ANN001, ANN003
        document_id = scroll_filter.must[0].match.value
        payloads = list(reversed(self._chunks.get(document_id, [])))
        return [SimpleNamespace(payload=payload) for payload in payloads], None

    async def close(self) -> None:  # pragma: no cover - parity with the real client
        return None


def _chunk(index: int, text: str, section: str | None = None, unit: str | None = None) -> dict:
    payload = {"chunk_index": index, "text": text}
    if section is not None:
        payload["section"] = section
    if unit is not None:
        payload["unit"] = unit
    return payload


# -- the lossy edges, in isolation -------------------------------------------


def test_the_synthetic_heading_path_prefix_is_removed() -> None:
    assert _strip_heading_prefix("[doc > Section]\nbody\n") == "body\n"


def test_a_body_that_merely_starts_with_a_bracket_is_left_alone() -> None:
    # The prefix is a whole line ending in `]`. A line that opens with `[` and
    # does not is content.
    assert _strip_heading_prefix("[TODO confirm with the client\nbody\n").startswith("[TODO")


def test_the_heading_level_is_recovered_from_the_path_depth() -> None:
    # The chunker drops the heading line itself, so without this a
    # reconstruction is one flat block and the section outline is useless.
    assert _heading_for("doc > Contract", "Contract") == "# Contract"
    assert _heading_for("doc > Contract > Fields", "Fields") == "## Fields"
    # A chunk whose section is only the document name is pre-heading content.
    assert _heading_for("doc", "doc") is None
    assert _heading_for(None, None) is None


def test_chunk_overlap_is_detected_and_dropped() -> None:
    assembled = "alpha beta gamma delta epsilon zeta eta theta iota kappa lambda mu"
    addition = "gamma delta epsilon zeta eta theta iota kappa lambda mu nu xi"

    joined = _append_without_overlap(assembled, addition)

    assert joined.count("epsilon") == 1
    assert joined.endswith("nu xi")


def test_a_short_coincidence_is_not_treated_as_overlap() -> None:
    # Deleting a real line is worse than keeping a duplicated one, so runs below
    # the minimum are left alone.
    joined = _append_without_overlap("first row\n", "row two\n")

    assert joined == "first row\nrow two\n"


# -- reconstruction ----------------------------------------------------------


async def test_chunks_are_reassembled_in_document_order_with_their_headings() -> None:
    client = FakeQdrant(
        {
            7: [
                _chunk(0, "[doc]\nPreamble.\n", section="doc", unit="doc"),
                _chunk(1, "[doc > Fields]\n| A | a |\n", section="doc > Fields", unit="Fields"),
                _chunk(2, "[doc > Fields]\n| B | b |\n", section="doc > Fields", unit="Fields"),
                _chunk(
                    3,
                    "[doc > Selection]\nABCLASS01 = 'FMD'\n",
                    section="doc > Selection",
                    unit="Selection",
                ),
            ]
        }
    )

    text = await reconstruct_document_text(client, 7)

    assert text is not None
    assert text.index("Preamble.") < text.index("| A | a |") < text.index("| B | b |")
    assert "# Fields" in text
    assert "# Selection" in text
    # The heading is emitted once per section, not once per chunk of it.
    assert text.count("# Fields") == 1


async def test_a_document_with_no_chunks_reconstructs_to_nothing() -> None:
    # Distinguishable from "reassembled to an empty string": the remedy is a
    # re-ingest, not a backfill.
    assert await reconstruct_document_text(FakeQdrant({}), 7) is None


# -- the command -------------------------------------------------------------


async def _document(db: AsyncSession, *, project_id: str = "acme") -> int:
    document = Document(
        filename=f"{project_id}/mapping.md",
        file_type="md",
        document_role="source",
        project_id=project_id,
    )
    db.add(document)
    await db.flush()
    return document.id


async def test_a_dry_run_writes_nothing(db_session: AsyncSession) -> None:
    # A corpus-wide mass write is not something a tool does because it was run.
    document_id = await _document(db_session)
    client = FakeQdrant({document_id: [_chunk(0, "[doc]\nbody\n", "doc", "doc")]})

    report = await backfill_document_texts(db_session, client)

    assert report.written == 1
    assert await db_session.get(DocumentTextRow, document_id) is None


async def test_apply_writes_a_row_marked_as_reconstructed(
    db_session: AsyncSession,
) -> None:
    document_id = await _document(db_session)
    client = FakeQdrant({document_id: [_chunk(0, "[doc]\nbody text\n", "doc", "doc")]})

    await backfill_document_texts(db_session, client, apply_changes=True)

    row = await db_session.get(DocumentTextRow, document_id)
    assert row is not None
    assert row.source == DOCUMENT_TEXT_SOURCE_RECONSTRUCTED
    assert "body text" in row.text
    assert row.char_count == len(row.text)
    assert row.sha256 == hashlib.sha256(row.text.encode("utf-8")).hexdigest()


async def test_a_parsed_text_is_never_replaced_by_a_reconstruction(
    db_session: AsyncSession,
) -> None:
    # The parsed copy came from the file itself and is strictly better. Even
    # --redo-reconstructed must not touch it.
    document_id = await _document(db_session)
    db_session.add(
        DocumentTextRow(
            document_id=document_id,
            text="the original",
            char_count=len("the original"),
            sha256="0" * 64,
            source=DOCUMENT_TEXT_SOURCE_PARSED,
        )
    )
    await db_session.flush()
    client = FakeQdrant({document_id: [_chunk(0, "[doc]\nsomething else\n", "doc", "doc")]})

    report = await backfill_document_texts(
        db_session, client, apply_changes=True, redo_reconstructed=True
    )

    assert report.already_stored == 1
    assert report.written == 0
    row = await db_session.get(DocumentTextRow, document_id)
    assert row is not None
    assert row.text == "the original"


async def test_a_second_pass_does_nothing(db_session: AsyncSession) -> None:
    document_id = await _document(db_session)
    client = FakeQdrant({document_id: [_chunk(0, "[doc]\nbody\n", "doc", "doc")]})

    await backfill_document_texts(db_session, client, apply_changes=True)
    second = await backfill_document_texts(db_session, client, apply_changes=True)

    assert second.written == 0
    assert second.already_stored == 1


async def test_a_document_without_chunks_is_reported_rather_than_written(
    db_session: AsyncSession,
) -> None:
    document_id = await _document(db_session)

    report = await backfill_document_texts(
        db_session, FakeQdrant({}), apply_changes=True
    )

    assert report.no_chunks == 1
    assert await db_session.get(DocumentTextRow, document_id) is None


async def test_the_project_filter_leaves_other_projects_alone(
    db_session: AsyncSession,
) -> None:
    mine = await _document(db_session, project_id="acme")
    theirs = await _document(db_session, project_id="other-client")
    client = FakeQdrant(
        {
            mine: [_chunk(0, "[doc]\nmine\n", "doc", "doc")],
            theirs: [_chunk(0, "[doc]\ntheirs\n", "doc", "doc")],
        }
    )

    report = await backfill_document_texts(
        db_session, client, project_id="acme", apply_changes=True
    )

    assert report.considered == 1
    assert report.written == 1
    assert await db_session.get(DocumentTextRow, theirs) is None


@pytest.mark.parametrize("redo", [False, True])
async def test_an_earlier_reconstruction_is_replaced_only_on_request(
    db_session: AsyncSession, redo: bool
) -> None:
    document_id = await _document(db_session)
    db_session.add(
        DocumentTextRow(
            document_id=document_id,
            text="stale reconstruction",
            char_count=len("stale reconstruction"),
            sha256="0" * 64,
            source=DOCUMENT_TEXT_SOURCE_RECONSTRUCTED,
        )
    )
    await db_session.flush()
    client = FakeQdrant({document_id: [_chunk(0, "[doc]\nfresh text\n", "doc", "doc")]})

    await backfill_document_texts(
        db_session, client, apply_changes=True, redo_reconstructed=redo
    )

    row = await db_session.get(DocumentTextRow, document_id)
    assert row is not None
    assert ("fresh text" in row.text) is redo
