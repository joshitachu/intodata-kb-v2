from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.generation.rag_chain import _execute_deep_layer_search, run_rag_query
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
    """Disable hybrid search, reranking, and query expansion for unit tests.

    Deep-layer escalation is off here for the same reason: these cases assert
    rung 1 in isolation, and rung 2 would otherwise fire on every one of them
    (their fixtures return fewer than ``deep_layer_min_source_hits`` chunks).
    ``TestDeepLayerEscalation`` covers it deliberately instead.
    """
    with patch("src.generation.rag_chain.settings") as mock_settings:
        mock_settings.query_expansion_enabled = False
        mock_settings.reranker_enabled = False
        mock_settings.hybrid_search_enabled = False
        mock_settings.system_knowledge_injection = False
        mock_settings.deep_layer_escalation_enabled = False
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


async def test_rag_query_does_not_keyword_select_template_context(mock_qdrant, mock_db):
    request = QueryRequest(query="Create a report outline using the template")

    with (
        patch("src.generation.rag_chain.search_similar", new_callable=AsyncMock) as mock_search,
        patch("src.generation.rag_chain.generate_answer", new_callable=AsyncMock) as mock_gen,
    ):
        mock_search.side_effect = [[], []]
        mock_gen.return_value = "Use this outline structure."

        response = await run_rag_query(
            request=request,
            user_groups=["engineering"],
            user_id=1,
            qdrant=mock_qdrant,
            db=mock_db,
        )

    assert mock_search.await_count == 2
    assert all(
        call.kwargs["document_roles"] != ["template"]
        for call in mock_search.await_args_list
    )
    assert response.sources == []


class TestDeepLayerEscalation:
    """Rung 2 exists so the internal partitions are reachable at all.

    ``search_layered`` shipped with five tuning settings and no production
    caller, so every internal chunk -- the per-group Phase 2 analyses and the
    deterministic support maps, 88% of one measured corpus -- could not be
    retrieved by any question, whatever its wording.
    """

    @pytest.fixture(autouse=True)
    def enable_escalation(self, disable_new_features):
        settings = disable_new_features
        settings.deep_layer_escalation_enabled = True
        settings.deep_layer_source_layers = ["technical_analysis", "support_maps"]
        settings.deep_layer_min_source_hits = 3
        settings.deep_layer_top_k = 6
        return settings

    async def test_thin_rung1_escalates_to_the_internal_layers(self) -> None:
        request = QueryRequest(query="What does tSendMail do?")
        with patch(
            "src.generation.rag_chain.search_layered", new_callable=AsyncMock
        ) as mock_deep:
            mock_deep.return_value = []
            await _execute_deep_layer_search(
                "What does tSendMail do?",
                request,
                ["engineering"],
                AsyncMock(),
                ["project-a"],
                rung1_hits=0,
            )

        assert mock_deep.await_count == 1
        assert mock_deep.await_args.kwargs["source_layers"] == [
            "technical_analysis",
            "support_maps",
        ]

    async def test_a_healthy_rung1_never_pays_for_rung2(self) -> None:
        """`search_layered` forces an LLM query expansion, so the ordinary
        question that rung 1 already answered must not trigger it."""
        request = QueryRequest(query="What does tSendMail do?")
        with patch(
            "src.generation.rag_chain.search_layered", new_callable=AsyncMock
        ) as mock_deep:
            result = await _execute_deep_layer_search(
                "What does tSendMail do?",
                request,
                ["engineering"],
                AsyncMock(),
                ["project-a"],
                rung1_hits=7,
            )

        assert mock_deep.await_count == 0
        assert result == []

    async def test_a_rung2_failure_degrades_instead_of_failing_the_turn(self) -> None:
        request = QueryRequest(query="What does tSendMail do?")
        with patch(
            "src.generation.rag_chain.search_layered", new_callable=AsyncMock
        ) as mock_deep:
            mock_deep.side_effect = RuntimeError("qdrant unavailable")
            result = await _execute_deep_layer_search(
                "What does tSendMail do?",
                request,
                ["engineering"],
                AsyncMock(),
                ["project-a"],
                rung1_hits=0,
            )

        assert result == []
