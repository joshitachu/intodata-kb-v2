from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.ingestion.pipeline import ingest_document


@pytest.fixture
def mock_db():
    db = AsyncMock(spec=AsyncSession)
    db.add = MagicMock()

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
        patch("src.ingestion.pipeline.ensure_collection", new_callable=AsyncMock),
        patch("src.ingestion.pipeline.upsert_chunks", new_callable=AsyncMock) as mock_upsert,
    ):
        mock_embed.return_value = [[0.1] * 768]  # 1 chunk → 1 embedding
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
