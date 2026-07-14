import asyncio
import datetime
import json
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.db.models import Conversation, ConversationMessage
from src.generation.agent_loop import RuntimeLoopOutcome
from src.generation.intent_classifier import Action, QueryAnalysis, QueryType
from src.generation.conversational_rag import (
    _NEED_SEARCH_MARKER,
    _SNIFF_BUFFER_CHARS,
    _drain_stripping_late_marker,
    _ends_with_partial_marker,
    _prepare_rag_context,
    _sniff_stream_sufficiency,
    _structured_answer_payload,
    build_context,
    build_context_from_document_sources,
    build_conversational_messages,
    build_conversational_messages_with_context,
    get_previous_sources,
    needs_new_search,
    run_conversational_rag,
    run_conversational_rag_stream,
    truncate_history,
)
from src.schemas import DocumentSource, Source


def _no_tool_support_loop() -> AsyncMock:
    """Patch target for run_runtime_tool_loop in pure RAG-routing tests: simulates a
    provider with no structured tool support, so QA turns deterministically fall back
    to the prepared document answer (with the unavailable note appended)."""
    return AsyncMock(
        return_value=RuntimeLoopOutcome(
            final_answer=None, used_runtime_data=False, progress_events=[], usage=None
        )
    )


def _mock_message(role: str, content: str, sources: list | None = None) -> ConversationMessage:
    msg = MagicMock(spec=ConversationMessage)
    msg.role = role
    msg.content = content
    msg.sources = sources
    return msg


class TestGetPreviousSources:
    def test_returns_sources_from_last_assistant(self) -> None:
        messages = [
            _mock_message("user", "What is X?"),
            _mock_message("assistant", "X is ...", sources=[
                {"document_id": 1, "filename": "doc.pdf", "score": 0.9, "text": "content", "reference_number": 1},
            ]),
        ]
        result = get_previous_sources(messages)
        assert len(result) == 1
        assert result[0].document_id == 1
        assert result[0].filename == "doc.pdf"

    def test_returns_empty_when_no_sources(self) -> None:
        messages = [
            _mock_message("user", "hello"),
            _mock_message("assistant", "hi there"),
        ]
        result = get_previous_sources(messages)
        assert result == []

    def test_skips_assistant_without_sources(self) -> None:
        messages = [
            _mock_message("user", "What is X?"),
            _mock_message("assistant", "X is ...", sources=[
                {"document_id": 1, "filename": "doc.pdf", "score": 0.9, "text": "content", "reference_number": 1},
            ]),
            _mock_message("user", "thanks"),
            _mock_message("assistant", "You're welcome!", sources=None),
        ]
        result = get_previous_sources(messages)
        assert len(result) == 1
        assert result[0].document_id == 1

    def test_returns_empty_for_no_messages(self) -> None:
        result = get_previous_sources([])
        assert result == []


class TestNeedsNewSearch:
    def test_detects_marker(self) -> None:
        assert needs_new_search("[NEED_SEARCH]") is True

    def test_detects_marker_in_longer_text(self) -> None:
        assert needs_new_search("I cannot answer that. [NEED_SEARCH]") is True

    def test_returns_false_for_normal_answer(self) -> None:
        assert needs_new_search("The vacation policy allows 20 days per year.") is False

    def test_returns_false_for_empty_string(self) -> None:
        assert needs_new_search("") is False


class TestBuildContextFromDocumentSources:
    def test_single_source(self) -> None:
        sources = [
            DocumentSource(document_id=1, filename="doc.pdf", score=0.9, text="Test content", reference_number=1),
        ]
        result = build_context_from_document_sources(sources)
        assert "[Reference 1: doc.pdf]" in result
        assert "Test content" in result

    def test_multiple_sources(self) -> None:
        sources = [
            DocumentSource(document_id=1, filename="a.pdf", score=0.9, text="First", reference_number=1),
            DocumentSource(document_id=2, filename="b.pdf", score=0.8, text="Second", reference_number=2),
        ]
        result = build_context_from_document_sources(sources)
        assert "[Reference 1: a.pdf]" in result
        assert "[Reference 2: b.pdf]" in result

    def test_empty_sources(self) -> None:
        result = build_context_from_document_sources([])
        assert result == ""

    def test_respects_token_budget(self) -> None:
        sources = [
            DocumentSource(document_id=i, filename=f"doc{i}.pdf", score=0.9, text="X" * 1000, reference_number=i)
            for i in range(10)
        ]
        result = build_context_from_document_sources(sources, max_tokens=500)
        assert result.count("Reference") < 10


class TestBuildConversationalMessagesWithContext:
    def test_includes_context(self) -> None:
        result = build_conversational_messages_with_context(
            "follow up question",
            "Some context from previous sources",
            [],
        )
        assert result[0]["role"] == "system"
        assert "knowledge-base assistant" in result[0]["content"]
        context_msgs = [m for m in result if "Retrieved context" in m.get("content", "")]
        assert len(context_msgs) == 1
        assert "Some context from previous sources" in context_msgs[0]["content"]
        assert result[-1]["content"] == "follow up question"

    def test_no_context_message_when_empty(self) -> None:
        result = build_conversational_messages_with_context("query", "", [])
        context_msgs = [m for m in result if "Retrieved context" in m.get("content", "")]
        assert len(context_msgs) == 0


class TestTruncateHistory:
    def test_empty_history(self) -> None:
        result = truncate_history([])
        assert result == []

    def test_few_messages_not_truncated(self) -> None:
        messages = [
            _mock_message("user", "Hello"),
            _mock_message("assistant", "Hi there!"),
        ]
        result = truncate_history(messages, max_tokens=1000)
        assert len(result) == 2
        assert result[0]["role"] == "user"
        assert result[0]["content"] == "Hello"

    def test_respects_max_messages(self) -> None:
        messages = [_mock_message("user", f"Message {i}") for i in range(20)]
        result = truncate_history(messages, max_messages=5, max_tokens=10000)
        assert len(result) <= 5

    def test_preserves_chronological_order(self) -> None:
        messages = [
            _mock_message("user", "First"),
            _mock_message("assistant", "Second"),
            _mock_message("user", "Third"),
        ]
        result = truncate_history(messages, max_tokens=10000)
        contents = [m["content"] for m in result]
        assert contents == ["First", "Second", "Third"]

    def test_truncates_when_over_token_limit(self) -> None:
        messages = [
            _mock_message("user", "A" * 500),
            _mock_message("assistant", "B" * 500),
            _mock_message("user", "C" * 500),
            _mock_message("assistant", "D" * 500),
        ]
        result = truncate_history(messages, max_tokens=300)
        assert len(result) < 4

    def test_excludes_failed_assistant_messages(self) -> None:
        failed = _mock_message("assistant", "")
        failed.status = "failed"
        failed.error_message = "Provider timed out"
        messages = [
            _mock_message("user", "What happened?"),
            failed,
            _mock_message("user", "Try again"),
        ]

        result = truncate_history(messages, max_tokens=10000)

        assert [message["content"] for message in result] == [
            "What happened?",
            "Try again",
        ]


class TestBuildContext:
    def test_empty_sources(self) -> None:
        result = build_context([])
        assert result == ""

    def test_single_source(self) -> None:
        sources = [
            Source(document_id=1, filename="doc.pdf", chunk_index=0, score=0.9, text="Test content"),
        ]
        result = build_context(sources)
        assert "[Source: doc.pdf, chunk 0]" in result
        assert "Test content" in result

    def test_single_source_with_reference_numbers(self) -> None:
        sources = [
            Source(document_id=1, filename="doc.pdf", chunk_index=0, score=0.9, text="Test content"),
        ]
        result = build_context(sources, reference_numbers={1: 1})
        assert "[Reference 1: doc.pdf]" in result
        assert "[Source:" not in result
        assert "Test content" in result

    def test_multiple_sources(self) -> None:
        sources = [
            Source(document_id=1, filename="a.pdf", chunk_index=0, score=0.9, text="First"),
            Source(document_id=2, filename="b.pdf", chunk_index=1, score=0.8, text="Second"),
        ]
        result = build_context(sources)
        assert "a.pdf" in result
        assert "b.pdf" in result
        assert "First" in result
        assert "Second" in result

    def test_truncates_at_token_limit(self) -> None:
        sources = [
            Source(document_id=i, filename=f"doc{i}.pdf", chunk_index=0, score=0.9, text="X" * 1000)
            for i in range(10)
        ]
        result = build_context(sources, max_tokens=500)
        assert result.count("doc") < 10


class TestBuildConversationalMessages:
    def test_basic_structure(self) -> None:
        sources = [
            Source(document_id=1, filename="doc.pdf", chunk_index=0, score=0.9, text="Info"),
        ]
        history = []
        result = build_conversational_messages("What is this?", sources, history)

        assert len(result) >= 3
        assert result[0]["role"] == "system"
        assert "knowledge-base assistant" in result[0]["content"]
        assert result[-1]["role"] == "user"
        assert result[-1]["content"] == "What is this?"

    def test_includes_history(self) -> None:
        sources = []
        history = [
            _mock_message("user", "Previous question"),
            _mock_message("assistant", "Previous answer"),
        ]
        result = build_conversational_messages("Follow up", sources, history)

        contents = [m["content"] for m in result]
        assert "Previous question" in contents
        assert "Previous answer" in contents

    def test_includes_context_from_sources(self) -> None:
        sources = [
            Source(document_id=1, filename="report.pdf", chunk_index=0, score=0.9, text="Revenue data"),
        ]
        history = []
        result = build_conversational_messages("Query", sources, history)

        context_messages = [m for m in result if "Retrieved context" in m.get("content", "")]
        assert len(context_messages) == 1
        assert "report.pdf" in context_messages[0]["content"]

    def test_empty_sources_no_context_message(self) -> None:
        sources = []
        history = []
        result = build_conversational_messages("Query", sources, history)

        context_messages = [m for m in result if "Retrieved context" in m.get("content", "")]
        assert len(context_messages) == 0

    def test_message_order(self) -> None:
        sources = [
            Source(document_id=1, filename="doc.pdf", chunk_index=0, score=0.9, text="Info"),
        ]
        history = [
            _mock_message("user", "Old question"),
            _mock_message("assistant", "Old answer"),
        ]
        result = build_conversational_messages("New question", sources, history)

        roles = [m["role"] for m in result]
        assert roles[0] == "system"
        assert roles[1] == "system"
        assert roles[-1] == "user"
        assert "New question" == result[-1]["content"]


