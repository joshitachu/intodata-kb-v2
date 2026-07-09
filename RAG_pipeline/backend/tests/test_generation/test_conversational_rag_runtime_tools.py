"""Integration tests for the backend runtime tool-use loop wired into
conversational RAG (EP-004 slice 3)."""

import datetime
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from src.db.models import Conversation
from src.generation.conversational_rag import RAGContext, run_conversational_rag, run_conversational_rag_stream
from src.generation.intent_classifier import Action, QueryAnalysis, QueryType
from src.generation.llm_client import RuntimeToolCallRequest, ToolAwareLLMResponse
from src.generation.usage import UsageStats
from src.schemas import Source


def _stream_of(*chunks: str):
    """Fake generate_answer_stream(messages, images=None) replacement — the
    synthesis tail of run_runtime_tool_loop_stream now calls this whenever at
    least one runtime tool ran, so every scenario reaching that tail needs it
    mocked (see agent_loop.run_runtime_tool_loop_stream, EP-004 slice 3)."""

    async def _stream(messages, images=None):
        for chunk in chunks:
            yield chunk

    return _stream


def _no_precompute_context(messages=None) -> RAGContext:
    return RAGContext(
        llm_messages=messages or [{"role": "user", "content": "Did the nightly sync fail last night?"}],
        document_sources=[],
        precomputed_answer=None,
        primary_source_layer=None,
        fallback_used=False,
        doc_gap=False,
    )


def _precomputed_context(answer: str) -> RAGContext:
    return RAGContext(
        llm_messages=[{"role": "user", "content": "hi"}],
        document_sources=[],
        precomputed_answer=answer,
    )


def _mock_conversation():
    conv = MagicMock(spec=Conversation)
    conv.id = 1
    conv.messages = []
    conv.title = None
    return conv


def _mock_db():
    db = AsyncMock()
    added: list = []

    def track_add(obj):
        added.append(obj)

    async def fake_flush():
        for obj in added:
            if not hasattr(obj, "id") or obj.id is None:
                obj.id = 1
            if not hasattr(obj, "created_at") or obj.created_at is None:
                obj.created_at = datetime.datetime(2025, 1, 1, tzinfo=datetime.timezone.utc)

    db.add = MagicMock(side_effect=track_add)
    db.flush = AsyncMock(side_effect=fake_flush)
    db.added_objects = added
    return db


