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
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Mapping

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
    RuntimeScopeOption,
    RuntimeScopeSelection,
    RuntimeSource,
    RuntimeSyncStatus,
    RuntimeWorkspace,
)
from src.runtime.providers import RuntimeProviderContext, RuntimeProviderError
from src.runtime.providers.base import CredentialField, compose_tenant_identity
from src.runtime.sync import RuntimeSyncBatch

logger = logging.getLogger(__name__)

# TMC rejects log page sizes above 200 with HTTP 400.
_LOG_PAGE_SIZE = 200
_LOG_CAPTURE_LIMIT = 10_000
# A long-running Route can exceed the capture ceiling by orders of magnitude.
# Keeping only the first N lines preserves startup noise and loses the terminal
# failure.  Reserve a small head for lifecycle context and spend the rest on
# the tail, where TMC reports the final component/error sequence.
_LOG_HEAD_CAPTURE = 1_000
_LOG_MAX_POLL_ATTEMPTS = 3
_LOG_POLL_INTERVAL_SECONDS = 0.0
_COMPONENT_PAGE_SIZE = 50
_DEFAULT_FETCH_LIMIT = 1000
_DEFAULT_HISTORY_DAYS = 90
_RUNS_PER_EXECUTABLE_LIMIT = 250
_EXECUTION_WINDOW_DAYS = 10
# Fallback ceilings used when settings supply none. The operative values come
# from settings (talend_tmc_failed_log_run_limit /
# talend_tmc_component_metric_run_limit /
# talend_tmc_runs_per_executable_limit) so the capture window is tunable per
# deployment rather than hardcoded.
_FAILED_LOG_RUN_LIMIT = 10
_COMPONENT_METRIC_RUN_LIMIT = 25
_DETAIL_FETCH_CONCURRENCY = 5
_TRANSPORT_MAX_ATTEMPTS = 3
_TRANSPORT_RETRY_DELAY_SECONDS = 0.25
# TMC caps page size per endpoint and does not publish the cap: /orchestration
# /executables/tasks rejects anything above 100 with HTTP 400 while others take
# 200. _get_all_pages halves its page size on rejection down to this floor
# before concluding the endpoint takes no pagination at all.
_MAX_PAGE_SIZE = 200
_MIN_PAGE_SIZE = 25
# The scope-discovery probe only needs each environment's task count, so it
# asks for the smallest legal page and reads the envelope total.
_SCOPE_PROBE_PAGE_SIZE = 1

