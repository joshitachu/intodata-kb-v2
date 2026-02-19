from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.retrieval.search import (
    merge_sources_by_document,
    search_similar,
    search_similar_hybrid,
    _process_results,
)
from src.schemas import Source


def _make_point(score: float, payload: dict):
    point = MagicMock()
    point.score = score
    point.payload = payload
    return point


@pytest.fixture
def mock_qdrant():
    return AsyncMock()


async def test_search_returns_sources_above_threshold(mock_qdrant):
    result = MagicMock()
    result.points = [
        _make_point(0.95, {
            "document_id": 1,
            "filename": "doc.pdf",
            "chunk_index": 0,
            "text": "Relevant text",
        }),
        _make_point(0.5, {
            "document_id": 2,
            "filename": "other.pdf",
            "chunk_index": 1,
            "text": "Irrelevant text",
        }),
    ]
    mock_qdrant.query_points.return_value = result

    with patch("src.retrieval.search.embed_text", new_callable=AsyncMock) as mock_embed:
        mock_embed.return_value = [0.1] * 768
        sources = await search_similar(mock_qdrant, "test query", ["engineering"])

    assert len(sources) == 2
    assert sources[0].document_id == 1
    assert sources[0].score == 0.95
    assert sources[1].document_id == 2
    assert sources[1].score == 0.5


async def test_search_no_results_below_threshold(mock_qdrant):
    result = MagicMock()
    result.points = [
        _make_point(0.15, {
            "document_id": 1,
            "filename": "doc.pdf",
            "chunk_index": 0,
            "text": "Low relevance",
        }),
    ]
    mock_qdrant.query_points.return_value = result

    with patch("src.retrieval.search.embed_text", new_callable=AsyncMock) as mock_embed:
        mock_embed.return_value = [0.1] * 768
        sources = await search_similar(mock_qdrant, "test query", ["engineering"])

    assert len(sources) == 0


async def test_search_with_empty_user_groups(mock_qdrant):
    result = MagicMock()
    result.points = []
    mock_qdrant.query_points.return_value = result

    with patch("src.retrieval.search.embed_text", new_callable=AsyncMock) as mock_embed:
        mock_embed.return_value = [0.1] * 768
        sources = await search_similar(mock_qdrant, "test query", [])

    # Should call query_points with query_filter=None
    call_kwargs = mock_qdrant.query_points.call_args.kwargs
    assert call_kwargs["query_filter"] is None


async def test_search_custom_top_k_and_threshold(mock_qdrant):
    result = MagicMock()
    result.points = []
    mock_qdrant.query_points.return_value = result

    with patch("src.retrieval.search.embed_text", new_callable=AsyncMock) as mock_embed:
        mock_embed.return_value = [0.1] * 768
        await search_similar(mock_qdrant, "test", ["eng"], top_k=10, threshold=0.5)

    call_kwargs = mock_qdrant.query_points.call_args.kwargs
    assert call_kwargs["limit"] == 10


# --- Tests for search_similar_hybrid ---


async def test_hybrid_search_calls_both_embeddings(mock_qdrant):
    """Hybrid search generates both dense and sparse embeddings."""
    result = MagicMock()
    result.points = [
        _make_point(0.8, {
            "document_id": 1,
            "filename": "doc.pdf",
            "chunk_index": 0,
            "text": "Hybrid result",
        }),
    ]
    mock_qdrant.query_points.return_value = result

    with (
        patch("src.retrieval.search.embed_text", new_callable=AsyncMock) as mock_dense,
        patch("src.retrieval.search.embed_text_sparse") as mock_sparse,
    ):
        mock_dense.return_value = [0.1] * 768
        mock_sparse_vec = MagicMock()
        mock_sparse_vec.indices = [1, 5, 10]
        mock_sparse_vec.values = [0.5, 0.3, 0.8]
        mock_sparse.return_value = mock_sparse_vec

        sources = await search_similar_hybrid(mock_qdrant, "test query", ["eng"])

    assert len(sources) == 1
    assert sources[0].filename == "doc.pdf"
    mock_dense.assert_called_once()
    mock_sparse.assert_called_once()


async def test_hybrid_search_uses_prefetch(mock_qdrant):
    """Hybrid search passes prefetch parameter to Qdrant."""
    result = MagicMock()
    result.points = []
    mock_qdrant.query_points.return_value = result

    with (
        patch("src.retrieval.search.embed_text", new_callable=AsyncMock) as mock_dense,
        patch("src.retrieval.search.embed_text_sparse") as mock_sparse,
    ):
        mock_dense.return_value = [0.1] * 768
        mock_sparse_vec = MagicMock()
        mock_sparse_vec.indices = [1]
        mock_sparse_vec.values = [0.5]
        mock_sparse.return_value = mock_sparse_vec

        await search_similar_hybrid(mock_qdrant, "test", [], prefetch_limit=30)

    call_kwargs = mock_qdrant.query_points.call_args.kwargs
    assert "prefetch" in call_kwargs
    assert len(call_kwargs["prefetch"]) == 2  # dense + sparse