class TestNonStreamingRuntimeLoop:
    async def test_final_answer_references_mocked_tool_result(self):
        """Tool-aware LLM requests detect_failures, the real mock runtime
        provider answers, and the final answer reflects that result."""
        decisions = [
            ToolAwareLLMResponse(
                provider="kimi",
                tool_calls=[RuntimeToolCallRequest(call_id="call_1", tool_name="detect_failures", arguments={})],
                assistant_message={"role": "assistant", "content": "", "tool_calls": []},
                usage=UsageStats(provider="kimi", model="kimi-k2.5", input_tokens=10, output_tokens=5),
            ),
            ToolAwareLLMResponse(
                provider="kimi",
                text="The nightly_sync job failed.",
                usage=UsageStats(provider="kimi", model="kimi-k2.5", input_tokens=10, output_tokens=5),
            ),
        ]

        with (
            patch("src.generation.conversational_rag._prepare_rag_context", new_callable=AsyncMock) as mock_ctx,
            patch("src.runtime.client.settings") as mock_runtime_settings,
            patch(
                "src.generation.agent_loop.generate_answer_with_runtime_tools",
                AsyncMock(side_effect=decisions),
            ),
            patch("src.generation.agent_loop.generate_answer_stream", _stream_of("The nightly_sync job failed.")),
            patch("src.generation.agent_loop.get_last_usage", return_value=None),
        ):
            mock_ctx.return_value = _no_precompute_context()
            mock_runtime_settings.runtime_provider_key = "mock"
            mock_runtime_settings.runtime_tool_timeout = 10.0
            mock_runtime_settings.runtime_tool_result_max_chars = 12000

            response = await run_conversational_rag(
                conversation=_mock_conversation(),
                query="Did the nightly sync fail last night?",
                user_groups=["engineering"],
                qdrant=AsyncMock(),
                db=_mock_db(),
            )

        assert response.content == "The nightly_sync job failed."

    async def test_source_and_doc_gap_metadata_preserved(self):
        """Document sources / doc-gap metadata from _prepare_rag_context still
        flow through to the persisted assistant message when the loop runs."""
        from src.schemas import DocumentSource

        ctx = _no_precompute_context()
        ctx.document_sources = [
            DocumentSource(document_id=1, filename="runbook.pdf", score=0.9, text="...", reference_number=1)
        ]
        ctx.doc_gap = True
        ctx.primary_source_layer = "internal_layers"
        ctx.fallback_used = True

        with (
            patch("src.generation.conversational_rag._prepare_rag_context", new_callable=AsyncMock) as mock_ctx,
            patch(
                "src.generation.agent_loop.generate_answer_with_runtime_tools",
                AsyncMock(return_value=ToolAwareLLMResponse(provider="kimi", no_tool_support=True)),
            ),
            patch("src.generation.conversational_rag.generate_answer", new_callable=AsyncMock) as mock_gen,
        ):
            mock_ctx.return_value = ctx
            mock_gen.return_value = "Fallback answer"
            db = _mock_db()

            response = await run_conversational_rag(
                conversation=_mock_conversation(),
                query="What does the runbook say?",
                user_groups=["engineering"],
                qdrant=AsyncMock(),
                db=db,
            )

        assert response.content.startswith("Fallback answer")
        assert "runtime tools are unavailable" in response.content
        assert response.doc_gap is True
        assert response.primary_source_layer == "internal_layers"
        assert len(response.sources) == 1
        assert response.sources[0].filename == "runbook.pdf"

    async def test_terminal_precomputed_answer_skips_runtime_loop(self):
        """A terminal precomputed answer (chitchat/metadata/summary/no-results,
        allow_runtime_tools=False) must short-circuit without running the loop."""
        with (
            patch("src.generation.conversational_rag._prepare_rag_context", new_callable=AsyncMock) as mock_ctx,
            patch("src.generation.conversational_rag.run_runtime_tool_loop", new_callable=AsyncMock) as mock_loop,
        ):
            mock_ctx.return_value = _precomputed_context("Greetings!")

            response = await run_conversational_rag(
                conversation=_mock_conversation(),
                query="hi",
                user_groups=["engineering"],
                qdrant=AsyncMock(),
                db=_mock_db(),
            )

        assert response.content == "Greetings!"
        mock_loop.assert_not_called()

    async def test_qa_precomputed_answer_still_runs_runtime_loop(self):
        """A QA document-backed answer (allow_runtime_tools=True) must invoke the
        runtime loop even though a precomputed answer exists, and prefer the loop's
        combined answer."""
        from src.generation.agent_loop import RuntimeLoopOutcome

        loop_mock = AsyncMock(
            return_value=RuntimeLoopOutcome(
                final_answer="Combined: job failed (from logs); doc says retry (from runbook.pdf).",
                used_runtime_data=True,
                progress_events=[],
                usage=None,
            )
        )
        with (
            patch("src.generation.conversational_rag._prepare_rag_context", new_callable=AsyncMock) as mock_ctx,
            patch("src.generation.conversational_rag.run_runtime_tool_loop", loop_mock),
        ):
            ctx = _precomputed_context("Doc-only answer.")
            ctx.allow_runtime_tools = True
            mock_ctx.return_value = ctx

            response = await run_conversational_rag(
                conversation=_mock_conversation(),
                query="the job failed — what does the runbook say?",
                user_groups=["engineering"],
                qdrant=AsyncMock(),
                db=_mock_db(),
            )

        loop_mock.assert_awaited_once()
        assert response.content.startswith("Combined:")

    async def test_request_project_ids_forwarded_as_scope(self):
        """The request principal's project_ids must reach the runtime loop as
        project_scope (and the db session must be forwarded) — the RBAC channel."""
        from src.generation.agent_loop import RuntimeLoopOutcome

        db = _mock_db()
        loop_mock = AsyncMock(
            return_value=RuntimeLoopOutcome(
                final_answer="ok", used_runtime_data=True, progress_events=[], usage=None
            )
        )
        with (
            patch("src.generation.conversational_rag._prepare_rag_context", new_callable=AsyncMock) as mock_ctx,
            patch("src.generation.conversational_rag.run_runtime_tool_loop", loop_mock),
        ):
            mock_ctx.return_value = _no_precompute_context()

            await run_conversational_rag(
                conversation=_mock_conversation(),
                query="did it fail?",
                user_groups=["engineering"],
                qdrant=AsyncMock(),
                db=db,
                project_ids=["project-a"],
            )

        _, kwargs = loop_mock.call_args
        assert kwargs["project_scope"] == ["project-a"]
        assert kwargs["db"] is db


