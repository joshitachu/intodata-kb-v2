"""Fixture tests for the Talend TMC runtime provider adapter."""

import logging
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import httpx
import pytest

from src.runtime.models import (
    GetExecutionDetailsInput,
    GetExecutionLogsInput,
    ListExecutionsInput,
    RuntimeExecutionState,
    RuntimeExecutableKind,
    RuntimeLogSeverity,
)
from src.runtime.providers import RuntimeProviderError
from src.runtime.providers.talend_tmc import (
    _THROTTLE_MAX_ATTEMPTS,
    _apply_route_metrics,
    _route_metrics_from_log,
    _strip_message_body,
    TalendTmcRuntimeProvider,
    _bounded_log_entries,
    _bounded_log_head_tail,
    _failure_summary_for_task_run,
    _to_job_run,
    _unknown_states_warned,
)

_TOKEN = "super-secret-talend-token-value"


def test_long_logs_keep_startup_context_and_terminal_failure():
    logs = [{"line": index} for index in range(12_000)]
    bounded = _bounded_log_head_tail(logs, 10_000)

    assert len(bounded) == 10_000
    assert bounded[0]["line"] == 0
    assert bounded[999]["line"] == 999
    assert bounded[1_000]["line"] == 3_000
    assert bounded[-1]["line"] == 11_999
    indexed = _bounded_log_entries(logs, 10_000)
    assert indexed[1_000][0] == 3_000
    assert indexed[-1][0] == 11_999


@pytest.fixture
def tmc(tmp_path):
    token_path = tmp_path / "talend_token"
    token_path.write_text(f"  {_TOKEN}\n", encoding="utf-8")

    s = MagicMock()
    s.talend_tmc_base_url = "https://api.us.cloud.talend.com"
    s.talend_tmc_api_token_file = str(token_path)
    s.talend_tmc_request_timeout = 10.0
    s.talend_tmc_failed_log_run_limit = 10
    s.talend_tmc_deployment_counter_run_limit = 100
    s.talend_tmc_component_metric_run_limit = 25
    s.talend_tmc_runs_per_executable_limit = 250
    s.runtime_sync_source_key = "talend_tmc"

    with patch("src.runtime.providers.talend_tmc.settings", s):
        yield SimpleNamespace(
            settings=s,
            token_path=token_path,
            tmp_path=tmp_path,
            provider=lambda handler: TalendTmcRuntimeProvider(transport=httpx.MockTransport(handler)),
        )


def _task_execution(**overrides: Any) -> dict[str, Any]:
    """A task execution shaped like the live TMC payload."""
    item: dict[str, Any] = {
        "executionId": "exec-1",
        "taskId": "task-1",
        "executionType": "SCHEDULED",
        "startTimestamp": "2026-06-18T00:05:57.252Z",
    }
    item.update(overrides)
    return item


def _run(**overrides: Any):
    return _to_job_run(_task_execution(**overrides), "talend_tmc", {}, {})


class TestExecutionStateMapping:
    """Every TMC status observed on a live tenant must reach a real state.

    Six statuses are sent in practice (execution_successful, execution_failed,
    deploy_failed, terminated_timeout, terminated, executing); four of them used
    to land as ``unknown``.
    """

    def test_deploy_failed_with_a_misfire_reason_is_a_misfire(self):
        run = _run(
            status="deploy_failed",
            executionStatus="DEPLOY_FAILED",
            misfiredExecutionReason="PARALLEL_EXECUTION_FORBIDDEN",
            # A misfire never starts, so TMC dates it from the trigger.
            startTimestamp=None,
            triggerTimestamp="2026-06-18T00:05:00.000Z",
        )

        assert run.state == RuntimeExecutionState.MISFIRED
        assert run.misfire_reason == "PARALLEL_EXECUTION_FORBIDDEN"
        assert run.raw_state == "deploy_failed"
        assert run.execution_type == "SCHEDULED"

    def test_deploy_failed_without_a_reason_is_a_failure(self):
        run = _run(status="deploy_failed", executionStatus="DEPLOY_FAILED")

        assert run.state == RuntimeExecutionState.FAILED
        assert run.misfire_reason is None
        assert run.raw_state == "deploy_failed"

    def test_executing_is_running_even_though_status_is_read_first(self):
        """The precedence regression: ``status`` is present on every execution.

        Resolving the first field that is PRESENT rather than the first that
        MAPS made ``executionStatus`` unreachable, so live Route deployments
        reporting ``executing`` were stored as ``unknown``.
        """
        run = _run(status="executing", executionStatus="STARTING_FLOW_EXECUTION")

        assert run.state == RuntimeExecutionState.RUNNING
        assert run.raw_state == "executing"

    def test_an_unmapped_status_falls_through_to_a_field_that_maps(self):
        run = _run(status="waiting_for_slot", executionStatus="EXECUTION_SUCCESSFUL")

        assert run.state == RuntimeExecutionState.SUCCESS
        # raw_state stays the first PRESENT value: it records what TMC said,
        # not which field the mapping happened to succeed on.
        assert run.raw_state == "waiting_for_slot"

    @pytest.mark.parametrize(
        ("status", "execution_status", "expected"),
        [
            ("terminated_timeout", "EXECUTION_TERMINATED_TIMEOUT", RuntimeExecutionState.TIMED_OUT),
            ("terminated", "EXECUTION_TERMINATED", RuntimeExecutionState.TERMINATED),
            ("execution_successful", "EXECUTION_SUCCESSFUL", RuntimeExecutionState.SUCCESS),
            ("execution_failed", "EXECUTION_FAILED", RuntimeExecutionState.FAILED),
        ],
    )
    def test_observed_statuses_map_to_distinct_states(self, status, execution_status, expected):
        run = _run(status=status, executionStatus=execution_status)

        assert run.state == expected
        assert run.raw_state == status

    def test_a_novel_status_is_unknown_and_warned_about_once(self, caplog):
        _unknown_states_warned.clear()
        with caplog.at_level(logging.WARNING, logger="src.runtime.providers.talend_tmc"):
            first = _run(status="quarantined_by_governance")
            second = _run(status="quarantined_by_governance")

        assert first.state == RuntimeExecutionState.UNKNOWN
        assert second.state == RuntimeExecutionState.UNKNOWN
        # The verbatim status survives even when the mapping does not.
        assert first.raw_state == "quarantined_by_governance"
        warnings = [record for record in caplog.records if record.levelno == logging.WARNING]
        assert len(warnings) == 1
        assert "quarantined_by_governance" in warnings[0].getMessage()

    def test_a_payload_with_no_status_at_all_is_not_warned_about(self, caplog):
        _unknown_states_warned.clear()
        with caplog.at_level(logging.WARNING, logger="src.runtime.providers.talend_tmc"):
            run = _run()

        assert run.state == RuntimeExecutionState.UNKNOWN
        assert run.raw_state is None
        assert [record for record in caplog.records if record.levelno == logging.WARNING] == []


