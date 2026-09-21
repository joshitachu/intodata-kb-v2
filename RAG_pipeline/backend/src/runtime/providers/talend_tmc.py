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
from typing import Any, Callable, Mapping

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
    RuntimeExecutableKind,
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
# A Route's deployment log is the shared OSGi Runtime log: engine-scoped,
# explicitly NOT attribution-safe, and enormous -- one verified route carried
# 376.820 lines. Pulling 10.000 of those per route, 200 to a page, timed out
# every single fetch and the mirror ended up with zero Route log lines at all.
# The tail is what an operator reads; the first 9.500 lines of a deployment
# that has been up for months are not evidence of anything.
# Measured on a live estate: the last `DS RUNNER METRIC` line sits between 1
# and 34 lines from the end of a deployment log. The counters are what this
# fetch is for, so a hundred lines of tail is generous. Five hundred bought
# nothing except bytes -- and bytes are what made the earlier fetch time out.
_DEPLOYMENT_LOG_CAPTURE_LIMIT = 100
# Counter recovery is cheap enough to do for EVERY deployment rather than the
# ten that the failed-job log ceiling allows. Sharing that ceiling is why 40 of
# 46 routes showed "Not supplied": not because TMC withheld anything, but
# because we never asked those forty.
_DEPLOYMENT_COUNTER_RUN_LIMIT = 100
# Log pages are bulk reads. The 10s that suits a list call is not enough for a
# page out of a multi-hundred-thousand-line log, and a timeout here reads
# downstream as "this run had no logs".
_LOG_REQUEST_TIMEOUT_SECONDS = 60.0
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
# TMC throttles. A 429 is a "come back later", not a failure of the sync, but it
# was raised straight through: one throttled request aborted the whole cycle,
# three cycles in a row auto-disabled the source, and the estate silently
# stopped being mirrored. Verified on a live tenant -- 1.67M records ingested
# over 103 cycles, then `GET .../executions returned HTTP 429` three times and
# the source switched itself off.
_RETRYABLE_STATUS: frozenset[int] = frozenset({429, 502, 503, 504})
# A throttle needs its OWN budget, larger than the transport one. A dropped
# connection is retried three times in under a second because it is a blip; a
# 429 is a quota, and a quota window outlives three back-to-back attempts.
# Measured on a live tenant: three attempts with 1s and 2s of backoff still
# came back 429, and the whole cycle failed.
_THROTTLE_MAX_ATTEMPTS = 6
_THROTTLE_BASE_DELAY_SECONDS = 1.0
_THROTTLE_MAX_DELAY_SECONDS = 30.0
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

# Status tokens that mean the same thing whatever else the payload says. Keys
# are lower-cased; both the `status` spelling (execution_successful) and its
# `executionStatus` twin (EXECUTION_SUCCESSFUL) fold onto the same key.
#
# Entries are never removed: a TMC deployment other than the one this was
# calibrated against may still send them, and an unmapped status degrades to
# UNKNOWN, which is exactly the data loss this map exists to prevent.
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
    "executing": RuntimeExecutionState.RUNNING,
    "in_progress": RuntimeExecutionState.RUNNING,
    "deployed": RuntimeExecutionState.RUNNING,
    "deploying": RuntimeExecutionState.RUNNING,
    "starting": RuntimeExecutionState.RUNNING,
    "starting_flow_execution": RuntimeExecutionState.RUNNING,
    "undeployed": RuntimeExecutionState.SUCCESS,
    "stopped": RuntimeExecutionState.SUCCESS,
    "terminated": RuntimeExecutionState.TERMINATED,
    "execution_terminated": RuntimeExecutionState.TERMINATED,
    "terminated_timeout": RuntimeExecutionState.TIMED_OUT,
    "execution_terminated_timeout": RuntimeExecutionState.TIMED_OUT,
    # Retained for other deployments; the live tenants send `deploy_failed`,
    # which is resolved from the payload by _STATE_RESOLVERS below.
    "deployment_failed": RuntimeExecutionState.FAILED,
}

# Field names that can carry a status, in the order they are consulted.
# `executionStatus` is the upper-case twin of `status`; `state` is a defensive
# spelling seen on older payload shapes.
_STATUS_FIELDS: tuple[str, ...] = ("status", "executionStatus", "execution_status", "state")

_MISFIRE_REASON_FIELDS: tuple[str, ...] = (
    "misfiredExecutionReason",
    "misfired_execution_reason",
)