class TestConversationalRagIntentRouting:
    """Tests for intent routing in run_conversational_rag."""

    @pytest.fixture(autouse=True)
    def disable_new_features(self):
        """Disable hybrid search, reranking, and query expansion for unit tests."""
        with patch("src.generation.conversational_rag.settings") as mock_settings:
            mock_settings.query_expansion_enabled = False
            mock_settings.reranker_enabled = False
            mock_settings.hybrid_search_enabled = False
            mock_settings.system_knowledge_injection = False
            yield mock_settings

    @pytest.fixture
    def mock_conversation(self):
        conv = MagicMock(spec=Conversation)
        conv.id = 1
        conv.messages = []
        conv.title = None
        return conv

    @pytest.fixture
    def mock_qdrant(self):
        return AsyncMock()

    @pytest.fixture
    def mock_db(self):
        db = AsyncMock()
        added_objects: list = []

        def track_add(obj):
            added_objects.append(obj)

        async def fake_flush():
            for obj in added_objects:
                if not hasattr(obj, "id") or obj.id is None:
                    obj.id = 1
                if not hasattr(obj, "created_at") or obj.created_at is None:
                    obj.created_at = datetime.datetime(2025, 1, 1, tzinfo=datetime.timezone.utc)

        db.add = MagicMock(side_effect=track_add)
        db.flush = AsyncMock(side_effect=fake_flush)
        return db

    async def test_greeting_goes_through_search(self, mock_conversation, mock_qdrant, mock_db):
        """Greetings now go through search (returns 0 results) then to no-results LLM fallback."""
        search_analysis = QueryAnalysis(
            action=Action.SEARCH, query_type=QueryType.QA,
            cleaned_query="hello",
        )
        with (
            patch("src.generation.conversational_rag.analyze_query_async", new_callable=AsyncMock, return_value=search_analysis),
            patch("src.generation.conversational_rag.search_similar", new_callable=AsyncMock) as mock_search,
            patch("src.generation.conversational_rag.search_layered", new_callable=AsyncMock) as mock_layered,
            patch("src.generation.conversational_rag.generate_answer", new_callable=AsyncMock) as mock_gen,
        ):
            # Search returns no results for "hello"
            mock_search.return_value = []
            mock_layered.return_value = []
            mock_gen.return_value = "Hello! How can I help you?"

            response = await run_conversational_rag(
                conversation=mock_conversation,
                query="hello",
                user_groups=["engineering"],
                qdrant=mock_qdrant,
                db=mock_db,
            )

        # Search WAS called (source evidence first, then bounded auxiliary context)
        assert mock_search.await_count == 2
        assert mock_search.await_args_list[0].kwargs["document_roles"] == ["source"]
        assert mock_search.await_args_list[1].kwargs["document_roles"] == [
            "reference", "guidance", "standard", "policy",
        ]
        mock_gen.assert_called_once()
        assert response.content == "Hello! How can I help you?"

    async def test_no_results_calls_llm_fallback(self, mock_conversation, mock_qdrant, mock_db):
        """When search returns no results, LLM generates a helpful response."""
        with (
            patch("src.generation.conversational_rag.search_similar", new_callable=AsyncMock) as mock_search,
            patch("src.generation.conversational_rag.search_layered", new_callable=AsyncMock) as mock_layered,
            patch("src.generation.conversational_rag.generate_answer", new_callable=AsyncMock) as mock_gen,
        ):
            mock_search.return_value = []
            mock_layered.return_value = []
            mock_gen.return_value = "Could you provide more details about what you're looking for?"

            await run_conversational_rag(
                conversation=mock_conversation,
                query="What about the thing?",
                user_groups=["engineering"],
                qdrant=mock_qdrant,
                db=mock_db,
            )

        assert mock_search.await_count == 2
        assert mock_search.await_args_list[0].kwargs["document_roles"] == ["source"]
        assert mock_search.await_args_list[1].kwargs["document_roles"] == [
            "reference", "guidance", "standard", "policy",
        ]
        mock_gen.assert_called_once()
        call_messages = mock_gen.call_args[0][0]
        assert "no relevant documents" in call_messages[0]["content"].lower()


class TestContextFirstFlow:
    """Tests for the context-first QA flow."""

    @pytest.fixture(autouse=True)
    def disable_new_features(self):
        """Disable hybrid search, reranking, and query expansion for unit tests."""
        with patch("src.generation.conversational_rag.settings") as mock_settings:
            mock_settings.query_expansion_enabled = False
            mock_settings.reranker_enabled = False
            mock_settings.hybrid_search_enabled = False
            mock_settings.system_knowledge_injection = False
            yield mock_settings

    @pytest.fixture
    def mock_conversation_with_sources(self):
        """Conversation with previous sources from an assistant message."""
        conv = MagicMock(spec=Conversation)
        conv.id = 1
        conv.title = "Test conversation"
        source_data = [
            {"document_id": 1, "filename": "hr_policy.pdf", "score": 0.9, "text": "Vacation policy: 20 days per year.", "reference_number": 1},
        ]
        conv.messages = [
            _mock_message("user", "What is the vacation policy?"),
            _mock_message("assistant", "The vacation policy allows 20 days per year [1].", sources=source_data),
        ]
        return conv

    @pytest.fixture
    def mock_conversation_no_sources(self):
        """Conversation with no previous sources (e.g., after chitchat)."""
        conv = MagicMock(spec=Conversation)
        conv.id = 2
        conv.title = None
        conv.messages = []
        return conv

    @pytest.fixture
    def mock_qdrant(self):
        return AsyncMock()

    @pytest.fixture
    def mock_db(self):
        db = AsyncMock()
        added_objects: list = []

        def track_add(obj):
            added_objects.append(obj)

        async def fake_flush():
            for obj in added_objects:
                if not hasattr(obj, "id") or obj.id is None:
                    obj.id = 1
                if not hasattr(obj, "created_at") or obj.created_at is None:
                    obj.created_at = datetime.datetime(2025, 1, 1, tzinfo=datetime.timezone.utc)

        db.add = MagicMock(side_effect=track_add)
        db.flush = AsyncMock(side_effect=fake_flush)
        return db

    async def test_qa_with_previous_sources_no_search(
        self, mock_conversation_with_sources, mock_qdrant, mock_db
    ):
        """When previous sources exist and LLM answers well, search is NOT called."""
        search_analysis = QueryAnalysis(
            action=Action.SEARCH, query_type=QueryType.QA,
            cleaned_query="How many vacation days do I get?",
        )
        with (
            patch("src.generation.conversational_rag.analyze_query_async", new_callable=AsyncMock, return_value=search_analysis),
            patch("src.generation.conversational_rag.search_similar", new_callable=AsyncMock) as mock_search,
            patch("src.generation.conversational_rag.run_runtime_tool_loop", _no_tool_support_loop()),
            patch("src.generation.conversational_rag.generate_answer", new_callable=AsyncMock) as mock_gen,
        ):
            mock_gen.return_value = "Yes, you get 20 vacation days per year [1]."

            response = await run_conversational_rag(
                conversation=mock_conversation_with_sources,
                query="How many vacation days do I get?",
                user_groups=["all"],
                qdrant=mock_qdrant,
                db=mock_db,
            )

        mock_search.assert_not_called()
        assert response.content.startswith("Yes, you get 20 vacation days per year [1].")
        assert response.sources is not None
        assert len(response.sources) == 1
        assert response.sources[0].filename == "hr_policy.pdf"

    async def test_qa_with_previous_sources_need_search(
        self, mock_conversation_with_sources, mock_qdrant, mock_db
    ):
        """When LLM responds [NEED_SEARCH], search IS called."""
        search_sources = [
            Source(document_id=2, filename="salary.pdf", chunk_index=0, score=0.85, text="Salary info here"),
        ]
        with (
            patch("src.generation.conversational_rag.search_similar", new_callable=AsyncMock) as mock_search,
            patch("src.generation.conversational_rag.run_runtime_tool_loop", _no_tool_support_loop()),
            patch("src.generation.conversational_rag.generate_answer", new_callable=AsyncMock) as mock_gen,
        ):
            # First call: context-first attempt returns NEED_SEARCH
            # Second call: after search, returns real answer
            mock_gen.side_effect = ["[NEED_SEARCH]", "The salary range is $50k-$100k [1]."]
            mock_search.return_value = search_sources

            response = await run_conversational_rag(
                conversation=mock_conversation_with_sources,
                query="What is the salary range?",
                user_groups=["all"],
                qdrant=mock_qdrant,
                db=mock_db,
            )

        assert mock_search.await_count == 2
        assert mock_search.await_args_list[0].kwargs["document_roles"] == ["source"]
        assert mock_search.await_args_list[1].kwargs["document_roles"] == [
            "reference", "guidance", "standard", "policy",
        ]
        assert mock_gen.call_count == 2
        assert response.content.startswith("The salary range is $50k-$100k [1].")
        assert response.sources is not None
        assert response.sources[0].filename == "salary.pdf"

    async def test_qa_without_previous_sources_searches_directly(
        self, mock_conversation_no_sources, mock_qdrant, mock_db
    ):
        """First message (no previous sources) triggers search directly."""
        search_sources = [
            Source(document_id=1, filename="hr.pdf", chunk_index=0, score=0.9, text="HR content"),
        ]
        search_analysis = QueryAnalysis(
            action=Action.SEARCH, query_type=QueryType.QA,
            cleaned_query="What is the HR policy?",
        )
        with (
            patch("src.generation.conversational_rag.analyze_query_async", new_callable=AsyncMock, return_value=search_analysis),
            patch("src.generation.conversational_rag.search_similar", new_callable=AsyncMock) as mock_search,
            patch("src.generation.conversational_rag.run_runtime_tool_loop", _no_tool_support_loop()),
            patch("src.generation.conversational_rag.generate_answer", new_callable=AsyncMock) as mock_gen,
        ):
            mock_search.return_value = search_sources
            mock_gen.return_value = "Here is the HR info [1]."

            response = await run_conversational_rag(
                conversation=mock_conversation_no_sources,
                query="What is the HR policy?",
                user_groups=["all"],
                qdrant=mock_qdrant,
                db=mock_db,
            )

        assert mock_search.await_count == 2
        assert mock_search.await_args_list[0].kwargs["document_roles"] == ["source"]
        assert mock_search.await_args_list[1].kwargs["document_roles"] == [
            "reference", "guidance", "standard", "policy",
        ]
        # Only 1 generate_answer call (the rung-1 precompute); the runtime loop uses
        # generate_answer_with_runtime_tools, which is separate and patched out here.
        mock_gen.assert_called_once()
        assert response.content.startswith("Here is the HR info [1].")

    async def test_need_search_marker_stripped_from_final_response(
        self, mock_conversation_no_sources, mock_qdrant, mock_db
    ):
        """[NEED_SEARCH] is never present in the response returned to the user."""
        with (
            patch("src.generation.conversational_rag.search_similar", new_callable=AsyncMock) as mock_search,
            patch("src.generation.conversational_rag.search_layered", new_callable=AsyncMock) as mock_layered,
            patch("src.generation.conversational_rag.generate_answer", new_callable=AsyncMock) as mock_gen,
        ):
            mock_search.return_value = []
            mock_layered.return_value = []
            # LLM accidentally includes [NEED_SEARCH] in a final answer
            mock_gen.return_value = "I don't know. [NEED_SEARCH]"

            response = await run_conversational_rag(
                conversation=mock_conversation_no_sources,
                query="What is the salary?",
                user_groups=["all"],
                qdrant=mock_qdrant,
                db=mock_db,
            )

        assert "[NEED_SEARCH]" not in response.content
        assert response.content == "I don't know."