class TestFailureSummaries:
    def test_a_misfire_without_an_error_message_reports_the_reason(self):
        raw = _task_execution(
            status="deploy_failed", misfiredExecutionReason="PARALLEL_EXECUTION_FORBIDDEN"
        )
        run = _to_job_run(raw, "talend_tmc", {}, {})

        assert run.state == RuntimeExecutionState.MISFIRED
        assert (
            _failure_summary_for_task_run(raw, run)
            == "Run did not start: PARALLEL_EXECUTION_FORBIDDEN"
        )

    def test_a_timeout_is_not_described_as_a_failure(self):
        raw = _task_execution(status="terminated_timeout")
        run = _to_job_run(raw, "talend_tmc", {}, {})

        assert _failure_summary_for_task_run(raw, run) == (
            "task-1 was stopped by its configured timeout"
        )

    def test_an_error_message_still_wins_and_stays_truncated(self):
        raw = _task_execution(
            status="deploy_failed",
            misfiredExecutionReason="TASK_IS_PAUSED",
            errorMessage="x" * 500,
        )
        run = _to_job_run(raw, "talend_tmc", {}, {})

        assert _failure_summary_for_task_run(raw, run) == "x" * 300


class TestEndpointMapping:
    async def test_reuses_one_http_pool_until_explicit_close(self, tmc):
        provider = tmc.provider(lambda request: httpx.Response(200, json={"items": []}))

        first = provider._client()
        second = provider._client()
        assert first is second

        await provider.aclose()
        assert provider._http_client is None

    async def test_list_and_details_use_tmc_processing_endpoints(self, tmc):
        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request.url.path)
            if request.url.path == "/processing/executions/exec-1":
                return httpx.Response(
                    200,
                    json={
                        "executionId": "exec-1",
                        "taskName": "orders_sync",
                        "status": "EXECUTION_SUCCESSFUL",
                        "startTime": "2026-01-01T00:00:00Z",
                    },
                )
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "executionId": "exec-1",
                            "taskName": "orders_sync",
                            "workspaceName": "demo",
                            "status": "EXECUTION_FAILED",
                            "startTime": "2026-01-01T00:00:00Z",
                            "finishTime": "2026-01-01T00:01:00Z",
                        }
                    ],
                    "total": 1,
                },
            )

        provider = tmc.provider(handler)
        listed = await provider.list_executions(ListExecutionsInput())
        detail = await provider.get_execution_details(GetExecutionDetailsInput(execution_id="exec-1"))

        assert seen == [
            "/processing/executables/tasks/executions",
            "/processing/executions/exec-1",
        ]
        assert listed[0].execution_id == "exec-1"
        assert listed[0].state == RuntimeExecutionState.FAILED
        assert listed[0].duration_ms == 60000
        assert detail.state == RuntimeExecutionState.SUCCESS

    async def test_bearer_token_is_sent(self, tmc):
        captured: dict[str, str] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["auth"] = request.headers.get("authorization", "")
            return httpx.Response(200, json={"items": [], "total": 0})

        await tmc.provider(handler).list_executions(ListExecutionsInput())
        assert captured["auth"] == f"Bearer {_TOKEN}"


class TestLogs:
    async def test_async_log_workflow_downloads_ready_file(self, tmc):
        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request.url.path)
            if request.url.path == "/monitoring/executions/exec-1/logs":
                return httpx.Response(200, json={"token": "log-token"})
            if request.url.path == "/monitoring/executions/exec-1/logs/status":
                return httpx.Response(
                    200,
                    json={
                        "status": "READY",
                        "downloadUrl": "https://files.us.cloud.talend.com/generated/logs/exec-1",
                    },
                )
            if request.url.host == "files.us.cloud.talend.com":
                return httpx.Response(
                    200,
                    json=[
                        {
                            "executionId": "exec-1",
                            "timestamp": "2026-01-01T00:00:00Z",
                            "severity": "ERROR",
                            "logMessage": "[tDBInput_1] ORA-12170 timeout",
                        }
                    ],
                )
            raise AssertionError(f"Unexpected request {request.url}")

        result = await tmc.provider(handler).get_execution_logs(
            GetExecutionLogsInput(execution_id="exec-1")
        )

        assert seen[:2] == [
            "/monitoring/executions/exec-1/logs",
            "/monitoring/executions/exec-1/logs/status",
        ]
        assert result[0].severity == RuntimeLogSeverity.ERROR
        assert result[0].message == "[tDBInput_1] ORA-12170 timeout"

    async def test_log_fetch_falls_back_to_paginated_get(self, tmc):
        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request.url.path)
            if request.method == "POST":
                return httpx.Response(500, json={"detail": "generation unavailable"})
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "execution_id": "exec-1",
                            "timestamp": "2026-01-01T00:00:00Z",
                            "level": "WARN",
                            "message": "retrying",
                        }
                    ],
                    "totalSize": 1,
                },
            )

        result = await tmc.provider(handler).get_execution_logs(
            GetExecutionLogsInput(execution_id="exec-1")
        )

        assert seen == [
            "/monitoring/executions/exec-1/logs",
            "/monitoring/executions/exec-1/logs",
        ]
        assert result[0].severity == RuntimeLogSeverity.WARNING


class TestComponentMetrics:
    async def test_component_metrics_map_to_generic_metric_facts(self, tmc):
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/monitoring/observability/executions/exec-1/component"
            return httpx.Response(
                200,
                json={
                    "metrics": {
                        "items": [
                            {
                                "connector_id": "tDBInput_1",
                                "connector_type": "tDBInput",
                                "target_id": "tMap_1",
                                "target_connector_type": "tMap",
                                "component_connection_rows_total": 42,
                                "component_execution_duration_milliseconds": 321,
                            }
                        ],
                        "total": 1,
                    }
                },
            )

        result = await tmc.provider(handler).get_component_metrics("exec-1")

        assert result[0].execution_id == "exec-1"
        assert result[0].component_key == "tDBInput_1"
        assert result[0].source_connector_type == "tDBInput"
        assert result[0].target_connector_type == "tMap"
        assert result[0].rows_total == 42
        assert result[0].duration_ms == 321


