"""Talend TMC runtime provider adapter.

Endpoint mechanics are reconciled against the reference MCP repos:

- GET /processing/executables/tasks/executions
- GET /processing/executables/tasks/{task_id}/executions
- GET /processing/executions/{execution_id}
- POST /monitoring/executions/{execution_id}/logs
- POST /monitoring/executions/{execution_id}/logs/status
- fallback GET /monitoring/executions/{execution_id}/logs
- GET /monitoring/observability/executions/{execution_id}/component

The adapter maps TMC-shaped payloads into the generic runtime models. TMC names
stay in this file and tests; callers use the provider-neutral runtime contract.
"""

from __future__ import annotations

import asyncio
import logging
import math
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from src.config import settings
from src.runtime.models import (
    DetectFailuresInput,
    GetExecutionDetailsInput,
    GetExecutionLogsInput,
    ListExecutionsInput,
    RuntimeArtifact,
    RuntimeComponentMetric,
    RuntimeConnection,
    RuntimeEngine,
    RuntimeEngineStatus,
    RuntimeEnvironment,
    RuntimeExecution,
    RuntimeExecutionState,
    RuntimeFailure,
    RuntimeJob,
    RuntimeJobDependency,
    RuntimeJobRun,
    RuntimeLogEvent,
    RuntimeLogSeverity,
    RuntimePlan,
    RuntimePlanRun,
    RuntimePlanRunStepResult,
    RuntimePlanStep,
    RuntimeSource,
    RuntimeSyncStatus,
    RuntimeWorkspace,
)
from src.runtime.providers import RuntimeProviderContext, RuntimeProviderError
from src.runtime.sync import RuntimeSyncBatch

logger = logging.getLogger(__name__)

_LOG_PAGE_SIZE = 200
_LOG_MAX_POLL_ATTEMPTS = 3
_LOG_POLL_INTERVAL_SECONDS = 0.0
_COMPONENT_PAGE_SIZE = 50
_DEFAULT_FETCH_LIMIT = 1000

_STATE_MAP: dict[str, RuntimeExecutionState] = {
    "execution_successful": RuntimeExecutionState.SUCCESS,
    "success": RuntimeExecutionState.SUCCESS,
    "succeeded": RuntimeExecutionState.SUCCESS,
    "ok": RuntimeExecutionState.SUCCESS,
    "execution_failed": RuntimeExecutionState.FAILED,
    "failed": RuntimeExecutionState.FAILED,
    "error": RuntimeExecutionState.FAILED,
    "execution_running": RuntimeExecutionState.RUNNING,
    "running": RuntimeExecutionState.RUNNING,
    "in_progress": RuntimeExecutionState.RUNNING,
}

_SEVERITY_MAP: dict[str, RuntimeLogSeverity] = {
    "debug": RuntimeLogSeverity.DEBUG,
    "info": RuntimeLogSeverity.INFO,
    "information": RuntimeLogSeverity.INFO,
    "warn": RuntimeLogSeverity.WARNING,
    "warning": RuntimeLogSeverity.WARNING,
    "error": RuntimeLogSeverity.ERROR,
    "fatal": RuntimeLogSeverity.ERROR,
    "critical": RuntimeLogSeverity.ERROR,
}