_EXECUTION_TYPE_FIELDS: tuple[str, ...] = ("executionType", "execution_type")


def _deploy_failed_state(item: dict[str, Any]) -> RuntimeExecutionState:
    """`deploy_failed` covers two different events, told apart by the payload.

    TMC reports a scheduled run that never started (the previous run was still
    holding the executable, the task was paused, deployment attempts were
    exhausted) with the same status as a deployment that genuinely broke. The
    misfire reason is the discriminator, and it is only present on the former --
    so its absence must fall back to FAILED rather than be assumed.
    """
    return (
        RuntimeExecutionState.MISFIRED
        if _misfire_reason(item)
        else RuntimeExecutionState.FAILED
    )


# Statuses whose meaning depends on the rest of the payload. Kept separate from
# _STATE_MAP so the common case stays a dict lookup and the exceptions have to
# justify themselves in one place.
_STATE_RESOLVERS: dict[str, Callable[[dict[str, Any]], RuntimeExecutionState]] = {
    "deploy_failed": _deploy_failed_state,
}

# One warning per distinct unrecognised status per process. A sync walks tens of
# thousands of executions and a novel status is systemic, not incidental: an
# un-deduplicated warning would emit thousands of identical lines and get muted,
# which is how the previous silent-UNKNOWN mapping survived unnoticed.
_UNKNOWN_STATE_WARN_LIMIT = 50
_unknown_states_warned: set[str] = set()

