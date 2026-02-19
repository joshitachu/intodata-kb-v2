from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.ingestion.pipeline import _replace_existing_document, ingest_document


@pytest.fixture
def mock_db():
    db = AsyncMock(spec=AsyncSession)
    db.add = MagicMock()
    db.delete = MagicMock()

    # Make flush assign an id to the first added object (Document)
    call_count = 0

    async def mock_flush():
        nonlocal call_count
        call_count += 1
        # After first flush, the Document should get an id
        for call_args in db.add.call_args_list:
            obj = call_args[0][0]
            if not hasattr(obj, '_id_set') and hasattr(obj, 'id') and obj.id is None:
                obj.id = 1
                obj._id_set = True

    db.flush = mock_flush

    # Mock db.execute to return no existing document (for _replace_existing_document)
    mock_result = MagicMock()
    mock_scalars = MagicMock()
    mock_scalars.first.return_value = None
    mock_result.scalars.return_value = mock_scalars
    db.execute.return_value = mock_result

    return db


@pytest.fixture
def mock_qdrant():
    return AsyncMock()


async def test_ingest_document_success(mock_db, mock_qdrant):
    # Create a simple docx file for testing
    import io
    from docx import Document as DocxDocument

    buf = io.BytesIO()
    doc = DocxDocument()
    doc.add_paragraph("This is test content for the ingestion pipeline test case.")
    doc.save(buf)
    buf.seek(0)
    file_bytes = buf.read()

    with (
        patch("src.ingestion.pipeline.embed_texts", new_callable=AsyncMock) as mock_embed,
        patch("src.ingestion.pipeline.embed_texts_sparse") as mock_sparse,
        patch("src.ingestion.pipeline.ensure_collection", new_callable=AsyncMock),
        patch("src.ingestion.pipeline.upsert_chunks", new_callable=AsyncMock) as mock_upsert,
        patch("src.ingestion.pipeline.settings") as mock_settings,
    ):
        mock_settings.hybrid_search_enabled = True
        mock_embed.return_value = [[0.1] * 768]  # 1 chunk → 1 embedding
        mock_sparse.return_value = [MagicMock(indices=[1], values=[0.5])]
        mock_upsert.return_value = ["point-uuid-1"]

        doc_record, job = await ingest_document(
            file_bytes=file_bytes,
            filename="test.docx",
            allowed_groups=["engineering"],
            uploaded_by=1,
            db=mock_db,
            qdrant=mock_qdrant,
        )

    assert doc_record.filename == "test.docx"
    assert doc_record.file_type == "docx"
    assert job.status == "completed"
    assert job.chunks_created >= 1


async def test_ingest_document_failure(mock_db, mock_qdrant):
    # Use invalid file content to trigger parser error
    with pytest.raises(Exception):
        await ingest_document(
            file_bytes=b"not a real docx",
            filename="bad.docx",
            allowed_groups=["engineering"],
            uploaded_by=1,
            db=mock_db,
            qdrant=mock_qdrant,
        )


class TestReplaceExistingDocument:
    @pytest.mark.asyncio
    async def test_no_existing_document_does_nothing(self) -> None:
        """When no document with the filename exists, nothing is deleted."""
        mock_db = AsyncMock(spec=AsyncSession)
        mock_qdrant = AsyncMock()

        # Simulate no matching document
        mock_result = MagicMock()
        mock_scalars = MagicMock()
        mock_scalars.first.return_value = None
        mock_result.scalars.return_value = mock_scalars
        mock_db.execute.return_value = mock_result

        await _replace_existing_document("new_doc.pdf", mock_db, mock_qdrant)

        mock_db.delete.assert_not_called()
        mock_db.flush.assert_not_called()

    @pytest.mark.asyncio
    async def test_existing_document_is_deleted(self) -> None:
        """When a document with the same filename exists, it is deleted."""
        mock_db = AsyncMock(spec=AsyncSession)
        mock_qdrant = AsyncMock()

        # Simulate existing document
        existing_doc = MagicMock()
        existing_doc.id = 42
        existing_doc.chunk_count = 15

        mock_result = MagicMock()
        mock_scalars = MagicMock()
        mock_scalars.first.return_value = existing_doc
        mock_result.scalars.return_value = mock_scalars
        mock_db.execute.return_value = mock_result

        with patch("src.ingestion.pipeline.delete_document_points", new_callable=AsyncMock) as mock_delete_points:
            await _replace_existing_document("existing.pdf", mock_db, mock_qdrant)

        # Should have deleted Qdrant vectors
        mock_delete_points.assert_called_once_with(mock_qdrant, 42)
        # Should have deleted DB record
        mock_db.delete.assert_called_once_with(existing_doc)
        mock_db.flush.assert_called_once()
