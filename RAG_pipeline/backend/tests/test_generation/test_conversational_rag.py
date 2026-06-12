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

    async def test_rung1_sufficient_answer_skips_rung2(
        self, mock_conversation, mock_db, disable_new_features
    ):
        from src.db.models import DocGapEvent

        with (
            patch("src.generation.conversational_rag.search_similar", new_callable=AsyncMock) as mock_search,
            patch("src.generation.conversational_rag.search_layered", new_callable=AsyncMock) as mock_layered,
            patch("src.generation.conversational_rag.generate_answer", new_callable=AsyncMock) as mock_gen,
        ):
            mock_search.return_value = [self._canonical_source()]
            mock_gen.return_value = "The job continues because DIE_ON_ERROR is false [1]."

            response = await run_conversational_rag(
                conversation=mock_conversation,
                query="What does tDBOutput_1 do on failure?",
                user_groups=["g"], qdrant=AsyncMock(), db=mock_db,
            )

        mock_layered.assert_not_awaited()
        assert mock_gen.await_count == 1, "happy path pays exactly one LLM call"
        assert response.fallback_used is False
        assert response.doc_gap is False
        assert response.primary_source_layer == "canonical_docs"
        assert not [o for o in mock_db.added_objects if isinstance(o, DocGapEvent)]

    async def test_need_search_descends_to_rung2_and_logs_gap(
        self, mock_conversation, mock_db, disable_new_features
    ):
        from src.db.models import DocGapEvent

        with (
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
        with (
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

        with (
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
        with (
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
        with (
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

        with (
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

        with (
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