# Outcomes that owe the operator an explanation, and therefore get a failure
# record. Not just FAILED, and not just Jobs: a Route that fails carries the
# same errorMessage a Job does, and a misfire is the one outcome where TMC's
# explanation is the ONLY evidence that will ever exist -- nothing ran, so no
# logs will be fetched for it.
_DIAGNOSABLE_STATES: frozenset[RuntimeExecutionState] = frozenset(
    {
        RuntimeExecutionState.FAILED,
        RuntimeExecutionState.MISFIRED,
        RuntimeExecutionState.TIMED_OUT,
        RuntimeExecutionState.TERMINATED,
    }
)

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
        # Learned once per sync: whether this tenant serves the bulk log export.
        self._async_logs_unsupported = False
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

    async def _get(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any] | list[dict[str, Any]]:
        throttled = 0
        for attempt in range(_TRANSPORT_MAX_ATTEMPTS + _THROTTLE_MAX_ATTEMPTS):
            try:
                client = self._client()
                response = await client.get(
                    path,
                    params={k: v for k, v in (params or {}).items() if v is not None},
                    **({"timeout": timeout} if timeout is not None else {}),
                )
                response.raise_for_status()
                data = response.json()
                break
            except httpx.HTTPStatusError as e:
                if e.response.status_code in _RETRYABLE_STATUS:
                    throttled += 1
                    if throttled < _THROTTLE_MAX_ATTEMPTS:
                        await asyncio.sleep(_throttle_delay(e.response, throttled - 1))
                        continue
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
        throttled = 0
        for attempt in range(_TRANSPORT_MAX_ATTEMPTS + _THROTTLE_MAX_ATTEMPTS):
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
                if e.response.status_code in _RETRYABLE_STATUS:
                    throttled += 1
                    if throttled < _THROTTLE_MAX_ATTEMPTS:
                        await asyncio.sleep(_throttle_delay(e.response, throttled - 1))
                        continue
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
        if params.state is not None:
            # `_tmc_status` only knows a server-side value for the states TMC
            # accepts as a query filter; for the rest it sends none. Without
            # this check, asking for MISFIRED would return every execution and
            # look like a filter that matched everything.
            executions = [item for item in executions if item.state == params.state]
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
        self,
        params: GetExecutionLogsInput,
        context: RuntimeProviderContext | None = None,
        head_capture: int = _LOG_HEAD_CAPTURE,
    ) -> list[RuntimeLogEvent]:
        logs = await self._get_execution_logs_async(params.execution_id)
        if logs is None:
            logs = await self._get_execution_logs_paginated(
                params.execution_id, params.limit, head_capture
            )
        events = [
            _to_log_event(item, params.execution_id, ordinal)
            for ordinal, item in _bounded_log_entries(logs, params.limit, head_capture)
        ]
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

        task_detail_semaphore = asyncio.Semaphore(_DETAIL_FETCH_CONCURRENCY)

        async def enrich_deployable_task(item: dict[str, Any]) -> dict[str, Any]:
            # EVERY executable kind, not just Routes. The task detail is the only
            # place TMC exposes `artifact.version`, and that version plus its
            # build stamp is what answers "since when has this been running
            # unchanged" -- up to 888 days on a verified estate. Narrowing this
            # to Routes left every Job with an empty version column, which then
            # read as "TMC does not expose it". Verified against a live tenant:
            # a standard Job's task detail carries a populated artifact.version.
            artifact_id = _str_field(item, "artifactId", "artifact_id")
            artifact = all_artifacts_by_id.get(artifact_id or "")
            if artifact is None:
                return item
            task_id = _str_field(item, "executable", "id")
            if task_id is None:
                return item
            try:
                async with task_detail_semaphore:
                    detail = await self._get(f"/orchestration/executables/tasks/{task_id}")
            except RuntimeProviderError:
                logger.warning("TMC task detail fetch failed for deployment %s", task_id)
                return item
            enriched = dict(item)
            enriched["taskDetail"] = detail
            return enriched

        tasks_raw = list(await asyncio.gather(*(enrich_deployable_task(item) for item in tasks_raw)))

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

        jobs = [
            _to_job(item, source_key, synced_at, engines_by_id, artifacts_by_id)
            for item in tasks_raw
        ]
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
                path = f"/processing/executables/tasks/{job.job_id}/executions"
                current: list[dict[str, Any]] = []
                if job.executable_kind in {
                    RuntimeExecutableKind.ROUTE,
                    RuntimeExecutableKind.DATA_SERVICE,
                }:
                    # Routes can stay deployed longer than the configured
                    # history horizon. A latest-page probe without a date
                    # window keeps that current deployment visible even when
                    # its start timestamp is months old.
                    try:
                        current = await self._get_all_pages(path, max_items=25)
                    except RuntimeProviderError:
                        logger.warning(
                            "TMC current deployment probe failed for executable %s; using bounded history",
                            job.job_id,
                        )
                history = await fetch_execution_history(path)
                merged: dict[str, dict[str, Any]] = {}
                for index, item in enumerate(current + history):
                    execution_id = _str_field(item, "executionId", "execution_id", "id")
                    merged.setdefault(execution_id or f"anonymous:{index}", item)
                return list(merged.values())

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
            if run.state in _DIAGNOSABLE_STATES
        ]
        runs_by_recency = sorted(job_runs, key=lambda item: item.started_at, reverse=True)
        # A ceiling of 0 means "no ceiling" — capture every failure in the window.
        log_ceiling = settings.talend_tmc_failed_log_run_limit or _FAILED_LOG_RUN_LIMIT
        metric_ceiling = (
            settings.talend_tmc_component_metric_run_limit or _COMPONENT_METRIC_RUN_LIMIT
        )
        all_failed_job_runs = [
            item
            for item in runs_by_recency
            if item.state == RuntimeExecutionState.FAILED
            and item.executable_kind == RuntimeExecutableKind.JOB
        ]
        failed_job_runs = all_failed_job_runs[:log_ceiling]
        if len(all_failed_job_runs) > len(failed_job_runs):
            # Silent truncation here reads downstream as "these runs had no logs",
            # which is indistinguishable from a genuinely log-free run.
            logger.warning(
                "TMC log capture ceiling reached: %d of %d failed runs will have logs "
                "downloaded this cycle (talend_tmc_failed_log_run_limit=%d)",
                len(failed_job_runs),
                len(all_failed_job_runs),
                log_ceiling,
            )
        # A Route/Data Service execution represents a deployment lifecycle, not
        # a batch failure. Capture the latest deployment's shared OSGi log even
        # while it is running so operators can actually inspect route activity.
        latest_deployment_runs: dict[tuple[str, str], RuntimeJobRun] = {}
        for run in runs_by_recency:
            if run.executable_kind in {
                RuntimeExecutableKind.ROUTE,
                RuntimeExecutableKind.DATA_SERVICE,
            }:
                latest_deployment_runs.setdefault((run.source_key, run.job_id), run)
        deployment_ceiling = (
            settings.talend_tmc_deployment_counter_run_limit
            or _DEPLOYMENT_COUNTER_RUN_LIMIT
        )
        deployment_log_runs = list(latest_deployment_runs.values())[:deployment_ceiling]
        if len(latest_deployment_runs) > len(deployment_log_runs):
            logger.warning(
                "TMC deployment log capture ceiling reached: %d of %d latest deployments "
                "will have logs downloaded this cycle",
                len(deployment_log_runs),
                len(latest_deployment_runs),
            )
        log_runs = failed_job_runs + deployment_log_runs
        # TMC component observability is a Data Integration Job API. Route
        # exchange metrics are carried on deployment/run payloads instead.
        metric_runs = [
            run for run in runs_by_recency if run.executable_kind == RuntimeExecutableKind.JOB
        ][:metric_ceiling]
        detail_fetch_semaphore = asyncio.Semaphore(_DETAIL_FETCH_CONCURRENCY)
        metric_failures = 0

        async def fetch_logs(run: RuntimeJobRun) -> list[RuntimeLogEvent]:
            try:
                deployment = run.executable_kind in {
                    RuntimeExecutableKind.ROUTE,
                    RuntimeExecutableKind.DATA_SERVICE,
                }
                async with detail_fetch_semaphore:
                    events = await self.get_execution_logs(
                        GetExecutionLogsInput(
                            execution_id=run.run_id,
                            limit=(
                                _DEPLOYMENT_LOG_CAPTURE_LIMIT
                                if deployment
                                else _LOG_CAPTURE_LIMIT
                            ),
                        ),
                        # A deployment log opens on the day the Route went live.
                        # Only its tail describes what the route is doing now.
                        head_capture=0 if deployment else _LOG_HEAD_CAPTURE,
                    )
                    if run.executable_kind in {
                        RuntimeExecutableKind.ROUTE,
                        RuntimeExecutableKind.DATA_SERVICE,
                    }:
                        # TMC exports the shared OSGi Runtime log during the
                        # deployment window; sibling Route lines may be present.
                        return [
                            event.model_copy(
                                update={
                                    "execution_id": run.run_id,
                                    "scope": "engine",
                                    "attribution_safe": False,
                                }
                            )
                            for event in events
                        ]
                    return events
            except RuntimeProviderError as exc:
                # The REASON matters. "log fetch failed" with no cause is
                # indistinguishable from a genuinely log-free run, and that is
                # exactly how 376.820 available Route log lines went unnoticed.
                logger.warning(
                    "TMC log fetch failed for %s execution %s: %s",
                    run.executable_kind.value if run.executable_kind else "unknown",
                    run.run_id,
                    exc,
                )
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
            *(fetch_logs(run) for run in log_runs),
            *(fetch_metrics(run) for run in metric_runs),
        )
        log_result_count = len(log_runs)
        log_events = [item for result in detail_results[:log_result_count] for item in result]
        # The exchange counters live in the log text, not in the API payload.
        # Read them back onto the runs they describe before the batch is built.
        gevuld = _apply_route_metrics(job_runs, log_events)
        if gevuld:
            logger.info(
                "TMC route throughput recovered from the engine log for %d deployments", gevuld
            )
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
        """The bulk-export path: request an export, poll, download when READY.

        Not every tenant has it. On one verified estate `/logs/status` answers
        404 for every execution, Job and Route alike, so this path could never
        succeed -- yet it still cost a POST plus three status polls on EVERY log
        fetch before falling back. `_async_logs_unsupported` remembers that
        answer for the rest of the sync instead of re-learning it per execution.
        """
        if self._async_logs_unsupported:
            return None
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
                try:
                    status = await self._post(
                        f"/monitoring/executions/{execution_id}/logs/status",
                        json_body=body,
                    )
                except RuntimeProviderError as exc:
                    # A 404 is the tenant saying this endpoint does not exist.
                    # Polling it twice more, and again for the next execution,
                    # is pure waste against an API that already throttles us.
                    if "HTTP 404" in str(exc):
                        self._async_logs_unsupported = True
                        logger.info(
                            "TMC bulk log export is unavailable on this tenant; "
                            "using the paginated log path for the rest of this sync"
                        )
                    return None
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
        self, execution_id: str, max_entries: int, head_capture: int = _LOG_HEAD_CAPTURE
    ) -> list[dict[str, Any]]:
        """Fetch a bounded chronological head+tail window.

        The first page discovers TMC's total. Short logs retain every line. A
        long log keeps up to `head_capture` startup lines and fills the
        remaining budget from the terminal tail.

        `head_capture=0` means pure tail, and that is what a DEPLOYMENT log
        needs. Startup context is the evidence closest to a batch failure, but
        a Route that has been up for 83 days starts its log on the day it was
        deployed: its head is three months of nothing. Verified -- the first
        capture of this estate returned lines from 20 August for deployments
        that were still running in September.
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
                timeout=_LOG_REQUEST_TIMEOUT_SECONDS,
            )
            batch = _extract_items(payload)
            total = _page_total(payload, "totalSize", "total") or (start + len(batch))
            return batch, total

        if head_capture <= 0:
            # A pure-tail request cannot use the head-first loop below: the
            # early return there hands back the FIRST page, which for a
            # deployment log is the day the route went live. Probe for the
            # total, then read one window at the end.
            _probe, total = await page(0, 1)
            if total <= max_entries:
                staart, _ = await page(0, max_entries)
                return staart[:max_entries]
            staart, _ = await page(max(total - max_entries, 0), max_entries)
            return staart[-max_entries:]

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

        head_target = min(head_capture, max_entries)
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


def _status_token(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    return value.strip().lower() or None


def _normalize_state(item: dict[str, Any]) -> RuntimeExecutionState:
    """Map a TMC execution payload onto the generic outcome vocabulary.

    Takes the whole payload, not one string, for two reasons:

    - `deploy_failed` cannot be resolved without `misfiredExecutionReason`.
    - The status fields must be tried until one MAPS, not until one is PRESENT.
      `status` is on 100% of executions, so a first-present rule made every
      other field unreachable -- `starting_flow_execution` sat in the map as
      dead code while its executions were stored as `unknown`.

    An unrecognised status is warned about once per process and stored as
    UNKNOWN; the verbatim string is kept on the run (see `_raw_state`) so the
    outcome is recoverable without re-fetching from TMC.
    """
    tokens: list[str] = []
    for name in _STATUS_FIELDS:
        token = _status_token(item.get(name))
        if token is None or token in tokens:
            continue
        tokens.append(token)
        resolver = _STATE_RESOLVERS.get(token)
        if resolver is not None:
            return resolver(item)
        state = _STATE_MAP.get(token)
        if state is not None:
            return state
    if tokens:
        _warn_unknown_state(tokens)
    return RuntimeExecutionState.UNKNOWN


def _warn_unknown_state(tokens: list[str]) -> None:
    key = "|".join(tokens)
    if key in _unknown_states_warned or len(_unknown_states_warned) >= _UNKNOWN_STATE_WARN_LIMIT:
        return
    _unknown_states_warned.add(key)
    logger.warning(
        "Talend TMC returned an execution status this adapter does not map: %s. "
        "Those executions are stored with state=unknown; add the status to "
        "_STATE_MAP in talend_tmc.py to count them correctly.",
        ", ".join(repr(token) for token in tokens),
    )


def _raw_state(item: dict[str, Any]) -> str | None:
    """The provider's status string, verbatim -- not lower-cased, not mapped."""
    return _str_field(item, *_STATUS_FIELDS)