class TestRuntimeBatchMapping:
    async def test_fetch_runtime_batch_maps_core_inventory_and_per_entity_runs(self, tmc):
        seen: list[str] = []

        fixtures = {
            "/orchestration/environments": {
                "items": [
                    {"id": "env-prod", "name": "PROD", "description": "Production", "default": True}
                ],
                "total": 1,
            },
            "/orchestration/workspaces": {
                "items": [
                    {
                        "id": "ws-1",
                        "name": "Analytics",
                        "owner": "alice",
                        "type": "custom",
                        "environment": {"id": "env-prod", "name": "PROD"},
                    }
                ],
                "total": 1,
            },
            "/processing/runtimes/remote-engine-clusters": {"items": [], "total": 0},
            "/processing/runtimes/remote-engines": {
                "items": [
                    {
                        "id": "eng-1",
                        "name": "prod-engine-01",
                        "status": "RUNNING",
                        "availability": "AVAILABLE",
                        "esbCompatibilityVersion": "8.0.1",
                        "workspace": {"environment": {"id": "env-prod", "name": "PROD"}},
                    }
                ],
                "total": 1,
            },
            "/orchestration/artifacts": {
                "items": [
                    {
                        "id": "artifact-1",
                        "name": "orders_route",
                        "type": "route",
                        "version": "1.2.3",
                        "workspace": {"id": "ws-1", "name": "Analytics"},
                        "owner": "alice",
                    },
                    {
                        "id": "artifact-2",
                        "name": "billing_route",
                        "type": "route",
                        "version": "2.0.0",
                        "workspace": {"id": "ws-1", "name": "Analytics"},
                        "owner": "bob",
                    },
                ],
                "total": 2,
            },
            "/orchestration/connections": {
                "items": [
                    {
                        "id": "conn-1",
                        "name": "Oracle Orders",
                        "type": "oracle",
                        "workspace": {"id": "ws-1", "name": "Analytics"},
                        "owner": "dba",
                    }
                ],
                "total": 1,
            },
            "/orchestration/executables/tasks": {
                "items": [
                    {
                        "executable": "task-1",
                        "name": "nightly-load",
                        "artifactId": "artifact-1",
                        "connections": [{"id": "conn-1"}],
                        "workspace": {
                            "id": "ws-1",
                            "name": "Analytics",
                            "owner": "alice",
                            "environment": {"id": "env-prod", "name": "PROD"},
                        },
                        "runtime": {"type": "REMOTE_ENGINE", "id": "eng-1"},
                    },
                    {
                        "executable": "task-2",
                        "name": "hourly-sync",
                        "artifactId": "artifact-2",
                        "connectionIds": ["conn-1"],
                        "workspace": {
                            "id": "ws-1",
                            "name": "Analytics",
                            "owner": "bob",
                            "environment": {"id": "env-prod", "name": "PROD"},
                        },
                        "runtime": {"type": "REMOTE_ENGINE", "id": "eng-1"},
                    },
                ],
                "total": 2,
            },
            "/orchestration/executables/tasks/task-1": {
                "id": "task-1",
                "artifact": {"id": "artifact-1", "name": "orders_route", "version": "1.2.3"},
                "parameters": {"metric_path": "orders_route"},
            },
            "/orchestration/executables/tasks/task-2": {
                "id": "task-2",
                "artifact": {"id": "artifact-2", "name": "billing_route", "version": "2.0.0"},
                "parameters": {"metric_path": "billing_route"},
            },
            "/processing/executables/tasks/task-1/executions": {
                "items": [
                    {
                        "executionId": "exec-1",
                        "taskId": "task-1",
                        "planId": "plan-1",
                        "status": "execution_successful",
                        "executionStatus": "EXECUTION_SUCCESS",
                        "runtime": {"type": "REMOTE_ENGINE", "engineId": "eng-1"},
                        "startTimestamp": "2026-06-18T00:05:57.252Z",
                        "finishTimestamp": "2026-06-18T00:06:00.531Z",
                    }
                ],
                "total": 1,
            },
            "/processing/executables/tasks/task-2/executions": {
                "items": [
                    {
                        "executionId": "exec-2",
                        "taskId": "task-2",
                        "status": "execution_failed",
                        "executionStatus": "EXECUTION_FAIL",
                        "runtime": {"type": "REMOTE_ENGINE", "engineId": "eng-1"},
                        "startTimestamp": "2026-06-18T01:00:05.000Z",
                        "finishTimestamp": "2026-06-18T01:00:20.000Z",
                        "errorMessage": "connection timeout",
                        "routeMetrics": {
                            "completedExchanges": 41,
                            "failedExchanges": 1,
                            "totalExchanges": 42,
                            "lastProcessTimeMs": 12,
                            "meanProcessTimeMs": 8,
                            "metricsUpdatedAt": "2026-06-18T01:00:19Z",
                        },
                    }
                ],
                "total": 1,
            },
            "/orchestration/executables/plans": {
                "items": [
                    {
                        "executable": "plan-1",
                        "name": "nightly-orchestration",
                        "workspace": {"id": "ws-1", "name": "Analytics"},
                        "planPauseDetails": {},
                        "chart": {
                            "flows": [{"workspaceId": "ws-1", "id": "task-1"}],
                            "nextStep": {"flows": [{"workspaceId": "ws-1", "id": "task-2"}], "nextStep": None},
                        },
                    }
                ],
                "total": 1,
            },
            "/processing/executables/plans/plan-1/executions": {
                "items": [
                    {
                        "executionId": "pexec-1",
                        "planId": "plan-1",
                        "status": "execution_failed",
                        "executionStatus": "EXECUTION_FAILED",
                        "startTimestamp": "2026-06-18T01:00:01.000Z",
                        "finishTimestamp": "2026-06-18T01:05:00.000Z",
                    }
                ],
                "total": 1,
            },
            "/monitoring/executions/exec-1/logs": {"data": [], "totalSize": 0},
            "/monitoring/executions/exec-2/logs": {
                "data": [
                    {
                        "executionId": "exec-2",
                        "logTimestamp": 1781744420000,
                        "severity": "ERROR",
                        "logMessage": "[tDBInput_1] connection timeout",
                    }
                ],
                "totalSize": 1,
            },
            "/monitoring/observability/executions/exec-1/component": {"metrics": {"items": [], "total": 0}},
            "/monitoring/observability/executions/exec-2/component": {"metrics": {"items": [], "total": 0}},
        }

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request.url.path)
            if request.method == "POST" and request.url.path.endswith("/logs"):
                return httpx.Response(500, json={"detail": "async logs unavailable"})
            payload = fixtures.get(request.url.path)
            if payload is not None:
                return httpx.Response(200, json=payload)
            raise AssertionError(f"Unexpected request {request.method} {request.url.path}")

        batch = await tmc.provider(handler).fetch_runtime_batch(project_id="project-a")

        assert "/processing/executables/tasks/executions" not in seen
        assert "/processing/executables/tasks/task-1/executions" in seen
        assert "/processing/executables/tasks/task-2/executions" in seen
        assert "/monitoring/executions/exec-1/logs" in seen
        assert "/monitoring/executions/exec-2/logs" in seen
        assert "/processing/executables/plans/plan-1/executions" in seen
        assert [source.source_key for source in batch.sources] == ["talend_tmc"]
        assert [environment.environment_id for environment in batch.environments] == ["env-prod"]
        assert [workspace.workspace_id for workspace in batch.workspaces] == ["ws-1"]
        assert [engine.name for engine in batch.engines] == ["prod-engine-01"]
        assert {job.name for job in batch.jobs} == {"nightly-load", "hourly-sync"}
        assert {job.executable_kind for job in batch.jobs} == {RuntimeExecutableKind.ROUTE}
        assert {job.metric_path for job in batch.jobs} == {"orders_route", "billing_route"}
        assert {artifact.name for artifact in batch.artifacts} == {"orders_route", "billing_route"}
        assert [connection.name for connection in batch.connections] == ["Oracle Orders"]
        assert {
            (dependency.job_id, dependency.dependency_type, dependency.dependency_id)
            for dependency in batch.job_dependencies
        } == {
            ("task-1", "artifact", "artifact-1"),
            ("task-1", "connection", "conn-1"),
            ("task-2", "artifact", "artifact-2"),
            ("task-2", "connection", "conn-1"),
        }
        assert {run.run_id for run in batch.job_runs} == {"exec-1", "exec-2"}
        failed_route = next(run for run in batch.job_runs if run.run_id == "exec-2")
        assert failed_route.total_exchanges == 42
        assert failed_route.failed_exchanges == 1
        assert failed_route.metric_path == "billing_route"
        # A failed Route is a failure. Its LOGS are engine-scoped and cannot be
        # attributed to one deployment (asserted below), but its own
        # errorMessage is the executable's, and gating failure records on
        # executable_kind == JOB discarded it along with every other Route
        # error in the window.
        assert [(failure.execution_id, failure.error_summary) for failure in batch.failures] == [
            ("exec-2", "connection timeout")
        ]
        assert [plan.name for plan in batch.plans] == ["nightly-orchestration"]
        assert batch.plans[0].steps[0].job_id == "task-1"
        assert [run.run_id for run in batch.plan_runs] == ["pexec-1"]
        assert batch.plan_runs[0].step_results[0].execution_id == "exec-1"
        assert batch.log_events[0].message == "[tDBInput_1] connection timeout"
        assert batch.log_events[0].timestamp.isoformat() == "2026-06-18T01:00:20+00:00"
        assert batch.log_events[0].scope == "engine"
        assert batch.log_events[0].attribution_safe is False

    async def test_sync_uses_supplied_source_credentials(self, tmc):
        seen_headers: list[str | None] = []
        seen_hosts: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen_headers.append(request.headers.get("authorization"))
            seen_hosts.append(str(request.url).split(request.url.path)[0])
            return httpx.Response(200, json={"items": [], "total": 0})

        batch = await tmc.provider(handler).sync(
            credentials={
                "api_token": "source-specific-token",
                "base_url": "https://source-specific.example",
            },
            project_id="project-a",
        )

        assert [source.source_key for source in batch.sources] == ["talend_tmc"]
        assert set(seen_headers) == {"Bearer source-specific-token"}
        assert set(seen_hosts) == {"https://source-specific.example"}

    async def test_werkorder_sync_scopes_tasks_history_plans_and_logs(self, tmc):
        seen: list[tuple[str, str]] = []
        list_fixtures: dict[str, list[dict]] = {
            "/orchestration/environments": [{"id": "env-prod", "name": "PROD"}],
            "/orchestration/workspaces": [
                {
                    "id": "ws-work",
                    "name": "Integrations",
                    "environment": {"id": "env-prod", "name": "PROD"},
                },
                {
                    "id": "ws-hr",
                    "name": "HR",
                    "environment": {"id": "env-prod", "name": "PROD"},
                },
            ],
            "/processing/runtimes/remote-engine-clusters": [],
            "/processing/runtimes/remote-engines": [
                {"id": "eng-1", "name": "engine", "status": "RUNNING"}
            ],
            "/orchestration/artifacts": [
                {"id": "art-work", "name": "EnterpriseExport"},
                {"id": "art-hr", "name": "PayrollExport"},
            ],
            "/orchestration/connections": [
                {"id": "conn-work", "name": "Maximo"},
                {"id": "conn-hr", "name": "Payroll"},
            ],
            "/orchestration/executables/tasks": [
                {
                    "executable": "task-work",
                    "name": "NightlyWorkOrderExport",
                    "artifactId": "art-work",
                    "connectionIds": ["conn-work"],
                    "workspace": {
                        "id": "ws-work",
                        "name": "Integrations",
                        "environment": {"id": "env-prod", "name": "PROD"},
                    },
                    "runtime": {"id": "eng-1"},
                },
                {
                    "executable": "task-hr",
                    "name": "PayrollExport",
                    "artifactId": "art-hr",
                    "connectionIds": ["conn-hr"],
                    "workspace": {"id": "ws-hr", "name": "HR"},
                    "runtime": {"id": "eng-1"},
                },
            ],
            "/orchestration/executables/plans": [
                {
                    "executable": "plan-work",
                    "name": "Work orchestration",
                    "chart": {"flows": [{"id": "task-work"}]},
                },
                {
                    "executable": "plan-hr",
                    "name": "HR orchestration",
                    "chart": {"flows": [{"id": "task-hr"}]},
                },
            ],
            "/processing/executables/tasks/task-work/executions": [
                {
                    "executionId": "exec-ok",
                    "taskId": "task-work",
                    "status": "execution_successful",
                    "startTimestamp": "2026-08-10T01:00:00Z",
                    "finishTimestamp": "2026-08-10T01:01:00Z",
                },
                {
                    "executionId": "exec-failed",
                    "taskId": "task-work",
                    "status": "execution_failed",
                    "startTimestamp": "2026-08-11T01:00:00Z",
                    "finishTimestamp": "2026-08-11T01:01:00Z",
                    "errorMessage": "Maximo timeout",
                },
            ],
            "/processing/executables/plans/plan-work/executions": [],
        }

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append((request.method, request.url.path))
            if request.url.path in {
                "/processing/executables/tasks/task-work/executions",
                "/processing/executables/plans/plan-work/executions",
            }:
                window_start = int(request.url.params["from"])
                window_end = int(request.url.params["to"])
                assert 0 < window_end - window_start <= 10 * 24 * 60 * 60 * 1000
                assert "lastDays" not in request.url.params
            if request.url.path == "/monitoring/executions/exec-failed/logs":
                if request.method == "POST":
                    return httpx.Response(500, json={"detail": "async logs unavailable"})
                return httpx.Response(
                    200,
                    json={
                        "data": [
                            {
                                "execution_id": "exec-failed",
                                "timestamp": "2026-08-11T01:01:00Z",
                                "level": "ERROR",
                                "message": "Maximo timeout",
                            }
                        ],
                        "totalSize": 1,
                    },
                )
            if request.url.path.startswith("/monitoring/observability/executions/"):
                return httpx.Response(200, json={"metrics": {"items": [], "total": 0}})
            # The task detail is now fetched for EVERY executable kind, because
            # it is the only place TMC exposes artifact.version -- the fact that
            # answers "since when has this run unchanged". Narrowed to Routes,
            # every Job carried an empty version and that read as "TMC does not
            # expose it".
            if request.url.path.startswith("/orchestration/executables/tasks/"):
                return httpx.Response(
                    200,
                    json={
                        "executable": request.url.path.rsplit("/", 1)[-1],
                        "artifact": {"id": "artifact-work", "version": "2.14.0.20262701024345"},
                    },
                )
            payload = list_fixtures.get(request.url.path)
            if payload is not None:
                return httpx.Response(200, json=payload)
            raise AssertionError(f"Unexpected request {request.method} {request.url.path}")

        batch = await tmc.provider(handler).sync(
            credentials={
                "api_token": "source-specific-token",
                "base_url": "https://source-specific.example",
            },
            project_id="werkorder",
        )

        assert [job.job_id for job in batch.jobs] == ["task-work"]
        # A BATCH task now carries its deployed version. It used to be empty for
        # every Job, because the task detail was fetched for Routes only -- and
        # that emptiness was read as "TMC does not expose it". It does.
        assert batch.jobs[0].artifact_version == "2.14.0.20262701024345"
        assert [artifact.artifact_id for artifact in batch.artifacts] == ["art-work"]
        assert [connection.connection_id for connection in batch.connections] == ["conn-work"]
        assert [plan.plan_id for plan in batch.plans] == ["plan-work"]
        assert {run.run_id for run in batch.job_runs} == {"exec-ok", "exec-failed"}
        assert [event.execution_id for event in batch.log_events] == ["exec-failed"]
        assert ("GET", "/processing/executables/tasks/task-hr/executions") not in seen
        assert ("GET", "/processing/executables/plans/plan-hr/executions") not in seen
        assert not any(path == "/monitoring/executions/exec-ok/logs" for _, path in seen)
        assert sum(
            path == "/processing/executables/tasks/task-work/executions" for _, path in seen
        ) == 9


    async def test_every_engine_of_a_cluster_gets_its_deployment_log_read(self, tmc):
        """One Route, one task, two engines -- both must be asked.

        TMC models a clustered deployment as ONE task with one execution per
        engine, so a selection keyed on the task kept a single engine and never
        asked the other. Verified on a live estate: 24 of 46 routes reported no
        throughput at all, decided by which engine finished its rolling
        redeploy last -- a margin of 23 seconds.
        """
        seen: list[tuple[str, str]] = []
        list_fixtures: dict[str, list[dict]] = {
            "/orchestration/environments": [{"id": "env-prod", "name": "PROD"}],
            "/orchestration/workspaces": [
                {
                    "id": "ws-esb",
                    "name": "ESB",
                    "environment": {"id": "env-prod", "name": "PROD"},
                }
            ],
            "/processing/runtimes/remote-engine-clusters": [],
            "/processing/runtimes/remote-engines": [
                {"id": "eng-1", "name": "PRD_RE_01", "status": "RUNNING"},
                {"id": "eng-2", "name": "PRD_RE_02", "status": "RUNNING"},
            ],
            "/orchestration/artifacts": [
                {"id": "art-route", "name": "sender_invoices", "type": "route"}
            ],
            "/orchestration/connections": [],
            "/orchestration/executables/tasks": [
                {
                    "executable": "task-route",
                    "name": "sender_invoices",
                    "artifactId": "art-route",
                    "workspace": {
                        "id": "ws-esb",
                        "name": "ESB",
                        "environment": {"id": "env-prod", "name": "PROD"},
                    },
                    "runtime": {"id": "eng-1"},
                }
            ],
            "/orchestration/executables/plans": [],
            "/processing/executables/tasks/task-route/executions": [
                {
                    "executionId": "exec-eng1",
                    "taskId": "task-route",
                    "status": "executing",
                    "startTimestamp": "2026-09-17T17:13:17Z",
                    "runtime": {"id": "eng-1"},
                },
                {
                    # 23 seconds later, exactly as the live cluster redeployed.
                    "executionId": "exec-eng2",
                    "taskId": "task-route",
                    "status": "executing",
                    "startTimestamp": "2026-09-17T17:13:40Z",
                    "runtime": {"id": "eng-2"},
                },
            ],
        }
        tellers = {"exec-eng1": 28461, "exec-eng2": 1204}

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append((request.method, request.url.path))
            for execution_id, totaal in tellers.items():
                if request.url.path == f"/monitoring/executions/{execution_id}/logs":
                    if request.method == "POST":
                        return httpx.Response(500, json={"detail": "async logs unavailable"})
                    return httpx.Response(
                        200,
                        json={
                            "data": [
                                {
                                    "execution_id": execution_id,
                                    "timestamp": "2026-09-21T01:01:00Z",
                                    "level": "INFO",
                                    "message": (
                                        "DS RUNNER METRIC - ROUTE: "
                                        "[RouteName:sender_invoices] "
                                        f"[RouteExchangesTotal:{totaal}]"
                                    ),
                                }
                            ],
                            "totalSize": 1,
                        },
                    )
            if request.url.path.startswith("/monitoring/observability/executions/"):
                return httpx.Response(200, json={"metrics": {"items": [], "total": 0}})
            if request.url.path.startswith("/orchestration/executables/tasks/"):
                return httpx.Response(
                    200,
                    json={
                        "executable": request.url.path.rsplit("/", 1)[-1],
                        "artifact": {"id": "art-route", "version": "1.0.0"},
                    },
                )
            payload = list_fixtures.get(request.url.path)
            if payload is not None:
                return httpx.Response(200, json=payload)
            raise AssertionError(f"Unexpected request {request.method} {request.url.path}")

        batch = await tmc.provider(handler).sync(
            credentials={"api_token": "t", "base_url": "https://source.example"},
            project_id="pluripharm",
        )

        opgevraagd = {
            path for method, path in seen if method == "GET" and path.endswith("/logs")
        }
        assert opgevraagd == {
            "/monitoring/executions/exec-eng1/logs",
            "/monitoring/executions/exec-eng2/logs",
        }, "a clustered route must have every engine's deployment log read"

        per_run = {run.run_id: run.total_exchanges for run in batch.job_runs}
        assert per_run == {"exec-eng1": 28461, "exec-eng2": 1204}, (
            "each engine keeps its own cumulative counters"
        )


