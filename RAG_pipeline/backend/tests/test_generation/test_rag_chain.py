from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.generation.intent_classifier import Action, QueryAnalysis, QueryType
from src.generation.rag_chain import run_rag_query
from src.schemas import DocumentSource, QueryRequest, Source


@pytest.fixture
def mock_qdrant():
    return AsyncMock()


@pytest.fixture
def mock_db():
    db = AsyncMock()
    db.add = MagicMock()
    return db


@pytest.fixture(autouse=True)
def disable_new_features():
    """Disable hybrid search, reranking, and query expansion for unit tests."""
    with patch("src.generation.rag_chain.settings") as mock_settings:
        mock_settings.query_expansion_enabled = False
        mock_settings.reranker_enabled = False
        mock_settings.hybrid_search_enabled = False
        yield mock_settings


def _make_sources():
    return [
        Source(
            document_id=1,
            filename="doc.pdf",
            chunk_index=0,
            score=0.92,
            text="Some relevant content.",
        ),
    ]


async def test_rag_query_with_sources(mock_qdrant, mock_db):
    request = QueryRequest(query="What is X?")

    with (
        patch("src.generation.rag_chain.search_similar", new_callable=AsyncMock) as mock_search,
        patch("src.generation.rag_chain.generate_answer", new_callable=AsyncMock) as mock_gen,
    ):
        mock_search.return_value = _make_sources()
        mock_gen.return_value = "X is a concept. [1]"

        response = await run_rag_query(
            request=request,
            user_groups=["engineering"],
            user_id=1,
            qdrant=mock_qdrant,
            db=mock_db,
        )

    assert "X is a concept" in response.answer
    assert len(response.sources) == 1
    # Sources are now DocumentSource (document-level, merged)
    assert isinstance(response.sources[0], DocumentSource)
    assert response.sources[0].reference_number == 1
    assert response.sources[0].filename == "doc.pdf"
    assert response.latency_ms >= 0
    # Verify query was logged
    mock_db.add.assert_called_once()


async def test_rag_query_no_sources_calls_llm_fallback(mock_qdrant, mock_db):
    """When search returns no results, LLM generates a helpful response instead of hardcoded string."""
    request = QueryRequest(query="What is the company refund policy?")

    with (
        patch("src.generation.rag_chain.search_similar", new_callable=AsyncMock) as mock_search,
        patch("src.generation.rag_chain.generate_answer", new_callable=AsyncMock) as mock_gen,
    ):
        mock_search.return_value = []
        mock_gen.return_value = "I couldn't find information about the refund policy. Could you provide more details?"

        response = await run_rag_query(
            request=request,
            user_groups=["engineering"],
            user_id=1,
            qdrant=mock_qdrant,
            db=mock_db,
        )

    assert len(response.sources) == 0
    # LLM was called (not hardcoded)
    mock_gen.assert_called_once()
    # The no-results prompt should be in the messages
    call_messages = mock_gen.call_args[0][0]
    assert "no relevant documents" in call_messages[0]["content"].lower()
    mock_db.add.assert_called_once()


async def test_rag_query_greeting_goes_through_search(mock_qdrant, mock_db):
    """Greetings now go through search (returns 0 results) then to no-results LLM fallback."""
    request = QueryRequest(query="hello")

    with (
        patch("src.generation.rag_chain.search_similar", new_callable=AsyncMock) as mock_search,
        patch("src.generation.rag_chain.generate_answer", new_callable=AsyncMock) as mock_gen,
    ):
        # Search returns no results for "hello"
        mock_search.return_value = []
        mock_gen.return_value = "Hello! How can I help you find information today?"

        response = await run_rag_query(
            request=request,
            user_groups=["engineering"],
            user_id=1,
            qdrant=mock_qdrant,
            db=mock_db,
        )

    # Search WAS called (greetings now go through search)
    mock_search.assert_called_once()
    # LLM was called with no-results prompt
    mock_gen.assert_called_once()
    assert len(response.sources) == 0
    assert response.answer == "Hello! How can I help you find information today?"