def _misfire_reason(item: dict[str, Any]) -> str | None:
    return _str_field(item, *_MISFIRE_REASON_FIELDS)


def _execution_type(item: dict[str, Any]) -> str | None:
    return _str_field(item, *_EXECUTION_TYPE_FIELDS)


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
        state=_normalize_state(item),
        raw_state=_raw_state(item),
        misfire_reason=_misfire_reason(item),
        execution_type=_execution_type(item),
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


# The OSGi Runtime log carries a periodic metric line per Camel route:
#   DS RUNNER METRIC - ROUTE: [RouteName:x] [RouteExchangesCompleted:n]
#   [RouteExchangesFailed:n][RouteExchangesTotal:n][RouteLastProcessingTime:n]
#   [RouteMeanProcessingTime:n]
# This is the message throughput TMC does NOT put in its API response -- every
# exchange column on a Route run is null, and that emptiness has been read as
# "Talend does not measure Route throughput". It measures it; it prints it.
# Verified on a live estate: 1.885 of 3.000 captured Route log lines are these,
# and they show one route failing 11 of 83 exchanges while the API reports
# nothing at all.
# A Camel trace line carries the whole message. Verified on a live estate: the
# captured Route logs held 109 XML order messages, 62 complete EDIFACT
# invoices, 115 SOAP payloads and 53 JSON bodies, lines up to 10.011 characters
# -- and 90 lines with an IBAN, all in the outbound invoice route. Mirroring
# those verbatim turns an operational log store into a copy of the traffic.
#
# The ENVELOPE is what an operator reads: which pattern, which body type, how
# large. Keeping that and dropping the content loses no diagnostic signal and
# no longer copies customer payments into our database.
_EXCHANGE_TRACE = re.compile(
    r"^(?P<kop>Exchange\[ExchangePattern:\s*(?P<patroon>\w+),\s*"
    r"BodyType:\s*(?P<soort>[^,]+),\s*Body:\s*)(?P<body>.*)$",
    re.S,
)
_EDIFACT_LINE = re.compile(r"^\s*UN[BH]\+")