class TestConfigAndSecretHygiene:
    async def test_get_retries_a_transient_transport_failure(self, tmc):
        attempts = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise httpx.ConnectError("temporary DNS failure", request=request)
            return httpx.Response(200, json={"items": [], "total": 0})

        result = await tmc.provider(handler)._get("/orchestration/environments")

        assert result == {"items": [], "total": 0}
        assert attempts == 2

    async def test_missing_token_file_is_provider_error(self, tmc):
        tmc.settings.talend_tmc_api_token_file = str(tmc.tmp_path / "does_not_exist")
        with pytest.raises(RuntimeProviderError):
            await tmc.provider(lambda r: httpx.Response(200, json={"items": []})).list_executions(
                ListExecutionsInput()
            )

    async def test_http_error_does_not_include_response_body_or_token(self, tmc):
        with pytest.raises(RuntimeProviderError) as exc_info:
            await tmc.provider(
                lambda r: httpx.Response(401, json={"detail": f"bad token {_TOKEN}"})
            ).list_executions(ListExecutionsInput())
        message = str(exc_info.value)
        assert _TOKEN not in message
        assert "bad token" not in message
        assert "HTTP 401" in message

    async def test_untrusted_download_url_falls_back_without_leaking_body(self, tmc):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "POST" and request.url.path.endswith("/logs"):
                return httpx.Response(200, json={"token": "log-token"})
            if request.method == "POST":
                return httpx.Response(
                    200,
                    json={"status": "READY", "downloadUrl": "http://evil.example/logs"},
                )
            return httpx.Response(200, json={"data": [], "totalSize": 0})

        result = await tmc.provider(handler).get_execution_logs(
            GetExecutionLogsInput(execution_id="exec-1")
        )
        assert result == []