class TestRetrievalLadderRouting:
    """Unit tests for rung-2 routing and identifier extraction."""

    def test_risk_queries_route_to_scorecard_first(self) -> None:
        from src.generation.conversational_rag import _route_internal_layers
        assert _route_internal_layers("What are the security risks?") == [
            "scorecard", "support_maps", "technical_analysis",
        ]

    def test_schema_queries_include_evidence_slices(self) -> None:
        from src.generation.conversational_rag import _route_internal_layers
        layers = _route_internal_layers("Which columns are in the target table?")
        assert layers == ["technical_analysis", "evidence_slices"]

    def test_config_queries_include_support_maps(self) -> None:
        from src.generation.conversational_rag import _route_internal_layers
        layers = _route_internal_layers("What is the connection string parameter?")
        assert layers == ["technical_analysis", "support_maps"]

    def test_generic_query_defaults_to_technical_analysis(self) -> None:
        from src.generation.conversational_rag import _route_internal_layers
        assert _route_internal_layers("what does the OffRamp job do") == ["technical_analysis"]

    def test_identifier_extraction(self) -> None:
        from src.generation.conversational_rag import _extract_query_identifiers
        idents = _extract_query_identifiers(
            "Where is tDBOutput_1 used and does SALESTABLE relate to the OffRamp flow in JSON?"
        )
        assert "tDBOutput_1" in idents
        assert "SALESTABLE" in idents
        assert "OffRamp" in idents
        assert "JSON" not in idents, "format acronyms are not identifiers"

    def test_identifiers_missing_from_sources(self) -> None:
        from src.generation.conversational_rag import _identifiers_missing_from_sources
        src = Source(document_id=1, filename="p/01_DOC.md", chunk_index=0, score=0.5,
                     text="This text mentions tDBOutput_1 explicitly.")
        assert _identifiers_missing_from_sources(["tDBOutput_1"], [src]) is False
        assert _identifiers_missing_from_sources(["GHOST_TABLE"], [src]) is True
        assert _identifiers_missing_from_sources([], [src]) is False