def _strip_message_body(message: str) -> str:
    """The trace envelope, without the payload it was carrying."""
    text = str(message or "")
    treffer = _EXCHANGE_TRACE.match(text)
    if treffer:
        body = treffer.group("body").rstrip("]").strip()
        return (
            f'Exchange[ExchangePattern: {treffer.group("patroon")}, '
            f'BodyType: {treffer.group("soort").strip()}, '
            f"Body: {len(body)} tekens, inhoud niet bewaard]"
        )
    if _EDIFACT_LINE.match(text):
        segmenten = [deel.split("+", 1)[0] for deel in text.split("'") if deel.strip()]
        uniek = list(dict.fromkeys(seg.strip() for seg in segmenten if seg.strip()))[:8]
        return (
            f"EDIFACT-bericht, {len(text)} tekens, segmenten "
            + ", ".join(uniek)
            + " -- inhoud niet bewaard"
        )
    return text


_ROUTE_METRIC_LINE = re.compile(r"DS RUNNER METRIC\s*-\s*ROUTE:", re.I)
_ROUTE_METRIC_FIELD = re.compile(r"\[\s*([A-Za-z]+)\s*:\s*([^\]]*?)\s*\]")


def _route_metrics_from_log(message: str) -> tuple[str, dict[str, int]] | None:
    """(route name, counters) from one metric line, or None.

    The route name in the line is what the counters describe. That is NOT
    necessarily the deployment whose log we downloaded: the export is the
    shared engine log, so a line for a sibling route is normal and attributing
    it to the downloaded deployment would be wrong.
    """
    text = str(message or "")
    if not _ROUTE_METRIC_LINE.search(text):
        return None
    velden = dict(_ROUTE_METRIC_FIELD.findall(text))
    naam = str(velden.pop("RouteName", "")).strip()
    if not naam:
        return None
    tellers: dict[str, int] = {}
    for sleutel, waarde in velden.items():
        try:
            tellers[sleutel] = int(float(waarde))
        except (TypeError, ValueError):
            continue
    return (naam, tellers) if tellers else None