class TestBareArrayResponses:
    """Real Talend TMC list endpoints return bare JSON arrays, not envelopes.

    Regression for the ``returned a non-object payload`` failure seen against
    the live API (recorded in RAG_pipeline/.claude/context.md follow-up:
    "reconcile talend_tmc assumed wire format against the live API").
    """

    async def test_list_executions_accepts_bare_array_payload(self, tmc):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json=[
                    {
                        "executionId": "exec-1",
                        "taskName": "orders_sync",
                        "status": "EXECUTION_FAILED",
                        "startTime": "2026-01-01T00:00:00Z",
                        "finishTime": "2026-01-01T00:01:00Z",
                    }
                ],
            )

        listed = await tmc.provider(handler).list_executions(ListExecutionsInput())

        assert [item.execution_id for item in listed] == ["exec-1"]
        assert listed[0].state == RuntimeExecutionState.FAILED

    async def test_fetch_runtime_batch_accepts_bare_array_payloads(self, tmc):
        bare_lists: dict[str, list[dict]] = {
            "/orchestration/environments": [
                {"id": "env-prod", "name": "PROD", "description": "Production", "default": True}
            ],
            "/orchestration/workspaces": [
                {
                    "id": "ws-1",
                    "name": "Analytics",
                    "owner": "alice",
                    "type": "custom",
                    "environment": {"id": "env-prod", "name": "PROD"},
                }
            ],
            "/processing/runtimes/remote-engine-clusters": [],
            "/processing/runtimes/remote-engines": [
                {
                    "id": "eng-1",
                    "name": "prod-engine-01",
                    "status": "RUNNING",
                    "availability": "AVAILABLE",
                    "esbCompatibilityVersion": "8.0.1",
                    "workspace": {"environment": {"id": "env-prod", "name": "PROD"}},
                }
            ],
            "/orchestration/artifacts": [
                {
                    "id": "artifact-1",
                    "name": "orders_route",
                    "type": "route",
                    "version": "1.2.3",
                    "workspace": {"id": "ws-1", "name": "Analytics"},
                    "owner": "alice",
                }
            ],
            "/orchestration/connections": [
                {
                    "id": "conn-1",
                    "name": "Oracle Orders",
                    "type": "oracle",
                    "workspace": {"id": "ws-1", "name": "Analytics"},
                    "owner": "dba",
                }
            ],
            "/orchestration/executables/tasks": [
                {
                    "executable": "task-1",
                    "name": "nightly-load",
                    "artifactId": "artifact-1",
                    "connections": [{"id": "conn-1"}],
                    "workspace": {
                        "id": "ws-1",
                        "name": "Analytics",
                        "owner": "alice",
                        "environment": {"id": "env-prod", "name": "PROD"},
                    },
                    "runtime": {"type": "REMOTE_ENGINE", "id": "eng-1"},
                }
            ],
            "/orchestration/executables/plans": [],
        }

        def handler(request: httpx.Request) -> httpx.Response:
            path = request.url.path
            if request.method == "POST" and path.endswith("/logs"):
                return httpx.Response(500, json={"detail": "async logs unavailable"})
            payload = bare_lists.get(path)
            if payload is not None:
                return httpx.Response(200, json=payload)
            # Per-task / per-plan / per-execution sub-fetches: empty bare arrays.
            return httpx.Response(200, json=[])

        batch = await tmc.provider(handler).fetch_runtime_batch(project_id="project-a")

        assert [environment.environment_id for environment in batch.environments] == ["env-prod"]
        assert [workspace.workspace_id for workspace in batch.workspaces] == ["ws-1"]
        assert [engine.name for engine in batch.engines] == ["prod-engine-01"]
        assert [job.name for job in batch.jobs] == ["nightly-load"]
        assert [artifact.name for artifact in batch.artifacts] == ["orders_route"]
        assert [connection.name for connection in batch.connections] == ["Oracle Orders"]


