"""Cross-reference tests (EP-005 slice 1): the conversational RAG turn combines
live runtime tool results with indexed documents, threads a single RBAC scope to
both data sources via RequestScope, and surfaces a visible signal when the active
provider has no structured tool support."""

import datetime
from unittest.mock import AsyncMock, MagicMock, patch

from src.db.models import Conversation
from src.generation.agent_loop import RuntimeLoopOutcome
from src.generation.conversational_rag import (
    _RUNTIME_UNAVAILABLE_NOTE,
    RAGContext,
    run_conversational_rag,
    run_conversational_rag_stream,
)
from src.runtime.scope import RequestScope
from src.schemas import DocumentSource


def _qa_doc_context(answer: str) -> RAGContext:
    """A QA document-backed context: precomputed doc answer that may be augmented
    with runtime tools (allow_runtime_tools=True)."""
    return RAGContext(
        llm_messages=[{"role": "user", "content": "the job failed — what does the runbook say?"}],
        document_sources=[
            DocumentSource(document_id=1, filename="esbmain/runbook.pdf", score=0.9, text="...", reference_number=1)
        ],
        precomputed_answer=answer,
        allow_runtime_tools=True,
        primary_source_layer="canonical_docs",
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


class TestRequestScope:
    def test_unrestricted_scope_passes_none_to_both(self):
        scope = RequestScope.from_project_ids(None)
        assert scope.project_ids is None
        assert scope.project_scope is None

    def test_restricted_scope_couples_both_names(self):
        scope = RequestScope.from_project_ids(["project-a"])
        assert scope.project_ids == ["project-a"]
        assert scope.project_scope == ["project-a"]
        # Both names resolve to the same value — the coupling guarantee.
        assert scope.project_ids == scope.project_scope

    def test_empty_scope_means_no_access(self):
        scope = RequestScope.from_project_ids([])
        assert scope.project_ids == []
        assert scope.project_scope == []

    def test_is_immutable(self):
        import dataclasses

        import pytest

        scope = RequestScope.from_project_ids(["p"])
        with pytest.raises(dataclasses.FrozenInstanceError):
            scope.allowed_project_ids = ("other",)  # type: ignore[misc]


class TestCombinedAnswer:
    async def test_combined_answer_uses_runtime_loop_result_over_doc_only(self):
        """A QA doc-backed turn runs the runtime loop and prefers the loop's
        combined (logs + docs) answer, keeping the document sources."""
        loop_mock = AsyncMock(
            return_value=RuntimeLoopOutcome(
                final_answer=(
                    "esbmain-route-7841 failed with ORA-12170 (from logs). The runbook "
                    "says to check the firewall to the Oracle host (from runbook.pdf)."
                ),
                used_runtime_data=True,
                progress_events=[],
                usage=None,
            )
        )
        with (
            patch("src.generation.conversational_rag._prepare_rag_context", new_callable=AsyncMock) as mock_ctx,
            patch("src.generation.conversational_rag.run_runtime_tool_loop", loop_mock),
        ):
            mock_ctx.return_value = _qa_doc_context("Doc-only: the runbook covers ORA errors.")

            response = await run_conversational_rag(
                conversation=_mock_conversation(),
                query="what failed and what does the ESB doc say?",
                user_groups=["engineering"],
                qdrant=AsyncMock(),
                db=_mock_db(),
            )

        loop_mock.assert_awaited_once()
        assert "(from logs)" in response.content
        assert "(from runbook.pdf)" in response.content
        assert response.sources and response.sources[0].filename == "esbmain/runbook.pdf"


class TestScopeCoupling:
    async def test_same_scope_reaches_both_document_prep_and_runtime_loop(self):
        """RequestScope threads the same authenticated allow-list into both
        document retrieval (_prepare_rag_context) and the runtime loop."""
        loop_mock = AsyncMock(
            return_value=RuntimeLoopOutcome(
                final_answer="ok", used_runtime_data=True, progress_events=[], usage=None
            )
        )
        with (
            patch("src.generation.conversational_rag._prepare_rag_context", new_callable=AsyncMock) as mock_ctx,
            patch("src.generation.conversational_rag.run_runtime_tool_loop", loop_mock),
        ):
            mock_ctx.return_value = _qa_doc_context("doc answer")

            await run_conversational_rag(
                conversation=_mock_conversation(),
                query="did project-a fail?",
                user_groups=["engineering"],
                qdrant=AsyncMock(),
                db=_mock_db(),
                project_ids=["project-a"],
            )

        prep_kwargs = mock_ctx.call_args.kwargs
        loop_kwargs = loop_mock.call_args.kwargs
        assert prep_kwargs["project_ids"] == ["project-a"]
        assert loop_kwargs["project_scope"] == ["project-a"]
        # The coupling invariant: document scope and runtime scope are identical.
        assert prep_kwargs["project_ids"] == loop_kwargs["project_scope"]

    async def test_no_access_scope_is_threaded_as_empty_to_both(self):
        """A user with no project assignments ([]) propagates [] to both sources,
        never None (which would be unrestricted)."""
        loop_mock = AsyncMock(
            return_value=RuntimeLoopOutcome(
                final_answer="ok", used_runtime_data=False, progress_events=[], usage=None
            )
        )
        with (
            patch("src.generation.conversational_rag._prepare_rag_context", new_callable=AsyncMock) as mock_ctx,
            patch("src.generation.conversational_rag.run_runtime_tool_loop", loop_mock),
        ):
            mock_ctx.return_value = _qa_doc_context("doc answer")

            await run_conversational_rag(
                conversation=_mock_conversation(),
                query="anything?",
                user_groups=[],
                qdrant=AsyncMock(),
                db=_mock_db(),
                project_ids=[],
            )

        assert mock_ctx.call_args.kwargs["project_ids"] == []
        assert loop_mock.call_args.kwargs["project_scope"] == []


class TestNoToolSupportVisibility:
    async def test_non_streaming_appends_visible_note(self):
        loop_mock = AsyncMock(
            return_value=RuntimeLoopOutcome(
                final_answer=None, used_runtime_data=False, progress_events=[], usage=None
            )
        )
        with (
            patch("src.generation.conversational_rag._prepare_rag_context", new_callable=AsyncMock) as mock_ctx,
            patch("src.generation.conversational_rag.run_runtime_tool_loop", loop_mock),
        ):
            mock_ctx.return_value = _qa_doc_context("The runbook explains the retry policy.")

            response = await run_conversational_rag(
                conversation=_mock_conversation(),
                query="did it fail and what does the doc say?",
                user_groups=["engineering"],
                qdrant=AsyncMock(),
                db=_mock_db(),
            )

        assert response.content.startswith("The runbook explains the retry policy.")
        assert _RUNTIME_UNAVAILABLE_NOTE in response.content

    async def test_streaming_emits_note_token(self):
        loop_mock = AsyncMock(
            return_value=RuntimeLoopOutcome(
                final_answer=None, used_runtime_data=False, progress_events=[], usage=None
            )
        )
        with (
            patch("src.generation.conversational_rag._prepare_rag_context", new_callable=AsyncMock) as mock_ctx,
            patch("src.generation.conversational_rag.run_runtime_tool_loop", loop_mock),
        ):
            ctx = _qa_doc_context("The runbook explains the retry policy.")
            ctx.agent_steps = []
            mock_ctx.return_value = ctx

            tokens = []
            async for event_type, data in run_conversational_rag_stream(
                conversation=_mock_conversation(),
                query="did it fail and what does the doc say?",
                user_groups=["engineering"],
                qdrant=AsyncMock(),
                db=_mock_db(),
            ):
                if event_type == "token":
                    tokens.append(data)

        joined = "".join(tokens)
        assert "retry policy" in joined
        assert _RUNTIME_UNAVAILABLE_NOTE in joined