class TestSlice4RestParity:
    """Non-streaming (REST) parity for the runtime-intent gate (EP-004 slice 4).

    Unlike the tests above, these deliberately do NOT mock
    _prepare_rag_context — they drive the real function end-to-end through
    run_conversational_rag so a regression in the gate logic itself (not just
    the dispatch if/else around it) would be caught."""

    @staticmethod
    def _canonical_source() -> Source:
        return Source(
            document_id=1, filename="phase2/06_OPERATIONS.md", chunk_index=0,
            score=0.8, text="The job uses tDBOutput_1 with DIE_ON_ERROR=false.",
            source_layer="canonical_docs", project_id="phase2",
            display_name="06_OPERATIONS.md (Project: phase2)",
        )

    @staticmethod
    def _guidance_source() -> Source:
        return Source(
            document_id=21, filename="phase2/failure-guidance.pdf", chunk_index=0,
            score=0.7, text="Explain failure behavior against operational guidance.",
            document_role="guidance", source_layer="canonical_docs", project_id="phase2",
            display_name="failure-guidance.pdf (Project: phase2)",
        )

    @staticmethod
    def _runtime_gate_source() -> Source:
        return Source(
            document_id=5, filename="ops/runbook.pdf", chunk_index=0,
            score=0.75, text="Runbook: retry the failed job after 10 minutes.",
            source_layer="canonical_docs", project_id="ops",
        )

    async def test_document_qa_generates_exactly_once_and_never_enters_tool_loop(self):
        """AC1: a document-QA REST call produces exactly one answer-generation
        call and never opens the runtime tool loop (allow_runtime_tools stays
        False through the real rung-1 ladder)."""
        search_analysis = QueryAnalysis(
            action=Action.SEARCH, query_type=QueryType.QA,
            cleaned_query="What does tDBOutput_1 do on failure?",
        )
        with (
            patch("src.generation.conversational_rag.settings") as mock_settings,
            patch(
                "src.generation.conversational_rag.analyze_query_async",
                new_callable=AsyncMock, return_value=search_analysis,
            ),
            patch("src.generation.conversational_rag.detect_runtime_keywords", return_value=False),
            patch("src.generation.conversational_rag.search_similar", new_callable=AsyncMock) as mock_search,
            patch("src.generation.conversational_rag.generate_answer", new_callable=AsyncMock) as mock_gen,
            patch(
                "src.generation.conversational_rag.run_runtime_tool_loop", new_callable=AsyncMock
            ) as mock_run_loop,
        ):
            mock_settings.query_expansion_enabled = False
            mock_settings.reranker_enabled = False
            mock_settings.hybrid_search_enabled = False
            mock_settings.system_knowledge_injection = False
            mock_search.side_effect = [[self._canonical_source()], [self._guidance_source()]]
            mock_gen.return_value = "The job continues because DIE_ON_ERROR is false [1]."

            response = await run_conversational_rag(
                conversation=_mock_conversation(),
                query="What does tDBOutput_1 do on failure?",
                user_groups=["engineering"],
                qdrant=AsyncMock(),
                db=_mock_db(),
            )

        assert mock_gen.await_count == 1, "happy path pays exactly one LLM call"
        mock_run_loop.assert_not_awaited()
        assert response.content == "The job continues because DIE_ON_ERROR is false [1]."

    async def test_chitchat_never_enters_tool_loop_or_builds_tool_schemas(self):
        """AC2: a chitchat REST call (Action.RESPOND) never includes tool
        schemas / a tool-use system prompt in its outbound LLM payload —
        structurally proven by generate_answer_with_runtime_tools (the only
        call site that ever builds tool schemas) never being invoked."""
        respond_analysis = QueryAnalysis(action=Action.RESPOND, cleaned_query="hi")
        with (
            patch("src.generation.conversational_rag.settings") as mock_settings,
            patch(
                "src.generation.conversational_rag.analyze_query_async",
                new_callable=AsyncMock, return_value=respond_analysis,
            ),
            patch("src.generation.conversational_rag.detect_runtime_keywords", return_value=False),
            patch("src.generation.conversational_rag.generate_answer", new_callable=AsyncMock) as mock_gen,
            patch(
                "src.generation.conversational_rag.run_runtime_tool_loop", new_callable=AsyncMock
            ) as mock_run_loop,
            patch(
                "src.generation.agent_loop.generate_answer_with_runtime_tools", new_callable=AsyncMock
            ) as mock_tool_aware,
        ):
            mock_settings.query_expansion_enabled = False
            mock_settings.reranker_enabled = False
            mock_settings.hybrid_search_enabled = False
            mock_settings.system_knowledge_injection = False
            mock_gen.return_value = "Hi there!"

            response = await run_conversational_rag(
                conversation=_mock_conversation(),
                query="hi",
                user_groups=["engineering"],
                qdrant=AsyncMock(),
                db=_mock_db(),
            )

        assert response.content == "Hi there!"
        mock_run_loop.assert_not_called()
        mock_tool_aware.assert_not_called()

    async def test_runtime_gated_call_returns_document_context_with_tool_loop_result(self):
        """AC3: a runtime-gated REST call (classifier runtime_intent=True)
        still returns document context alongside the tool loop's combined
        answer — parity with Slice 1's streaming-path assertion."""
        from src.generation.agent_loop import RuntimeLoopOutcome

        runtime_analysis = QueryAnalysis(
            action=Action.SEARCH, query_type=QueryType.QA,
            cleaned_query="Did the nightly sync fail — what does the runbook say?",
            runtime_intent=True,
        )
        loop_mock = AsyncMock(
            return_value=RuntimeLoopOutcome(
                final_answer="Combined: job failed (from logs); retry after 10 minutes (from runbook.pdf).",
                used_runtime_data=True,
                progress_events=[],
                usage=None,
            )
        )
        with (
            patch("src.generation.conversational_rag.settings") as mock_settings,
            patch(
                "src.generation.conversational_rag.analyze_query_async",
                new_callable=AsyncMock, return_value=runtime_analysis,
            ),
            patch("src.generation.conversational_rag.detect_runtime_keywords", return_value=False),
            patch(
                "src.generation.conversational_rag._search_qa_sources",
                new_callable=AsyncMock, return_value=[self._runtime_gate_source()],
            ) as mock_search_qa,
            patch("src.generation.conversational_rag.run_runtime_tool_loop", loop_mock),
        ):
            mock_settings.query_expansion_enabled = False
            mock_settings.reranker_enabled = False
            mock_settings.hybrid_search_enabled = False
            mock_settings.system_knowledge_injection = False

            response = await run_conversational_rag(
                conversation=_mock_conversation(),
                query="Did the nightly sync fail — what does the runbook say?",
                user_groups=["engineering"],
                qdrant=AsyncMock(),
                db=_mock_db(),
            )

        assert mock_search_qa.await_count == 2, "one source-role call + one auxiliary-role call"
        loop_mock.assert_awaited_once()
        assert response.content == "Combined: job failed (from logs); retry after 10 minutes (from runbook.pdf)."
        assert response.sources is not None
        assert len(response.sources) == 1
        assert response.sources[0].filename == "ops/runbook.pdf"

        # The tool loop must have actually received the retrieved document
        # text in its llm_messages, not just have response.sources populated
        # independently of what was sent to the loop.
        llm_messages = loop_mock.await_args.args[0]
        assert any(
            "retry the failed job after 10 minutes" in m.get("content", "")
            for m in llm_messages
        )