def _apply_route_metrics(
    runs: list[RuntimeJobRun], events: list[RuntimeLogEvent]
) -> int:
    """Fill the empty exchange columns on Route runs from their own log lines.

    Only the LATEST line per route is used: the counters are cumulative for the
    life of the deployment, so an earlier line is a strictly worse answer to
    the same question. Matching is on route name against `job_name`; a name we
    do not recognise is skipped rather than guessed at.
    """
    laatste: dict[str, dict[str, int]] = {}
    for event in sorted(events, key=lambda item: (item.timestamp, item.line_ordinal)):
        gelezen = _route_metrics_from_log(event.message)
        if gelezen:
            laatste[gelezen[0]] = gelezen[1]
    if not laatste:
        return 0
    # The counters are cumulative for the RUNNING deployment, so they belong to
    # the newest run of that route and to no other. Writing them onto every run
    # of the same route would hand a deployment that ended in July the totals of
    # the one running today.
    nieuwste: dict[str, RuntimeJobRun] = {}
    for run in runs:
        if run.executable_kind not in {
            RuntimeExecutableKind.ROUTE,
            RuntimeExecutableKind.DATA_SERVICE,
        }:
            continue
        naam = run.job_name or ""
        huidig = nieuwste.get(naam)
        if huidig is None or run.started_at > huidig.started_at:
            nieuwste[naam] = run

    gevuld = 0
    for naam, run in nieuwste.items():
        tellers = laatste.get(naam)
        if not tellers:
            continue
        run.completed_exchanges = tellers.get("RouteExchangesCompleted")
        run.failed_exchanges = tellers.get("RouteExchangesFailed")
        run.total_exchanges = tellers.get("RouteExchangesTotal")
        run.last_process_time_ms = tellers.get("RouteLastProcessingTime")
        run.mean_process_time_ms = tellers.get("RouteMeanProcessingTime")
        gevuld += 1
    return gevuld


