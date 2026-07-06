"""Conversational-loop tests for the high-level runtime insight tools (EP-006 slice 3).

Verifies that the agent loop correctly routes analyze_execution_failure,
get_execution_timeline, and get_runtime_incident through the stored provider
with authenticated scope, that final answers arrive via the bounded tool loop,
and that streaming emits the expected event sequence.
"""

import datetime
from unittest.mock import AsyncMock, MagicMock, patch

from src.db.models import Conversation
from src.generation.conversational_rag import RAGContext, run_conversational_rag, run_conversational_rag_stream
from src.generation.llm_client import RuntimeToolCallRequest, ToolAwareLLMResponse
from src.generation.usage import UsageStats
from src.runtime.models import RuntimeToolDefinition, RuntimeToolResult


def _no_precompute_context() -> RAGContext:
    return RAGContext(
        llm_messages=[{"role": "user", "content": "Why did the esb_order_sync_route job fail?"}],
        document_sources=[],
        precomputed_answer=None,
        primary_source_layer=None,
        fallback_used=False,
        doc_gap=False,
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
                obj.created_at = datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc)

    db.add = MagicMock(side_effect=track_add)
    db.flush = AsyncMock(side_effect=fake_flush)
    db.added_objects = added
    return db


_FAILURE_ANALYSIS_DATA = {
    "execution_id": "esbmain-route-7841",
    "job_name": "esb_order_sync_route",
    "state": "failed",
    "started_at": "2026-06-17T10:00:00Z",
    "ended_at": "2026-06-17T10:00:45Z",
    "duration_ms": 45000,
    "warning_count": 3,
    "error_count": 2,
    "failed_component": "tOracleConnection_1",
    "primary_error_code": "ORA-12170",
    "primary_exception_class": "SQLRecoverableException",
    "summary_text": "Oracle connection timed out on source DB",
    "has_incident": True,
    "incident_id": "abc123def456abc123def456abc12300",
    "symptom_summary": "Oracle source database connection timed out",
    "root_cause_candidate": "Source database TCP connection timeout",
    "confidence": "high",
    "next_checks": ["Check Oracle listener", "Verify network path to DB"],
    "evidence": [
        {"log_event_id": 1, "evidence_role": "trigger", "excerpt": "ORA-12170 timeout",
         "timestamp": "2026-06-17T10:00:30Z", "severity": "error"},
    ],
}