class TalendTmcRuntimeProvider:
    """HTTP adapter to Talend TMC."""

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._transport = transport
        self._token: str | None = None

    def _read_token(self) -> str:
        if self._token is not None:
            return self._token
        token_file = settings.talend_tmc_api_token_file
        if not token_file:
            raise RuntimeProviderError("Talend TMC token file is not configured")
        try:
            raw = Path(token_file).read_text(encoding="utf-8")
        except OSError as e:
            raise RuntimeProviderError("Talend TMC token file could not be read") from e
        token = raw.strip()
        if not token:
            raise RuntimeProviderError("Talend TMC token file is empty")
        self._token = token
        return token

    def _base_url(self) -> str:
        base_url = settings.talend_tmc_base_url
        if not base_url:
            raise RuntimeProviderError("Talend TMC base URL is not configured")
        return base_url.rstrip("/")

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self._base_url(),
            timeout=settings.talend_tmc_request_timeout,
            transport=self._transport,
            headers={"Authorization": f"Bearer {self._read_token()}"},
        )

    async def _get(self, path: str, *, params: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            async with self._client() as client:
                response = await client.get(path, params={k: v for k, v in (params or {}).items() if v is not None})
                response.raise_for_status()
                data = response.json()
        except httpx.HTTPStatusError as e:
            raise RuntimeProviderError(f"Talend TMC GET {path} returned HTTP {e.response.status_code}") from e
        except httpx.HTTPError as e:
            raise RuntimeProviderError(f"Talend TMC GET {path} transport failure: {type(e).__name__}") from e
        if not isinstance(data, dict):
            raise RuntimeProviderError(f"Talend TMC GET {path} returned a non-object payload")
        return data

    async def _post(self, path: str, *, json_body: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            async with self._client() as client:
                response = await client.post(path, json={k: v for k, v in (json_body or {}).items() if v is not None})
                response.raise_for_status()
                data = response.json()
        except httpx.HTTPStatusError as e:
            raise RuntimeProviderError(f"Talend TMC POST {path} returned HTTP {e.response.status_code}") from e
        except httpx.HTTPError as e:
            raise RuntimeProviderError(f"Talend TMC POST {path} transport failure: {type(e).__name__}") from e
        if not isinstance(data, dict):
            raise RuntimeProviderError(f"Talend TMC POST {path} returned a non-object payload")
        return data

    async def _get_all_pages(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        max_items: int = 1000,
    ) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        offset = 0
        page_size = min(max_items, 200)
        while len(items) < max_items:
            page = await self._get(path, params={**(params or {}), "limit": page_size, "offset": offset})
            batch = _extract_items(page)
            items.extend(batch)
            total = _as_int(page.get("total") or page.get("totalSize")) or len(items)
            offset += len(batch)
            if not batch or offset >= total:
                break
        return items[:max_items]

    async def list_executions(
        self, params: ListExecutionsInput, context: RuntimeProviderContext | None = None
    ) -> list[RuntimeExecution]:
        path = "/processing/executables/tasks/executions"
        query = {
            "lastDays": _last_days_from_window(params.since, params.until),
            "status": _tmc_status(params.state),
        }
        items = await self._get_all_pages(path, params=query, max_items=params.limit)
        executions = [_to_execution(item) for item in items]
        if params.job_name:
            executions = [item for item in executions if item.job_name == params.job_name]
        if params.project_name:
            executions = [item for item in executions if item.project_name == params.project_name]
        return executions[: params.limit]

    async def get_execution_details(
        self, params: GetExecutionDetailsInput, context: RuntimeProviderContext | None = None
    ) -> RuntimeExecution:
        data = await self._get(f"/processing/executions/{params.execution_id}")
        return _to_execution(data.get("execution") if isinstance(data.get("execution"), dict) else data)

    async def detect_failures(
        self, params: DetectFailuresInput, context: RuntimeProviderContext | None = None
    ) -> list[RuntimeFailure]:
        list_params = ListExecutionsInput(
            state=RuntimeExecutionState.FAILED,
            since=params.since,
            until=params.until,
            limit=params.limit,
        )
        executions = await self.list_executions(list_params, context)
        return [
            RuntimeFailure(
                execution_id=item.execution_id,
                job_name=item.job_name,
                failed_at=item.ended_at or item.started_at,
                error_summary=f"{item.job_name} failed in Talend TMC",
            )
            for item in executions
        ]

    async def get_execution_logs(
        self, params: GetExecutionLogsInput, context: RuntimeProviderContext | None = None
    ) -> list[RuntimeLogEvent]:
        logs = await self._get_execution_logs_async(params.execution_id)
        if logs is None:
            logs = await self._get_execution_logs_paginated(params.execution_id, params.limit)
        events = [_to_log_event(item, params.execution_id) for item in logs]
        if params.min_severity is not None:
            allowed = _severities_at_or_above(params.min_severity)
            events = [item for item in events if item.severity in allowed]
        return events[: params.limit]

    async def get_component_metrics(self, execution_id: str) -> list[RuntimeComponentMetric]:
        items = await self._get_component_items(execution_id)
        return [_to_component_metric(item, execution_id) for item in items if _is_component_edge(item)]

    async def fetch_runtime_batch(self, *, project_id: str) -> RuntimeSyncBatch:
        synced_at = datetime.now(UTC)
        source_key = settings.runtime_sync_source_key or "talend_tmc"

        environments_raw = await self._get_all_pages("/orchestration/environments", max_items=_DEFAULT_FETCH_LIMIT)
        workspaces_raw = await self._get_all_pages("/orchestration/workspaces", max_items=_DEFAULT_FETCH_LIMIT)
        engines_raw = await self._get_all_pages("/processing/runtimes/remote-engines", max_items=_DEFAULT_FETCH_LIMIT)
        artifacts_raw = await self._get_all_pages("/orchestration/artifacts", max_items=_DEFAULT_FETCH_LIMIT)
        connections_raw = await self._get_all_pages("/orchestration/connections", max_items=_DEFAULT_FETCH_LIMIT)
        tasks_raw = await self._get_all_pages("/orchestration/executables/tasks", max_items=_DEFAULT_FETCH_LIMIT)
        plans_raw = await self._get_all_pages("/orchestration/executables/plans", max_items=_DEFAULT_FETCH_LIMIT)

        environments = [_to_environment(item, source_key, synced_at) for item in environments_raw]
        workspaces = [_to_workspace(item, source_key, synced_at) for item in workspaces_raw]
        engines = [_to_engine(item, source_key, synced_at) for item in engines_raw]
        engines_by_id = {engine.engine_id: engine for engine in engines}
        workspaces_by_id = {workspace.workspace_id: workspace for workspace in workspaces}
        artifacts = [_to_artifact(item, source_key, synced_at) for item in artifacts_raw]
        artifacts_by_id = {artifact.artifact_id: artifact for artifact in artifacts}
        connections = [_to_connection(item, source_key, synced_at) for item in connections_raw]
        connections_by_id = {connection.connection_id: connection for connection in connections}

        jobs = [_to_job(item, source_key, synced_at, engines_by_id) for item in tasks_raw]
        jobs_by_id = {job.job_id: job for job in jobs}
        job_dependencies = _dependency_edges_from_tasks(
            tasks_raw,
            jobs_by_id=jobs_by_id,
            artifacts_by_id=artifacts_by_id,
            connections_by_id=connections_by_id,
            source_key=source_key,
            synced_at=synced_at,
        )

        task_runs_raw: list[dict[str, Any]] = []
        for job in jobs:
            task_runs_raw.extend(
                await self._get_all_pages(
                    f"/processing/executables/tasks/{job.job_id}/executions",
                    params={"lastDays": 7},
                    max_items=_DEFAULT_FETCH_LIMIT,
                )
            )

        plans = [_to_plan(item, source_key, synced_at, jobs_by_id, workspaces_by_id) for item in plans_raw]
        plans_by_id = {plan.plan_id: plan for plan in plans}
        plan_runs_raw: list[dict[str, Any]] = []
        for plan in plans:
            plan_runs_raw.extend(
                await self._get_all_pages(
                    f"/processing/executables/plans/{plan.plan_id}/executions",
                    params={"lastDays": 7},
                    max_items=_DEFAULT_FETCH_LIMIT,
                )
            )

        job_runs = [_to_job_run(item, source_key, jobs_by_id, engines_by_id) for item in task_runs_raw]
        plan_runs = [_to_plan_run(item, source_key, plans_by_id, task_runs_raw) for item in plan_runs_raw]
        executions = [_execution_from_job_run(run, project_id) for run in job_runs]
        failures = [
            RuntimeFailure(
                execution_id=run.run_id,
                job_name=run.job_name,
                failed_at=run.ended_at or run.started_at,
                error_summary=_failure_summary_for_task_run(raw, run),
            )
            for raw, run in zip(task_runs_raw, job_runs, strict=False)
            if run.state == RuntimeExecutionState.FAILED
        ]
        log_events: list[RuntimeLogEvent] = []
        component_metrics: list[RuntimeComponentMetric] = []
        for run in job_runs:
            if run.state == RuntimeExecutionState.FAILED:
                try:
                    log_events.extend(await self.get_execution_logs(GetExecutionLogsInput(execution_id=run.run_id, limit=200)))
                except RuntimeProviderError:
                    logger.warning("TMC log fetch failed for execution %s", run.run_id)
            try:
                component_metrics.extend(await self.get_component_metrics(run.run_id))
            except RuntimeProviderError:
                logger.warning("TMC component metric fetch failed for execution %s", run.run_id)
        return RuntimeSyncBatch(
            sources=[
                RuntimeSource(
                    project_id=project_id,
                    source_key=source_key,
                    source_type="talend_tmc",
                    label="Talend TMC",
                    status=RuntimeSyncStatus.SUCCESS,
                    is_demo=False,
                    last_synced_at=synced_at,
                    last_seen_at=synced_at,
                    ingested_at=synced_at,
                )
            ],
            environments=environments,
            workspaces=workspaces,
            engines=engines,
            jobs=jobs,
            artifacts=artifacts,
            connections=connections,
            job_dependencies=job_dependencies,
            job_runs=job_runs,
            plans=plans,
            plan_runs=plan_runs,
            executions=executions,
            failures=failures,
            log_events=log_events,
            component_metrics=component_metrics,
            synced_at=synced_at,
        )

    async def _get_execution_logs_async(self, execution_id: str) -> list[dict[str, Any]] | None:
        try:
            generated = await self._post(
                f"/monitoring/executions/{execution_id}/logs",
                json_body={"fileFormat": "JSON"},
            )
            token = generated.get("token")
            body = {"token": token, "fileFormat": "JSON"} if token else {"fileFormat": "JSON"}
            for _ in range(_LOG_MAX_POLL_ATTEMPTS):
                if _LOG_POLL_INTERVAL_SECONDS:
                    await asyncio.sleep(_LOG_POLL_INTERVAL_SECONDS)
                status = await self._post(
                    f"/monitoring/executions/{execution_id}/logs/status",
                    json_body=body,
                )
                if status.get("status") == "READY":
                    url = status.get("downloadUrl")
                    if not isinstance(url, str) or not _is_allowed_download_url(url):
                        raise RuntimeProviderError("Talend TMC log download URL is not trusted")
                    return await self._download_logs(url)
                if status.get("status") in {"FAILED", "ERROR"}:
                    return None
        except RuntimeProviderError:
            return None
        return None

    async def _download_logs(self, url: str) -> list[dict[str, Any]]:
        try:
            async with self._client() as client:
                response = await client.get(url, timeout=60)
                response.raise_for_status()
                data = response.json()
        except (httpx.HTTPError, ValueError) as e:
            raise RuntimeProviderError(f"Talend TMC log download failed: {type(e).__name__}") from e
        return _extract_items(data) if isinstance(data, dict) else data if isinstance(data, list) else []

    async def _get_execution_logs_paginated(self, execution_id: str, max_entries: int) -> list[dict[str, Any]]:
        all_logs: list[dict[str, Any]] = []
        start_index = 0
        while len(all_logs) < max_entries:
            page = await self._get(
                f"/monitoring/executions/{execution_id}/logs",
                params={"count": _LOG_PAGE_SIZE, "startIndex": start_index, "order": "ASC"},
            )
            batch = _extract_items(page)
            all_logs.extend(batch)
            total = _as_int(page.get("totalSize") or page.get("total")) or len(all_logs)
            start_index += len(batch)
            if not batch or start_index >= total:
                break
        return all_logs[:max_entries]

    async def _get_component_items(self, execution_id: str) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        offset = 0
        while True:
            page = await self._get(
                f"/monitoring/observability/executions/{execution_id}/component",
                params={"limit": _COMPONENT_PAGE_SIZE, "offset": offset},
            )
            metrics = page.get("metrics", {}) if isinstance(page, dict) else {}
            batch = metrics.get("items", []) if isinstance(metrics, dict) else []
            batch = [item for item in batch if isinstance(item, dict)]
            items.extend(batch)
            total = _as_int(metrics.get("total")) if isinstance(metrics, dict) else len(items)
            offset += len(batch)
            if not batch or offset >= (total or len(items)):
                break
        return items


def _extract_items(data: dict[str, Any]) -> list[dict[str, Any]]:
    raw = data.get("items", data.get("data", data.get("logs", [])))
    return [item for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []


def _dict_field(item: dict[str, Any], *names: str) -> dict[str, Any]:
    value = _field(item, *names)
    return value if isinstance(value, dict) else {}


def _field(item: dict[str, Any], *names: str) -> Any:
    for name in names:
        value = item.get(name)
        if value is not None:
            return value
    return None


def _str_field(item: dict[str, Any], *names: str) -> str | None:
    value = _field(item, *names)
    return value if isinstance(value, str) and value else None


def _required_str(item: dict[str, Any], *names: str) -> str:
    value = _str_field(item, *names)
    if value is None:
        raise RuntimeProviderError(f"Talend TMC payload missing field: {'/'.join(names)}")
    return value


def _parse_dt(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as e:
        raise RuntimeProviderError("Talend TMC payload has unparseable datetime") from e


def _normalize_state(raw: Any) -> RuntimeExecutionState:
    if not isinstance(raw, str):
        return RuntimeExecutionState.UNKNOWN
    return _STATE_MAP.get(raw.strip().lower(), RuntimeExecutionState.UNKNOWN)


def _normalize_engine_status(raw: Any, availability: Any = None) -> RuntimeEngineStatus:
    raw_value = raw.strip().lower() if isinstance(raw, str) else ""
    availability_value = availability.strip().lower() if isinstance(availability, str) else ""
    if raw_value in {"running", "paired", "online", "available"} and availability_value != "unavailable":
        return RuntimeEngineStatus.ONLINE
    if raw_value in {"stopped", "offline"} or availability_value == "unavailable":
        return RuntimeEngineStatus.OFFLINE
    if raw_value in {"degraded", "warning"}:
        return RuntimeEngineStatus.DEGRADED
    if raw_value in {"stale"}:
        return RuntimeEngineStatus.STALE
    return RuntimeEngineStatus.UNKNOWN


def _normalize_severity(raw: Any) -> RuntimeLogSeverity:
    if not isinstance(raw, str):
        return RuntimeLogSeverity.INFO
    return _SEVERITY_MAP.get(raw.strip().lower(), RuntimeLogSeverity.INFO)


def _to_execution(item: Any) -> RuntimeExecution:
    if not isinstance(item, dict):
        raise RuntimeProviderError("Talend TMC execution entry is not an object")
    started = _parse_dt(_field(item, "startTime", "start_time", "startedAt", "startDate"))
    if started is None:
        raise RuntimeProviderError("Talend TMC execution entry missing start time")
    ended = _parse_dt(_field(item, "finishTime", "endTime", "end_time", "endedAt"))
    duration = _as_int(_field(item, "durationMs", "duration_ms", "duration"))
    if duration is None and ended is not None:
        duration = int((ended - started).total_seconds() * 1000)
    return RuntimeExecution(
        execution_id=_required_str(item, "executionId", "id"),
        job_name=_required_str(item, "taskName", "name", "job_name", "artifactName"),
        project_name=_str_field(item, "workspaceName", "workspace_name", "projectName", "project_name"),
        state=_normalize_state(_field(item, "status", "state")),
        started_at=started,
        ended_at=ended,
        duration_ms=duration,
    )


def _to_environment(item: dict[str, Any], source_key: str, synced_at: datetime) -> RuntimeEnvironment:
    return RuntimeEnvironment(
        source_key=source_key,
        environment_id=_required_str(item, "id", "environment_id"),
        name=_str_field(item, "name") or _required_str(item, "id", "environment_id"),
        description=_str_field(item, "description"),
        last_seen_at=synced_at,
    )


def _to_workspace(item: dict[str, Any], source_key: str, synced_at: datetime) -> RuntimeWorkspace:
    environment = _dict_field(item, "environment")
    return RuntimeWorkspace(
        source_key=source_key,
        workspace_id=_required_str(item, "id", "workspace_id"),
        name=_str_field(item, "name") or _required_str(item, "id", "workspace_id"),
        environment_id=_str_field(environment, "id", "environment_id"),
        environment_name=_str_field(environment, "name", "environment_name"),
        owner=_str_field(item, "owner"),
        workspace_type=_str_field(item, "type", "workspace_type"),
        last_seen_at=synced_at,
    )


def _to_engine(item: dict[str, Any], source_key: str, synced_at: datetime) -> RuntimeEngine:
    workspace = _dict_field(item, "workspace")
    environment = _dict_field(workspace, "environment")
    raw_status = _str_field(item, "status")
    availability = _str_field(item, "availability")
    return RuntimeEngine(
        source_key=source_key,
        engine_id=_required_str(item, "id", "engine_id"),
        name=_str_field(item, "name") or _required_str(item, "id", "engine_id"),
        status=_normalize_engine_status(raw_status, availability),
        raw_status=raw_status,
        availability=availability,
        version=_str_field(item, "esbCompatibilityVersion", "version"),
        environment_id=_str_field(environment, "id", "environment_id"),
        environment_name=_str_field(environment, "name", "environment_name"),
        last_seen_at=synced_at,
        source_updated_at=synced_at,
    )


def _to_job(
    item: dict[str, Any],
    source_key: str,
    synced_at: datetime,
    engines_by_id: dict[str, RuntimeEngine],
) -> RuntimeJob:
    workspace = _dict_field(item, "workspace")
    environment = _dict_field(workspace, "environment")
    runtime = _dict_field(item, "runtime")
    engine_id = _str_field(runtime, "id", "engineId", "engine_id")
    engine = engines_by_id.get(engine_id or "")
    job_id = _required_str(item, "executable", "task_id", "id")
    name = _str_field(item, "name") or job_id
    return RuntimeJob(
        source_key=source_key,
        job_id=job_id,
        name=name,
        display_name=name,
        job_type="task",
        status=_str_field(item, "status") or "active",
        environment_id=_str_field(environment, "id", "environment_id"),
        environment_name=_str_field(environment, "name", "environment_name"),
        workspace_id=_str_field(workspace, "id", "workspace_id"),
        workspace_name=_str_field(workspace, "name", "workspace_name"),
        engine_id=engine_id,
        engine_name=engine.name if engine is not None else None,
        owner=_str_field(workspace, "owner"),
        artifact_name=_str_field(item, "artifactId", "artifact_id"),
        last_seen_at=synced_at,
        source_updated_at=synced_at,
    )


def _to_artifact(item: dict[str, Any], source_key: str, synced_at: datetime) -> RuntimeArtifact:
    workspace = _dict_field(item, "workspace")
    artifact_id = _required_str(item, "id", "artifactId", "artifact_id")
    name = _str_field(item, "name", "artifactName") or artifact_id
    return RuntimeArtifact(
        source_key=source_key,
        artifact_id=artifact_id,
        name=name,
        artifact_type=_str_field(item, "type", "artifactType", "artifact_type"),
        version=_str_field(item, "version", "artifactVersion"),
        workspace_id=_str_field(workspace, "id", "workspace_id") or _str_field(item, "workspaceId"),
        workspace_name=_str_field(workspace, "name", "workspace_name"),
        owner=_str_field(item, "owner", "createdBy"),
        last_seen_at=synced_at,
        source_updated_at=_parse_dt(_field(item, "updatedAt", "lastUpdated", "lastModified")) or synced_at,
    )


def _to_connection(item: dict[str, Any], source_key: str, synced_at: datetime) -> RuntimeConnection:
    workspace = _dict_field(item, "workspace")
    connection_id = _required_str(item, "id", "connectionId", "connection_id")
    name = _str_field(item, "name", "connectionName") or connection_id
    return RuntimeConnection(
        source_key=source_key,
        connection_id=connection_id,
        name=name,
        connection_type=_str_field(item, "type", "connectionType", "connectorType"),
        workspace_id=_str_field(workspace, "id", "workspace_id") or _str_field(item, "workspaceId"),
        workspace_name=_str_field(workspace, "name", "workspace_name"),
        owner=_str_field(item, "owner", "createdBy"),
        last_seen_at=synced_at,
        source_updated_at=_parse_dt(_field(item, "updatedAt", "lastUpdated", "lastModified")) or synced_at,
    )


def _dependency_edges_from_tasks(
    tasks_raw: list[dict[str, Any]],
    *,
    jobs_by_id: dict[str, RuntimeJob],
    artifacts_by_id: dict[str, RuntimeArtifact],
    connections_by_id: dict[str, RuntimeConnection],
    source_key: str,
    synced_at: datetime,
) -> list[RuntimeJobDependency]:
    edges: list[RuntimeJobDependency] = []
    seen: set[tuple[str, str, str]] = set()
    for raw in tasks_raw:
        job_id = _str_field(raw, "executable", "task_id", "id")
        if job_id is None:
            continue
        job = jobs_by_id.get(job_id)
        job_name = job.name if job is not None else job_id
        artifact_id = _str_field(raw, "artifactId", "artifact_id")
        if artifact_id:
            artifact = artifacts_by_id.get(artifact_id)
            key = (job_id, "artifact", artifact_id)
            if key not in seen:
                seen.add(key)
                edges.append(
                    RuntimeJobDependency(
                        source_key=source_key,
                        job_id=job_id,
                        job_name=job_name,
                        dependency_type="artifact",
                        dependency_id=artifact_id,
                        dependency_name=artifact.name if artifact is not None else _str_field(raw, "artifactName"),
                        dependency_kind=artifact.artifact_type if artifact is not None else None,
                        workspace_id=job.workspace_id if job is not None else None,
                        workspace_name=job.workspace_name if job is not None else None,
                        last_seen_at=synced_at,
                    )
                )
        for connection_id in _connection_ids(raw):
            connection = connections_by_id.get(connection_id)
            key = (job_id, "connection", connection_id)
            if key in seen:
                continue
            seen.add(key)
            edges.append(
                RuntimeJobDependency(
                    source_key=source_key,
                    job_id=job_id,
                    job_name=job_name,
                    dependency_type="connection",
                    dependency_id=connection_id,
                    dependency_name=connection.name if connection is not None else None,
                    dependency_kind=connection.connection_type if connection is not None else None,
                    workspace_id=job.workspace_id if job is not None else None,
                    workspace_name=job.workspace_name if job is not None else None,
                    last_seen_at=synced_at,
                )
            )
    return edges


def _connection_ids(item: dict[str, Any]) -> list[str]:
    ids: list[str] = []
    for field_name in ("connectionIds", "connections"):
        raw = item.get(field_name)
        if isinstance(raw, list):
            for entry in raw:
                if isinstance(entry, str):
                    ids.append(entry)
                elif isinstance(entry, dict):
                    value = _str_field(entry, "id", "connectionId", "connection_id")
                    if value:
                        ids.append(value)
        elif isinstance(raw, dict):
            value = _str_field(raw, "id", "connectionId", "connection_id")
            if value:
                ids.append(value)
    single = _str_field(item, "connectionId", "connection_id")
    if single:
        ids.append(single)
    return list(dict.fromkeys(ids))


def _to_job_run(
    item: dict[str, Any],
    source_key: str,
    jobs_by_id: dict[str, RuntimeJob],
    engines_by_id: dict[str, RuntimeEngine],
) -> RuntimeJobRun:
    job_id = _required_str(item, "taskId", "task_id")
    job = jobs_by_id.get(job_id)
    runtime = _dict_field(item, "runtime")
    engine_id = _str_field(runtime, "engineId", "id", "engine_id") or (job.engine_id if job else None)
    engine = engines_by_id.get(engine_id or "")
    started = _parse_dt(_field(item, "startTimestamp", "startTime", "startedAt")) or _parse_dt(_field(item, "triggerTimestamp"))
    if started is None:
        raise RuntimeProviderError("Talend TMC task execution missing start timestamp")
    ended = _parse_dt(_field(item, "finishTimestamp", "finishTime", "endedAt"))
    return RuntimeJobRun(
        source_key=source_key,
        run_id=_required_str(item, "executionId", "execution_id", "id"),
        job_id=job_id,
        job_name=job.name if job is not None else job_id,
        execution_id=_required_str(item, "executionId", "execution_id", "id"),
        state=_normalize_state(_field(item, "status", "executionStatus", "state")),
        environment_id=job.environment_id if job else None,
        environment_name=job.environment_name if job else None,
        workspace_id=job.workspace_id if job else None,
        workspace_name=job.workspace_name if job else None,
        engine_id=engine_id,
        engine_name=engine.name if engine is not None else (job.engine_name if job else None),
        started_at=started,
        ended_at=ended,
        duration_ms=_duration_ms(started, ended),
    )


def _to_plan(
    item: dict[str, Any],
    source_key: str,
    synced_at: datetime,
    jobs_by_id: dict[str, RuntimeJob],
    workspaces_by_id: dict[str, RuntimeWorkspace],
) -> RuntimePlan:
    workspace = _dict_field(item, "workspace")
    workspace_id = _str_field(workspace, "id", "workspace_id")
    workspace_model = workspaces_by_id.get(workspace_id or "")
    plan_id = _required_str(item, "executable", "plan_id", "id")
    name = _str_field(item, "name") or plan_id
    return RuntimePlan(
        source_key=source_key,
        plan_id=plan_id,
        name=name,
        display_name=name,
        status=_plan_status(item),
        environment_id=workspace_model.environment_id if workspace_model is not None else None,
        environment_name=workspace_model.environment_name if workspace_model is not None else None,
        workspace_id=workspace_id,
        workspace_name=_str_field(workspace, "name", "workspace_name"),
        steps=_plan_steps(_dict_field(item, "chart"), jobs_by_id),
        last_seen_at=synced_at,
        source_updated_at=synced_at,
    )


def _to_plan_run(
    item: dict[str, Any],
    source_key: str,
    plans_by_id: dict[str, RuntimePlan],
    task_runs_raw: list[dict[str, Any]],
) -> RuntimePlanRun:
    plan_id = _required_str(item, "planId", "plan_id")
    plan = plans_by_id.get(plan_id)
    started = _parse_dt(_field(item, "startTimestamp", "startTime", "startedAt"))
    if started is None:
        raise RuntimeProviderError("Talend TMC plan execution missing start timestamp")
    ended = _parse_dt(_field(item, "finishTimestamp", "finishTime", "endedAt"))
    run_id = _required_str(item, "executionId", "execution_id", "id")
    return RuntimePlanRun(
        source_key=source_key,
        run_id=run_id,
        plan_id=plan_id,
        plan_name=plan.name if plan is not None else plan_id,
        state=_normalize_state(_field(item, "status", "executionStatus", "state")),
        environment_id=plan.environment_id if plan is not None else None,
        environment_name=plan.environment_name if plan is not None else None,
        workspace_id=plan.workspace_id if plan is not None else None,
        workspace_name=plan.workspace_name if plan is not None else None,
        started_at=started,
        ended_at=ended,
        duration_ms=_duration_ms(started, ended),
        step_results=_plan_step_results(plan_id, task_runs_raw),
    )


def _execution_from_job_run(run: RuntimeJobRun, project_id: str) -> RuntimeExecution:
    return RuntimeExecution(
        execution_id=run.run_id,
        job_name=run.job_name,
        project_name=project_id,
        state=run.state,
        started_at=run.started_at,
        ended_at=run.ended_at,
        duration_ms=run.duration_ms,
    )


def _to_log_event(item: Any, fallback_execution_id: str) -> RuntimeLogEvent:
    if not isinstance(item, dict):
        raise RuntimeProviderError("Talend TMC log entry is not an object")
    timestamp = _parse_dt(_field(item, "timestamp", "logTimestamp", "time", "date"))
    if timestamp is None:
        timestamp = datetime.now(UTC)
    return RuntimeLogEvent(
        execution_id=_str_field(item, "executionId", "execution_id") or fallback_execution_id,
        timestamp=timestamp,
        severity=_normalize_severity(_field(item, "severity", "level", "logLevel")),
        message=_required_str(item, "logMessage", "message", "text"),
    )


def _to_component_metric(item: dict[str, Any], execution_id: str) -> RuntimeComponentMetric:
    component_key = _required_str(item, "connector_id", "source_connector_id", "componentName", "component_name")
    return RuntimeComponentMetric(
        execution_id=execution_id,
        component_key=component_key,
        component_type=_str_field(item, "component_type", "connector_type"),
        source_connector_type=_str_field(item, "connector_type", "source_connector_type"),
        target_connector_type=_str_field(item, "target_connector_type"),
        rows_total=_as_int(_field(item, "component_connection_rows_total", "rows_total", "rowsProcessed")),
        rows_rejected=_as_int(_field(item, "rows_rejected", "rejectedRows")),
        duration_ms=_as_int(_field(item, "component_execution_duration_milliseconds", "duration_ms")),
        measured_at=_parse_dt(_field(item, "endTime", "finishTime", "timestamp")),
    )


def _duration_ms(started: datetime, ended: datetime | None) -> int | None:
    if ended is None:
        return None
    return int((ended - started).total_seconds() * 1000)


def _failure_summary_for_task_run(raw: dict[str, Any], run: RuntimeJobRun) -> str:
    message = _str_field(raw, "errorMessage", "error_message")
    if message:
        return message[:300]
    return f"{run.job_name} failed in Talend TMC"


def _plan_status(item: dict[str, Any]) -> str | None:
    if _field(item, "disabled", "paused") is True:
        return "paused"
    if isinstance(_field(item, "planPauseDetails"), dict) and _field(item, "planPauseDetails"):
        return "paused"
    return _str_field(item, "status") or "active"


def _plan_steps(chart: dict[str, Any], jobs_by_id: dict[str, RuntimeJob]) -> list[RuntimePlanStep]:
    steps: list[RuntimePlanStep] = []
    node: Any = chart
    order = 1
    while isinstance(node, dict):
        flows = node.get("flows") or []
        for index, flow in enumerate(flows, start=1):
            if not isinstance(flow, dict):
                continue
            job_id = _str_field(flow, "id", "taskId", "job_id")
            if job_id is None:
                continue
            job = jobs_by_id.get(job_id)
            steps.append(
                RuntimePlanStep(
                    step_id=f"step-{order}-{index}-{job_id}",
                    name=_str_field(flow, "name") or (job.display_name if job else None) or job_id,
                    job_id=job_id,
                    job_name=job.name if job else _str_field(flow, "name"),
                    order=order,
                )
            )
        node = node.get("nextStep")
        order += 1
    return steps


def _plan_step_results(plan_id: str, task_runs_raw: list[dict[str, Any]]) -> list[RuntimePlanRunStepResult]:
    results: list[RuntimePlanRunStepResult] = []
    for item in task_runs_raw:
        if _str_field(item, "planId", "plan_id") != plan_id:
            continue
        job_id = _str_field(item, "taskId", "task_id")
        execution_id = _str_field(item, "executionId", "execution_id")
        if job_id is None and execution_id is None:
            continue
        results.append(
            RuntimePlanRunStepResult(
                step_id=job_id or execution_id or "unknown",
                job_id=job_id,
                job_name=None,
                state=_normalize_state(_field(item, "status", "executionStatus", "state")),
                execution_id=execution_id,
            )
        )
    return results


def _is_component_edge(item: dict[str, Any]) -> bool:
    return _field(item, "component_connection_rows_total", "rows_total", "rowsProcessed") is not None


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return int(value)
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _last_days_from_window(since: datetime | None, until: datetime | None) -> int:
    if since is None:
        return 7
    end = until or datetime.now(UTC)
    days = max(1, math.ceil((end - since).total_seconds() / 86400))
    return min(days, 60)


def _tmc_status(state: RuntimeExecutionState | None) -> str | None:
    if state == RuntimeExecutionState.FAILED:
        return "EXECUTION_FAILED"
    if state == RuntimeExecutionState.SUCCESS:
        return "EXECUTION_SUCCESSFUL"
    if state == RuntimeExecutionState.RUNNING:
        return "EXECUTION_RUNNING"
    return None


def _severities_at_or_above(minimum: RuntimeLogSeverity) -> set[RuntimeLogSeverity]:
    order = list(RuntimeLogSeverity)
    return set(order[order.index(minimum):])


def _is_allowed_download_url(url: str) -> bool:
    if not url.startswith("https://"):
        return False
    try:
        domain = url.split("//", 1)[1].split("/", 1)[0].split(":")[0]
    except IndexError:
        return False
    return domain.endswith(".cloud.talend.com")


provider = TalendTmcRuntimeProvider()