class TestRetrievalLadder:
    """End-to-end ladder behavior through run_conversational_rag."""

    @pytest.fixture(autouse=True)
    def disable_new_features(self):
        with patch("src.generation.conversational_rag.settings") as mock_settings:
            mock_settings.query_expansion_enabled = False
            mock_settings.reranker_enabled = False
            mock_settings.hybrid_search_enabled = False
            mock_settings.system_knowledge_injection = False
            yield mock_settings

    @pytest.fixture
    def mock_conversation(self):
        conv = MagicMock(spec=Conversation)
        conv.id = 1
        conv.messages = []
        conv.title = None
        return conv

    @pytest.fixture
    def mock_db(self):
        db = AsyncMock()
        added: list = []
        db.add = MagicMock(side_effect=added.append)
        db.added_objects = added

        async def fake_flush():
            for obj in added:
                if getattr(obj, "id", None) is None:
                    obj.id = 1
                if getattr(obj, "created_at", None) is None:
                    obj.created_at = datetime.datetime(2025, 1, 1, tzinfo=datetime.timezone.utc)

        db.flush = AsyncMock(side_effect=fake_flush)
        return db

    @staticmethod
    def _canonical_source() -> Source:
        return Source(
            document_id=1, filename="phase2/06_OPERATIONS.md", chunk_index=0,
            score=0.8, text="The job uses tDBOutput_1 with DIE_ON_ERROR=false.",
            source_layer="canonical_docs", project_id="phase2",
            display_name="06_OPERATIONS.md (Project: phase2)",
        )

    @staticmethod
    def _internal_source() -> Source:
        return Source(
            document_id=12, filename="phase2/_internal/technical_analysis/Item.md",
            chunk_index=3, score=0.6,
            text="ERROR_HANDLING: tDBOutput_1 DIE_ON_ERROR=false, job continues.",
            source_layer="technical_analysis", project_id="phase2", run_id=209,
            display_name="Technical Analysis: Item (Project: phase2)",
        )

    @staticmethod
    def _guidance_source() -> Source:
        return Source(
            document_id=21, filename="phase2/failure-guidance.pdf", chunk_index=0,
            score=0.7, text="Explain failure behavior against operational guidance.",
            document_role="guidance", source_layer="canonical_docs", project_id="phase2",
            display_name="failure-guidance.pdf (Project: phase2)",
        )

    async def test_rung1_sufficient_answer_skips_rung2(
        self, mock_conversation, mock_db, disable_new_features
    ):
        from src.db.models import DocGapEvent

        # This test exercises rung-1/rung-2 ladder logic, not intent
        # classification or the runtime gate — pin the classifier's output so
        # the assertions below don't depend on live-model sampling variance
        # (this query is genuinely borderline between search/project_detail
        # for the real classifier) or on the runtime keyword backstop (the
        # query contains "failure", a runtime keyword, which is irrelevant to
        # what this test verifies).
        search_analysis = QueryAnalysis(
            action=Action.SEARCH, query_type=QueryType.QA,
            cleaned_query="What does tDBOutput_1 do on failure?",
        )
        with (
            patch("src.generation.conversational_rag.analyze_query_async", new_callable=AsyncMock, return_value=search_analysis),
            patch("src.generation.conversational_rag.detect_runtime_keywords", return_value=False),
            patch("src.generation.conversational_rag.search_similar", new_callable=AsyncMock) as mock_search,
            patch("src.generation.conversational_rag.search_layered", new_callable=AsyncMock) as mock_layered,
            patch("src.generation.conversational_rag.generate_answer", new_callable=AsyncMock) as mock_gen,
        ):
            mock_search.side_effect = [[self._canonical_source()], [self._guidance_source()]]
            mock_gen.return_value = "The job continues because DIE_ON_ERROR is false [1]."

            response = await run_conversational_rag(
                conversation=mock_conversation,
                query="What does tDBOutput_1 do on failure?",
                user_groups=["g"], qdrant=AsyncMock(), db=mock_db,
            )

        mock_layered.assert_not_awaited()
        assert mock_search.await_args_list[0].kwargs["document_roles"] == ["source"]
        assert mock_search.await_args_list[1].kwargs["document_roles"] == [
            "reference", "guidance", "standard", "policy",
        ]
        assert mock_gen.await_count == 1, "happy path pays exactly one LLM call"
        assert response.fallback_used is False
        assert response.doc_gap is False
        assert response.primary_source_layer == "canonical_docs"
        assert not [o for o in mock_db.added_objects if isinstance(o, DocGapEvent)]

    async def test_need_search_descends_to_rung2_and_logs_gap(
        self, mock_conversation, mock_db, disable_new_features
    ):
        from src.db.models import DocGapEvent

        # Pin the classifier + runtime gate for the same reason as
        # test_rung1_sufficient_answer_skips_rung2 above: this query contains
        # "job"/"fails", which the runtime keyword backstop is intentionally
        # broad enough to match (an accepted false-positive per
        # .plans/02_prd.md), but this test verifies ladder descent, not gating.
        search_analysis = QueryAnalysis(
            action=Action.SEARCH, query_type=QueryType.QA,
            cleaned_query="What does the job do when the insert fails?",
        )
        with (
            patch("src.generation.conversational_rag.analyze_query_async", new_callable=AsyncMock, return_value=search_analysis),
            patch("src.generation.conversational_rag.detect_runtime_keywords", return_value=False),
            patch("src.generation.conversational_rag.search_similar", new_callable=AsyncMock) as mock_search,
            patch("src.generation.conversational_rag.search_layered", new_callable=AsyncMock) as mock_layered,
            patch("src.generation.conversational_rag.generate_answer", new_callable=AsyncMock) as mock_gen,
        ):
            mock_search.return_value = [self._canonical_source()]
            mock_layered.return_value = [self._internal_source()]
            mock_gen.side_effect = [
                "[NEED_SEARCH]",
                "Per the Phase 2 analysis, the job continues on DB failure [1].",
            ]

            response = await run_conversational_rag(
                conversation=mock_conversation,
                query="What does the job do when the insert fails?",
                user_groups=["g"], qdrant=AsyncMock(), db=mock_db,
            )

        mock_layered.assert_awaited_once()
        assert mock_layered.await_args.kwargs["source_layers"] == ["technical_analysis"]
        assert response.fallback_used is True
        assert response.doc_gap is True
        assert response.primary_source_layer == "technical_analysis"
        assert response.sources[0].filename.startswith("phase2/_internal/")

        events = [o for o in mock_db.added_objects if isinstance(o, DocGapEvent)]
        assert len(events) == 1
        event = events[0]
        assert event.project_id == "phase2"
        assert event.run_id == 209
        assert event.insufficiency_reason == "need_search"
        assert event.layers_used == ["technical_analysis"]
        assert event.supporting_artifacts == ["phase2/_internal/technical_analysis/Item.md"]
        assert event.message_id == 1

    async def test_missing_identifiers_triggers_rung2(
        self, mock_conversation, mock_db, disable_new_features
    ):
        # Pin the classifier + runtime gate — see test_rung1_sufficient_answer_skips_rung2
        # for why this is necessary (live-classifier borderline query).
        search_analysis = QueryAnalysis(
            action=Action.SEARCH, query_type=QueryType.QA,
            cleaned_query="What does tSetGlobalVar_4 store?",
        )
        with (
            patch("src.generation.conversational_rag.analyze_query_async", new_callable=AsyncMock, return_value=search_analysis),
            patch("src.generation.conversational_rag.detect_runtime_keywords", return_value=False),
            patch("src.generation.conversational_rag.search_similar", new_callable=AsyncMock) as mock_search,
            patch("src.generation.conversational_rag.search_layered", new_callable=AsyncMock) as mock_layered,
            patch("src.generation.conversational_rag.generate_answer", new_callable=AsyncMock) as mock_gen,
        ):
            mock_search.return_value = [self._canonical_source()]
            mock_layered.return_value = [self._internal_source()]
            # Rung-1 answer looks confident but the asked-about identifier is
            # absent from canonical text — the entity check must descend anyway.
            mock_gen.side_effect = [
                "Here is a plausible answer without the entity [1].",
                "Grounded internal answer [1].",
            ]

            response = await run_conversational_rag(
                conversation=mock_conversation,
                query="What does tSetGlobalVar_4 store?",
                user_groups=["g"], qdrant=AsyncMock(), db=mock_db,
            )

        mock_layered.assert_awaited_once()
        assert response.doc_gap is True

    async def test_rung2_unsupported_refuses_cleanly_no_gap_event(
        self, mock_conversation, mock_db, disable_new_features
    ):
        from src.db.models import DocGapEvent

        # Pin the classifier + runtime gate — see test_rung1_sufficient_answer_skips_rung2
        # for why this is necessary (isolates ladder logic from classification).
        search_analysis = QueryAnalysis(
            action=Action.SEARCH, query_type=QueryType.QA,
            cleaned_query="What is the unknown thing?",
        )
        with (
            patch("src.generation.conversational_rag.analyze_query_async", new_callable=AsyncMock, return_value=search_analysis),
            patch("src.generation.conversational_rag.detect_runtime_keywords", return_value=False),
            patch("src.generation.conversational_rag.search_similar", new_callable=AsyncMock) as mock_search,
            patch("src.generation.conversational_rag.search_layered", new_callable=AsyncMock) as mock_layered,
            patch("src.generation.conversational_rag.generate_answer", new_callable=AsyncMock) as mock_gen,
        ):
            mock_search.return_value = [self._canonical_source()]
            mock_layered.return_value = []
            mock_gen.side_effect = [
                "[NEED_SEARCH]",
                "I could not find this in the available documentation.",
            ]

            response = await run_conversational_rag(
                conversation=mock_conversation,
                query="What is the unknown thing?",
                user_groups=["g"], qdrant=AsyncMock(), db=mock_db,
            )

        assert response.fallback_used is True
        assert response.doc_gap is False
        assert response.sources is None
        assert not [o for o in mock_db.added_objects if isinstance(o, DocGapEvent)]

    async def test_rung1_passes_images_to_generation(
        self, mock_conversation, mock_db, disable_new_features
    ):
        """Vision input must reach the rung-1 generation call (inspection finding)."""
        # Pin the classifier + runtime gate — see test_rung1_sufficient_answer_skips_rung2
        # for why this is necessary (isolates ladder logic from classification).
        search_analysis = QueryAnalysis(
            action=Action.SEARCH, query_type=QueryType.QA,
            cleaned_query="What does tDBOutput_1 show in this screenshot?",
        )
        with (
            patch("src.generation.conversational_rag.analyze_query_async", new_callable=AsyncMock, return_value=search_analysis),
            patch("src.generation.conversational_rag.detect_runtime_keywords", return_value=False),
            patch("src.generation.conversational_rag.search_similar", new_callable=AsyncMock) as mock_search,
            patch("src.generation.conversational_rag.generate_answer", new_callable=AsyncMock) as mock_gen,
        ):
            mock_search.return_value = [self._canonical_source()]
            mock_gen.return_value = "Answer about tDBOutput_1 grounded in the screenshot [1]."

            await run_conversational_rag(
                conversation=mock_conversation,
                query="What does tDBOutput_1 show in this screenshot?",
                user_groups=["g"], qdrant=AsyncMock(), db=mock_db,
                images=["base64imagedata"],
            )

        assert mock_gen.await_args.kwargs.get("images") == ["base64imagedata"]


class TestLadderEntityMatching:
    """Slice 5: alias-table entity matching drives descent and unit retrieval."""

    disable_new_features = TestRetrievalLadder.__dict__["disable_new_features"]
    mock_conversation = TestRetrievalLadder.__dict__["mock_conversation"]
    mock_db = TestRetrievalLadder.__dict__["mock_db"]
    _canonical_source = TestRetrievalLadder.__dict__["_canonical_source"]
    _internal_source = TestRetrievalLadder.__dict__["_internal_source"]

    @staticmethod
    def _match(alias="resource_logDir", unit=None, entity_type="config"):
        from src.retrieval.entity_matcher import EntityMatch
        return EntityMatch(
            alias=alias, canonical_name=alias, entity_type=entity_type,
            project_id="phase2", unit_name=unit, source_path=None,
            source_artifact="entity_index.csv", confidence=1.0,
        )

    async def test_alias_match_descends_and_passes_units(
        self, mock_conversation, mock_db, disable_new_features
    ):
        from src.db.models import DocGapEvent
        from src.retrieval.entity_matcher import EntityMatchResult

        match = self._match(alias="tDBOutput_9", unit="sjItem_OnRamp", entity_type="component")
        # Pin the classifier + runtime gate — see test_rung1_sufficient_answer_skips_rung2
        # for why this is necessary (isolates ladder logic from classification).
        search_analysis = QueryAnalysis(
            action=Action.SEARCH, query_type=QueryType.QA,
            cleaned_query="What does tDBOutput_9 do?",
        )
        with (
            patch("src.generation.conversational_rag.analyze_query_async", new_callable=AsyncMock, return_value=search_analysis),
            patch("src.generation.conversational_rag.detect_runtime_keywords", return_value=False),
            patch("src.generation.conversational_rag.match_query_entities", new_callable=AsyncMock) as mock_match,
            patch("src.generation.conversational_rag.search_similar", new_callable=AsyncMock) as mock_search,
            patch("src.generation.conversational_rag.search_layered", new_callable=AsyncMock) as mock_layered,
            patch("src.generation.conversational_rag.generate_answer", new_callable=AsyncMock) as mock_gen,
        ):
            mock_match.return_value = EntityMatchResult(matches=[match], alias_rows_present=True)
            mock_search.return_value = [self._canonical_source()]  # text lacks tDBOutput_9
            mock_layered.return_value = [self._internal_source()]
            mock_gen.side_effect = [
                "Confident answer that never mentions the asked entity [1].",
                "Grounded internal answer about tDBOutput_9 [1].",
            ]

            response = await run_conversational_rag(
                conversation=mock_conversation,
                query="What does tDBOutput_9 do?",
                user_groups=["g"], qdrant=AsyncMock(), db=mock_db,
                project_ids=["phase2"],
            )

        assert response.doc_gap is True
        assert mock_layered.await_args.kwargs["matched_units"] == ["sjItem_OnRamp"]
        events = [o for o in mock_db.added_objects if isinstance(o, DocGapEvent)]
        assert events[0].detected_entities == ["tDBOutput_9"]
        assert events[0].insufficiency_reason == "missing_identifiers"

    async def test_no_alias_match_means_no_heuristic_descent(
        self, mock_conversation, mock_db, disable_new_features
    ):
        from src.retrieval.entity_matcher import EntityMatchResult

        # Pin the classifier + runtime gate — see test_rung1_sufficient_answer_skips_rung2
        # for why this is necessary (isolates ladder logic from classification).
        search_analysis = QueryAnalysis(
            action=Action.SEARCH, query_type=QueryType.QA,
            cleaned_query="Is JavaScript used in the OffRamp frontend?",
        )
        with (
            patch("src.generation.conversational_rag.analyze_query_async", new_callable=AsyncMock, return_value=search_analysis),
            patch("src.generation.conversational_rag.detect_runtime_keywords", return_value=False),
            patch("src.generation.conversational_rag.match_query_entities", new_callable=AsyncMock) as mock_match,
            patch("src.generation.conversational_rag.search_similar", new_callable=AsyncMock) as mock_search,
            patch("src.generation.conversational_rag.search_layered", new_callable=AsyncMock) as mock_layered,
            patch("src.generation.conversational_rag.generate_answer", new_callable=AsyncMock) as mock_gen,
        ):
            # Alias table has rows; "JavaScript" matches none of them.
            mock_match.return_value = EntityMatchResult(matches=[], alias_rows_present=True)
            mock_search.return_value = [self._canonical_source()]
            mock_gen.return_value = "Confident grounded answer [1]."

            response = await run_conversational_rag(
                conversation=mock_conversation,
                query="Is JavaScript used in the OffRamp frontend?",
                user_groups=["g"], qdrant=AsyncMock(), db=mock_db,
                project_ids=["phase2"],
            )

        mock_layered.assert_not_awaited(), "PascalCase without alias match must not descend"
        assert response.fallback_used is False

    async def test_legacy_project_falls_back_to_heuristic(
        self, mock_conversation, mock_db, disable_new_features
    ):
        from src.retrieval.entity_matcher import EntityMatchResult

        # Pin the classifier + runtime gate — see test_rung1_sufficient_answer_skips_rung2
        # for why this is necessary (isolates ladder logic from classification).
        search_analysis = QueryAnalysis(
            action=Action.SEARCH, query_type=QueryType.QA,
            cleaned_query="What does tSetGlobalVar_9 store?",
        )
        with (
            patch("src.generation.conversational_rag.analyze_query_async", new_callable=AsyncMock, return_value=search_analysis),
            patch("src.generation.conversational_rag.detect_runtime_keywords", return_value=False),
            patch("src.generation.conversational_rag.match_query_entities", new_callable=AsyncMock) as mock_match,
            patch("src.generation.conversational_rag.search_similar", new_callable=AsyncMock) as mock_search,
            patch("src.generation.conversational_rag.search_layered", new_callable=AsyncMock) as mock_layered,
            patch("src.generation.conversational_rag.generate_answer", new_callable=AsyncMock) as mock_gen,
        ):
            # No alias rows at all (project never synced) — heuristic applies.
            mock_match.return_value = EntityMatchResult(matches=[], alias_rows_present=False)
            mock_search.return_value = [self._canonical_source()]
            mock_layered.return_value = [self._internal_source()]
            mock_gen.side_effect = [
                "Answer that lacks the asked identifier [1].",
                "Grounded internal answer [1].",
            ]

            response = await run_conversational_rag(
                conversation=mock_conversation,
                query="What does tSetGlobalVar_9 store?",
                user_groups=["g"], qdrant=AsyncMock(), db=mock_db,
                project_ids=["phase2"],
            )

        mock_layered.assert_awaited_once()
        assert response.doc_gap is True