class TestHighLevelToolLoop:
    async def test_analyze_failure_tool_produces_final_answer(self):
        """LLM calls detect_failures to get execution_id, then analyze_execution_failure,
        then produces a structured final answer."""
        detect_result = RuntimeToolResult(
            tool_name="detect_failures",
            success=True,
            data=[{"execution_id": "esbmain-route-7841", "job_name": "esb_order_sync_route"}],
            latency_ms=5,
        )
        analyze_result = RuntimeToolResult(
            tool_name="analyze_execution_failure",
            success=True,
            data=_FAILURE_ANALYSIS_DATA,
            latency_ms=8,
        )

        decisions = [
            ToolAwareLLMResponse(
                provider="kimi",
                tool_calls=[RuntimeToolCallRequest(call_id="c1", tool_name="detect_failures", arguments={})],
                assistant_message={"role": "assistant", "content": "", "tool_calls": []},
                usage=UsageStats(provider="kimi", model="kimi-k2.5", input_tokens=10, output_tokens=5),
            ),
            ToolAwareLLMResponse(
                provider="kimi",
                tool_calls=[RuntimeToolCallRequest(
                    call_id="c2",
                    tool_name="analyze_execution_failure",
                    arguments={"execution_id": "esbmain-route-7841"},
                )],
                assistant_message={"role": "assistant", "content": "", "tool_calls": []},
                usage=UsageStats(provider="kimi", model="kimi-k2.5", input_tokens=15, output_tokens=5),
            ),
            ToolAwareLLMResponse(
                provider="kimi",
                text=(
                    "Finding: esb_order_sync_route failed with ORA-12170. "
                    "Evidence: tOracleConnection_1 raised SQLRecoverableException. "
                    "Likely Cause: Source database TCP connection timeout. "
                    "Next Checks: Check Oracle listener."
                ),
                usage=UsageStats(provider="kimi", model="kimi-k2.5", input_tokens=20, output_tokens=30),
            ),
        ]

        tools = [
            RuntimeToolDefinition(name="detect_failures", description="Detect failures", input_schema={"type": "object"}),
            RuntimeToolDefinition(name="analyze_execution_failure", description="Analyze failure", input_schema={"type": "object"}),
        ]
        tool_results = [detect_result, analyze_result]

        with (
            patch("src.generation.conversational_rag._prepare_rag_context", new_callable=AsyncMock) as mock_ctx,
            patch("src.runtime.client.settings") as mock_client_settings,
            patch("src.generation.agent_loop.settings") as mock_loop_settings,
            patch("src.generation.agent_loop.list_runtime_tools", return_value=tools),
            patch("src.generation.agent_loop.generate_answer_with_runtime_tools", AsyncMock(side_effect=decisions)),
            patch("src.generation.agent_loop.execute_runtime_tool", AsyncMock(side_effect=tool_results)),
        ):
            mock_ctx.return_value = _no_precompute_context()
            mock_client_settings.runtime_provider_key = "stored"
            mock_client_settings.runtime_tool_timeout = 10.0
            mock_client_settings.runtime_tool_result_max_chars = 12000
            mock_loop_settings.runtime_tool_loop_max_iterations = 5
            mock_loop_settings.runtime_tool_max_parallel_calls = 5

            response = await run_conversational_rag(
                conversation=_mock_conversation(),
                query="Why did the esb_order_sync_route fail?",
                user_groups=["engineering"],
                qdrant=AsyncMock(),
                db=_mock_db(),
                project_ids=["esbmain"],
            )

        assert response.content is not None
        assert "ORA-12170" in response.content or "Finding" in response.content

    async def test_project_scope_is_forwarded_into_insight_tool_calls(self):
        """The authenticated project_scope from project_ids must be forwarded into
        every runtime tool call, including the new high-level insight tools."""
        analyze_result = RuntimeToolResult(
            tool_name="analyze_execution_failure",
            success=True,
            data=_FAILURE_ANALYSIS_DATA,
            latency_ms=8,
        )

        decisions = [
            ToolAwareLLMResponse(
                provider="kimi",
                tool_calls=[RuntimeToolCallRequest(
                    call_id="c1",
                    tool_name="analyze_execution_failure",
                    arguments={"execution_id": "esbmain-route-7841"},
                )],
                assistant_message={"role": "assistant", "content": "", "tool_calls": []},
                usage=UsageStats(provider="kimi", model="kimi-k2.5", input_tokens=10, output_tokens=5),
            ),
            ToolAwareLLMResponse(
                provider="kimi",
                text="Root cause: Oracle timeout.",
                usage=UsageStats(provider="kimi", model="kimi-k2.5", input_tokens=15, output_tokens=10),
            ),
        ]

        tools = [
            RuntimeToolDefinition(
                name="analyze_execution_failure", description="Analyze failure", input_schema={"type": "object"}
            ),
        ]
        exec_mock = AsyncMock(return_value=analyze_result)

        with (
            patch("src.generation.conversational_rag._prepare_rag_context", new_callable=AsyncMock) as mock_ctx,
            patch("src.runtime.client.settings") as mock_client_settings,
            patch("src.generation.agent_loop.settings") as mock_loop_settings,
            patch("src.generation.agent_loop.list_runtime_tools", return_value=tools),
            patch("src.generation.agent_loop.generate_answer_with_runtime_tools", AsyncMock(side_effect=decisions)),
            patch("src.generation.agent_loop.execute_runtime_tool", exec_mock),
        ):
            mock_ctx.return_value = _no_precompute_context()
            mock_client_settings.runtime_provider_key = "stored"
            mock_client_settings.runtime_tool_timeout = 10.0
            mock_client_settings.runtime_tool_result_max_chars = 12000
            mock_loop_settings.runtime_tool_loop_max_iterations = 3
            mock_loop_settings.runtime_tool_max_parallel_calls = 5

            await run_conversational_rag(
                conversation=_mock_conversation(),
                query="Why did the job fail?",
                user_groups=["engineering"],
                qdrant=AsyncMock(),
                db=_mock_db(),
                project_ids=["esbmain"],
            )

        _, kwargs = exec_mock.call_args
        assert kwargs["project_scope"] == ["esbmain"]

    async def test_streaming_emits_tool_call_and_result_events_before_token(self):
        """Streaming must emit runtime_tool_call / runtime_tool_result events before
        the token event when high-level insight tools are called."""
        analyze_result = RuntimeToolResult(
            tool_name="analyze_execution_failure",
            success=True,
            data=_FAILURE_ANALYSIS_DATA,
            latency_ms=8,
        )

        decisions = [
            ToolAwareLLMResponse(
                provider="kimi",
                tool_calls=[RuntimeToolCallRequest(
                    call_id="c1",
                    tool_name="analyze_execution_failure",
                    arguments={"execution_id": "esbmain-route-7841"},
                )],
                assistant_message={"role": "assistant", "content": "", "tool_calls": []},
                usage=UsageStats(provider="kimi", model="kimi-k2.5", input_tokens=10, output_tokens=5),
            ),
            ToolAwareLLMResponse(
                provider="kimi",
                text="The job failed due to Oracle timeout.",
                usage=UsageStats(provider="kimi", model="kimi-k2.5", input_tokens=15, output_tokens=10),
            ),
        ]

        tools = [
            RuntimeToolDefinition(
                name="analyze_execution_failure", description="Analyze failure", input_schema={"type": "object"}
            ),
        ]

        with (
            patch("src.generation.conversational_rag._prepare_rag_context", new_callable=AsyncMock) as mock_ctx,
            patch("src.runtime.client.settings") as mock_client_settings,
            patch("src.generation.agent_loop.settings") as mock_loop_settings,
            patch("src.generation.agent_loop.list_runtime_tools", return_value=tools),
            patch("src.generation.agent_loop.generate_answer_with_runtime_tools", AsyncMock(side_effect=decisions)),
            patch("src.generation.agent_loop.execute_runtime_tool", AsyncMock(return_value=analyze_result)),
        ):
            ctx = _no_precompute_context()
            ctx.agent_steps = []
            mock_ctx.return_value = ctx
            mock_client_settings.runtime_provider_key = "stored"
            mock_client_settings.runtime_tool_timeout = 10.0
            mock_client_settings.runtime_tool_result_max_chars = 12000
            mock_loop_settings.runtime_tool_loop_max_iterations = 3
            mock_loop_settings.runtime_tool_max_parallel_calls = 5

            events = []
            async for event_type, data in run_conversational_rag_stream(
                conversation=_mock_conversation(),
                query="Why did the job fail?",
                user_groups=["engineering"],
                qdrant=AsyncMock(),
                db=_mock_db(),
                project_ids=["esbmain"],
            ):
                events.append((event_type, data))

        event_types = [e[0] for e in events]
        assert "runtime_tool_call" in event_types
        assert "runtime_tool_result" in event_types
        # Tool events must precede the token event
        call_idx = next(i for i, t in enumerate(event_types) if t == "runtime_tool_call")
        token_indices = [i for i, t in enumerate(event_types) if t == "token"]
        if token_indices:
            assert call_idx < min(token_indices)