class TestRuntimeToolSummaryPersistence:
    @staticmethod
    def _assistant_msg(db):
        return next(m for m in db.added_objects if getattr(m, "role", None) == "assistant")

    async def test_runtime_turn_persists_summary(self):
        """A turn that uses runtime tools writes a runtime_tool_summary referencing the
        execution, built from the (sanitized) tool result."""
        decisions = [
            ToolAwareLLMResponse(
                provider="kimi",
                tool_calls=[RuntimeToolCallRequest(call_id="call_1", tool_name="detect_failures", arguments={})],
                assistant_message={"role": "assistant", "content": "", "tool_calls": []},
                usage=UsageStats(provider="kimi", model="kimi-k2.5", input_tokens=10, output_tokens=5),
            ),
            ToolAwareLLMResponse(
                provider="kimi",
                text="The nightly_sync job failed.",
                usage=UsageStats(provider="kimi", model="kimi-k2.5", input_tokens=10, output_tokens=5),
            ),
        ]
        db = _mock_db()
        with (
            patch("src.generation.conversational_rag._prepare_rag_context", new_callable=AsyncMock) as mock_ctx,
            patch("src.runtime.client.settings") as mock_runtime_settings,
            patch("src.generation.agent_loop.generate_answer_with_runtime_tools", AsyncMock(side_effect=decisions)),
            patch("src.generation.agent_loop.generate_answer_stream", _stream_of("The nightly_sync job failed.")),
            patch("src.generation.agent_loop.get_last_usage", return_value=None),
        ):
            mock_ctx.return_value = _no_precompute_context()
            mock_runtime_settings.runtime_provider_key = "mock"
            mock_runtime_settings.runtime_tool_timeout = 10.0
            mock_runtime_settings.runtime_tool_result_max_chars = 12000

            await run_conversational_rag(
                conversation=_mock_conversation(),
                query="Did the nightly sync fail last night?",
                user_groups=["engineering"],
                qdrant=AsyncMock(),
                db=db,
            )

        summary = self._assistant_msg(db).runtime_tool_summary
        assert summary is not None
        assert any(e.get("tool_name") == "detect_failures" and e.get("execution_id") for e in summary)

    async def test_precomputed_turn_leaves_summary_null(self):
        db = _mock_db()
        with patch("src.generation.conversational_rag._prepare_rag_context", new_callable=AsyncMock) as mock_ctx:
            mock_ctx.return_value = _precomputed_context("Greetings!")
            await run_conversational_rag(
                conversation=_mock_conversation(),
                query="hi",
                user_groups=["engineering"],
                qdrant=AsyncMock(),
                db=db,
            )
        assert self._assistant_msg(db).runtime_tool_summary is None


