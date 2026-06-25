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
        mock_settings.system_knowledge_injection = False
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
    assert mock_db.add.call_count >= 1  # QueryUsage + QueryLog


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
    assert mock_db.add.call_count >= 1  # QueryUsage + QueryLog


async def test_rag_query_greeting_goes_through_search(mock_qdrant, mock_db):
    """Greetings now go through search (returns 0 results) then to no-results LLM fallback."""
    request = QueryRequest(query="hello")

    search_analysis = QueryAnalysis(
        action=Action.SEARCH,
        query_type=QueryType.QA,
        cleaned_query="hello",
    )

    with (
        patch("src.generation.rag_chain.analyze_query_async", new_callable=AsyncMock, return_value=search_analysis),
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

    # Search WAS called (source evidence first, then bounded auxiliary context)
    assert mock_search.await_count == 2
    assert mock_search.await_args_list[0].kwargs["document_roles"] == ["source"]
    assert mock_search.await_args_list[1].kwargs["document_roles"] == [
        "reference", "guidance", "standard", "policy",
    ]
    # LLM was called with no-results prompt
    mock_gen.assert_called_once()
    assert len(response.sources) == 0
    assert response.answer == "Hello! How can I help you find information today?"


async def test_rag_query_template_intent_retrieves_template_context(mock_qdrant, mock_db):
    request = QueryRequest(query="Create a report outline using the template")

    search_analysis = QueryAnalysis(
        action=Action.SEARCH,
        query_type=QueryType.QA,
        cleaned_query="Create a report outline using the template",
    )
    template_source = Source(
        document_id=9,
        filename="report-template.docx",
        chunk_index=0,
        score=0.8,
        text="Template sections: Summary, Evidence, Risks.",
        document_role="template",
    )

    with (
        patch("src.generation.rag_chain.analyze_query_async", new_callable=AsyncMock, return_value=search_analysis),
        patch("src.generation.rag_chain.search_similar", new_callable=AsyncMock) as mock_search,
        patch("src.generation.rag_chain.generate_answer", new_callable=AsyncMock) as mock_gen,
    ):
        mock_search.side_effect = [[], [], [template_source]]
        mock_gen.return_value = "Use this outline structure."

        response = await run_rag_query(
            request=request,
            user_groups=["engineering"],
            user_id=1,
            qdrant=mock_qdrant,
            db=mock_db,
        )

    assert mock_search.await_count == 3
    assert mock_search.await_args_list[2].kwargs["document_roles"] == ["template"]
    assert len(response.sources) == 1
    assert response.sources[0].document_role == "template"