class TestRuntimeIntentGate:
    """Chat response-time fix (.plans/04_active_plan.md, Slice 1): the
    runtime-intent gate is the single source of truth for tool-loop entry,
    replacing the old "any non-cached turn" heuristic."""

    @pytest.fixture(autouse=True)
    def disable_new_features(self):
        with patch("src.generation.conversational_rag.settings") as mock_settings:
            mock_settings.query_expansion_enabled = False
            mock_settings.reranker_enabled = False
            mock_settings.hybrid_search_enabled = False
            mock_settings.system_knowledge_injection = False
            yield mock_settings

    @pytest.fixture
    def mock_conversation(self):
        conv = MagicMock(spec=Conversation)
        conv.id = 1
        conv.messages = []
        conv.title = None
        return conv

    @pytest.fixture
    def mock_db(self):
        db = AsyncMock()
        added: list = []
        db.add = MagicMock(side_effect=added.append)
        db.added_objects = added

        async def fake_flush():
            for obj in added:
                if getattr(obj, "id", None) is None:
                    obj.id = 1
                if getattr(obj, "created_at", None) is None:
                    obj.created_at = datetime.datetime(
                        2025, 1, 1, tzinfo=datetime.timezone.utc
                    )

        db.flush = AsyncMock(side_effect=fake_flush)
        return db

    @staticmethod
    def _runtime_analysis(query: str) -> QueryAnalysis:
        return QueryAnalysis(action=Action.SEARCH, query_type=QueryType.QA, cleaned_query=query, runtime_intent=True)

    @staticmethod
    def _search_analysis(query: str) -> QueryAnalysis:
        return QueryAnalysis(action=Action.SEARCH, query_type=QueryType.QA, cleaned_query=query, runtime_intent=False)

    @staticmethod
    def _docs_only_turn_plan() -> dict:
        return {
            "version": 1,
            "objective": "List jobs and connections",
            "task_type": "document_answer",
            "resolved_scope": {"project_id": "alitalend"},
            "output_contract": "component_inventory",
            "execution_mode": "evidence",
            "confidence": 0.9,
            "evidence_policy": {
                "allowed": ["indexed_documentation", "generated_documentation"],
                "denied": ["runtime_evidence", "connector_evidence"],
                "preferred": ["indexed_documentation"],
                "required": [],
            }
        }

    async def test_docs_only_plan_blocks_runtime_non_streaming(
        self, mock_conversation, mock_db
    ):
        source = Source(
            document_id=1, filename="inventory.md", chunk_index=0, score=0.9,
            text="Job A connects CRM to the warehouse.", source_layer="canonical_docs",
            project_id="alitalend", display_name="inventory.md (Project: alitalend)",
        )
        with (
            patch(
                "src.generation.conversational_rag.analyze_query_async",
                new_callable=AsyncMock,
                return_value=QueryAnalysis(
                    action=Action.RESPOND,
                    cleaned_query="list jobs from docs, not runtime logs",
                    runtime_intent=True,
                ),
            ),
            patch("src.generation.conversational_rag._search_qa_sources", new_callable=AsyncMock) as search,
            patch("src.generation.conversational_rag.generate_answer", new_callable=AsyncMock) as generate,
            patch("src.generation.conversational_rag.run_runtime_tool_loop", new_callable=AsyncMock) as runtime_loop,
        ):
            search.return_value = [source]
            generate.return_value = "Job A connects CRM to the warehouse [1]."
            response = await run_conversational_rag(
                conversation=mock_conversation,
                query="List all jobs from documentation; do not use runtime logs.",
                user_groups=["engineering"], qdrant=AsyncMock(), db=mock_db,
                project_ids=["alitalend"], turn_plan=self._docs_only_turn_plan(),
            )

        runtime_loop.assert_not_awaited()
        assert "Job A" in response.content
        assert response.turn_plan == self._docs_only_turn_plan()
        assert response.structured_answer is not None
        assert response.structured_answer["output_contract"] == "component_inventory"
        assert response.structured_answer["execution_status"] == "completed"
        assert response.structured_answer["domain_status"] == "unknown"
        prompt = "\n".join(
            message["content"] for message in generate.await_args.args[0]
        )
        assert "Component, Purpose, Inputs" in prompt

    async def test_docs_only_plan_blocks_runtime_streaming(
        self, mock_conversation, mock_db
    ):
        source = Source(
            document_id=1, filename="inventory.md", chunk_index=0, score=0.9,
            text="Job A connects CRM to the warehouse.", source_layer="canonical_docs",
            project_id="alitalend", display_name="inventory.md (Project: alitalend)",
        )

        async def answer_stream(_messages, images=None):
            yield "Job A connects CRM to the warehouse [1]."

        with (
            patch(
                "src.generation.conversational_rag.analyze_query_async",
                new_callable=AsyncMock,
                return_value=self._runtime_analysis("list jobs from docs, not runtime logs"),
            ),
            patch("src.generation.conversational_rag._search_qa_sources", new_callable=AsyncMock) as search,
            patch("src.generation.conversational_rag.generate_answer_stream", side_effect=answer_stream),
            patch("src.generation.conversational_rag.run_runtime_tool_loop_stream") as runtime_loop,
        ):
            search.return_value = [source]
            events = [
                event
                async for event in run_conversational_rag_stream(
                    conversation=mock_conversation,
                    query="List all jobs from documentation; do not use runtime logs.",
                    user_groups=["engineering"], qdrant=AsyncMock(), db=mock_db,
                    project_ids=["alitalend"], turn_plan=self._docs_only_turn_plan(),
                )
            ]

        runtime_loop.assert_not_called()
        assert any(kind == "token" and "Job A" in json.loads(data) for kind, data in events)
        final_answer = next(
            json.loads(data) for kind, data in events if kind == "final_answer"
        )
        assert final_answer["output_contract"] == "component_inventory"
        assert final_answer["answer_status"] == "complete"

    async def test_better_output_recomposes_prior_evidence_without_new_reads(
        self, mock_conversation
    ):
        prior = _mock_message(
            "assistant",
            "Job A connects CRM to the warehouse [1].",
            sources=[
                {
                    "document_id": 1,
                    "filename": "alitalend/inventory.md",
                    "score": 0.9,
                    "text": "Job A connects CRM to the warehouse.",
                    "reference_number": 1,
                    "project_id": "alitalend",
                }
            ],
        )
        prior.primary_source_layer = "canonical_docs"
        prior.structured_answer = {
            "output_contract": "component_inventory",
            "headline": "Component inventory",
            "summary": "Job A connects CRM to the warehouse.",
            "sections": [{"heading": "Findings", "body": prior.content, "rows": []}],
            "limitations": [],
            "citations": [
                {
                    "evidence_id": "1",
                    "label": "inventory.md",
                    "uri": "alitalend/inventory.md",
                    "source_class": "indexed_doc",
                }
            ],
            "evidence_refs": ["1"],
            "answer_status": "complete",
            "execution_status": "completed",
            "domain_status": "unknown",
            "artifacts": [],
        }
        mock_conversation.messages = [prior]
        with (
            patch(
                "src.generation.conversational_rag.analyze_query_async",
                new_callable=AsyncMock,
                return_value=self._runtime_analysis("give me better output"),
            ) as analyze,
            patch(
                "src.generation.conversational_rag._search_qa_sources",
                new_callable=AsyncMock,
            ) as search,
            patch(
                "src.generation.conversational_rag.generate_answer",
                new_callable=AsyncMock,
                return_value="| Component | Purpose |\n|---|---|\n| Job A | CRM load |",
            ) as generate,
        ):
            ctx = await _prepare_rag_context(
                conversation=mock_conversation,
                query="give me better output",
                user_groups=["engineering"],
                qdrant=AsyncMock(),
                project_ids=["alitalend"],
                planned_task_type="document_answer",
                planned_output_contract="component_inventory",
            )

        search.assert_not_awaited()
        analyze.assert_not_awaited()
        generate.assert_awaited_once()
        assert ctx.allow_runtime_tools is False
        assert ctx.precomputed_answer.startswith("| Component")
        assert len(ctx.document_sources) == 1
        payload = _structured_answer_payload(
            query="give me better output",
            answer=ctx.precomputed_answer,
            turn_plan=self._docs_only_turn_plan(),
            document_sources=[],
            runtime_progress=[],
            reused_structured_answer=ctx.reused_structured_answer,
        )
        assert payload is not None
        assert payload["output_contract"] == "component_inventory"
        assert payload["evidence_refs"] == ["1"]
        assert payload["citations"][0]["label"] == "inventory.md"
        assert payload["sections"][0]["body"].startswith("| Component")

    async def test_runtime_gated_turn_with_zero_documents_still_gates_in(self, mock_conversation):
        """A pure runtime question that retrieves no matching documents must
        still reach the tool loop -- not the old ladder's refusal path."""
        with (
            patch(
                "src.generation.conversational_rag.analyze_query_async",
                new_callable=AsyncMock,
                return_value=self._runtime_analysis("why did the nightly sync fail?"),
            ),
            patch("src.generation.conversational_rag.detect_runtime_keywords", return_value=True),
            patch("src.generation.conversational_rag._search_qa_sources", new_callable=AsyncMock) as mock_search,
        ):
            mock_search.return_value = []

            ctx = await _prepare_rag_context(
                conversation=mock_conversation,
                query="why did the nightly sync fail?",
                user_groups=["engineering"],
                qdrant=AsyncMock(),
            )

        assert ctx.allow_runtime_tools is True
        assert ctx.precomputed_answer is None
        assert ctx.document_sources == []

    async def test_runtime_gated_turn_attaches_retrieved_documents(self, mock_conversation):
        """Mixed questions (doc + runtime) must still carry document context
        into the tool loop."""
        source = Source(
            document_id=1, filename="runbook.pdf", chunk_index=0, score=0.8,
            text="Retry the job after a transient DB timeout.",
            source_layer="canonical_docs", project_id="phase2",
            display_name="runbook.pdf (Project: phase2)",
        )
        with (
            patch(
                "src.generation.conversational_rag.analyze_query_async",
                new_callable=AsyncMock,
                return_value=self._runtime_analysis("the job failed, what does the runbook say?"),
            ),
            patch("src.generation.conversational_rag.detect_runtime_keywords", return_value=True),
            patch("src.generation.conversational_rag._search_qa_sources", new_callable=AsyncMock) as mock_search,
        ):
            mock_search.return_value = [source]

            ctx = await _prepare_rag_context(
                conversation=mock_conversation,
                query="the job failed, what does the runbook say?",
                user_groups=["engineering"],
                qdrant=AsyncMock(),
            )

        assert ctx.allow_runtime_tools is True
        assert ctx.precomputed_answer is None
        assert len(ctx.document_sources) == 1
        assert ctx.document_sources[0].filename == "runbook.pdf"

    async def test_non_runtime_qa_turn_generates_exactly_once(self, mock_conversation):
        """Regression test for the double-generation bug: a non-runtime QA
        turn must call generate_answer exactly once for the whole turn."""
        source = Source(
            document_id=1, filename="policy.pdf", chunk_index=0, score=0.8,
            text="Vacation requests go through the manager.",
            source_layer="canonical_docs", project_id="hr",
            display_name="policy.pdf (Project: hr)",
        )
        with (
            patch(
                "src.generation.conversational_rag.analyze_query_async",
                new_callable=AsyncMock,
                return_value=self._search_analysis("what is the vacation policy?"),
            ),
            patch("src.generation.conversational_rag.detect_runtime_keywords", return_value=False),
            patch("src.generation.conversational_rag._search_qa_sources", new_callable=AsyncMock) as mock_search,
            patch("src.generation.conversational_rag.generate_answer", new_callable=AsyncMock) as mock_gen,
        ):
            mock_search.return_value = [source]
            mock_gen.return_value = "Vacation requests go through your manager [1]."

            ctx = await _prepare_rag_context(
                conversation=mock_conversation,
                query="what is the vacation policy?",
                user_groups=["engineering"],
                qdrant=AsyncMock(),
            )

        assert mock_gen.await_count == 1, "non-runtime QA must pay exactly one LLM call"
        assert ctx.allow_runtime_tools is False
        assert ctx.precomputed_answer == "Vacation requests go through your manager [1]."

    async def test_chitchat_streaming_turn_never_enters_tool_loop(self, mock_conversation):
        """RESPOND turns on the streaming path (precomputed_answer=None) must
        stream directly -- never invoke the runtime tool loop, never pay for
        tool schemas."""
        from src.generation.conversational_rag import run_conversational_rag_stream

        async def fake_stream(messages, images=None):
            for token in ["Hello", "!"]:
                yield token

        with (
            patch(
                "src.generation.conversational_rag.analyze_query_async",
                new_callable=AsyncMock,
                return_value=QueryAnalysis(action=Action.RESPOND, cleaned_query="hi"),
            ),
            patch("src.generation.conversational_rag.run_runtime_tool_loop", new_callable=AsyncMock) as mock_loop,
            patch("src.generation.conversational_rag.generate_answer_stream", side_effect=fake_stream),
        ):
            db = AsyncMock()
            added: list = []
            db.add = MagicMock(side_effect=added.append)

            async def fake_flush():
                for obj in added:
                    if getattr(obj, "id", None) is None:
                        obj.id = 1
                    if getattr(obj, "created_at", None) is None:
                        obj.created_at = datetime.datetime(2025, 1, 1, tzinfo=datetime.timezone.utc)

            db.flush = AsyncMock(side_effect=fake_flush)

            events = []
            async for event_type, data in run_conversational_rag_stream(
                conversation=mock_conversation,
                query="hi",
                user_groups=["engineering"],
                qdrant=AsyncMock(),
                db=db,
            ):
                events.append((event_type, data))

        mock_loop.assert_not_called()
        token_payloads = [data for etype, data in events if etype == "token"]
        assert token_payloads == ['"Hello"', '"!"']

    async def test_gate_decision_logged(self, mock_conversation, caplog):
        """The gate decision (IN/OUT) must be observable in logs for
        diagnosing false negatives in production."""
        import logging

        with (
            patch(
                "src.generation.conversational_rag.analyze_query_async",
                new_callable=AsyncMock,
                return_value=self._runtime_analysis("why did job X fail?"),
            ),
            patch("src.generation.conversational_rag.detect_runtime_keywords", return_value=True),
            patch("src.generation.conversational_rag._search_qa_sources", new_callable=AsyncMock) as mock_search,
        ):
            mock_search.return_value = []
            with caplog.at_level(logging.INFO, logger="src.generation.conversational_rag"):
                await _prepare_rag_context(
                    conversation=mock_conversation,
                    query="why did job X fail?",
                    user_groups=["engineering"],
                    qdrant=AsyncMock(),
                )

        gate_logs = [r.message for r in caplog.records if "Runtime gate" in r.message]
        assert len(gate_logs) == 1
        assert "IN" in gate_logs[0]

    async def test_keyword_hit_cannot_override_confident_classifier_no(self, mock_conversation):
        """Regression: a doc question that happens to contain a runtime
        keyword (e.g. 'job', 'talend') must NOT enter the tool loop when the
        LLM classifier confidently classified it as a non-runtime search.
        The keyword backstop exists for recall on classifier FAILURE only --
        it must never override a confident classifier 'no'."""
        source = Source(
            document_id=1, filename="talend-guide.pdf", chunk_index=0, score=0.8,
            text="The tRunJob component runs a Talend job.",
            source_layer="canonical_docs", project_id="phase2",
            display_name="talend-guide.pdf (Project: phase2)",
        )
        with (
            patch(
                "src.generation.conversational_rag.analyze_query_async",
                new_callable=AsyncMock,
                return_value=self._search_analysis("what does the talend job do?"),
            ),
            patch("src.generation.conversational_rag.detect_runtime_keywords", return_value=True),
            patch("src.generation.conversational_rag._search_qa_sources", new_callable=AsyncMock) as mock_search,
            patch("src.generation.conversational_rag.generate_answer", new_callable=AsyncMock) as mock_gen,
        ):
            mock_search.return_value = [source]
            mock_gen.return_value = "The tRunJob component runs a Talend job [1]."

            ctx = await _prepare_rag_context(
                conversation=mock_conversation,
                query="what does the talend job do?",
                user_groups=["engineering"],
                qdrant=AsyncMock(),
            )

        assert ctx.allow_runtime_tools is False, (
            "keyword hit must not override a confident classifier 'no'"
        )

    async def test_keyword_backstop_rescues_classifier_failure(self, mock_conversation):
        """When every LLM provider raised (classifier_failed=True), the
        keyword backstop must still route a genuine runtime question into the
        tool loop -- recall insurance is preserved for the failure case."""
        failed = QueryAnalysis(
            action=Action.SEARCH, query_type=QueryType.QA,
            cleaned_query="why did the nightly sync fail?",
            runtime_intent=False, classifier_failed=True,
        )
        with (
            patch(
                "src.generation.conversational_rag.analyze_query_async",
                new_callable=AsyncMock,
                return_value=failed,
            ),
            patch("src.generation.conversational_rag.detect_runtime_keywords", return_value=True),
            patch("src.generation.conversational_rag._search_qa_sources", new_callable=AsyncMock) as mock_search,
        ):
            mock_search.return_value = []

            ctx = await _prepare_rag_context(
                conversation=mock_conversation,
                query="why did the nightly sync fail?",
                user_groups=["engineering"],
                qdrant=AsyncMock(),
            )

        assert ctx.allow_runtime_tools is True