class TestRuntimeToolSummaryReplay:
    """Slice 2: the most recent prior summary is injected into the next turn's runtime
    context, and only from the same conversation (RBAC isolation)."""

    async def _system_prompt_for_turn(self, conversation):
        captured: dict = {}

        async def _capture(messages, tools, images=None):
            captured["messages"] = messages
            return ToolAwareLLMResponse(
                provider="kimi",
                text="done",
                usage=UsageStats(provider="kimi", model="kimi-k2.5", input_tokens=1, output_tokens=1),
            )

        with (
            patch("src.generation.conversational_rag._prepare_rag_context", new_callable=AsyncMock) as mock_ctx,
            patch("src.runtime.client.settings") as mock_rt,
            patch("src.generation.agent_loop.generate_answer_with_runtime_tools", _capture),
        ):
            mock_ctx.return_value = _no_precompute_context()
            mock_rt.runtime_provider_key = "mock"
            mock_rt.runtime_tool_timeout = 10.0
            mock_rt.runtime_tool_result_max_chars = 12000
            await run_conversational_rag(
                conversation=conversation,
                query="show me the logs for that execution",
                user_groups=["engineering"],
                qdrant=AsyncMock(),
                db=_mock_db(),
            )
        return next(m["content"] for m in captured["messages"] if m["role"] == "system")

    async def test_prior_summary_injected_for_same_conversation(self):
        conv = _mock_conversation()
        conv.messages = [
            SimpleNamespace(
                role="assistant",
                runtime_tool_summary=[
                    {"tool_name": "detect_failures", "execution_id": "esbmain-route-7841", "job_name": "esb_order_sync_route"}
                ],
            )
        ]
        content = await self._system_prompt_for_turn(conv)
        assert "Recent runtime context" in content
        assert "esbmain-route-7841" in content

    async def test_other_conversation_summary_not_injected(self):
        # A conversation with no prior summary of its own must not receive another
        # conversation's runtime memory (the reader only sees this conversation).
        conv = _mock_conversation()
        conv.messages = []
        content = await self._system_prompt_for_turn(conv)
        assert "Recent runtime context" not in content
        assert "esbmain-route-7841" not in content

    async def test_no_tool_support_falls_back_to_plain_generate_answer(self):
        """Provider chain offers no tool support → existing generate_answer()
        path is used, never claiming live runtime data."""
        with (
            patch("src.generation.conversational_rag._prepare_rag_context", new_callable=AsyncMock) as mock_ctx,
            patch(
                "src.generation.agent_loop.generate_answer_with_runtime_tools",
                AsyncMock(return_value=ToolAwareLLMResponse(provider="kimi", no_tool_support=True)),
            ),
            patch("src.generation.conversational_rag.generate_answer", new_callable=AsyncMock) as mock_gen,
        ):
            mock_ctx.return_value = _no_precompute_context()
            mock_gen.return_value = "I don't have access to live runtime data."

            response = await run_conversational_rag(
                conversation=_mock_conversation(),
                query="Did the nightly sync fail?",
                user_groups=["engineering"],
                qdrant=AsyncMock(),
                db=_mock_db(),
            )

        mock_gen.assert_awaited_once()
        assert response.content.startswith("I don't have access to live runtime data.")
        assert "runtime tools are unavailable" in response.content


