"""End-to-end: seed mock fixtures into the held store, then answer through the
runtime tool boundary with RUNTIME_PROVIDER_KEY=stored.

Proves the full Slice 1+2+3 chain (seed -> repository -> stored provider ->
execute_runtime_tool) returns project-scoped held data and enforces RBAC.
"""

import datetime
from datetime import UTC
from unittest.mock import AsyncMock, patch

from src.generation.agent_loop import run_runtime_tool_loop
from src.generation.llm_client import RuntimeToolCallRequest, ToolAwareLLMResponse
from src.generation.usage import UsageStats
from src.runtime.client import execute_runtime_tool
from src.runtime.models import RuntimeToolCall, RuntimeToolDefinition, RuntimeToolErrorCode
from src.runtime.store.seed import seed_mock_runtime_data

_SYNCED_AT = datetime.datetime(2026, 6, 16, 12, 0, tzinfo=UTC)
_PROJECT = "demo-runtime"


def _stored_settings(mock_settings):
    mock_settings.runtime_provider_key = "stored"
    mock_settings.runtime_tool_timeout = 10.0
    mock_settings.runtime_tool_result_max_chars = 12000


async def _call(tool_name, db, *, project_scope, arguments=None):
    with patch("src.runtime.client.settings") as mock_settings:
        _stored_settings(mock_settings)
        return await execute_runtime_tool(
            RuntimeToolCall(tool_name=tool_name, arguments=arguments or {}),
            project_scope=project_scope,
            db=db,
        )


class TestStoredEndToEnd:
    async def test_detect_failures_returns_seeded_failure(self, db_session):
        await seed_mock_runtime_data(db_session, project_id=_PROJECT, synced_at=_SYNCED_AT)
        result = await _call("detect_failures", db_session, project_scope=[_PROJECT])
        assert result.success is True
        assert len(result.data) > 0
        assert all("job_name" in f for f in result.data)

    async def test_get_execution_logs_returns_seeded_logs(self, db_session):
        await seed_mock_runtime_data(db_session, project_id=_PROJECT, synced_at=_SYNCED_AT)
        failures = await _call("detect_failures", db_session, project_scope=[_PROJECT])
        execution_id = failures.data[0]["execution_id"]
        result = await _call(
            "get_execution_logs", db_session, project_scope=[_PROJECT],
            arguments={"execution_id": execution_id},
        )
        assert result.success is True
        assert len(result.data) > 0

    async def test_other_project_scope_cannot_read_seeded_rows(self, db_session):
        await seed_mock_runtime_data(db_session, project_id=_PROJECT, synced_at=_SYNCED_AT)
        result = await _call("detect_failures", db_session, project_scope=["someone-else"])
        assert result.success is True
        assert result.data == []

    async def test_view_all_scope_reads_seeded_rows(self, db_session):
        await seed_mock_runtime_data(db_session, project_id=_PROJECT, synced_at=_SYNCED_AT)
        result = await _call("list_executions", db_session, project_scope=None)
        assert result.success is True
        assert len(result.data) > 0

    async def test_missing_db_is_structured_provider_error(self, db_session):
        await seed_mock_runtime_data(db_session, project_id=_PROJECT, synced_at=_SYNCED_AT)
        result = await _call("list_executions", None, project_scope=[_PROJECT])
        assert result.success is False
        assert result.error.code == RuntimeToolErrorCode.PROVIDER_ERROR


class TestStoredChatLoop:
    """The chat-side runtime loop pulls seeded, project-scoped held data when the
    tool-aware LLM requests a runtime tool. LLM decisions are mocked; no real LLM."""

    async def test_loop_answers_from_seeded_stored_data(self, db_session):
        await seed_mock_runtime_data(db_session, project_id=_PROJECT, synced_at=_SYNCED_AT)

        messages = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "What failed in the last 24 hours?"},
        ]
        tools = [
            RuntimeToolDefinition(
                name="detect_failures", description="Detect failures", input_schema={"type": "object"}
            )
        ]
        decisions = [
            ToolAwareLLMResponse(
                provider="kimi",
                tool_call=RuntimeToolCallRequest(call_id="c1", tool_name="detect_failures", arguments={}),
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
            patch("src.runtime.client.settings") as mock_client_settings,
            patch("src.generation.agent_loop.settings") as mock_loop_settings,
            patch("src.generation.agent_loop.list_runtime_tools", return_value=tools),
            patch(
                "src.generation.agent_loop.generate_answer_with_runtime_tools",
                AsyncMock(side_effect=decisions),
            ),
        ):
            _stored_settings(mock_client_settings)
            mock_loop_settings.runtime_tool_loop_max_iterations = 2

            outcome = await run_runtime_tool_loop(
                messages, project_scope=[_PROJECT], db=db_session
            )

        assert outcome.final_answer == "The nightly_sync job failed."
        assert outcome.used_runtime_data is True
        results = [e for e in outcome.progress_events if e.event_type == "runtime_tool_result"]
        assert results and results[0].payload["success"] is True
        assert len(results[0].payload["data"]) > 0  # seeded failure reached the loop