def _throttle_delay(response: httpx.Response, attempt: int) -> float:
    """How long to wait before retrying a throttled request.

    `Retry-After` is the server telling us exactly when it will serve us again;
    honour it. Without it, back off exponentially. Capped, because a sync that
    sleeps for minutes is its own kind of failure.
    """
    header = response.headers.get("Retry-After", "").strip()
    if header:
        try:
            return min(float(header), _THROTTLE_MAX_DELAY_SECONDS)
        except ValueError:
            pass
    return min(_THROTTLE_BASE_DELAY_SECONDS * (2**attempt), _THROTTLE_MAX_DELAY_SECONDS)


def _executable_kind(artifact_type: str | None) -> RuntimeExecutableKind:
    token = re.sub(r"[\s-]+", "_", (artifact_type or "").strip().lower())
    if token in {"standard", "job", "batch", "data_integration"}:
        return RuntimeExecutableKind.JOB
    if token == "route":
        return RuntimeExecutableKind.ROUTE
    if token in {"data_service", "dataservice"}:
        return RuntimeExecutableKind.DATA_SERVICE
    # A TMC executable task with missing/novel artifact metadata retains the
    # historical batch-job semantics. Only explicit Route/Data Service types
    # switch to deployment semantics.
    return RuntimeExecutableKind.JOB


def _route_metric_objects(item: dict[str, Any]) -> list[dict[str, Any]]:
    objects = [item]
    for name in ("metrics", "routeMetrics", "camelMetrics", "deploymentMetrics", "deployment"):
        value = item.get(name)
        if isinstance(value, dict):
            objects.append(value)
    return objects


def _route_metric_int(item: dict[str, Any], *names: str) -> int | None:
    for candidate in _route_metric_objects(item):
        value = _field(candidate, *names)
        parsed = _as_int(value)
        if parsed is not None:
            return parsed
    return None


def _route_metric_float(item: dict[str, Any], *names: str) -> float | None:
    for candidate in _route_metric_objects(item):
        value = _field(candidate, *names)
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)) and math.isfinite(float(value)):
            return float(value)
        if isinstance(value, str):
            try:
                parsed = float(value)
            except ValueError:
                continue
            if math.isfinite(parsed):
                return parsed
    return None


def _route_metric_datetime(item: dict[str, Any]) -> datetime | None:
    for candidate in _route_metric_objects(item):
        value = _field(
            candidate,
            "metricsUpdatedAt",
            "metricTimestamp",
            "metrics_updated_at",
            "lastUpdated",
        )
        parsed = _parse_dt(value)
        if parsed is not None:
            return parsed
    return None