def _chunked_stream(chunks: list[str]):
    """Build a generate_answer_stream-compatible fake: an async-generator
    *function* (messages, images=None) -> AsyncGenerator[str, None] that
    yields the given chunks in order."""

    async def _stream(messages, images=None):
        for chunk in chunks:
            yield chunk

    return _stream


class TestSniffBufferHelpers:
    """Slice 2: unit tests for the sniff-buffer / late-marker helpers in
    isolation, no run_conversational_rag_stream involved."""

    def test_ends_with_partial_marker_true_for_prefix(self) -> None:
        assert _ends_with_partial_marker("some text [NEED_SEA") is True
        assert _ends_with_partial_marker("some text [") is True
        assert _ends_with_partial_marker("some text [NEED_SEARCH") is True

    def test_ends_with_partial_marker_false_for_complete_or_absent(self) -> None:
        assert _ends_with_partial_marker("some text [NEED_SEARCH]") is False
        assert _ends_with_partial_marker("some plain text") is False
        assert _ends_with_partial_marker("") is False

    async def test_sniff_sufficient_short_answer_exhausts_within_buffer(self) -> None:
        """Stream ends before the buffer fills, no marker, no cant-answer
        heuristic phrase -- sufficient, nothing left to drain."""
        with patch(
            "src.generation.conversational_rag.generate_answer_stream",
            side_effect=_chunked_stream(["The vacation policy allows 20 days per year [1]."]),
        ):
            result = await _sniff_stream_sufficiency([{"role": "user", "content": "q"}])

        assert result.insufficient is False
        assert result.remainder is None
        assert result.prefix == "The vacation policy allows 20 days per year [1]."

    async def test_sniff_marker_within_buffer_window_is_insufficient(self) -> None:
        with patch(
            "src.generation.conversational_rag.generate_answer_stream",
            side_effect=_chunked_stream([_NEED_SEARCH_MARKER]),
        ):
            result = await _sniff_stream_sufficiency([{"role": "user", "content": "q"}])

        assert result.insufficient is True
        assert result.remainder is None

    async def test_sniff_short_cant_answer_heuristic_is_insufficient(self) -> None:
        """Answer under 200 chars matching _CANT_ANSWER_PATTERNS, exhausted
        inside the buffer window -- same safety net as needs_new_search()."""
        with patch(
            "src.generation.conversational_rag.generate_answer_stream",
            side_effect=_chunked_stream(["I could not find that information in the documents."]),
        ):
            result = await _sniff_stream_sufficiency([{"role": "user", "content": "q"}])

        assert result.insufficient is True
        assert result.remainder is None

    async def test_sniff_marker_split_across_buffer_boundary_detected(self) -> None:
        """AC: marker exactly on the buffer boundary must not falsely pass
        as sufficient. Chunk 1 crosses _SNIFF_BUFFER_CHARS mid-marker;
        _ends_with_partial_marker must force one more read."""
        chunk1 = "X" * 195 + "[NEED_"   # 201 chars total, ends mid-marker
        chunk2 = "SEARCH] more text after the marker"
        with patch(
            "src.generation.conversational_rag.generate_answer_stream",
            side_effect=_chunked_stream([chunk1, chunk2]),
        ):
            result = await _sniff_stream_sufficiency([{"role": "user", "content": "q"}])

        assert result.insufficient is True
        assert result.remainder is None

    async def test_sniff_cant_answer_phrase_split_across_buffer_boundary_detected(self) -> None:
        """Same boundary concern for the heuristic phrase: the exact split
        point must not matter because chunk1 alone is well under 200 chars
        (so the exhausted-in-buffer branch and its heuristic still apply)."""
        chunk1 = "I could not find that "
        chunk2 = "information anywhere in the documents."
        with patch(
            "src.generation.conversational_rag.generate_answer_stream",
            side_effect=_chunked_stream([chunk1, chunk2]),
        ):
            result = await _sniff_stream_sufficiency([{"role": "user", "content": "q"}])

        assert result.insufficient is True
        assert result.remainder is None

    async def test_sniff_heuristic_not_applied_once_stream_continues_past_buffer(self) -> None:
        """AC/decision 2: if the buffer reaches _SNIFF_BUFFER_CHARS mid-phrase
        but the stream keeps producing tokens past it, the original
        needs_new_search() heuristic (len(stripped) < 200) would never have
        fired for that answer either -- skipping it here introduces no gap."""
        # Chunk 1 alone is >= _SNIFF_BUFFER_CHARS and contains the phrase, but
        # the stream is NOT exhausted (chunk 2 follows) -- must break early
        # without ever consulting the heuristic.
        chunk1 = "I could not find that information in the documents. " + "X" * 150
        assert len(chunk1) >= _SNIFF_BUFFER_CHARS
        chunk2 = " but here is more context that follows anyway."
        with patch(
            "src.generation.conversational_rag.generate_answer_stream",
            side_effect=_chunked_stream([chunk1, chunk2]),
        ):
            result = await _sniff_stream_sufficiency([{"role": "user", "content": "q"}])

        assert result.insufficient is False
        assert result.remainder is not None
        await result.remainder.aclose()

    async def test_drain_no_marker_yields_all_chunks(self) -> None:
        async def remainder():
            for chunk in ["hello ", "world"]:
                yield chunk

        out = []
        async for chunk in _drain_stripping_late_marker(remainder()):
            out.append(chunk)

        assert "".join(out) == "hello world"

    async def test_drain_marker_split_across_two_chunks_is_stripped(self, caplog) -> None:
        """A marker whose halves arrive in separate yielded chunks must still
        be detected via the rolling lookback window."""
        async def remainder():
            yield "prefix text [NEED_SEA"
            yield "RCH] suffix text"

        with caplog.at_level(logging.WARNING, logger="src.generation.conversational_rag"):
            out = []
            async for chunk in _drain_stripping_late_marker(remainder()):
                out.append(chunk)

        joined = "".join(out)
        assert _NEED_SEARCH_MARKER not in joined
        assert "prefix text" in joined
        assert "suffix text" in joined
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert any("Late" in r.message and "NEED_SEARCH" in r.message for r in warnings)

    async def test_drain_marker_at_the_tail_of_a_large_chunk_is_stripped(self, caplog) -> None:
        """Regression guard: a marker whose prefix lands at the very tail of
        a LARGE provider chunk (much bigger than the marker itself, unlike
        the small-chunk case above) and whose suffix arrives in the next
        chunk must still be detected and stripped. This guards against an
        algorithm that only checks a fixed-size slice for the marker instead
        of scanning however much of the buffer is actually pending -- such
        an algorithm can hand out the marker's prefix un-stripped before its
        suffix ever arrives, once a chunk is bigger than the lookback
        window."""
        chunk1 = "some preamble text " + "X" * 200 + _NEED_SEARCH_MARKER[:6]
        chunk2 = _NEED_SEARCH_MARKER[6:] + " and the rest of the answer follows here."

        async def remainder():
            yield chunk1
            yield chunk2

        with caplog.at_level(logging.WARNING, logger="src.generation.conversational_rag"):
            out = []
            async for chunk in _drain_stripping_late_marker(remainder()):
                out.append(chunk)

        joined = "".join(out)
        assert _NEED_SEARCH_MARKER not in joined
        assert "[NEED_SEA" not in joined, "marker prefix must not leak un-stripped"
        assert "some preamble text" in joined
        assert "and the rest of the answer follows here" in joined
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert any("Late" in r.message and "NEED_SEARCH" in r.message for r in warnings)


