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
from src.runtime.store.seed import seed_mock_runtime_data, seed_rich_runtime_data

_SYNCED_AT = datetime.datetime(2026, 6, 16, 12, 0, tzinfo=UTC)
_PROJECT = "demo-runtime"
_ESB = "esbmain"
_FAILED_EXEC = "esbmain-route-7841"
_SUCCESS_EXEC = "esbmain-route-7840"


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

    async def test_get_execution_logs_is_not_user_facing(self, db_session):
        await seed_mock_runtime_data(db_session, project_id=_PROJECT, synced_at=_SYNCED_AT)
        failures = await _call("detect_failures", db_session, project_scope=[_PROJECT])
        execution_id = failures.data[0]["execution_id"]
        result = await _call(
            "get_execution_logs", db_session, project_scope=[_PROJECT],
            arguments={"execution_id": execution_id},
        )
        assert result.success is False
        assert result.error.code == RuntimeToolErrorCode.UNKNOWN_TOOL

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


class TestStoredHighLevelTools:
    """High-level insight tools on the stored provider, seeded with rich runtime data."""

    async def test_analyze_execution_failure_returns_diagnostic_fields(self, db_session):
        await seed_rich_runtime_data(db_session, project_id=_ESB, synced_at=_SYNCED_AT, build_insights=True)
        result = await _call(
            "analyze_execution_failure", db_session,
            project_scope=[_ESB], arguments={"execution_id": _FAILED_EXEC},
        )
        assert result.success is True
        data = result.data
        assert data["execution_id"] == _FAILED_EXEC
        assert data["failed_component"] == "tOracleConnection_1"
        assert data["primary_error_code"] == "ORA-12170"
        assert data["primary_exception_class"] == "SQLRecoverableException"
        assert data["has_incident"] is True
        assert data["incident_id"] is not None
        assert data["root_cause_candidate"] is not None
        assert data["confidence"] in ("high", "medium", "low")
        assert isinstance(data["next_checks"], list)
        assert data["estate_context"]["source_key"] == "mock"
        assert data["estate_context"]["environment_name"] == "Production"
        assert data["estate_context"]["workspace_name"] == "ESB Main"
        assert data["estate_context"]["engine_name"] == "remote-engine-prod-02"
        assert data["estate_context"]["job_display_name"] == "Order Sync Route"
        assert data["estate_context"]["plan_display_name"] == "Nightly Order Flow"
        # Evidence must be bounded — never raw log dumps
        assert isinstance(data["evidence"], list)
        for ev in data["evidence"]:
            if ev.get("excerpt"):
                assert len(ev["excerpt"]) <= 300

    async def test_chat_estate_tools_answer_engine_inventory_from_seeded_db(self, db_session):
        await seed_rich_runtime_data(db_session, project_id=_ESB, synced_at=_SYNCED_AT, build_insights=True)
        result = await _call("list_runtime_engines", db_session, project_scope=[_ESB])

        assert result.success is True
        assert {engine["name"] for engine in result.data} == {
            "remote-engine-prod-01",
            "remote-engine-prod-02",
        }
        assert {engine["environment_name"] for engine in result.data} == {"Production"}

    async def test_chat_unhealthy_tool_returns_jobs_plans_and_freshness(self, db_session):
        await seed_rich_runtime_data(db_session, project_id=_ESB, synced_at=_SYNCED_AT, build_insights=True)
        result = await _call("list_runtime_unhealthy", db_session, project_scope=[_ESB])

        assert result.success is True
        data = result.data
        assert data["overview"]["engines_unhealthy"] == 1
        assert data["overview"]["jobs_unhealthy"] == 1
        assert data["overview"]["plans_unhealthy"] == 1
        assert data["sources"][0]["last_synced_at"] == _SYNCED_AT.isoformat().replace("+00:00", "Z")
        assert [engine["engine_name"] for engine in data["unhealthy_engines"]] == ["remote-engine-prod-02"]
        assert [job["job_name"] for job in data["unhealthy_jobs"]] == ["esb_order_sync_route"]
        assert [plan["plan_name"] for plan in data["unhealthy_plans"]] == ["nightly_order_flow"]

    async def test_chat_estate_tool_arguments_cannot_widen_scope(self, db_session):
        await seed_rich_runtime_data(db_session, project_id=_ESB, synced_at=_SYNCED_AT, build_insights=True)
        await seed_rich_runtime_data(db_session, project_id="private-project", synced_at=_SYNCED_AT, build_insights=True)

        result = await _call(
            "list_runtime_engines",
            db_session,
            project_scope=[_ESB],
            arguments={"project_id": "private-project"},
        )

        assert result.success is True
        assert result.data == []

    async def test_analyze_execution_failure_success_exec_has_no_incident(self, db_session):
        await seed_rich_runtime_data(db_session, project_id=_ESB, synced_at=_SYNCED_AT, build_insights=True)
        result = await _call(
            "analyze_execution_failure", db_session,
            project_scope=[_ESB], arguments={"execution_id": _SUCCESS_EXEC},
        )
        assert result.success is True
        assert result.data["has_incident"] is False
        assert result.data["incident_id"] is None
        assert result.data["error_count"] == 0

    async def test_analyze_execution_failure_cross_project_returns_provider_error(self, db_session):
        await seed_rich_runtime_data(db_session, project_id=_ESB, synced_at=_SYNCED_AT, build_insights=True)
        result = await _call(
            "analyze_execution_failure", db_session,
            project_scope=["other-proj"], arguments={"execution_id": _FAILED_EXEC},
        )
        assert result.success is False
        assert result.error.code == RuntimeToolErrorCode.PROVIDER_ERROR

    async def test_get_execution_timeline_returns_chronological_events(self, db_session):
        await seed_rich_runtime_data(db_session, project_id=_ESB, synced_at=_SYNCED_AT, build_insights=True)
        result = await _call(
            "get_execution_timeline", db_session,
            project_scope=[_ESB], arguments={"execution_id": _FAILED_EXEC},
        )
        assert result.success is True
        events = result.data
        assert len(events) > 0
        timestamps = [e["timestamp"] for e in events]
        assert timestamps == sorted(timestamps)

    async def test_get_execution_timeline_severity_filter(self, db_session):
        await seed_rich_runtime_data(db_session, project_id=_ESB, synced_at=_SYNCED_AT, build_insights=True)
        result = await _call(
            "get_execution_timeline", db_session,
            project_scope=[_ESB],
            arguments={"execution_id": _FAILED_EXEC, "min_severity": "error"},
        )
        assert result.success is True
        events = result.data
        assert len(events) > 0
        assert all(e["severity"] == "error" for e in events)

    async def test_get_execution_timeline_messages_are_capped(self, db_session):
        await seed_rich_runtime_data(db_session, project_id=_ESB, synced_at=_SYNCED_AT, build_insights=True)
        result = await _call(
            "get_execution_timeline", db_session,
            project_scope=[_ESB], arguments={"execution_id": _FAILED_EXEC},
        )
        assert result.success is True
        for event in result.data:
            assert "message" not in event
            assert len(event["signal"]) <= 200

    async def test_get_execution_timeline_cross_project_returns_provider_error(self, db_session):
        await seed_rich_runtime_data(db_session, project_id=_ESB, synced_at=_SYNCED_AT, build_insights=True)
        result = await _call(
            "get_execution_timeline", db_session,
            project_scope=["other-proj"], arguments={"execution_id": _FAILED_EXEC},
        )
        assert result.success is False
        assert result.error.code == RuntimeToolErrorCode.PROVIDER_ERROR

    async def test_get_runtime_incident_returns_incident_with_evidence(self, db_session):
        await seed_rich_runtime_data(db_session, project_id=_ESB, synced_at=_SYNCED_AT, build_insights=True)
        # Get incident_id from analyze first
        analysis = await _call(
            "analyze_execution_failure", db_session,
            project_scope=[_ESB], arguments={"execution_id": _FAILED_EXEC},
        )
        incident_id = analysis.data["incident_id"]

        result = await _call(
            "get_runtime_incident", db_session,
            project_scope=[_ESB], arguments={"incident_id": incident_id},
        )
        assert result.success is True
        assert result.data["incident"]["incident_id"] == incident_id
        assert result.data["incident"]["execution_id"] == _FAILED_EXEC
        assert result.data["incident"]["confidence"] in ("high", "medium", "low")
        assert isinstance(result.data["evidence"], list)
        assert len(result.data["evidence"]) > 0

    async def test_get_runtime_incident_cross_project_returns_provider_error(self, db_session):
        await seed_rich_runtime_data(db_session, project_id=_ESB, synced_at=_SYNCED_AT, build_insights=True)
        analysis = await _call(
            "analyze_execution_failure", db_session,
            project_scope=[_ESB], arguments={"execution_id": _FAILED_EXEC},
        )
        incident_id = analysis.data["incident_id"]
        result = await _call(
            "get_runtime_incident", db_session,
            project_scope=["other-proj"], arguments={"incident_id": incident_id},
        )
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
                tool_calls=[RuntimeToolCallRequest(call_id="c1", tool_name="detect_failures", arguments={})],
                assistant_message={"role": "assistant", "content": "", "tool_calls": []},
                usage=UsageStats(provider="kimi", model="kimi-k2.5", input_tokens=10, output_tokens=5),
            ),
            ToolAwareLLMResponse(
                provider="kimi",
                text="The nightly_sync job failed.",
                usage=UsageStats(provider="kimi", model="kimi-k2.5", input_tokens=10, output_tokens=5),
            ),
        ]

        async def _fake_stream(messages, images=None):
            yield "The nightly_sync job failed."

        with (
            patch("src.runtime.client.settings") as mock_client_settings,
            patch("src.generation.agent_loop.settings") as mock_loop_settings,
            patch("src.generation.agent_loop.list_runtime_tools", return_value=tools),
            # Topology tool bodies are owned by the topology layer; this test only
            # exercises the stored runtime path, so the catalogue is stubbed empty.
            patch("src.generation.agent_loop.list_topology_tools", return_value=[]),
            patch(
                "src.generation.agent_loop.generate_answer_with_runtime_tools",
                AsyncMock(side_effect=decisions),
            ),
            # The loop's synthesis tail streams the final answer via
            # generate_answer_stream once a tool has run (EP-004 slice 3).
            patch("src.generation.agent_loop.generate_answer_stream", _fake_stream),
            patch("src.generation.agent_loop.get_last_usage", return_value=None),
        ):
            _stored_settings(mock_client_settings)
            mock_loop_settings.runtime_tool_loop_max_iterations = 2
            mock_loop_settings.runtime_tool_max_parallel_calls = 5

            outcome = await run_runtime_tool_loop(
                messages, project_scope=[_PROJECT], db=db_session
            )

        assert outcome.final_answer == "The nightly_sync job failed."
        assert outcome.used_runtime_data is True
        results = [e for e in outcome.progress_events if e.event_type == "runtime_tool_result"]
        assert results and results[0].payload["success"] is True
        assert len(results[0].payload["data"]) > 0  # seeded failure reached the loop
