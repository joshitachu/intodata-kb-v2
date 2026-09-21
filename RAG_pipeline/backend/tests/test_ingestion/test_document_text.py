"""Every document's parsed text survives ingestion.

These run against a real (SQLite) session rather than a mocked one on purpose:
the two properties worth proving -- that a re-ingest replaces the stored text and
that deleting a document takes its text with it -- are both enforced by the
database (FK cascade), not by Python, and a mocked session would assert nothing
about either.
"""

from __future__ import annotations

import hashlib
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import Document, DocumentTextRow, IngestionJob
from src.db.models.document_text import (
    DOCUMENT_TEXT_MAX_CHARS,
    DOCUMENT_TEXT_SOURCE_PARSED,
)
from src.harness.guidance.material import MATERIALIZE_MAX_CHARS
from src.ingestion.pipeline import ingest_document

SMALL_POLICY = "# Retention policy\n\nClause 1. Keep records for seven years.\n"


def _markdown_bytes(text: str) -> bytes:
    return text.encode("utf-8")


async def _ingest(
    db: AsyncSession,
    *,
    text: str,
    filename: str,
    role: str,
) -> tuple[Document, IngestionJob]:
    """Run the real pipeline with only the embedding/Qdrant edges stubbed."""

    with (
        patch("src.ingestion.pipeline.embed_texts", new_callable=AsyncMock) as embed,
        patch("src.ingestion.pipeline.embed_texts_sparse") as sparse,
        patch("src.ingestion.pipeline.ensure_collection", new_callable=AsyncMock),
        patch("src.ingestion.pipeline.upsert_chunks", new_callable=AsyncMock),
        patch("src.ingestion.pipeline.delete_document_points", new_callable=AsyncMock),
        patch("src.ingestion.pipeline.settings") as pipeline_settings,
    ):
        pipeline_settings.hybrid_search_enabled = False
        embed.return_value = [[0.1] * 768]
        sparse.return_value = []
        return await ingest_document(
            file_bytes=_markdown_bytes(text),
            filename=filename,
            allowed_groups=["proj"],
            uploaded_by=None,
            db=db,
            qdrant=AsyncMock(),
            project_id="proj",
            document_role=role,
        )


async def _stored_text(db: AsyncSession, document_id: int) -> DocumentTextRow | None:
    # Expire first: without it the identity map would answer from the object
    # this test just added, and the assertion would never reach the database.
    db.expire_all()
    return await db.get(DocumentTextRow, document_id)


async def _text_row_count(db: AsyncSession) -> int:
    return (await db.execute(select(func.count()).select_from(DocumentTextRow))).scalar_one()


async def test_small_policy_upload_stores_its_text(db_session: AsyncSession) -> None:
    doc, job = await _ingest(
        db_session, text=SMALL_POLICY, filename="retention.md", role="policy"
    )

    assert job.status == "completed"
    row = await _stored_text(db_session, doc.id)
    assert row is not None
    assert row.text == SMALL_POLICY
    assert row.char_count == len(SMALL_POLICY)
    assert row.sha256 == hashlib.sha256(SMALL_POLICY.encode("utf-8")).hexdigest()


@pytest.mark.parametrize("role", ["reference", "guidance", "standard", "template", "policy"])
async def test_every_non_source_role_is_stored(db_session: AsyncSession, role: str) -> None:
    doc, _job = await _ingest(
        db_session, text=SMALL_POLICY, filename=f"{role}.md", role=role
    )

    assert await _stored_text(db_session, doc.id) is not None


async def test_source_upload_is_stored_too(db_session: AsyncSession) -> None:
    """The source corpus is where the completeness questions are asked.

    It used to be excluded, on the argument that evidence is searched by design.
    That left the table with 3 rows against 551 documents and left the one
    question similarity cannot answer -- "how many fields does this mapping
    have" -- with no tool that could. Role no longer decides.
    """

    doc, job = await _ingest(
        db_session, text=SMALL_POLICY, filename="evidence.md", role="source"
    )

    assert job.status == "completed"
    row = await _stored_text(db_session, doc.id)
    assert row is not None
    assert row.text == SMALL_POLICY
    assert row.source == DOCUMENT_TEXT_SOURCE_PARSED


async def test_a_document_above_the_workspace_mount_ceiling_is_still_stored(
    db_session: AsyncSession,
) -> None:
    """Storing and mounting are two decisions, and only one of them has that cap.

    ``MATERIALIZE_MAX_CHARS`` still governs which documents are written into the
    agent's workspace as files. It never governed whether a document can be READ
    when asked for, and conflating the two is what emptied this table.
    """

    oversize = "# Handbook\n\n" + ("policy clause. " * 4_000)
    assert len(oversize) > MATERIALIZE_MAX_CHARS

    doc, job = await _ingest(
        db_session, text=oversize, filename="handbook.md", role="policy"
    )

    assert job.status == "completed"
    row = await _stored_text(db_session, doc.id)
    assert row is not None
    assert row.char_count == len(oversize)