class TestStreamingSniffBuffer:
    """Slice 2: streamed doc-QA answers via the sniff-buffer, exercised
    end-to-end through run_conversational_rag_stream / run_conversational_rag."""

    @pytest.fixture(autouse=True)
    def disable_new_features(self):
        with patch("src.generation.conversational_rag.settings") as mock_settings:
            mock_settings.query_expansion_enabled = False
            mock_settings.reranker_enabled = False
            mock_settings.hybrid_search_enabled = False
            mock_settings.system_knowledge_injection = False
            yield mock_settings

    @pytest.fixture
    def mock_conversation(self):
        conv = MagicMock(spec=Conversation)
        conv.id = 1
        conv.messages = []
        conv.title = None
        return conv

    @pytest.fixture
    def mock_conversation_with_sources(self):
        conv = MagicMock(spec=Conversation)
        conv.id = 1
        conv.title = "Test conversation"
        source_data = [
            {"document_id": 1, "filename": "hr_policy.pdf", "score": 0.9,
             "text": "Vacation policy: 20 days per year.", "reference_number": 1},
        ]
        conv.messages = [
            _mock_message("user", "What is the vacation policy?"),
            _mock_message("assistant", "The vacation policy allows 20 days per year [1].", sources=source_data),
        ]
        return conv

    @pytest.fixture
    def mock_db(self):
        db = AsyncMock()
        added: list = []
        db.add = MagicMock(side_effect=added.append)
        db.added_objects = added

        async def fake_flush():
            for obj in added:
                if getattr(obj, "id", None) is None:
                    obj.id = 1
                if getattr(obj, "created_at", None) is None:
                    obj.created_at = datetime.datetime(2025, 1, 1, tzinfo=datetime.timezone.utc)

        db.flush = AsyncMock(side_effect=fake_flush)
        return db

    @staticmethod
    def _canonical_source() -> Source:
        return Source(
            document_id=1, filename="phase2/06_OPERATIONS.md", chunk_index=0,
            score=0.8, text="The job uses tDBOutput_1 with DIE_ON_ERROR=false.",
            source_layer="canonical_docs", project_id="phase2",
            display_name="06_OPERATIONS.md (Project: phase2)",
        )

    @staticmethod
    def _internal_source() -> Source:
        return Source(
            document_id=12, filename="phase2/_internal/technical_analysis/Item.md",
            chunk_index=3, score=0.6,
            text="ERROR_HANDLING: tDBOutput_1 DIE_ON_ERROR=false, job continues.",
            source_layer="technical_analysis", project_id="phase2", run_id=209,
            display_name="Technical Analysis: Item (Project: phase2)",
        )

    @staticmethod
    def _search_analysis(query: str) -> QueryAnalysis:
        return QueryAnalysis(action=Action.SEARCH, query_type=QueryType.QA, cleaned_query=query)

    async def _collect_until_first_token(self, agen):
        """Pull events off an async generator until (and including) the
        first "token" event, returning (events, remaining_agen_is_open)."""
        events = []
        while True:
            event_type, data = await agen.__anext__()
            events.append((event_type, data))
            if event_type == "token":
                return events

    async def test_rung1_streams_tokens_before_generation_completes(
        self, mock_conversation, mock_db
    ):
        """AC: a document question with sufficient context shows the first
        token event before generation has fully resolved."""
        release = asyncio.Event()

        async def fake_stream(messages, images=None):
            yield "X" * 205  # crosses the sniff buffer, no marker
            await release.wait()
            yield " -- the rest of the answer, delivered live."

        with (
            patch(
                "src.generation.conversational_rag.analyze_query_async",
                new_callable=AsyncMock,
                return_value=self._search_analysis("What does tDBOutput_1 do?"),
            ),
            patch("src.generation.conversational_rag.detect_runtime_keywords", return_value=False),
            patch("src.generation.conversational_rag.search_similar", new_callable=AsyncMock) as mock_search,
            patch("src.generation.conversational_rag.generate_answer_stream", side_effect=fake_stream),
        ):
            mock_search.return_value = [self._canonical_source()]

            agen = run_conversational_rag_stream(
                conversation=mock_conversation,
                query="What does tDBOutput_1 do?",
                user_groups=["g"], qdrant=AsyncMock(), db=mock_db,
            )

            events = await asyncio.wait_for(
                self._collect_until_first_token(agen), timeout=2.0,
            )
            # The first token arrived without the release event ever being set --
            # proof the whole answer was not awaited before the first chunk.
            assert release.is_set() is False

            release.set()
            async for event_type, data in agen:
                events.append((event_type, data))

        token_payloads = [json.loads(data) for etype, data in events if etype == "token"]
        accumulated = "".join(token_payloads)
        assert "X" * 205 in accumulated
        assert "the rest of the answer, delivered live" in accumulated

        assistant_messages = [
            o for o in mock_db.added_objects
            if isinstance(o, ConversationMessage) and o.role == "assistant"
        ]
        assert len(assistant_messages) == 1
        assert assistant_messages[0].content == accumulated
        assert any(etype == "done" for etype, _ in events)

    async def test_previous_source_reuse_streams_before_generation_completes(
        self, mock_conversation_with_sources, mock_db
    ):
        """AC: the previous-source-reuse streaming branch also streams
        before the full generation resolves."""
        release = asyncio.Event()

        async def fake_stream(messages, images=None):
            yield "Y" * 210
            await release.wait()
            yield " -- more from previous-source reuse."

        with (
            patch(
                "src.generation.conversational_rag.analyze_query_async",
                new_callable=AsyncMock,
                return_value=self._search_analysis("How many vacation days do I get?"),
            ),
            patch("src.generation.conversational_rag.detect_runtime_keywords", return_value=False),
            patch("src.generation.conversational_rag.generate_answer_stream", side_effect=fake_stream),
        ):
            agen = run_conversational_rag_stream(
                conversation=mock_conversation_with_sources,
                query="How many vacation days do I get?",
                user_groups=["all"], qdrant=AsyncMock(), db=mock_db,
            )

            events = await asyncio.wait_for(
                self._collect_until_first_token(agen), timeout=2.0,
            )
            assert release.is_set() is False

            release.set()
            async for event_type, data in agen:
                events.append((event_type, data))

        token_payloads = [json.loads(data) for etype, data in events if etype == "token"]
        accumulated = "".join(token_payloads)
        assert "Y" * 210 in accumulated
        assert "more from previous-source reuse" in accumulated

    async def test_rung1_insufficient_sniff_emits_no_tokens_and_falls_to_rung2(
        self, mock_conversation, mock_db
    ):
        """AC: an insufficient sniff result produces zero token events for
        the aborted attempt and falls through to rung 2, matching the
        existing non-streaming ladder-descent contract."""
        with (
            patch(
                "src.generation.conversational_rag.analyze_query_async",
                new_callable=AsyncMock,
                return_value=self._search_analysis("What does the unknown widget do?"),
            ),
            patch("src.generation.conversational_rag.detect_runtime_keywords", return_value=False),
            patch("src.generation.conversational_rag.search_similar", new_callable=AsyncMock) as mock_search,
            patch("src.generation.conversational_rag.search_layered", new_callable=AsyncMock) as mock_layered,
            patch(
                "src.generation.conversational_rag.generate_answer_stream",
                side_effect=_chunked_stream([_NEED_SEARCH_MARKER]),
            ),
            patch("src.generation.conversational_rag.generate_answer", new_callable=AsyncMock) as mock_gen,
        ):
            mock_search.return_value = [self._canonical_source()]
            mock_layered.return_value = [self._internal_source()]
            mock_gen.return_value = "Per the internal analysis, the widget retries on failure [1]."

            events = []
            async for event_type, data in run_conversational_rag_stream(
                conversation=mock_conversation,
                query="What does the unknown widget do?",
                user_groups=["g"], qdrant=AsyncMock(), db=mock_db,
            ):
                events.append((event_type, data))

        mock_layered.assert_awaited_once()
        token_payloads = [json.loads(data) for etype, data in events if etype == "token"]
        assert token_payloads == ["Per the internal analysis, the widget retries on failure [1]."]
        assert not any(_NEED_SEARCH_MARKER in t for t in token_payloads)

        done_events = [json.loads(data) for etype, data in events if etype == "done"]
        assert len(done_events) == 1
        assert done_events[0]["doc_gap"] is True

    async def test_late_need_search_marker_stripped_and_logged(
        self, mock_conversation, mock_db, caplog
    ):
        """AC: a marker arriving after the sniff buffer has already flushed
        is stripped from displayed output, logged at WARNING, and the rest
        of the answer is delivered -- no crash, no onscreen artifact."""
        async def fake_stream(messages, images=None):
            yield "F" * 210  # clearly sufficient, crosses the buffer, no marker
            yield f" then a late marker arrives {_NEED_SEARCH_MARKER} mid-stream"
            yield " and more text follows normally."

        with (
            patch(
                "src.generation.conversational_rag.analyze_query_async",
                new_callable=AsyncMock,
                return_value=self._search_analysis("What does tDBOutput_1 do on failure?"),
            ),
            patch("src.generation.conversational_rag.detect_runtime_keywords", return_value=False),
            patch("src.generation.conversational_rag.search_similar", new_callable=AsyncMock) as mock_search,
            patch("src.generation.conversational_rag.generate_answer_stream", side_effect=fake_stream),
        ):
            mock_search.return_value = [self._canonical_source()]

            with caplog.at_level(logging.WARNING, logger="src.generation.conversational_rag"):
                events = []
                async for event_type, data in run_conversational_rag_stream(
                    conversation=mock_conversation,
                    query="What does tDBOutput_1 do on failure?",
                    user_groups=["g"], qdrant=AsyncMock(), db=mock_db,
                ):
                    events.append((event_type, data))

        token_payloads = [json.loads(data) for etype, data in events if etype == "token"]
        for payload in token_payloads:
            assert _NEED_SEARCH_MARKER not in payload

        accumulated = "".join(token_payloads)
        assert "F" * 210 in accumulated
        assert "then a late marker arrives" in accumulated
        assert "mid-stream" in accumulated
        assert "more text follows normally" in accumulated

        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert any("Late" in r.message and "NEED_SEARCH" in r.message for r in warnings)
        assert any(etype == "done" for etype, _ in events)

    async def test_precompute_true_rung1_unchanged_by_sniff_refactor(
        self, mock_conversation, mock_db
    ):
        """Non-streaming parity: run_conversational_rag (REST) must never
        touch generate_answer_stream -- it stays on the unchanged blocking
        generate_answer path."""
        async def _fail_if_called(*args, **kwargs):
            raise AssertionError("REST path must not call generate_answer_stream")

        with (
            patch(
                "src.generation.conversational_rag.analyze_query_async",
                new_callable=AsyncMock,
                return_value=self._search_analysis("What does tDBOutput_1 do?"),
            ),
            patch("src.generation.conversational_rag.detect_runtime_keywords", return_value=False),
            patch("src.generation.conversational_rag.search_similar", new_callable=AsyncMock) as mock_search,
            patch("src.generation.conversational_rag.generate_answer_stream", side_effect=_fail_if_called),
            patch("src.generation.conversational_rag.run_runtime_tool_loop", _no_tool_support_loop()),
            patch("src.generation.conversational_rag.generate_answer", new_callable=AsyncMock) as mock_gen,
        ):
            mock_search.return_value = [self._canonical_source()]
            mock_gen.return_value = "Grounded rung-1 answer [1]."

            response = await run_conversational_rag(
                conversation=mock_conversation,
                query="What does tDBOutput_1 do?",
                user_groups=["g"], qdrant=AsyncMock(), db=mock_db,
            )

        assert response.content.startswith("Grounded rung-1 answer [1].")
