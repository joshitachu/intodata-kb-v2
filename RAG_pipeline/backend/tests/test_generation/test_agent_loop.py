from unittest.mock import AsyncMock, patch

from src.generation.agent_loop import (
    RuntimeProgressEvent,
    _flatten_incident_detail,
    _with_runtime_guide,
    build_runtime_tool_summary,
    run_runtime_tool_loop,
)
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


class TestRuntimeToolGuide:
    def test_guide_merges_into_leading_system_message(self):
        out = _with_runtime_guide(
            [{"role": "system", "content": "Base prompt."}, {"role": "user", "content": "hi"}]
        )
        assert out[0]["role"] == "system"
        assert "Base prompt." in out[0]["content"]
        assert "get_execution_logs" in out[0]["content"]
        assert "execution_id" in out[0]["content"]
        assert out[1] == {"role": "user", "content": "hi"}  # rest untouched

    def test_guide_prepends_when_no_system_message(self):
        out = _with_runtime_guide([{"role": "user", "content": "hi"}])
        assert out[0]["role"] == "system"
        assert "list_executions" in out[0]["content"]
        assert out[1]["role"] == "user"

    def test_guide_includes_per_source_label_instruction(self):
        """Combined turns must tell the model to label each claim by origin so it
        never presents document content as live logs or vice versa."""
        out = _with_runtime_guide([{"role": "system", "content": "Base."}])
        content = out[0]["content"]
        assert "(from logs)" in content
        assert "(from <doc name>)" in content

    def test_guide_lists_high_level_tools_with_primary_marker(self):
        out = _with_runtime_guide([{"role": "system", "content": "Base."}])
        content = out[0]["content"]
        assert "analyze_execution_failure" in content
        assert "get_execution_timeline" in content
        assert "get_runtime_incident" in content
        assert "PRIMARY" in content

    def test_guide_includes_answer_discipline_sections(self):
        out = _with_runtime_guide([{"role": "system", "content": "Base."}])
        content = out[0]["content"]
        assert "Finding:" in content
        assert "Evidence:" in content
        assert "Likely Cause:" in content
        assert "Next Checks:" in content

    def test_guide_steers_away_from_raw_logs_for_failure_questions(self):
        out = _with_runtime_guide([{"role": "system", "content": "Base."}])
        content = out[0]["content"]
        assert "analyze_execution_failure" in content
        assert "get_execution_logs" in content
        # Guide must tell model to prefer analyze over raw logs
        assert "prefer" in content.lower() or "ALWAYS" in content or "before" in content

    def test_guide_includes_prior_runtime_summary_when_provided(self):
        summary = [{"tool_name": "detect_failures", "execution_id": "exec-7841", "job_name": "esb_order_sync_route", "state": "failed"}]
        out = _with_runtime_guide([{"role": "system", "content": "Base."}], prior_runtime_summary=summary)
        content = out[0]["content"]
        assert "Recent runtime context" in content
        assert "exec-7841" in content
        assert "esb_order_sync_route" in content

    def test_guide_omits_memory_note_when_no_summary(self):
        out = _with_runtime_guide([{"role": "system", "content": "Base."}], prior_runtime_summary=None)
        assert "Recent runtime context" not in out[0]["content"]
        # empty list also yields no note
        out_empty = _with_runtime_guide([{"role": "system", "content": "Base."}], prior_runtime_summary=[])
        assert "Recent runtime context" not in out_empty[0]["content"]

    async def test_loop_forwards_prior_summary_into_system_message(self):
        summary = [{"tool_name": "detect_failures", "execution_id": "exec-7841"}]
        captured = {}

        async def _capture(messages, tools, images=None):
            captured["messages"] = messages
            return _decision(text="ok")

        with (
            patch("src.generation.agent_loop.list_runtime_tools", return_value=TOOLS),
            patch("src.generation.agent_loop.generate_answer_with_runtime_tools", _capture),
        ):
            await run_runtime_tool_loop(MESSAGES, prior_runtime_summary=summary)

        system = next(m for m in captured["messages"] if m["role"] == "system")
        assert "exec-7841" in system["content"]


def _result(tool_name, data):
    return RuntimeProgressEvent("runtime_tool_result", {"tool_name": tool_name, "success": True, "data": data})


