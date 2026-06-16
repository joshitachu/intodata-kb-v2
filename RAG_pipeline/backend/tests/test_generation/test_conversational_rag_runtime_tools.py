"""Integration tests for the backend runtime tool-use loop wired into
conversational RAG (EP-004 slice 3)."""

import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from src.db.models import Conversation
from src.generation.conversational_rag import RAGContext, run_conversational_rag, run_conversational_rag_stream
from src.generation.llm_client import RuntimeToolCallRequest, ToolAwareLLMResponse
from src.generation.usage import UsageStats


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
                tool_call=RuntimeToolCallRequest(call_id="call_1", tool_name="detect_failures", arguments={}),
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
                tool_call=RuntimeToolCallRequest(call_id="call_1", tool_name="detect_failures", arguments={}),
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
                tool_call=RuntimeToolCallRequest(call_id="call_1", tool_name="detect_failures", arguments={}),
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
        ):
            ctx = _no_precompute_context()
            ctx.agent_steps = []
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
        assert event_types == ["sources", "runtime_tool_call", "runtime_tool_result", "token", "usage", "done"]
        token_payload = events[event_types.index("token")][1]
        assert "nightly_sync" in token_payload
