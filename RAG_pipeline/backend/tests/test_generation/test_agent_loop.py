from unittest.mock import AsyncMock, patch

from src.generation.agent_loop import run_runtime_tool_loop
from src.generation.llm_client import RuntimeToolCallRequest, ToolAwareLLMResponse
from src.generation.usage import UsageStats
from src.runtime.models import RuntimeToolDefinition, RuntimeToolError, RuntimeToolErrorCode, RuntimeToolResult

MESSAGES = [
    {"role": "system", "content": "You are helpful."},
    {"role": "user", "content": "Did the nightly sync fail?"},
]

TOOLS = [
    RuntimeToolDefinition(name="detect_failures", description="Detect failures", input_schema={"type": "object"}),
]


def _decision(tool_call=None, text=None, no_tool_support=False, usage=None):
    return ToolAwareLLMResponse(
        provider="kimi",
        tool_call=tool_call,
        text=text,
        assistant_message={"role": "assistant", "content": text or "", "tool_calls": []},
        no_tool_support=no_tool_support,
        usage=usage or UsageStats(provider="kimi", model="kimi-k2.5", input_tokens=10, output_tokens=5),
    )


class TestRuntimeToolLoopHappyPath:
    async def test_tool_call_then_final_answer(self):
        """LLM requests detect_failures, gets the mock result, then answers."""
        decisions = [
            _decision(tool_call=RuntimeToolCallRequest(call_id="call_1", tool_name="detect_failures", arguments={})),
            _decision(text="The nightly_sync job failed at 02:00 UTC."),
        ]
        tool_result = RuntimeToolResult(
            tool_name="detect_failures", success=True, data=[{"job_name": "nightly_sync"}], latency_ms=5
        )

        with (
            patch("src.generation.agent_loop.list_runtime_tools", return_value=TOOLS),
            patch("src.generation.agent_loop.generate_answer_with_runtime_tools", AsyncMock(side_effect=decisions)),
            patch("src.generation.agent_loop.execute_runtime_tool", AsyncMock(return_value=tool_result)),
        ):
            outcome = await run_runtime_tool_loop(MESSAGES)

        assert outcome.final_answer == "The nightly_sync job failed at 02:00 UTC."
        assert outcome.used_runtime_data is True
        assert len(outcome.progress_events) == 2
        assert outcome.progress_events[0].event_type == "runtime_tool_call"
        assert outcome.progress_events[1].event_type == "runtime_tool_result"
        assert outcome.progress_events[1].payload["success"] is True
        assert outcome.usage.input_tokens == 20  # two decisions, 10 each

    async def test_project_scope_and_db_are_forwarded_to_execute(self):
        """The loop must forward the authenticated project_scope and db into every
        execute_runtime_tool call — the RBAC channel for the stored provider."""
        decisions = [
            _decision(tool_call=RuntimeToolCallRequest(call_id="call_1", tool_name="detect_failures", arguments={})),
            _decision(text="done"),
        ]
        tool_result = RuntimeToolResult(tool_name="detect_failures", success=True, data=[], latency_ms=5)
        exec_mock = AsyncMock(return_value=tool_result)
        sentinel_db = object()

        with (
            patch("src.generation.agent_loop.list_runtime_tools", return_value=TOOLS),
            patch("src.generation.agent_loop.generate_answer_with_runtime_tools", AsyncMock(side_effect=decisions)),
            patch("src.generation.agent_loop.execute_runtime_tool", exec_mock),
        ):
            await run_runtime_tool_loop(MESSAGES, project_scope=["project-a"], db=sentinel_db)

        _, kwargs = exec_mock.call_args
        assert kwargs["project_scope"] == ["project-a"]
        assert kwargs["db"] is sentinel_db

    async def test_unknown_tool_call_produces_structured_failure_and_continues(self):
        """Model hallucinates a tool name → execute_runtime_tool's own validation
        rejects it (no patching needed) and the loop still reaches a final answer."""
        decisions = [
            _decision(tool_call=RuntimeToolCallRequest(call_id="call_1", tool_name="not_a_real_tool", arguments={})),
            _decision(text="I could not check that."),
        ]

        with (
            patch("src.runtime.client.settings") as mock_runtime_settings,
            patch("src.generation.agent_loop.list_runtime_tools", return_value=TOOLS),
            patch("src.generation.agent_loop.generate_answer_with_runtime_tools", AsyncMock(side_effect=decisions)),
        ):
            mock_runtime_settings.runtime_provider_key = "mock"
            mock_runtime_settings.runtime_tool_timeout = 10.0
            mock_runtime_settings.runtime_tool_result_max_chars = 12000
            outcome = await run_runtime_tool_loop(MESSAGES)

        assert outcome.final_answer == "I could not check that."
        result_payload = outcome.progress_events[1].payload
        assert result_payload["success"] is False
        assert result_payload["error"]["code"] == "unknown_tool"