class TestBuildRuntimeToolSummary:
    def test_detect_failures_summary(self):
        out = build_runtime_tool_summary(
            [_result("detect_failures", [{"execution_id": "e2", "job_name": "nightly_sync", "error_summary": "db timeout"}])]
        )
        assert out == [
            {"tool_name": "detect_failures", "execution_id": "e2", "job_name": "nightly_sync", "headline": "db timeout"}
        ]

    def test_list_executions_summary_keeps_state(self):
        out = build_runtime_tool_summary(
            [_result("list_executions", [{"execution_id": "e1", "job_name": "sync", "state": "success"}])]
        )
        assert out == [{"tool_name": "list_executions", "execution_id": "e1", "job_name": "sync", "state": "success"}]

    def test_logs_summary_keeps_only_execution_pointer_no_message_text(self):
        out = build_runtime_tool_summary(
            [_result("get_execution_logs", [
                {"execution_id": "e2", "timestamp": "2026-06-16T00:00:00Z", "severity": "error", "message": "SECRET ORA-12170 stacktrace"},
            ])]
        )
        assert out == [{"tool_name": "get_execution_logs", "execution_id": "e2"}]
        # raw log message text must never appear anywhere in the summary
        assert "SECRET" not in str(out)

    def test_single_object_details_result(self):
        out = build_runtime_tool_summary(
            [_result("get_execution_details", {"execution_id": "e9", "job_name": "j", "state": "failed"})]
        )
        assert out == [{"tool_name": "get_execution_details", "execution_id": "e9", "job_name": "j", "state": "failed"}]

    def test_dedup_and_cap(self):
        dupes = [{"execution_id": f"e{i}", "job_name": "j"} for i in range(20)]
        dupes.append({"execution_id": "e0", "job_name": "j"})  # duplicate of first
        out = build_runtime_tool_summary([_result("list_executions", dupes)])
        assert len(out) == 10  # capped
        ids = [e["execution_id"] for e in out]
        assert len(ids) == len(set(ids))  # deduped

    def test_no_runtime_results_returns_none(self):
        assert build_runtime_tool_summary([]) is None
        assert build_runtime_tool_summary([RuntimeProgressEvent("runtime_tool_call", {"tool_name": "detect_failures"})]) is None

    def test_malformed_payload_is_skipped(self):
        out = build_runtime_tool_summary(
            [
                _result("detect_failures", "not-a-list-or-dict"),
                _result("list_executions", [None, {"no_id": True}, {"execution_id": "ok"}]),
            ]
        )
        assert out == [{"tool_name": "list_executions", "execution_id": "ok"}]

    def test_analyze_execution_failure_summary_includes_incident_and_headline(self):
        data = {
            "execution_id": "exec-7841",
            "job_name": "esb_order_sync_route",
            "state": "failed",
            "incident_id": "abc123def456",
            "primary_error_code": "ORA-12170",
            "root_cause_candidate": "Source database connection timeout",
            "evidence": [{"log_event_id": 1, "excerpt": "ORA-12170 timeout"}],  # must NOT appear
        }
        out = build_runtime_tool_summary([_result("analyze_execution_failure", data)])
        assert len(out) == 1
        entry = out[0]
        assert entry["execution_id"] == "exec-7841"
        assert entry["job_name"] == "esb_order_sync_route"
        assert entry["state"] == "failed"
        assert entry["incident_id"] == "abc123def456"
        assert entry["headline"] == "ORA-12170"
        # Evidence excerpts must not appear anywhere in the summary
        assert "excerpt" not in str(out)
        assert "timeout" not in str(out)  # only in evidence, not in headline path

    def test_get_execution_timeline_summary_keeps_no_event_messages(self):
        # timeline events have no execution_id → all records skipped → no summary
        events = [
            {"log_event_id": 1, "timestamp": "2026-06-17T00:00:00Z", "severity": "error",
             "message": "SECRET ORA-12170 stacktrace detail"},
            {"log_event_id": 2, "timestamp": "2026-06-17T00:00:01Z", "severity": "info",
             "message": "Job started"},
        ]
        out = build_runtime_tool_summary([_result("get_execution_timeline", events)])
        assert out is None
        # No message text must leak
        assert "SECRET" not in str(out)

    def test_get_runtime_incident_summary_stores_no_evidence(self):
        data = {
            "incident": {
                "incident_id": "abc123",
                "execution_id": "exec-7841",
                "job_name": "esb_order_sync_route",
                "state": "failed",
                "root_cause_candidate": "Source database timeout",
            },
            "evidence": [
                {"log_event_id": 1, "excerpt": "SENSITIVE: ORA-12170 password=hunter2"},
            ],
        }
        out = build_runtime_tool_summary([_result("get_runtime_incident", data)])
        assert len(out) == 1
        entry = out[0]
        assert entry["incident_id"] == "abc123"
        assert entry["execution_id"] == "exec-7841"
        assert entry["job_name"] == "esb_order_sync_route"
        # Evidence and excerpts must never appear in the summary
        assert "SENSITIVE" not in str(out)
        assert "hunter2" not in str(out)
        assert "excerpt" not in str(out)

    def test_flatten_incident_detail_extracts_reference_fields(self):
        data = {
            "incident": {
                "incident_id": "abc123",
                "execution_id": "exec-7841",
                "job_name": "esb_order_sync_route",
                "state": "failed",
                "root_cause_candidate": "timeout",
            },
            "evidence": [{"log_event_id": 1, "excerpt": "sensitive"}],
        }
        record = _flatten_incident_detail(data)
        assert record["incident_id"] == "abc123"
        assert record["execution_id"] == "exec-7841"
        assert "evidence" not in record
        assert "excerpt" not in str(record)


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
