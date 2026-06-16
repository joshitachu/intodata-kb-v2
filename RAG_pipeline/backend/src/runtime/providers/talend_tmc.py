"""Talend TMC runtime provider adapter (EP-004 slice 5).

Implements the generic RuntimeProvider protocol against the external Talend
Management Console (TMC) runtime service. Callers in runtime/client.py only ever
use the four generic tool names; the Talend-specific operation names live here.

WIRE FORMAT IS ASSUMED, NOT CONFIRMED.
The external TMC provider service contract (operation names, request/response
shapes, auth) was not available in this repository when this adapter was built.
The mapping and payload shapes below follow the Slice 5 plan's documented
assumptions and are exercised only against httpx.MockTransport. Real-data
reconciliation against the live TMC API is Slice 6. If the real protocol differs,
change only this file — the generic runtime contract must stay intact.

Generic tool -> assumed TMC operation:
  list_executions        -> get_task_executions
  get_execution_details  -> get_execution_details
  detect_failures        -> detect_execution_failures
  get_execution_logs     -> get_execution_logs

Security: the API token is read from a mounted secret file, cached in memory, and
sent as a Bearer header. It must never appear in logs or exception messages.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

from src.config import settings
from src.runtime.models import (
    DetectFailuresInput,
    GetExecutionDetailsInput,
    GetExecutionLogsInput,
    ListExecutionsInput,
    RuntimeExecution,
    RuntimeExecutionState,
    RuntimeFailure,
    RuntimeLogEvent,
    RuntimeLogSeverity,
)
from src.runtime.providers import RuntimeProviderContext, RuntimeProviderError

logger = logging.getLogger(__name__)

_OP_LIST_EXECUTIONS = "get_task_executions"
_OP_GET_EXECUTION_DETAILS = "get_execution_details"
_OP_DETECT_FAILURES = "detect_execution_failures"
_OP_GET_EXECUTION_LOGS = "get_execution_logs"

# Upstream status/severity tokens are matched case-insensitively against these.
_STATE_MAP: dict[str, RuntimeExecutionState] = {
    "success": RuntimeExecutionState.SUCCESS,
    "succeeded": RuntimeExecutionState.SUCCESS,
    "ok": RuntimeExecutionState.SUCCESS,
    "failed": RuntimeExecutionState.FAILED,
    "error": RuntimeExecutionState.FAILED,
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


def _normalize_state(raw: Any) -> RuntimeExecutionState:
    if not isinstance(raw, str):
        return RuntimeExecutionState.UNKNOWN
    return _STATE_MAP.get(raw.strip().lower(), RuntimeExecutionState.UNKNOWN)


def _normalize_severity(raw: Any) -> RuntimeLogSeverity:
    if not isinstance(raw, str):
        return RuntimeLogSeverity.INFO
    return _SEVERITY_MAP.get(raw.strip().lower(), RuntimeLogSeverity.INFO)


def _parse_required_dt(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise RuntimeProviderError(f"Missing or non-string datetime field: {field}")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as e:
        raise RuntimeProviderError(f"Unparseable datetime field: {field}") from e


def _parse_optional_dt(value: Any, field: str) -> datetime | None:
    if value is None:
        return None
    return _parse_required_dt(value, field)


def _require_str(obj: dict[str, Any], key: str) -> str:
    value = obj.get(key)
    if not isinstance(value, str) or not value:
        raise RuntimeProviderError(f"Missing or non-string field: {key}")
    return value


class TalendTmcRuntimeProvider:
    """HTTP adapter to the external Talend TMC runtime service.

    The transport is injectable so unit tests can drive it with
    httpx.MockTransport without real network access.
    """

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._transport = transport
        self._token: str | None = None

    # --- token + transport ---

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

    async def _post(self, operation: str, payload: dict[str, Any]) -> dict[str, Any]:
        base_url = self._base_url()
        token = self._read_token()
        body = {k: v for k, v in payload.items() if v is not None}
        try:
            async with httpx.AsyncClient(
                base_url=base_url,
                timeout=settings.talend_tmc_request_timeout,
                transport=self._transport,
            ) as client:
                response = await client.post(
                    f"/{operation}",
                    json=body,
                    headers={"Authorization": f"Bearer {token}"},
                )
                response.raise_for_status()
                data = response.json()
        except httpx.HTTPStatusError as e:
            # Never include the response body — it may carry sensitive payloads.
            raise RuntimeProviderError(
                f"Talend TMC operation {operation} returned HTTP {e.response.status_code}"
            ) from e
        except httpx.HTTPError as e:
            raise RuntimeProviderError(
                f"Talend TMC operation {operation} transport failure: {type(e).__name__}"
            ) from e

        logger.info("Talend TMC operation %s succeeded (status=%s)", operation, "2xx")
        if not isinstance(data, dict):
            raise RuntimeProviderError(f"Talend TMC operation {operation} returned a non-object payload")
        return data

    # --- generic provider methods ---

    async def list_executions(
        self, params: ListExecutionsInput, context: RuntimeProviderContext | None = None
    ) -> list[RuntimeExecution]:
        payload = {
            "project_name": params.project_name,
            "job_name": params.job_name,
            "since": params.since.isoformat() if params.since else None,
            "until": params.until.isoformat() if params.until else None,
            "limit": params.limit,
        }
        data = await self._post(_OP_LIST_EXECUTIONS, payload)
        return [_to_execution(item) for item in _require_list(data, "executions")]

    async def get_execution_details(
        self, params: GetExecutionDetailsInput, context: RuntimeProviderContext | None = None
    ) -> RuntimeExecution:
        data = await self._post(_OP_GET_EXECUTION_DETAILS, {"execution_id": params.execution_id})
        execution = data.get("execution")
        if not isinstance(execution, dict):
            raise RuntimeProviderError("Talend TMC execution details payload missing 'execution' object")
        return _to_execution(execution)

    async def detect_failures(
        self, params: DetectFailuresInput, context: RuntimeProviderContext | None = None
    ) -> list[RuntimeFailure]:
        payload = {
            "since": params.since.isoformat() if params.since else None,
            "until": params.until.isoformat() if params.until else None,
            "limit": params.limit,
        }
        data = await self._post(_OP_DETECT_FAILURES, payload)
        return [_to_failure(item) for item in _require_list(data, "failures")]

    async def get_execution_logs(
        self, params: GetExecutionLogsInput, context: RuntimeProviderContext | None = None
    ) -> list[RuntimeLogEvent]:
        payload = {
            "execution_id": params.execution_id,
            "limit": params.limit,
            "min_severity": params.min_severity.value if params.min_severity else None,
        }
        data = await self._post(_OP_GET_EXECUTION_LOGS, payload)
        return [_to_log_event(item) for item in _require_list(data, "logs")]


def _require_list(data: dict[str, Any], key: str) -> list[Any]:
    items = data.get(key)
    if not isinstance(items, list):
        raise RuntimeProviderError(f"Talend TMC payload missing list field: {key}")
    return items


def _to_execution(item: Any) -> RuntimeExecution:
    if not isinstance(item, dict):
        raise RuntimeProviderError("Talend TMC execution entry is not an object")
    duration = item.get("duration_ms")
    return RuntimeExecution(
        execution_id=_require_str(item, "id"),
        job_name=_require_str(item, "job_name"),
        project_name=item.get("project_name"),
        state=_normalize_state(item.get("status")),
        started_at=_parse_required_dt(item.get("start_time"), "start_time"),
        ended_at=_parse_optional_dt(item.get("end_time"), "end_time"),
        duration_ms=duration if isinstance(duration, int) else None,
    )


def _to_failure(item: Any) -> RuntimeFailure:
    if not isinstance(item, dict):
        raise RuntimeProviderError("Talend TMC failure entry is not an object")
    return RuntimeFailure(
        execution_id=_require_str(item, "execution_id"),
        job_name=_require_str(item, "job_name"),
        failed_at=_parse_required_dt(item.get("failed_at"), "failed_at"),
        error_summary=_require_str(item, "error"),
    )


def _to_log_event(item: Any) -> RuntimeLogEvent:
    if not isinstance(item, dict):
        raise RuntimeProviderError("Talend TMC log entry is not an object")
    return RuntimeLogEvent(
        execution_id=_require_str(item, "execution_id"),
        timestamp=_parse_required_dt(item.get("timestamp"), "timestamp"),
        severity=_normalize_severity(item.get("level")),
        message=_require_str(item, "message"),
    )


provider = TalendTmcRuntimeProvider()
