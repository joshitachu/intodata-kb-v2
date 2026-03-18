import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.db.models import Conversation, ConversationMessage
from src.generation.intent_classifier import Action, QueryAnalysis, QueryType
from src.generation.conversational_rag import (
    build_context,
    build_context_from_document_sources,
    build_conversational_messages,
    build_conversational_messages_with_context,
    get_previous_sources,
    needs_new_search,
    run_conversational_rag,
    truncate_history,
)
from src.schemas import DocumentSource, Source


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
            patch("src.generation.conversational_rag.generate_answer", new_callable=AsyncMock) as mock_gen,
        ):
            # Search returns no results for "hello"
            mock_search.return_value = []
            mock_gen.return_value = "Hello! How can I help you?"

            response = await run_conversational_rag(
                conversation=mock_conversation,
                query="hello",
                user_groups=["engineering"],
                qdrant=mock_qdrant,
                db=mock_db,
            )

        # Search WAS called (greetings now go through search)
        mock_search.assert_called_once()
        mock_gen.assert_called_once()
        assert response.content == "Hello! How can I help you?"

    async def test_no_results_calls_llm_fallback(self, mock_conversation, mock_qdrant, mock_db):
        """When search returns no results, LLM generates a helpful response."""
        with (
            patch("src.generation.conversational_rag.search_similar", new_callable=AsyncMock) as mock_search,
            patch("src.generation.conversational_rag.generate_answer", new_callable=AsyncMock) as mock_gen,
        ):
            mock_search.return_value = []
            mock_gen.return_value = "Could you provide more details about what you're looking for?"

            response = await run_conversational_rag(
                conversation=mock_conversation,
                query="What about the thing?",
                user_groups=["engineering"],
                qdrant=mock_qdrant,
                db=mock_db,
            )

        mock_search.assert_called_once()
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
        assert response.content == "Yes, you get 20 vacation days per year [1]."
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

        mock_search.assert_called_once()
        assert mock_gen.call_count == 2
        assert response.content == "The salary range is $50k-$100k [1]."
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

        mock_search.assert_called_once()
        # Only 1 generate call (no context-first attempt since no previous sources)
        mock_gen.assert_called_once()
        assert response.content == "Here is the HR info [1]."

    async def test_need_search_marker_stripped_from_final_response(
        self, mock_conversation_no_sources, mock_qdrant, mock_db
    ):
        """[NEED_SEARCH] is never present in the response returned to the user."""
        with (
            patch("src.generation.conversational_rag.search_similar", new_callable=AsyncMock) as mock_search,
            patch("src.generation.conversational_rag.generate_answer", new_callable=AsyncMock) as mock_gen,
        ):
            mock_search.return_value = []
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