class TestStreamingRuntimeLoop:
    async def test_stream_emits_runtime_events_then_token_usage_done(self):
        decisions = [
            ToolAwareLLMResponse(
                provider="kimi",
                tool_calls=[RuntimeToolCallRequest(call_id="call_1", tool_name="detect_failures", arguments={})],
                assistant_message={"role": "assistant", "content": "", "tool_calls": []},
                usage=UsageStats(provider="kimi", model="kimi-k2.5", input_tokens=10, output_tokens=5),
            ),
            ToolAwareLLMResponse(
                provider="kimi",
                text="The nightly_sync job failed.",
                usage=UsageStats(provider="kimi", model="kimi-k2.5", input_tokens=10, output_tokens=5),
            ),
        ]

        with (
            patch("src.generation.conversational_rag._prepare_rag_context", new_callable=AsyncMock) as mock_ctx,
            patch("src.runtime.client.settings") as mock_runtime_settings,
            patch(
                "src.generation.agent_loop.generate_answer_with_runtime_tools",
                AsyncMock(side_effect=decisions),
            ),
            patch(
                "src.generation.agent_loop.generate_answer_stream",
                _stream_of("The ", "nightly_sync ", "job failed."),
            ),
            patch("src.generation.agent_loop.get_last_usage", return_value=None),
        ):
            ctx = _no_precompute_context()
            ctx.agent_steps = []
            # allow_runtime_tools must be explicit now: the streaming handler
            # gates tool-loop entry on this field, not on precomputed_answer
            # being None. "Did the nightly sync fail?" is exactly the kind of
            # query the runtime-intent gate (conversational_rag.py) routes in.
            ctx.allow_runtime_tools = True
            mock_ctx.return_value = ctx
            mock_runtime_settings.runtime_provider_key = "mock"
            mock_runtime_settings.runtime_tool_timeout = 10.0
            mock_runtime_settings.runtime_tool_result_max_chars = 12000

            events = []
            async for event_type, data in run_conversational_rag_stream(
                conversation=_mock_conversation(),
                query="Did the nightly sync fail?",
                user_groups=["engineering"],
                qdrant=AsyncMock(),
                db=_mock_db(),
            ):
                events.append((event_type, data))

        event_types = [e[0] for e in events]
        # Tool-call/result events, then the final answer streamed as multiple
        # live token events (EP-004 slice 3) — not batched at the end.
        assert event_types == [
            "sources", "runtime_tool_call", "runtime_tool_result",
            "token", "token", "token", "usage", "done",
        ]
        call_idx = event_types.index("runtime_tool_call")
        result_idx = event_types.index("runtime_tool_result")
        token_indices = [i for i, t in enumerate(event_types) if t == "token"]
        assert call_idx < result_idx < min(token_indices)
        assert len(token_indices) > 1  # streamed, not one block
        token_text = "".join(json.loads(events[i][1]) for i in token_indices)
        assert token_text == "The nightly_sync job failed."