# A source may provide its own comma-separated filters. The WerkOrder default
# stays business-object-specific: application-only terms such as ``Maximo`` or
# ``RHM`` also match payroll, HR, asset, and supplier flows and therefore need
# an explicit operator override rather than silently widening this source.
_PROJECT_SCOPE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "werkorder": (
        "werkorder",
        "werkorders",
        "workorder",
        "workorders",
        "work order",
        "work orders",
    ),
}

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
        self._base_url_override: str | None = None
        self._task_scope_keywords: tuple[str, ...] = ()
        self._history_days = _DEFAULT_HISTORY_DAYS
        self._environment_ids: tuple[str, ...] = ()
        # One client/connection pool per sync. Creating a new AsyncClient for
        # every history page, log poll and metric probe repeatedly constructs
        # SSL contexts and transports; hundreds of short-lived clients caused
        # RSS to climb to the 1GiB container limit during one import.
        self._http_client: httpx.AsyncClient | None = None
        # Page-size caps are per endpoint and discovered on first rejection;
        # caching them keeps the retry cost to one wasted call per path.
        self._page_size_by_path: dict[str, int] = {}

    def provider_key(self) -> str:
        return "tmc"

    def provider_label(self) -> str:
        return "Talend Management Console"

    def credential_schema(self) -> list[CredentialField]:
        return [
            {"name": "api_token", "type": "secret", "label": "API token", "required": True},
            {"name": "base_url", "type": "url", "label": "Base URL", "required": True},
            {
                "name": "task_name_filter",
                "type": "text",
                "label": "Task/artifact filters (comma-separated)",
                "required": False,
            },
            {
                "name": "history_days",
                "type": "text",
                "label": "Run history in days (1-90)",
                "required": False,
            },
        ]

    def scope_selection(self) -> RuntimeScopeSelection:
        return RuntimeScopeSelection(
            field="environment_ids",
            label="Environments",
            description=(
                "Only tasks, plans, and run history from the selected environments "
                "are polled."
            ),
            required=True,
            multiple=True,
        )

    def identity_credential_fields(self) -> set[str]:
        return {"base_url"}

    async def discover_scopes(self, credentials: Mapping[str, Any]) -> list[RuntimeScopeOption]:
        """List the TMC environments the token can see, with their task counts.

        Reaching /orchestration/environments is exactly what validate_credentials
        does, so a successful discovery is also a successful connection test.
        Counts come from the tasks endpoint's envelope total, one cheap probe per
        environment, so an operator can tell PROD from an empty scratch env
        before committing to a poll interval.
        """
        token = str(credentials.get("api_token") or "").strip()
        base_url = str(credentials.get("base_url") or "").strip().rstrip("/")
        if not token or not base_url:
            raise RuntimeProviderError("Missing required TMC credentials")
        probe = TalendTmcRuntimeProvider(transport=self._transport)
        probe._token = token
        probe._base_url_override = base_url
        try:
            environments = await probe._get_all_pages(
                "/orchestration/environments", max_items=_DEFAULT_FETCH_LIMIT
            )

            async def option(item: dict[str, Any]) -> RuntimeScopeOption | None:
                environment_id = _str_field(item, "id", "environment_id")
                if not environment_id:
                    return None
                name = _str_field(item, "name") or environment_id
                try:
                    count = await probe._count_tasks_in_environment(environment_id)
                except RuntimeProviderError:
                    # A count is a nicety; failing to get one must not hide the
                    # environment from the operator choosing scopes.
                    logger.warning("Could not count TMC tasks for environment %s", environment_id)
                    count = None
                return RuntimeScopeOption(
                    id=environment_id,
                    label=name,
                    detail=None if count is None else f"{count} task{'' if count == 1 else 's'}",
                    # Empty environments are almost never what an operator wants to
                    # poll, so they start unticked while staying selectable.
                    default_selected=bool(count),
                )

            options = await asyncio.gather(*(option(item) for item in environments))
            return [item for item in options if item is not None]
        finally:
            await probe.aclose()

    async def _count_tasks_in_environment(self, environment_id: str) -> int | None:
        page = await self._get(
            "/orchestration/executables/tasks",
            params={"environmentId": environment_id, "limit": _SCOPE_PROBE_PAGE_SIZE, "offset": 0},
        )
        return _page_total(page, "total", "totalSize")

    def container_key_field(self) -> str:
        return "workspace_id"

    def static_ref_strategy(self) -> None:
        return None

    def physical_source_identity(self, credentials: Mapping[str, Any]) -> str:
        base_url = str(credentials.get("base_url") or "").strip().rstrip("/").lower()
        if not base_url:
            raise RuntimeProviderError("Missing TMC base URL")
        return f"tmc:{base_url}"

    async def resolve_physical_identity(self, credentials: Mapping[str, Any]) -> str:
        """Narrow the regional endpoint to the Talend account the token belongs to.

        Every Talend Cloud tenant in a region shares one base URL, so the
        endpoint alone cannot tell two customers apart. Environment ids are
        account-scoped, so the set of them the token can see identifies the
        tenant without sending the token itself into storage.
        """
        environment_ids = await self._tenant_environment_ids(credentials)
        return compose_tenant_identity(self.physical_source_identity(credentials), environment_ids)

    def physical_identity_from_batch(
        self, credentials: Mapping[str, Any], batch: RuntimeSyncBatch
    ) -> str | None:
        environment_ids = [item.environment_id for item in batch.environments if item.environment_id]
        if not environment_ids:
            # A scoped sync can return no environments; keeping the stored
            # identity is safer than narrowing it to an empty marker set.
            return None
        return compose_tenant_identity(self.physical_source_identity(credentials), environment_ids)

    async def _tenant_environment_ids(self, credentials: Mapping[str, Any]) -> list[str]:
        token = str(credentials.get("api_token") or "").strip()
        base_url = str(credentials.get("base_url") or "").strip().rstrip("/")
        if not token or not base_url:
            raise RuntimeProviderError("Missing required TMC credentials")
        provider = TalendTmcRuntimeProvider(transport=self._transport)
        provider._token = token
        provider._base_url_override = base_url
        items = await provider._get_all_pages(
            "/orchestration/environments", max_items=_DEFAULT_FETCH_LIMIT
        )
        environment_ids = [
            environment_id
            for item in items
            if (environment_id := _str_field(item, "id", "environment_id"))
        ]
        if not environment_ids:
            raise RuntimeProviderError(
                "Talend TMC returned no environments for this token, so the account "
                "cannot be identified. Check that the token has environment access."
            )
        return environment_ids

    def source_type_aliases(self) -> set[str]:
        # Historical runtime mirrors used ``talend_tmc`` before self-service
        # source configuration standardized on the shorter provider key.
        return {self.provider_key(), "talend_tmc"}

    async def validate_credentials(self, credentials: Mapping[str, Any]) -> tuple[bool, str | None]:
        token = str(credentials.get("api_token") or "").strip()
        base_url = str(credentials.get("base_url") or "").strip().rstrip("/")
        if not token or not base_url:
            return False, "Missing required TMC credentials"
        try:
            _history_days(credentials)
            _environment_ids(credentials)
        except RuntimeProviderError as error:
            return False, str(error)
        try:
            async with httpx.AsyncClient(
                base_url=base_url,
                timeout=10.0,
                transport=self._transport,
                headers={"Authorization": f"Bearer {token}"},
            ) as client:
                response = await client.get("/orchestration/environments")
            if response.status_code == 200:
                return True, None
            return False, _sanitize_tmc_error(f"{response.status_code} {response.reason_phrase}", token)
        except httpx.TimeoutException:
            return False, "Connection timed out after 10s"
        except httpx.HTTPError as e:
            return False, _sanitize_tmc_error(str(e), token)
        except ValueError:
            # httpx encodes header values as ASCII (UnicodeEncodeError is a
            # ValueError subclass); a corrupted paste (e.g. a homoglyph
            # swapped into the token) must not surface as an unhandled 500.
            return False, "Credentials contain a character that could not be sent in an HTTP header"

    async def sync(self, *, credentials: Mapping[str, Any], project_id: str) -> RuntimeSyncBatch:
        token = str(credentials.get("api_token") or "").strip()
        base_url = str(credentials.get("base_url") or "").strip().rstrip("/")
        if not token or not base_url:
            raise RuntimeProviderError("Missing required TMC credentials")
        provider = TalendTmcRuntimeProvider(transport=self._transport)
        provider._token = token
        provider._base_url_override = base_url
        provider._task_scope_keywords = _task_scope_keywords(credentials, project_id)
        provider._history_days = _history_days(credentials)
        provider._environment_ids = _environment_ids(credentials)
        try:
            return await provider.fetch_runtime_batch(project_id=project_id)
        finally:
            await provider.aclose()

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
        if self._base_url_override:
            return self._base_url_override
        base_url = settings.talend_tmc_base_url
        if not base_url:
            raise RuntimeProviderError("Talend TMC base URL is not configured")
        return base_url.rstrip("/")

    def _client(self) -> httpx.AsyncClient:
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(
                base_url=self._base_url(),
                timeout=settings.talend_tmc_request_timeout,
                transport=self._transport,
                headers={"Authorization": f"Bearer {self._read_token()}"},
            )
        return self._http_client

    async def aclose(self) -> None:
        """Close the sync-scoped HTTP pool, if it was opened."""
        if self._http_client is not None:
            await self._http_client.aclose()
            self._http_client = None

    async def _get(self, path: str, *, params: dict[str, Any] | None = None) -> dict[str, Any] | list[dict[str, Any]]:
        for attempt in range(_TRANSPORT_MAX_ATTEMPTS):
            try:
                client = self._client()
                response = await client.get(
                    path,
                    params={k: v for k, v in (params or {}).items() if v is not None},
                )
                response.raise_for_status()
                data = response.json()
                break
            except httpx.HTTPStatusError as e:
                raise RuntimeProviderError(
                    f"Talend TMC GET {path} returned HTTP {e.response.status_code}"
                ) from e
            except httpx.TransportError as e:
                if attempt + 1 < _TRANSPORT_MAX_ATTEMPTS:
                    await asyncio.sleep(_TRANSPORT_RETRY_DELAY_SECONDS * (attempt + 1))
                    continue
                raise RuntimeProviderError(
                    f"Talend TMC GET {path} transport failure after "
                    f"{_TRANSPORT_MAX_ATTEMPTS} attempts: {type(e).__name__}"
                ) from e
            except ValueError as e:
                raise RuntimeProviderError(
                    f"Talend TMC GET {path} credentials could not be encoded for the request"
                ) from e
        # Talend TMC list endpoints return a bare JSON array; single-resource
        # endpoints return an object. Accept either; reject scalars/null.
        if isinstance(data, dict):
            return data
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        raise RuntimeProviderError(f"Talend TMC GET {path} returned a non-object/non-array payload")

    async def _post(self, path: str, *, json_body: dict[str, Any] | None = None) -> dict[str, Any]:
        for attempt in range(_TRANSPORT_MAX_ATTEMPTS):
            try:
                client = self._client()
                response = await client.post(
                    path,
                    json={k: v for k, v in (json_body or {}).items() if v is not None},
                )
                response.raise_for_status()
                data = response.json()
                break
            except httpx.HTTPStatusError as e:
                raise RuntimeProviderError(
                    f"Talend TMC POST {path} returned HTTP {e.response.status_code}"
                ) from e
            except httpx.TransportError as e:
                if attempt + 1 < _TRANSPORT_MAX_ATTEMPTS:
                    await asyncio.sleep(_TRANSPORT_RETRY_DELAY_SECONDS * (attempt + 1))
                    continue
                raise RuntimeProviderError(
                    f"Talend TMC POST {path} transport failure after "
                    f"{_TRANSPORT_MAX_ATTEMPTS} attempts: {type(e).__name__}"
                ) from e
            except ValueError as e:
                raise RuntimeProviderError(
                    f"Talend TMC POST {path} credentials could not be encoded for the request"
                ) from e
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
        cache_key = _page_size_cache_key(path)
        page_size = min(max_items, self._page_size_by_path.get(cache_key, _MAX_PAGE_SIZE))
        # TMC list endpoints do not share a uniform pagination contract: each
        # caps `limit` at its own undocumented value (tasks reject anything over
        # 100), and some reject limit/offset outright. A rejection therefore
        # cannot be read as "no pagination here" -- doing so drops every page
        # after the first and silently truncates the result to one default page.
        # Halve the page size instead, and only give up on pagination once even
        # the floor is refused. The working size is cached per path so the probe
        # costs one wasted call per endpoint rather than one per page.
        supports_pagination = True
        while len(items) < max_items:
            page_params = dict(params or {})
            if supports_pagination:
                page_params["limit"] = page_size
                page_params["offset"] = offset
            try:
                page = await self._get(path, params=page_params)
            except RuntimeProviderError as err:
                if not supports_pagination or not _is_pagination_rejected(err):
                    raise
                if page_size > _MIN_PAGE_SIZE:
                    page_size = max(_MIN_PAGE_SIZE, page_size // 2)
                    logger.info(
                        "Talend TMC %s rejected the page size; retrying with limit=%d",
                        path,
                        page_size,
                    )
                    continue
                supports_pagination = False
                continue
            if supports_pagination:
                self._page_size_by_path[cache_key] = page_size
            batch = _extract_items(page)
            items.extend(batch)
            total = _page_total(page, "total", "totalSize") or len(items)
            offset += len(batch)
            if not supports_pagination or not batch or offset >= total:
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
        else:
            logs = _bounded_log_head_tail(logs, params.limit)
        events = [_to_log_event(item, params.execution_id, ordinal) for ordinal, item in enumerate(logs)]
        if params.min_severity is not None:
            allowed = _severities_at_or_above(params.min_severity)
            events = [item for item in events if item.severity in allowed]
        return events[: params.limit]

    async def get_component_metrics(self, execution_id: str) -> list[RuntimeComponentMetric]:
        items = await self._get_component_items(execution_id)
        return [_to_component_metric(item, execution_id) for item in items if _is_component_edge(item)]

    async def _fetch_executables(self, path: str) -> list[dict[str, Any]]:
        """Fetch tasks or plans, narrowed to the selected environments.

        TMC honours ``environmentId`` on the executable endpoints, so scoping
        happens server-side: the request never carries the environments an
        operator excluded, and the expensive per-executable run-history fan-out
        downstream shrinks with it. Results are de-duplicated by executable id
        because the per-environment requests are independent.
        """
        if not self._environment_ids:
            return await self._get_all_pages(path, max_items=_DEFAULT_FETCH_LIMIT)
        pages = await asyncio.gather(
            *(
                self._get_all_pages(
                    path,
                    params={"environmentId": environment_id},
                    max_items=_DEFAULT_FETCH_LIMIT,
                )
                for environment_id in self._environment_ids
            )
        )
        merged: dict[str, dict[str, Any]] = {}
        for index, page in enumerate(pages):
            for item in page:
                key = _str_field(item, "executable", "id") or f"{index}:{len(merged)}"
                merged.setdefault(key, item)
        return list(merged.values())

    async def fetch_runtime_batch(self, *, project_id: str) -> RuntimeSyncBatch:
        synced_at = datetime.now(UTC)
        source_key = settings.runtime_sync_source_key or "talend_tmc"

        environments_raw = await self._get_all_pages("/orchestration/environments", max_items=_DEFAULT_FETCH_LIMIT)
        workspaces_raw = await self._get_all_pages("/orchestration/workspaces", max_items=_DEFAULT_FETCH_LIMIT)
        engines_raw = await self._get_all_pages("/processing/runtimes/remote-engines", max_items=_DEFAULT_FETCH_LIMIT)
        artifacts_raw = await self._get_all_pages("/orchestration/artifacts", max_items=_DEFAULT_FETCH_LIMIT)
        connections_raw = await self._get_all_pages("/orchestration/connections", max_items=_DEFAULT_FETCH_LIMIT)
        tasks_raw = await self._fetch_executables("/orchestration/executables/tasks")
        plans_raw = await self._fetch_executables("/orchestration/executables/plans")

        all_environments = [_to_environment(item, source_key, synced_at) for item in environments_raw]
        all_workspaces = [_to_workspace(item, source_key, synced_at) for item in workspaces_raw]
        all_engines = [_to_engine(item, source_key, synced_at) for item in engines_raw]
        all_artifacts = [_to_artifact(item, source_key, synced_at) for item in artifacts_raw]
        all_connections = [_to_connection(item, source_key, synced_at) for item in connections_raw]
        all_artifacts_by_id = {artifact.artifact_id: artifact for artifact in all_artifacts}
        all_connections_by_id = {
            connection.connection_id: connection for connection in all_connections
        }

        if self._environment_ids:
            known_environment_ids = {item.environment_id for item in all_environments}
            unknown = [
                environment_id
                for environment_id in self._environment_ids
                if environment_id not in known_environment_ids
            ]
            if unknown:
                # An environment deleted or moved out of the token's reach would
                # otherwise just shrink the batch, which reads downstream as
                # "those jobs stopped existing" rather than "the scope is stale".
                raise RuntimeProviderError(
                    f"{len(unknown)} selected Talend TMC environment(s) are no longer visible to "
                    "this token. Re-select the environments for this source."
                )
            if not tasks_raw:
                raise RuntimeProviderError(
                    "No Talend TMC tasks exist in the selected environments"
                )

        if self._task_scope_keywords:
            tasks_raw = [
                item
                for item in tasks_raw
                if _task_matches_scope(
                    item,
                    self._task_scope_keywords,
                    all_artifacts_by_id,
                    all_connections_by_id,
                )
            ]
            if not tasks_raw:
                raise RuntimeProviderError("No Talend TMC tasks matched the configured project scope")

        selected_artifact_ids = {
            artifact_id
            for item in tasks_raw
            if (artifact_id := _str_field(item, "artifactId", "artifact_id"))
        }
        selected_connection_ids = {
            connection_id for item in tasks_raw for connection_id in _connection_ids(item)
        }
        selected_workspace_ids = {
            workspace_id
            for item in tasks_raw
            if (workspace_id := _str_field(_dict_field(item, "workspace"), "id", "workspace_id"))
        }
        selected_engine_ids = {
            engine_id
            for item in tasks_raw
            if (engine_id := _str_field(_dict_field(item, "runtime"), "id", "engineId", "engine_id"))
        }

        # Environments are never narrowed. They are the markers the physical
        # identity is derived from (see physical_identity_from_batch), so
        # shipping a scoped subset would recompute a different identity on every
        # sync and let a re-registration of the same tenant slip past the
        # duplicate guard. They are also only a handful of rows: the estate has
        # these environments whether or not this source ingests jobs from them.
        environments = all_environments
        if self._task_scope_keywords:
            artifacts = [item for item in all_artifacts if item.artifact_id in selected_artifact_ids]
            connections = [item for item in all_connections if item.connection_id in selected_connection_ids]
            workspaces = [item for item in all_workspaces if item.workspace_id in selected_workspace_ids]
            engines = [item for item in all_engines if item.engine_id in selected_engine_ids]
        elif self._environment_ids:
            selected = set(self._environment_ids)
            workspaces = [
                item
                for item in all_workspaces
                if item.environment_id in selected or item.workspace_id in selected_workspace_ids
            ]
            # An engine referenced by a surviving task is kept even when it sits
            # outside the scope: dropping it would resolve that task's engine to
            # None and blank the engine name on every one of its runs.
            engines = [
                item
                for item in all_engines
                if item.environment_id in selected or item.engine_id in selected_engine_ids
            ]
            # Artifacts and connections carry a workspace but no environment, so
            # they follow the scoped workspaces plus anything a task references.
            scoped_workspace_ids = {item.workspace_id for item in workspaces}
            artifacts = [
                item
                for item in all_artifacts
                if item.workspace_id in scoped_workspace_ids
                or item.artifact_id in selected_artifact_ids
            ]
            connections = [
                item
                for item in all_connections
                if item.workspace_id in scoped_workspace_ids
                or item.connection_id in selected_connection_ids
            ]
        else:
            workspaces = all_workspaces
            engines = all_engines
            artifacts = all_artifacts
            connections = all_connections

        engines_by_id = {engine.engine_id: engine for engine in engines}
        workspaces_by_id = {workspace.workspace_id: workspace for workspace in workspaces}
        artifacts_by_id = {artifact.artifact_id: artifact for artifact in artifacts}
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

        run_fetch_semaphore = asyncio.Semaphore(_DETAIL_FETCH_CONCURRENCY)

        # Decides how far back each executable's history actually reaches: the
        # windows below are walked newest-first and abandoned once this budget is
        # spent, so a high-frequency task stops far short of history_days while a
        # low-frequency one covers all of it.
        runs_per_executable = (
            settings.talend_tmc_runs_per_executable_limit or _RUNS_PER_EXECUTABLE_LIMIT
        )

        async def fetch_execution_history(path: str) -> list[dict[str, Any]]:
            collected: list[dict[str, Any]] = []
            seen_execution_ids: set[str] = set()
            for window_start, window_end in _execution_windows(synced_at, self._history_days):
                remaining = runs_per_executable - len(collected)
                if remaining <= 0:
                    break
                page = await self._get_all_pages(
                    path,
                    params={
                        "from": int(window_start.timestamp() * 1000),
                        "to": int(window_end.timestamp() * 1000),
                    },
                    max_items=remaining,
                )
                for item in page:
                    execution_id = _str_field(item, "executionId", "execution_id", "id")
                    if execution_id is not None and execution_id in seen_execution_ids:
                        continue
                    if execution_id is not None:
                        seen_execution_ids.add(execution_id)
                    collected.append(item)
            return collected

        async def fetch_task_runs(job: RuntimeJob) -> list[dict[str, Any]]:
            async with run_fetch_semaphore:
                return await fetch_execution_history(
                    f"/processing/executables/tasks/{job.job_id}/executions"
                )

        task_run_pages = await asyncio.gather(*(fetch_task_runs(job) for job in jobs))
        task_runs_raw = [item for page in task_run_pages for item in page]

        if self._task_scope_keywords:
            selected_job_ids = set(jobs_by_id)
            plans_raw = [item for item in plans_raw if _plan_references_job(item, selected_job_ids)]
        plans = [_to_plan(item, source_key, synced_at, jobs_by_id, workspaces_by_id) for item in plans_raw]
        plans_by_id = {plan.plan_id: plan for plan in plans}

        async def fetch_plan_runs(plan: RuntimePlan) -> list[dict[str, Any]]:
            async with run_fetch_semaphore:
                return await fetch_execution_history(
                    f"/processing/executables/plans/{plan.plan_id}/executions"
                )

        plan_run_pages = await asyncio.gather(*(fetch_plan_runs(plan) for plan in plans))
        plan_runs_raw = [item for page in plan_run_pages for item in page]

        job_runs = [_to_job_run(item, source_key, jobs_by_id, engines_by_id) for item in task_runs_raw]
        plan_runs = [_to_plan_run(item, source_key, plans_by_id, task_runs_raw) for item in plan_runs_raw]
        executions = [_execution_from_job_run(run, project_id) for run in job_runs]
        failures = [
            RuntimeFailure(
                execution_id=run.run_id,
                job_name=run.job_name,
                failed_at=run.ended_at or run.started_at,
                error_summary=_failure_summary_for_task_run(raw, run),
                error_detail=_failure_detail_for_task_run(raw),
            )
            for raw, run in zip(task_runs_raw, job_runs, strict=False)
            if run.state == RuntimeExecutionState.FAILED
        ]
        runs_by_recency = sorted(job_runs, key=lambda item: item.started_at, reverse=True)
        # A ceiling of 0 means "no ceiling" — capture every failure in the window.
        log_ceiling = settings.talend_tmc_failed_log_run_limit or _FAILED_LOG_RUN_LIMIT
        metric_ceiling = (
            settings.talend_tmc_component_metric_run_limit or _COMPONENT_METRIC_RUN_LIMIT
        )
        all_failed_runs = [
            item for item in runs_by_recency if item.state == RuntimeExecutionState.FAILED
        ]
        failed_runs = all_failed_runs[:log_ceiling]
        if len(all_failed_runs) > len(failed_runs):
            # Silent truncation here reads downstream as "these runs had no logs",
            # which is indistinguishable from a genuinely log-free run.
            logger.warning(
                "TMC log capture ceiling reached: %d of %d failed runs will have logs "
                "downloaded this cycle (talend_tmc_failed_log_run_limit=%d)",
                len(failed_runs),
                len(all_failed_runs),
                log_ceiling,
            )
        metric_runs = runs_by_recency[:metric_ceiling]
        detail_fetch_semaphore = asyncio.Semaphore(_DETAIL_FETCH_CONCURRENCY)
        metric_failures = 0

        async def fetch_logs(run: RuntimeJobRun) -> list[RuntimeLogEvent]:
            try:
                async with detail_fetch_semaphore:
                    return await self.get_execution_logs(
                        GetExecutionLogsInput(execution_id=run.run_id, limit=_LOG_CAPTURE_LIMIT)
                    )
            except RuntimeProviderError:
                logger.warning("TMC log fetch failed for execution %s", run.run_id)
                return []

        async def fetch_metrics(run: RuntimeJobRun) -> list[RuntimeComponentMetric]:
            nonlocal metric_failures
            try:
                async with detail_fetch_semaphore:
                    return await self.get_component_metrics(run.run_id)
            except RuntimeProviderError:
                metric_failures += 1
                return []

        detail_results = await asyncio.gather(
            *(fetch_logs(run) for run in failed_runs),
            *(fetch_metrics(run) for run in metric_runs),
        )
        log_result_count = len(failed_runs)
        log_events = [item for result in detail_results[:log_result_count] for item in result]
        component_metrics = [
            item for result in detail_results[log_result_count:] for item in result
        ]
        if metric_failures:
            logger.warning(
                "TMC component metric fetch failed for %d of %d bounded executions; "
                "execution and log import continues",
                metric_failures,
                len(metric_runs),
            )
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
            response = await self._client().get(url, timeout=60)
            response.raise_for_status()
            data = response.json()
        except (httpx.HTTPError, ValueError) as e:
            raise RuntimeProviderError(f"Talend TMC log download failed: {type(e).__name__}") from e
        return _extract_items(data)

    async def _get_execution_logs_paginated(
        self, execution_id: str, max_entries: int
    ) -> list[dict[str, Any]]:
        """Fetch a bounded chronological head+tail window.

        The first page discovers TMC's total.  Short logs retain every line. A
        long log keeps up to 1,000 startup lines and fills the remaining budget
        from the terminal tail. This costs no more pages than the former
        first-10k loop and preserves the evidence closest to failure.
        """
        path = f"/monitoring/executions/{execution_id}/logs"

        async def page(start: int, count: int) -> tuple[list[dict[str, Any]], int]:
            payload = await self._get(
                path,
                params={
                    "count": min(_LOG_PAGE_SIZE, count),
                    "startIndex": start,
                    "order": "ASC",
                },
            )
            batch = _extract_items(payload)
            total = _page_total(payload, "totalSize", "total") or (start + len(batch))
            return batch, total

        first, total = await page(0, max_entries)
        if not first or len(first) >= max_entries:
            return first[:max_entries]

        if total <= max_entries:
            result = list(first)
            start = len(result)
            while start < total and len(result) < max_entries:
                batch, _ = await page(start, max_entries - len(result))
                if not batch:
                    break
                result.extend(batch)
                start += len(batch)
            return result[:max_entries]

        head_target = min(_LOG_HEAD_CAPTURE, max_entries)
        head = list(first)
        start = len(head)
        while start < head_target:
            batch, _ = await page(start, head_target - len(head))
            if not batch:
                break
            head.extend(batch)
            start += len(batch)
        head = head[:head_target]

        tail_target = max_entries - len(head)
        tail: list[dict[str, Any]] = []
        start = max(total - tail_target, len(head))
        while start < total and len(tail) < tail_target:
            batch, _ = await page(start, tail_target - len(tail))
            if not batch:
                break
            tail.extend(batch)
            start += len(batch)
        return (head + tail)[:max_entries]

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


def _task_scope_keywords(credentials: Mapping[str, Any], project_id: str) -> tuple[str, ...]:
    configured = str(credentials.get("task_name_filter") or "").strip()
    if configured:
        return tuple(dict.fromkeys(part.strip() for part in configured.split(",") if part.strip()))
    project_key = re.sub(r"[^a-z0-9]+", "", project_id.casefold())
    return _PROJECT_SCOPE_KEYWORDS.get(project_key, ())


def _environment_ids(credentials: Mapping[str, Any]) -> tuple[str, ...]:
    """Return the selected TMC environment ids, empty meaning the whole tenant.

    Sources registered before scope selection existed carry no value, and must
    keep ingesting everything rather than silently narrowing to nothing.
    """
    configured = credentials.get("environment_ids")
    if configured is None:
        return ()
    if isinstance(configured, str):
        parts: list[str] = [part.strip() for part in configured.split(",")]
    elif isinstance(configured, (list, tuple)):
        parts = [str(part).strip() for part in configured]
    else:
        raise RuntimeProviderError("TMC environment selection must be a list of environment ids")
    return tuple(dict.fromkeys(part for part in parts if part))


def _history_days(credentials: Mapping[str, Any]) -> int:
    configured = credentials.get("history_days")
    if configured is None or (isinstance(configured, str) and not configured.strip()):
        return _DEFAULT_HISTORY_DAYS
    value = _as_int(configured)
    if value is None or not 1 <= value <= 90:
        raise RuntimeProviderError("TMC run history must be a whole number from 1 to 90 days")
    return value


def _execution_windows(ending_at: datetime, history_days: int) -> list[tuple[datetime, datetime]]:
    windows: list[tuple[datetime, datetime]] = []
    window_end = ending_at
    days_remaining = history_days
    while days_remaining > 0:
        window_days = min(_EXECUTION_WINDOW_DAYS, days_remaining)
        window_start = window_end - timedelta(days=window_days)
        windows.append((window_start, window_end))
        window_end = window_start
        days_remaining -= window_days
    return windows


def _task_matches_scope(
    task: dict[str, Any],
    keywords: tuple[str, ...],
    artifacts_by_id: dict[str, RuntimeArtifact],
    connections_by_id: dict[str, RuntimeConnection],
) -> bool:
    artifact_id = _str_field(task, "artifactId", "artifact_id")
    artifact = artifacts_by_id.get(artifact_id or "")
    workspace = _dict_field(task, "workspace")
    connections = [
        connections_by_id[connection_id]
        for connection_id in _connection_ids(task)
        if connection_id in connections_by_id
    ]
    candidates = (
        _str_field(task, "name"),
        _str_field(task, "description"),
        _str_field(task, "artifactName"),
        artifact.name if artifact is not None else None,
        _str_field(workspace, "name", "workspace_name"),
        *(connection.name for connection in connections),
    )
    normalized_candidates = []
    for candidate in candidates:
        if not candidate:
            continue
        camel_split = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", candidate)
        normalized_candidates.append(
            f" {' '.join(re.findall(r'[a-z0-9]+', camel_split.casefold()))} "
        )
    for keyword in keywords:
        normalized_keyword = " ".join(re.findall(r"[a-z0-9]+", keyword.casefold()))
        if not normalized_keyword:
            continue
        if any(f" {normalized_keyword} " in candidate for candidate in normalized_candidates):
            return True
    return False


def _plan_references_job(plan: dict[str, Any], selected_job_ids: set[str]) -> bool:
    return any(
        step.job_id in selected_job_ids
        for step in _plan_steps(_dict_field(plan, "chart"), {})
        if step.job_id is not None
    )


def _extract_items(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        raw = data.get("items", data.get("data", data.get("logs", [])))
        return [item for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []
    return []


def _page_total(page: dict[str, Any] | list[dict[str, Any]], *keys: str) -> int | None:
    """Best-effort total-count extraction from a page.

    Bare-array TMC responses have no envelope, so callers fall back to
    ``len(items)`` when this returns ``None``.
    """
    if isinstance(page, dict):
        for key in keys:
            value = _as_int(page.get(key))
            if value is not None:
                return value
    return None


def _page_size_cache_key(path: str) -> str:
    """Collapse resource ids so a page-size cap is learned once per endpoint.

    ``/processing/executables/tasks/{id}/executions`` is one endpoint with one
    cap, but every task has its own path. Caching against the raw path made the
    discovery probe cost one wasted request per task per sync instead of one per
    endpoint. Any segment carrying a digit and long enough to be an identifier
    (TMC uses 24-char hex ids; executions use UUIDs) stands in as ``{id}``.
    """
    return "/".join(
        "{id}" if len(segment) >= 8 and any(char.isdigit() for char in segment) else segment
        for segment in path.split("/")
    )


def _is_pagination_rejected(err: RuntimeProviderError) -> bool:
    """True when TMC rejected the request specifically with HTTP 400.

    Other HTTP errors (auth, server faults) and transport failures are not
    treated as pagination rejection so they propagate unchanged. The original
    ``httpx.HTTPStatusError`` is preserved on ``__cause__`` by ``_get``.
    """
    cause = err.__cause__
    return isinstance(cause, httpx.HTTPStatusError) and cause.response.status_code == 400


def _sanitize_tmc_error(message: str, token: str | None = None) -> str:
    sanitized = message
    if token:
        sanitized = sanitized.replace(token, "[redacted]")
    sanitized = re.sub(r"Authorization:\s*Bearer\s+\S+", "Authorization: Bearer [redacted]", sanitized, flags=re.I)
    sanitized = re.sub(r"([?&](?:api[_-]?key|token|secret)=)[^&\s]+", r"\1[redacted]", sanitized, flags=re.I)
    return sanitized


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
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        # Monitoring logs use Unix epoch milliseconds, while other payloads
        # commonly use ISO-8601 strings. Accept seconds as well for defensive
        # compatibility with older endpoints.
        seconds = value / 1000 if abs(value) >= 100_000_000_000 else value
        try:
            return datetime.fromtimestamp(seconds, tz=UTC)
        except (OSError, OverflowError, ValueError) as e:
            raise RuntimeProviderError("Talend TMC payload has unparseable datetime") from e
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


def _bounded_log_head_tail(
    items: list[dict[str, Any]], limit: int
) -> list[dict[str, Any]]:
    """Keep lifecycle context and terminal evidence when a download is oversized."""
    if limit <= 0:
        return []
    if len(items) <= limit:
        return items
    head_size = min(_LOG_HEAD_CAPTURE, limit)
    tail_size = limit - head_size
    if tail_size <= 0:
        return items[:limit]
    return items[:head_size] + items[-tail_size:]


def _to_log_event(item: Any, fallback_execution_id: str, line_ordinal: int = 0) -> RuntimeLogEvent:
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
        line_ordinal=line_ordinal,
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


def _failure_detail_for_task_run(raw: dict[str, Any]) -> str | None:
    """Preserve the provider error payload; redaction happens at the sync boundary."""
    return _str_field(raw, "errorMessage", "error_message", "errorDetails", "error_details")


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