class TestPaginationFallback:
    """TMC list endpoints disagree about ``limit``/``offset``.

    Some cap ``limit`` at their own undocumented value (``/orchestration
    /executables/tasks`` rejects anything above 100), others reject the params
    outright. Regression for ``GET /orchestration/artifacts returned HTTP 400``
    against the live API.
    """

    async def test_retries_without_pagination_params_on_400(self, tmc):
        seen_param_sets: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen_param_sets.append(str(request.url.params))
            if "limit" in request.url.params or "offset" in request.url.params:
                return httpx.Response(400, json={"detail": "unsupported query parameter"})
            return httpx.Response(
                200,
                json=[{"id": "art-1", "name": "x"}, {"id": "art-2", "name": "y"}],
            )

        items = await tmc.provider(handler)._get_all_pages("/orchestration/artifacts", max_items=100)

        assert [item["id"] for item in items] == ["art-1", "art-2"]
        # The page size is halved down to the floor before pagination is
        # abandoned, because a 400 does not distinguish "limit too large" from
        # "limit unsupported". The last attempt drops the params entirely.
        assert "limit" in seen_param_sets[0]
        assert seen_param_sets[-1] == ""

    async def test_discovers_a_lower_page_size_cap_instead_of_dropping_pagination(self, tmc):
        """A ``limit`` cap must not silently truncate the result to one page.

        ``/orchestration/executables/tasks`` rejects limit>100. Treating that as
        "this endpoint has no pagination" returns the first default page only,
        which dropped 138 of 238 tasks against the live tenant.
        """
        seen_limits: list[str | None] = []
        total = 250

        def handler(request: httpx.Request) -> httpx.Response:
            limit = request.url.params.get("limit")
            seen_limits.append(limit)
            if limit is None:
                return httpx.Response(200, json={"items": [{"id": "t-0"}], "total": total})
            if int(limit) > 100:
                return httpx.Response(
                    400, json={"message": "limit: Parameter `limit` must be in range [1, 100]"}
                )
            offset = int(request.url.params.get("offset") or 0)
            page = [{"id": f"t-{index}"} for index in range(offset, min(offset + int(limit), total))]
            return httpx.Response(200, json={"items": page, "total": total})

        items = await tmc.provider(handler)._get_all_pages(
            "/orchestration/executables/tasks", max_items=1000
        )

        assert len(items) == total
        assert items[-1]["id"] == "t-249"
        # 200 rejected once, then every page served at the discovered cap.
        assert seen_limits[0] == "200"
        assert set(seen_limits[1:]) == {"100"}

    async def test_page_size_cap_is_cached_per_path(self, tmc):
        """The cap probe costs one wasted call per endpoint, not one per page."""
        seen_limits: list[str | None] = []

        def handler(request: httpx.Request) -> httpx.Response:
            limit = request.url.params.get("limit")
            seen_limits.append(limit)
            if limit is not None and int(limit) > 100:
                return httpx.Response(400, json={"message": "limit out of range"})
            return httpx.Response(200, json={"items": [{"id": "t-1"}], "total": 1})

        provider = tmc.provider(handler)
        await provider._get_all_pages("/orchestration/executables/tasks", max_items=1000)
        await provider._get_all_pages("/orchestration/executables/tasks", max_items=1000)

        assert seen_limits == ["200", "100", "100"]

    async def test_the_cap_is_shared_across_one_endpoints_resource_ids(self, tmc):
        """One cap per endpoint, not per task.

        ``/processing/executables/tasks/{id}/executions`` is a single endpoint
        with a single cap, but each task has its own path. Keying the cache on
        the raw path re-ran the probe for every task, adding one wasted request
        per task per sync.
        """
        seen_limits: list[str | None] = []

        def handler(request: httpx.Request) -> httpx.Response:
            limit = request.url.params.get("limit")
            seen_limits.append(limit)
            if limit is not None and int(limit) > 100:
                return httpx.Response(400, json={"message": "limit out of range"})
            return httpx.Response(200, json={"items": [{"id": "run-1"}], "total": 1})

        provider = tmc.provider(handler)
        for task_id in ("66059329e8e3a0209a567b5f", "6745ea7fa3a9c1387e990d4f"):
            await provider._get_all_pages(f"/processing/executables/tasks/{task_id}/executions")

        # One rejection total, not one per task.
        assert seen_limits == ["200", "100", "100"]

    async def test_non_400_error_is_not_swallowed(self, tmc):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, json={"detail": "unauthorized"})

        with pytest.raises(RuntimeProviderError):
            await tmc.provider(handler)._get_all_pages("/orchestration/environments")