def _to_job(
    item: dict[str, Any],
    source_key: str,
    synced_at: datetime,
    engines_by_id: dict[str, RuntimeEngine],
    artifacts_by_id: dict[str, RuntimeArtifact],
) -> RuntimeJob:
    workspace = _dict_field(item, "workspace")
    environment = _dict_field(workspace, "environment")
    runtime = _dict_field(item, "runtime")
    engine_id = _str_field(runtime, "id", "engineId", "engine_id")
    engine = engines_by_id.get(engine_id or "")
    job_id = _required_str(item, "executable", "task_id", "id")
    name = _str_field(item, "name") or job_id
    artifact_id = _str_field(item, "artifactId", "artifact_id")
    artifact = artifacts_by_id.get(artifact_id or "")
    task_detail = _dict_field(item, "taskDetail")
    parameters = _dict_field(task_detail, "parameters")
    detail_artifact = _dict_field(task_detail, "artifact")
    executable_kind = _executable_kind(artifact.artifact_type if artifact else None)
    return RuntimeJob(
        source_key=source_key,
        job_id=job_id,
        name=name,
        display_name=name,
        job_type="task",
        executable_kind=executable_kind,
        status=_str_field(item, "status") or "active",
        environment_id=_str_field(environment, "id", "environment_id"),
        environment_name=_str_field(environment, "name", "environment_name"),
        workspace_id=_str_field(workspace, "id", "workspace_id"),
        workspace_name=_str_field(workspace, "name", "workspace_name"),
        engine_id=engine_id,
        engine_name=engine.name if engine is not None else None,
        owner=_str_field(workspace, "owner"),
        artifact_name=artifact.name if artifact is not None else artifact_id,
        artifact_id=artifact_id,
        artifact_version=(
            _str_field(detail_artifact, "version")
            or (artifact.version if artifact is not None else None)
        ),
        metric_path=_str_field(parameters, "metric_path", "metricPath"),
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
        state=_normalize_state(item),
        raw_state=_raw_state(item),
        misfire_reason=_misfire_reason(item),
        execution_type=_execution_type(item),
        executable_kind=(
            job.executable_kind if job is not None else RuntimeExecutableKind.UNKNOWN
        ),
        environment_id=job.environment_id if job else None,
        environment_name=job.environment_name if job else None,
        workspace_id=job.workspace_id if job else None,
        workspace_name=job.workspace_name if job else None,
        engine_id=engine_id,
        engine_name=engine.name if engine is not None else (job.engine_name if job else None),
        started_at=started,
        ended_at=ended,
        duration_ms=_duration_ms(started, ended),
        completed_exchanges=_route_metric_int(
            item, "completedExchanges", "completed_exchanges", "exchangesCompleted"
        ),
        failed_exchanges=_route_metric_int(
            item, "failedExchanges", "failed_exchanges", "exchangesFailed"
        ),
        total_exchanges=_route_metric_int(
            item, "totalExchanges", "total_exchanges", "exchangesTotal"
        ),
        last_process_time_ms=_route_metric_int(
            item, "lastProcessTime", "lastProcessTimeMs", "last_process_time_ms"
        ),
        mean_process_time_ms=_route_metric_int(
            item, "meanProcessTime", "meanProcessTimeMs", "mean_process_time_ms"
        ),
        metrics_updated_at=_route_metric_datetime(item),
        metric_path=job.metric_path if job is not None else None,
        service_call_count=_route_metric_int(item, "Count", "count", "serviceCallCount"),
        one_minute_rate=_route_metric_float(item, "OneMinuteRate", "oneMinuteRate"),
        five_minute_rate=_route_metric_float(item, "FiveMinuteRate", "fiveMinuteRate"),
        fifteen_minute_rate=_route_metric_float(item, "FifteenMinuteRate", "fifteenMinuteRate"),
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
        state=_normalize_state(item),
        raw_state=_raw_state(item),
        misfire_reason=_misfire_reason(item),
        execution_type=_execution_type(item),
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
        raw_state=run.raw_state,
        misfire_reason=run.misfire_reason,
        execution_type=run.execution_type,
        executable_kind=run.executable_kind,
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


def _bounded_log_entries(
    items: list[dict[str, Any]], limit: int, head_capture: int = _LOG_HEAD_CAPTURE
) -> list[tuple[int, dict[str, Any]]]:
    """Bound logs while retaining source positions for idempotent re-imports.

    With `head_capture` at or above `limit` this returns the HEAD, which is
    right for a failed batch run and wrong for a long-lived deployment. Pass 0
    to keep the tail instead.
    """
    if limit <= 0:
        return []
    indexed = list(enumerate(items))
    if len(indexed) <= limit:
        return indexed
    head_size = min(head_capture, limit)
    if head_size <= 0:
        return indexed[-limit:]
    tail_size = limit - head_size
    if tail_size <= 0:
        return indexed[:limit]
    return indexed[:head_size] + indexed[-tail_size:]


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
        message=_strip_message_body(
            _required_str(item, "logMessage", "message", "text")
        ),
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
    return _synthesized_failure_summary(run)[:300]


def _synthesized_failure_summary(run: RuntimeJobRun) -> str:
    """Describe a non-success outcome TMC attached no error message to.

    Each state gets its own sentence because they are not the same event: "did
    not start" and "failed" send an operator to different places, and a summary
    that says "failed" for a run that never ran is a false diagnosis.
    """
    if run.state == RuntimeExecutionState.MISFIRED:
        return (
            f"Run did not start: {run.misfire_reason}"
            if run.misfire_reason
            else f"{run.job_name} did not start in Talend TMC"
        )
    if run.state == RuntimeExecutionState.TIMED_OUT:
        return f"{run.job_name} was stopped by its configured timeout"
    if run.state == RuntimeExecutionState.TERMINATED:
        return f"{run.job_name} was terminated before completing"
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
                state=_normalize_state(item),
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
    """Server-side narrowing hint only; `list_executions` still filters locally.

    Returning None means "let TMC send everything" -- which is correct for the
    states whose accepted query spelling is unverified, because a wrong value
    silently narrows the result to nothing.
    """
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
