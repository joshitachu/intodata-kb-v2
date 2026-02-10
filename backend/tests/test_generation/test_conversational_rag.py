import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.db.models import Conversation, ConversationMessage
from src.generation.conversational_rag import (
    expand_query,
    truncate_history,
    build_context,
    build_conversational_messages,
    run_conversational_rag,
)
from src.schemas import Source


def _mock_message(role: str, content: str) -> ConversationMessage:
    msg = MagicMock(spec=ConversationMessage)
    msg.role = role
    msg.content = content
    return msg


class TestExpandQuery:
    def test_no_expansion_without_pronouns(self) -> None:
        messages = [_mock_message("user", "What is the company revenue?")]
        result = expand_query("How many employees do we have?", messages)
        assert result == "How many employees do we have?"

    def test_no_expansion_without_history(self) -> None:
        result = expand_query("Tell me about it", [])
        assert result == "Tell me about it"

    def test_expands_with_it_pronoun(self) -> None:
        messages = [
            _mock_message("user", "What is the company revenue?"),
            _mock_message("assistant", "Revenue was $1M in Q4."),
        ]
        result = expand_query("Tell me more about it", messages)
        assert "company revenue" in result
        assert "Tell me more about it" in result

    def test_expands_with_that_pronoun(self) -> None:
        messages = [
            _mock_message("user", "Explain the vacation policy"),
        ]
        result = expand_query("When was that updated?", messages)
        assert "vacation policy" in result
        assert "When was that updated" in result

    def test_expands_with_this_pronoun(self) -> None:
        messages = [
            _mock_message("user", "What is our hiring process?"),
        ]
        result = expand_query("Can you elaborate on this?", messages)
        assert "hiring process" in result

    def test_uses_last_two_user_messages(self) -> None:
        messages = [
            _mock_message("user", "First question"),
            _mock_message("assistant", "First answer"),
            _mock_message("user", "Second question"),
            _mock_message("assistant", "Second answer"),
            _mock_message("user", "Third question"),
            _mock_message("assistant", "Third answer"),
        ]
        result = expand_query("What about it?", messages)
        # Should include Second and Third user messages, not First
        assert "Second question" in result
        assert "Third question" in result


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
        # Create messages with known large content
        messages = [
            _mock_message("user", "A" * 500),  # ~500 tokens
            _mock_message("assistant", "B" * 500),
            _mock_message("user", "C" * 500),
            _mock_message("assistant", "D" * 500),
        ]
        # With a small token limit, should only keep most recent
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
        # Create sources with large text
        sources = [
            Source(document_id=i, filename=f"doc{i}.pdf", chunk_index=0, score=0.9, text="X" * 1000)
            for i in range(10)
        ]
        result = build_context(sources, max_tokens=500)
        # Should not include all 10 sources
        assert result.count("doc") < 10


class TestBuildConversationalMessages:
    def test_basic_structure(self) -> None:
        sources = [
            Source(document_id=1, filename="doc.pdf", chunk_index=0, score=0.9, text="Info"),
        ]
        history = []
        result = build_conversational_messages("What is this?", sources, history)

        # Should have: system prompt, context, user query
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

        # Find user and assistant messages from history
        contents = [m["content"] for m in result]
        assert "Previous question" in contents
        assert "Previous answer" in contents

    def test_includes_context_from_sources(self) -> None:
        sources = [
            Source(document_id=1, filename="report.pdf", chunk_index=0, score=0.9, text="Revenue data"),
        ]
        history = []
        result = build_conversational_messages("Query", sources, history)

        # Find the context message
        context_messages = [m for m in result if "Retrieved context" in m.get("content", "")]
        assert len(context_messages) == 1
        assert "report.pdf" in context_messages[0]["content"]

    def test_empty_sources_no_context_message(self) -> None:
        sources = []
        history = []
        result = build_conversational_messages("Query", sources, history)

        # Should not have a context message
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

        # Order should be: system, context, history..., current query
        roles = [m["role"] for m in result]
        assert roles[0] == "system"  # System prompt
        assert roles[1] == "system"  # Context
        assert roles[-1] == "user"   # Current query
        assert "New question" == result[-1]["content"]


class TestConversationalRagIntentRouting:
    """Tests for intent routing in run_conversational_rag."""

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

    async def test_greeting_skips_search(self, mock_conversation, mock_qdrant, mock_db):
        """Greetings skip search and go directly to LLM."""
        with (
            patch("src.generation.conversational_rag.search_similar", new_callable=AsyncMock) as mock_search,
            patch("src.generation.conversational_rag.generate_answer", new_callable=AsyncMock) as mock_gen,
        ):
            mock_gen.return_value = "Hello! How can I help you?"

            response = await run_conversational_rag(
                conversation=mock_conversation,
                query="hello",
                user_groups=["engineering"],
                qdrant=mock_qdrant,
                db=mock_db,
            )

        mock_search.assert_not_called()
        mock_gen.assert_called_once()
        assert response.content == "Hello! How can I help you?"
        assert response.sources is None

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
        # Should use no-results prompt
        call_messages = mock_gen.call_args[0][0]
        assert "no relevant documents" in call_messages[0]["content"].lower()