class TestThrottlingAndLogPaths:
    """Three defects verified against a live tenant, all of them ours.

    The estate stopped being mirrored entirely: 1.67M records over 103 cycles,
    then three HTTP 429s in a row and the source auto-disabled itself. A 429 is
    "come back later", not a failure, and it was raised straight through.
    """

    @pytest.mark.asyncio
    async def test_a_throttled_request_is_retried_instead_of_failing_the_sync(self, tmc):
        attempts: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            attempts.append(request.url.path)
            if len(attempts) == 1:
                return httpx.Response(429, headers={"Retry-After": "0"}, json={})
            return httpx.Response(200, json={"id": "task-1"})

        provider = tmc.provider(handler)
        result = await provider._get("/orchestration/executables/tasks/task-1")

        assert result == {"id": "task-1"}
        assert len(attempts) == 2, "a 429 must be retried, not raised"

    @pytest.mark.asyncio
    async def test_a_throttle_that_never_clears_still_surfaces(self, tmc):
        """Retrying forever would hide a real outage behind a silent sync."""
        attempts: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            attempts.append(request.url.path)
            return httpx.Response(429, headers={"Retry-After": "0"}, json={})

        provider = tmc.provider(handler)
        with pytest.raises(RuntimeProviderError, match="HTTP 429"):
            await provider._get("/orchestration/executables/tasks/task-1")
        # Throttling gets its own, larger budget than a transport blip: a quota
        # window outlives three back-to-back attempts. It is still bounded --
        # retrying forever would hide a real outage behind a silent sync.
        assert len(attempts) == _THROTTLE_MAX_ATTEMPTS

    @pytest.mark.asyncio
    async def test_a_404_on_logs_status_abandons_the_bulk_export_for_the_sync(self, tmc):
        """Verified: one tenant answers 404 for every execution, Job and Route.

        That path then cost a POST plus three status polls on every single log
        fetch and never once succeeded -- against an API that throttles us.
        """
        calls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(f"{request.method} {request.url.path}")
            if request.url.path.endswith("/logs/status"):
                return httpx.Response(404, json={})
            if request.method == "POST":
                return httpx.Response(200, json={"token": "t", "size": 376820})
            return httpx.Response(200, json={"data": [], "totalSize": 0})

        provider = tmc.provider(handler)

        assert await provider._get_execution_logs_async("exec-1") is None
        assert provider._async_logs_unsupported is True
        # One status poll, not three: the 404 is an answer, not a wait.
        assert sum(1 for c in calls if c.endswith("/logs/status")) == 1

        calls.clear()
        assert await provider._get_execution_logs_async("exec-2") is None
        assert calls == [], "a tenant without the export is not asked twice"


