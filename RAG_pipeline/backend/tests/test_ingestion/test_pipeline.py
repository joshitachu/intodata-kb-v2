from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.ingestion.pipeline import (
    _build_chunk_metadatas,
    _replace_existing_document,
    _scoped_filename,
    ingest_document,
    ingest_text,
)
from src.ingestion.markdown_chunker import MarkdownChunk


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
    assert doc_record.document_role == "source"
    assert job.status == "completed"
    assert job.chunks_created >= 1
    chunk_metadatas = mock_upsert.await_args.kwargs["chunk_metadatas"]
    assert chunk_metadatas[0]["document_role"] == "source"


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


class TestScopedFilename:
    def test_with_project_id(self) -> None:
        assert _scoped_filename("file.md", "proj") == "proj/file.md"

    def test_none_project_id(self) -> None:
        assert _scoped_filename("file.md", None) == "file.md"

    def test_empty_project_id(self) -> None:
        assert _scoped_filename("file.md", "") == "file.md"


class TestBuildChunkMetadatas:
    def test_defaults_applied_when_no_request_metadata(self) -> None:
        chunks = ["chunk a", "chunk b"]
        metas = _build_chunk_metadatas(
            chunks,
            {},
            default_source_layer="canonical_docs",
            default_artifact_type="final_doc",
        )
        assert len(metas) == 2
        assert metas[0]["source_layer"] == "canonical_docs"
        assert metas[0]["visibility"] == "public"
        assert metas[0]["artifact_type"] == "final_doc"
        assert metas[0]["document_role"] == "source"

    def test_caller_overrides_layer_and_type(self) -> None:
        chunks = ["chunk"]
        metas = _build_chunk_metadatas(
            chunks,
            {
                "source_layer": "technical_analysis",
                "artifact_type": "group_analysis",
                "document_role": "guidance",
            },
            default_source_layer="canonical_docs",
            default_artifact_type="final_doc",
        )
        assert metas[0]["source_layer"] == "technical_analysis"
        assert metas[0]["artifact_type"] == "group_analysis"
        assert metas[0]["document_role"] == "guidance"

    def test_invalid_document_role_rejected(self) -> None:
        with pytest.raises(ValueError, match="Invalid document_role"):
            _build_chunk_metadatas(
                ["chunk"],
                {"document_role": "manual"},
                default_source_layer="canonical_docs",
                default_artifact_type="final_doc",
            )

    def test_markdown_chunk_section_unit_included(self) -> None:
        md_chunks = [
            MarkdownChunk(
                text="[doc > H2]\ncontent",
                section="doc > H2",
                unit="H2",
            )
        ]
        metas = _build_chunk_metadatas(
            md_chunks,
            {},
            default_source_layer="canonical_docs",
            default_artifact_type="final_doc",
        )
        assert metas[0]["section"] == "doc > H2"
        assert metas[0]["unit"] == "H2"

    def test_absent_optional_fields_not_in_metadata(self) -> None:
        chunks = ["chunk"]
        metas = _build_chunk_metadatas(
            chunks,
            {},
            default_source_layer="canonical_docs",
            default_artifact_type="final_doc",
        )
        # Optional fields not provided → not in dict (no None values cluttering payload)
        assert "group" not in metas[0]
        assert "run_id" not in metas[0]

    def test_length_matches_chunks(self) -> None:
        chunks = ["a", "b", "c"]
        metas = _build_chunk_metadatas(
            chunks,
            {},
            default_source_layer="canonical_docs",
            default_artifact_type="final_doc",
        )
        assert len(metas) == len(chunks)

    def test_uploaded_file_default_for_ingest_document_path(self) -> None:
        chunks = ["chunk"]
        metas = _build_chunk_metadatas(
            chunks,
            {},
            default_source_layer="canonical_docs",
            default_artifact_type="uploaded_file",
        )
        assert metas[0]["artifact_type"] == "uploaded_file"


async def test_ingest_text_defaults_document_role_to_source(mock_db, mock_qdrant):
    with (
        patch("src.ingestion.pipeline.embed_texts", new_callable=AsyncMock) as mock_embed,
        patch("src.ingestion.pipeline.embed_texts_sparse") as mock_sparse,
        patch("src.ingestion.pipeline.ensure_collection", new_callable=AsyncMock),
        patch("src.ingestion.pipeline.upsert_chunks", new_callable=AsyncMock) as mock_upsert,
        patch("src.ingestion.pipeline.settings") as mock_settings,
    ):
        mock_settings.hybrid_search_enabled = True
        mock_embed.return_value = [[0.1] * 768]
        mock_sparse.return_value = [MagicMock(indices=[1], values=[0.5])]

        doc_record, job = await ingest_text(
            text="# Guide\n\nGenerated source content.",
            filename="01_GUIDE.md",
            source="intodoc:test",
            project_id="proj",
            metadata={},
            db=mock_db,
            qdrant=mock_qdrant,
        )

    assert doc_record.document_role == "source"
    assert job.status == "completed"
    chunk_metadatas = mock_upsert.await_args.kwargs["chunk_metadatas"]
    assert chunk_metadatas[0]["document_role"] == "source"


async def test_ingest_text_stores_explicit_document_role(mock_db, mock_qdrant):
    with (
        patch("src.ingestion.pipeline.embed_texts", new_callable=AsyncMock) as mock_embed,
        patch("src.ingestion.pipeline.ensure_collection", new_callable=AsyncMock),
        patch("src.ingestion.pipeline.upsert_chunks", new_callable=AsyncMock) as mock_upsert,
        patch("src.ingestion.pipeline.settings") as mock_settings,
    ):
        mock_settings.hybrid_search_enabled = False
        mock_embed.return_value = [[0.1] * 768]

        doc_record, job = await ingest_text(
            text="# Handleiding\n\nUse this as guidance.",
            filename="handleiding.md",
            source="manual",
            project_id="proj",
            metadata={"document_role": "guidance"},
            db=mock_db,
            qdrant=mock_qdrant,
        )

    assert doc_record.document_role == "guidance"
    assert job.status == "completed"
    chunk_metadatas = mock_upsert.await_args.kwargs["chunk_metadatas"]
    assert chunk_metadatas[0]["document_role"] == "guidance"


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
