"""Fixture tests for the Talend TMC runtime provider adapter."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import httpx
import pytest

from src.runtime.models import (
    GetExecutionDetailsInput,
    GetExecutionLogsInput,
    ListExecutionsInput,
    RuntimeExecutionState,
    RuntimeLogSeverity,
)
from src.runtime.providers import RuntimeProviderError
from src.runtime.providers.talend_tmc import TalendTmcRuntimeProvider

_TOKEN = "super-secret-talend-token-value"


@pytest.fixture
def tmc(tmp_path):
    token_path = tmp_path / "talend_token"
    token_path.write_text(f"  {_TOKEN}\n", encoding="utf-8")

    s = MagicMock()
    s.talend_tmc_base_url = "https://api.us.cloud.talend.com"
    s.talend_tmc_api_token_file = str(token_path)
    s.talend_tmc_request_timeout = 10.0
    s.runtime_sync_source_key = "talend_tmc"

    with patch("src.runtime.providers.talend_tmc.settings", s):
        yield SimpleNamespace(
            settings=s,
            token_path=token_path,
            tmp_path=tmp_path,
            provider=lambda handler: TalendTmcRuntimeProvider(transport=httpx.MockTransport(handler)),
        )


class TestEndpointMapping:
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
                        "execution_id": "exec-2",
                        "timestamp": "2026-06-18T01:00:20.000Z",
                        "level": "ERROR",
                        "message": "[tDBInput_1] connection timeout",
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
        assert "/processing/executables/plans/plan-1/executions" in seen
        assert [source.source_key for source in batch.sources] == ["talend_tmc"]
        assert [environment.environment_id for environment in batch.environments] == ["env-prod"]
        assert [workspace.workspace_id for workspace in batch.workspaces] == ["ws-1"]
        assert [engine.name for engine in batch.engines] == ["prod-engine-01"]
        assert {job.name for job in batch.jobs} == {"nightly-load", "hourly-sync"}
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
        assert [failure.error_summary for failure in batch.failures] == ["connection timeout"]
        assert [plan.name for plan in batch.plans] == ["nightly-orchestration"]
        assert batch.plans[0].steps[0].job_id == "task-1"
        assert [run.run_id for run in batch.plan_runs] == ["pexec-1"]
        assert batch.plan_runs[0].step_results[0].execution_id == "exec-1"
        assert batch.log_events[0].message == "[tDBInput_1] connection timeout"

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


class TestConfigAndSecretHygiene:
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