class TestRouteThroughputAndPayloads:
    """Three findings from reading 3.000 captured Route log lines.

    The exchange counters TMC "does not supply" are printed in the log text;
    the captured window was the wrong end of the log; and the lines carried
    complete customer invoices, IBANs included.
    """

    def test_exchange_counters_are_read_back_off_the_engine_log(self):
        line = (
            "DS RUNNER METRIC - ROUTE: [RouteName:DAS_report_axium] "
            "[RouteExchangesCompleted:72][RouteExchangesFailed:11]"
            "[RouteExchangesTotal:83][RouteLastProcessingTime:746]"
            "[RouteMeanProcessingTime:847]"
        )
        naam, tellers = _route_metrics_from_log(line)
        assert naam == "DAS_report_axium"
        # 11 of 83 exchanges failing, on a route whose API columns are all null.
        assert tellers["RouteExchangesFailed"] == 11
        assert tellers["RouteExchangesTotal"] == 83

    def test_a_metric_line_is_attributed_to_the_route_it_names(self):
        """The export is the SHARED engine log. A sibling route's counters must
        not be written onto the deployment whose log we happened to download."""
        from datetime import datetime, timezone

        from src.runtime.models import RuntimeLogEvent

        def run(job_name: str):
            item = _task_execution(executionId=f"exec-{job_name}", taskId=job_name)
            job_run = _to_job_run(item, "talend_tmc", {}, {})
            return job_run.model_copy(
                update={"job_name": job_name, "executable_kind": RuntimeExecutableKind.ROUTE}
            )

        runs = [run("route-a"), run("route-b")]
        events = [
            RuntimeLogEvent(
                execution_id="exec-route-a",
                timestamp=datetime(2026, 8, 20, tzinfo=timezone.utc),
                severity=RuntimeLogSeverity.INFO,
                message=(
                    "DS RUNNER METRIC - ROUTE: [RouteName:route-b] "
                    "[RouteExchangesCompleted:9][RouteExchangesTotal:9]"
                ),
                line_ordinal=0,
            )
        ]
        assert _apply_route_metrics(runs, events) == 1
        assert runs[0].total_exchanges is None, "route-a's log said nothing about route-a"
        assert runs[1].total_exchanges == 9

    def test_counters_land_on_the_running_deployment_only(self):
        """They are cumulative for the deployment that is up. An earlier run of
        the same route did not process today's traffic."""
        from datetime import datetime, timedelta, timezone

        from src.runtime.models import RuntimeLogEvent

        nu = datetime(2026, 9, 21, tzinfo=timezone.utc)

        def run(gestart: datetime):
            item = _task_execution(executionId=f"exec-{gestart.day}", taskId="route-a")
            return _to_job_run(item, "talend_tmc", {}, {}).model_copy(
                update={
                    "job_name": "route-a",
                    "executable_kind": RuntimeExecutableKind.ROUTE,
                    "started_at": gestart,
                }
            )

        oud, nieuw = run(nu - timedelta(days=60)), run(nu)
        events = [
            RuntimeLogEvent(
                execution_id="exec-21",
                timestamp=nu,
                severity=RuntimeLogSeverity.INFO,
                message="DS RUNNER METRIC - ROUTE: [RouteName:route-a] [RouteExchangesTotal:3109]",
                line_ordinal=0,
            )
        ]
        assert _apply_route_metrics([oud, nieuw], events) == 1
        assert nieuw.total_exchanges == 3109
        assert oud.total_exchanges is None

    def test_two_engines_of_a_cluster_keep_their_own_counters(self):
        """One Route, one job_id, two engines, two cumulative counters.

        Verified on a live estate: every Route ran on both engines of a
        two-engine cluster. Attributing on route name alone gave one engine's
        totals to whichever deployment started last -- a margin of 23 seconds
        decided it -- and published one engine's share as the route's
        throughput.
        """
        from datetime import datetime, timedelta, timezone

        from src.runtime.models import RuntimeLogEvent

        nu = datetime(2026, 9, 21, tzinfo=timezone.utc)

        def run(engine: str, gestart: datetime):
            item = _task_execution(executionId=f"exec-{engine}", taskId="route-a")
            return _to_job_run(item, "talend_tmc", {}, {}).model_copy(
                update={
                    "job_name": "route-a",
                    "job_id": "task-1",
                    "executable_kind": RuntimeExecutableKind.ROUTE,
                    "engine_id": engine,
                    "started_at": gestart,
                }
            )

        # Engine 02 redeployed 23 seconds after engine 01, exactly as measured.
        engine1 = run("PRD_RE_01", nu)
        engine2 = run("PRD_RE_02", nu + timedelta(seconds=23))

        def regel(execution_id: str, totaal: int):
            return RuntimeLogEvent(
                execution_id=execution_id,
                timestamp=nu,
                severity=RuntimeLogSeverity.INFO,
                message=(
                    "DS RUNNER METRIC - ROUTE: [RouteName:route-a] "
                    f"[RouteExchangesTotal:{totaal}]"
                ),
                line_ordinal=0,
            )

        events = [regel("exec-PRD_RE_01", 28461), regel("exec-PRD_RE_02", 1204)]
        assert _apply_route_metrics([engine1, engine2], events) == 2
        assert engine1.total_exchanges == 28461
        assert engine2.total_exchanges == 1204, (
            "the later deployment must not inherit the other engine's counters"
        )

    def test_a_line_from_an_unknown_deployment_is_not_attributed(self):
        """Without a deployment we cannot know the engine, and an engine-less
        guess is what the per-engine key exists to prevent."""
        from datetime import datetime, timezone

        from src.runtime.models import RuntimeLogEvent

        item = _task_execution(executionId="exec-1", taskId="route-a")
        run = _to_job_run(item, "talend_tmc", {}, {}).model_copy(
            update={
                "job_name": "route-a",
                "executable_kind": RuntimeExecutableKind.ROUTE,
                "engine_id": "PRD_RE_01",
            }
        )
        events = [
            RuntimeLogEvent(
                execution_id="exec-van-een-andere-batch",
                timestamp=datetime(2026, 9, 21, tzinfo=timezone.utc),
                severity=RuntimeLogSeverity.INFO,
                message="DS RUNNER METRIC - ROUTE: [RouteName:route-a] [RouteExchangesTotal:9]",
                line_ordinal=0,
            )
        ]
        assert _apply_route_metrics([run], events) == 0
        assert run.total_exchanges is None

    def test_a_deployment_keeps_the_tail_not_the_opening_day(self):
        """A Route up for 83 days starts its log on the day it was deployed."""
        lines = [{"line": index} for index in range(5_000)]
        tail = _bounded_log_entries(lines, 500, head_capture=0)
        assert [item[1]["line"] for item in tail][:2] == [4_500, 4_501]
        # A failed batch run still keeps its startup context.
        head = _bounded_log_entries(lines, 500)
        assert [item[1]["line"] for item in head][:2] == [0, 1]

    def test_a_message_body_is_counted_not_copied(self):
        """90 captured lines carried an IBAN. The envelope is the diagnosis."""
        trace = (
            "Exchange[ExchangePattern: InOnly, BodyType: String, "
            "Body: <DISTRIMED_INVOICES><IBAN>NL02ABNA0123456789</IBAN></DISTRIMED_INVOICES>]"
        )
        stripped = _strip_message_body(trace)
        assert "NL02ABNA0123456789" not in stripped
        assert "InOnly" in stripped and "BodyType: String" in stripped
        assert "tekens" in stripped

    @pytest.mark.asyncio
    async def test_a_tail_request_reads_the_end_not_the_first_page(self, tmc):
        """The head-first loop returns page one, which for a deployment log is
        the day the route went live. A tail request must probe the total and
        read the window at the end."""
        gezien: list[tuple[int, int]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            start = int(request.url.params.get("startIndex", 0))
            count = int(request.url.params.get("count", 0))
            gezien.append((start, count))
            regels = [
                {"logMessage": f"line {index}", "timestamp": "2026-09-21T00:00:00Z"}
                for index in range(start, min(start + count, 5_000))
            ]
            return httpx.Response(200, json={"data": regels, "totalSize": 5_000})

        provider = tmc.provider(handler)
        logs = await provider._get_execution_logs_paginated("exec-1", 100, head_capture=0)

        assert [item["logMessage"] for item in logs][0] == "line 4900"
        assert [item["logMessage"] for item in logs][-1] == "line 4999"
        # One cheap probe for the total, then one window. Not a walk.
        assert gezien == [(0, 1), (4_900, 100)]

    def test_an_ordinary_log_line_is_left_alone(self):
        line = "Route started in 12 ms"
        assert _strip_message_body(line) == line