async def test_hybrid_search_lower_default_threshold(mock_qdrant):
    """Hybrid search has a lower default threshold (0.25 vs 0.4 for dense)."""
    result = MagicMock()
    result.points = [
        _make_point(0.3, {
            "document_id": 1,
            "filename": "doc.pdf",
            "chunk_index": 0,
            "text": "RRF result",
        }),
    ]
    mock_qdrant.query_points.return_value = result

    with (
        patch("src.retrieval.search.embed_text", new_callable=AsyncMock) as mock_dense,
        patch("src.retrieval.search.embed_text_sparse") as mock_sparse,
    ):
        mock_dense.return_value = [0.1] * 768
        mock_sparse_vec = MagicMock()
        mock_sparse_vec.indices = [1]
        mock_sparse_vec.values = [0.5]
        mock_sparse.return_value = mock_sparse_vec

        # Default threshold is 0.25, so 0.3 should pass
        sources = await search_similar_hybrid(mock_qdrant, "test", [])

    assert len(sources) == 1


# --- Tests for _process_results ---


def test_process_results_filters_by_threshold():
    points = [
        _make_point(0.9, {"document_id": 1, "filename": "a.pdf", "chunk_index": 0, "text": "Hi"}),
        _make_point(0.1, {"document_id": 2, "filename": "b.pdf", "chunk_index": 0, "text": "Lo"}),
    ]
    sources = _process_results(points, threshold=0.5)
    assert len(sources) == 1
    assert sources[0].document_id == 1


def test_process_results_empty():
    sources = _process_results([], threshold=0.5)
    assert sources == []


# --- Tests for merge_sources_by_document ---

def test_merge_sources_single_chunk():
    sources = [
        Source(document_id=1, filename="doc.pdf", chunk_index=0, score=0.9, text="Hello"),
    ]
    merged = merge_sources_by_document(sources)
    assert len(merged) == 1
    assert merged[0].document_id == 1
    assert merged[0].filename == "doc.pdf"
    assert merged[0].score == 0.9
    assert merged[0].text == "Hello"
    assert merged[0].reference_number == 1


def test_merge_sources_multiple_chunks_same_doc():
    sources = [
        Source(document_id=1, filename="doc.pdf", chunk_index=2, score=0.8, text="Part C"),
        Source(document_id=1, filename="doc.pdf", chunk_index=0, score=0.95, text="Part A"),
        Source(document_id=1, filename="doc.pdf", chunk_index=1, score=0.7, text="Part B"),
    ]
    merged = merge_sources_by_document(sources)
    assert len(merged) == 1
    assert merged[0].score == 0.95  # highest score
    # Text should be sorted by chunk_index: A, B, C
    assert merged[0].text == "Part A\n\nPart B\n\nPart C"
    assert merged[0].reference_number == 1


def test_merge_sources_multiple_documents_default():
    """Default max_documents=1 returns only the top-scoring document."""
    sources = [
        Source(document_id=1, filename="a.pdf", chunk_index=0, score=0.9, text="Doc A"),
        Source(document_id=2, filename="b.pdf", chunk_index=0, score=0.95, text="Doc B"),
        Source(document_id=3, filename="c.pdf", chunk_index=0, score=0.7, text="Doc C"),
    ]
    merged = merge_sources_by_document(sources)
    assert len(merged) == 1
    assert merged[0].filename == "b.pdf"
    assert merged[0].reference_number == 1


def test_merge_sources_multiple_documents_max_3():
    """max_documents=3 returns all documents with correct reference numbers."""
    sources = [
        Source(document_id=1, filename="a.pdf", chunk_index=0, score=0.9, text="Doc A"),
        Source(document_id=2, filename="b.pdf", chunk_index=0, score=0.95, text="Doc B"),
        Source(document_id=3, filename="c.pdf", chunk_index=0, score=0.7, text="Doc C"),
    ]
    merged = merge_sources_by_document(sources, max_documents=3)
    assert len(merged) == 3
    # Sorted by score descending
    assert merged[0].filename == "b.pdf"
    assert merged[1].filename == "a.pdf"
    assert merged[2].filename == "c.pdf"
    # Reference numbers assigned after sorting
    assert merged[0].reference_number == 1
    assert merged[1].reference_number == 2
    assert merged[2].reference_number == 3


def test_merge_sources_max_documents_limits():
    """max_documents=2 returns only top 2 out of 3."""
    sources = [
        Source(document_id=1, filename="a.pdf", chunk_index=0, score=0.9, text="Doc A"),
        Source(document_id=2, filename="b.pdf", chunk_index=0, score=0.95, text="Doc B"),
        Source(document_id=3, filename="c.pdf", chunk_index=0, score=0.7, text="Doc C"),
    ]
    merged = merge_sources_by_document(sources, max_documents=2)
    assert len(merged) == 2
    assert merged[0].filename == "b.pdf"
    assert merged[1].filename == "a.pdf"


def test_merge_sources_empty():
    merged = merge_sources_by_document([])
    assert merged == []