class TestRuntimeToolLoopBounds:
    async def test_max_iterations_cap_stops_recursive_tool_calls(self):
        """LLM keeps requesting tools forever → loop stops after the configured
        cap and synthesizes a plain-text answer instead of looping forever."""
        always_tool_call = _decision(
            tool_call=RuntimeToolCallRequest(call_id="call_x", tool_name="detect_failures", arguments={})
        )
        tool_result = RuntimeToolResult(tool_name="detect_failures", success=True, data=[], latency_ms=1)

        with (
            patch("src.generation.agent_loop.settings") as mock_settings,
            patch("src.generation.agent_loop.list_runtime_tools", return_value=TOOLS),
            patch(
                "src.generation.agent_loop.generate_answer_with_runtime_tools",
                AsyncMock(return_value=always_tool_call),
            ) as mock_decide,
            patch("src.generation.agent_loop.execute_runtime_tool", AsyncMock(return_value=tool_result)),
            patch("src.generation.agent_loop.generate_answer", AsyncMock(return_value="Final synthesized answer")),
            patch("src.generation.agent_loop.get_last_usage", return_value=None),
        ):
            mock_settings.runtime_tool_loop_max_iterations = 2
            outcome = await run_runtime_tool_loop(MESSAGES)

        assert outcome.final_answer == "Final synthesized answer"
        assert mock_decide.await_count == 2  # capped, not unbounded
        assert outcome.used_runtime_data is True


class TestRuntimeToolLoopNoToolSupport:
    async def test_no_tool_support_returns_none_for_fallback(self):
        """Provider chain has no structured tool support → caller falls back."""
        with (
            patch("src.generation.agent_loop.list_runtime_tools", return_value=TOOLS),
            patch(
                "src.generation.agent_loop.generate_answer_with_runtime_tools",
                AsyncMock(return_value=_decision(no_tool_support=True, text=None)),
            ),
        ):
            outcome = await run_runtime_tool_loop(MESSAGES)

        assert outcome.final_answer is None
        assert outcome.used_runtime_data is False
        assert outcome.progress_events == []


class TestRuntimeToolLoopSanitization:
    async def test_sanitized_result_appended_not_raw_provider_object(self):
        """Only the bounded/sanitized RuntimeToolResult.model_dump(mode='json')
        ever reaches the next LLM call's messages, never a raw provider object."""
        decisions = [
            _decision(tool_call=RuntimeToolCallRequest(call_id="call_1", tool_name="detect_failures", arguments={})),
            _decision(text="done"),
        ]
        tool_result = RuntimeToolResult(
            tool_name="detect_failures",
            success=False,
            error=RuntimeToolError(code=RuntimeToolErrorCode.TIMEOUT, message="detect_failures timed out"),
            latency_ms=10000,
        )

        captured_messages = []

        async def _fake_decide(messages, tools, images=None):
            captured_messages.append(messages)
            return decisions[len(captured_messages) - 1]

        with (
            patch("src.generation.agent_loop.list_runtime_tools", return_value=TOOLS),
            patch("src.generation.agent_loop.generate_answer_with_runtime_tools", side_effect=_fake_decide),
            patch("src.generation.agent_loop.execute_runtime_tool", AsyncMock(return_value=tool_result)),
        ):
            await run_runtime_tool_loop(MESSAGES)

        # Second call's messages must include the sanitized tool result as a
        # plain JSON string, not a RuntimeToolResult instance or raw object.
        second_call_messages = captured_messages[1]
        tool_message = next(m for m in second_call_messages if m["role"] == "tool")
        assert isinstance(tool_message["content"], str)
        assert "timed out" in tool_message["content"]