async def test_a_document_above_the_storage_ceiling_stores_nothing(
    db_session: AsyncSession,
) -> None:
    """Nothing rather than a prefix: a prefix is indistinguishable from the whole."""

    from src.ingestion.pipeline import _persist_document_text

    doc = Document(filename="proj/huge.md", file_type="md", document_role="reference")
    db_session.add(doc)
    await db_session.flush()

    stored = await _persist_document_text(
        document_id=doc.id,
        text="x" * (DOCUMENT_TEXT_MAX_CHARS + 1),
        role="reference",
        db=db_session,
    )

    assert stored is False
    assert await _text_row_count(db_session) == 0


async def test_reingest_replaces_the_stored_text(db_session: AsyncSession) -> None:
    first, _ = await _ingest(
        db_session, text=SMALL_POLICY, filename="retention.md", role="policy"
    )
    revised = "# Retention policy\n\nClause 1. Keep records for ten years.\n"
    second, _ = await _ingest(
        db_session, text=revised, filename="retention.md", role="policy"
    )

    # Not asserting that the two documents differ by id: the replacement frees
    # the old row and SQLite hands the same rowid straight back. What has to
    # hold is that one stored text exists and it is the new one.
    assert first.filename == second.filename
    assert await _text_row_count(db_session) == 1
    row = await _stored_text(db_session, second.id)
    assert row is not None
    assert row.text == revised


async def test_deleting_the_document_removes_the_text(db_session: AsyncSession) -> None:
    doc, _job = await _ingest(
        db_session, text=SMALL_POLICY, filename="retention.md", role="policy"
    )
    assert await _text_row_count(db_session) == 1

    await db_session.delete(await db_session.get(Document, doc.id))
    await db_session.flush()

    assert await _text_row_count(db_session) == 0


async def test_failing_to_store_the_text_does_not_fail_the_ingestion(
    db_session: AsyncSession,
) -> None:
    """A readable copy is an enhancement; the chunks are the deliverable.

    Losing the upload because the convenience copy could not be written would be
    strictly worse than not having the copy.
    """

    with patch("src.ingestion.pipeline.DocumentTextRow") as row_cls:
        row_cls.side_effect = SQLAlchemyError("materialization is unavailable")
        doc, job = await _ingest(
            db_session, text=SMALL_POLICY, filename="retention.md", role="policy"
        )

    assert job.status == "completed"
    assert doc.chunk_count >= 1
    assert await _text_row_count(db_session) == 0
    # The session survived the failure: the enclosing transaction is still
    # usable, which is what makes "the ingestion still succeeds" true rather
    # than merely postponed to the commit.
    await db_session.flush()


async def test_a_failed_write_leaves_the_transaction_usable(
    db_session: AsyncSession,
) -> None:
    """Regression guard for the savepoint.

    Without it a failed INSERT poisons the transaction, and the swallowed error
    reappears as a failed commit one step later -- the upload fails anyway, just
    with a less useful error.
    """

    from src.ingestion.pipeline import _persist_document_text

    doc = Document(filename="proj/orphan.md", file_type="md", document_role="policy")
    db_session.add(doc)
    await db_session.flush()

    # A document_id with no document behind it violates the FK.
    stored = await _persist_document_text(
        document_id=doc.id + 10_000, text=SMALL_POLICY, role="policy", db=db_session
    )

    assert stored is False
    db_session.add(
        DocumentTextRow(
            document_id=doc.id,
            text=SMALL_POLICY,
            char_count=len(SMALL_POLICY),
            sha256="0" * 64,
        )
    )
    await db_session.flush()
    assert await _text_row_count(db_session) == 1


async def test_the_pipeline_does_not_restate_the_storage_ceiling() -> None:
    """The ceiling must live in exactly one place.

    Asserted structurally rather than behaviourally: a second copy of the number
    would pass every case above on the day it was written and diverge silently
    the day the first copy is tuned.
    """

    from pathlib import Path

    from src.ingestion import pipeline

    body = Path(pipeline.__file__).read_text(encoding="utf-8")
    assert str(DOCUMENT_TEXT_MAX_CHARS) not in body
    assert body.count("DOCUMENT_TEXT_MAX_CHARS") >= 1


async def test_the_workspace_mount_still_applies_the_narrow_rule() -> None:
    """Storing everything must not mount everything.

    ``materialized`` used to mean "a document_texts row exists", which was the
    same fact as "small and non-evidence" only because ingestion wrote rows
    under exactly that rule. Now that it writes them for everything, the mount
    decision has to re-apply the rule or a 300-page reference is written into
    the workspace as a file the agent is told to read.
    """

    from pathlib import Path

    from src.harness.guidance import material_repository

    body = Path(material_repository.__file__).read_text(encoding="utf-8")
    assert "should_materialize(" in body
    assert not material_repository.should_materialize("source", 10)
    assert not material_repository.should_materialize("policy", MATERIALIZE_MAX_CHARS + 1)
    assert material_repository.should_materialize("policy", 10)
