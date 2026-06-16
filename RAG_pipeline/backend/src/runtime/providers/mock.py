"""Deterministic, credential-free mock runtime provider (EP-004 slice 2).

Enabled via runtime_provider_key="mock". Exists only for tests and local
validation — never reads env secrets or calls the network.
"""

from __future__ import annotations

from datetime import UTC, datetime

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
from src.runtime.providers import RuntimeProviderError

_FIXED_NOW = datetime(2026, 1, 1, tzinfo=UTC)

_EXECUTIONS: list[RuntimeExecution] = [
    RuntimeExecution(
        execution_id="mock-exec-1",
        job_name="nightly_sync",
        project_name="demo_project",
        state=RuntimeExecutionState.SUCCESS,
        started_at=_FIXED_NOW,
        ended_at=_FIXED_NOW,
        duration_ms=4200,
    ),
    RuntimeExecution(
        execution_id="mock-exec-2",
        job_name="nightly_sync",
        project_name="demo_project",
        state=RuntimeExecutionState.FAILED,
        started_at=_FIXED_NOW,
        ended_at=_FIXED_NOW,
        duration_ms=1800,
    ),
]

_FAILURES: list[RuntimeFailure] = [
    RuntimeFailure(
        execution_id="mock-exec-2",
        job_name="nightly_sync",
        failed_at=_FIXED_NOW,
        error_summary="Connection to source database timed out",
    ),
]

_LOGS: dict[str, list[RuntimeLogEvent]] = {
    "mock-exec-1": [
        RuntimeLogEvent(
            execution_id="mock-exec-1",
            timestamp=_FIXED_NOW,
            severity=RuntimeLogSeverity.INFO,
            message="Job started",
        ),
        RuntimeLogEvent(
            execution_id="mock-exec-1",
            timestamp=_FIXED_NOW,
            severity=RuntimeLogSeverity.INFO,
            message="Job completed successfully",
        ),
    ],
    "mock-exec-2": [
        RuntimeLogEvent(
            execution_id="mock-exec-2",
            timestamp=_FIXED_NOW,
            severity=RuntimeLogSeverity.INFO,
            message="Job started",
        ),
        RuntimeLogEvent(
            execution_id="mock-exec-2",
            timestamp=_FIXED_NOW,
            severity=RuntimeLogSeverity.ERROR,
            message="Connection to source database timed out",
        ),
    ],
}

_SEVERITY_RANK = list(RuntimeLogSeverity)


class MockRuntimeProvider:
    """Deterministic fixture-backed provider. No network, no secrets."""

    async def list_executions(self, params: ListExecutionsInput) -> list[RuntimeExecution]:
        results = _EXECUTIONS
        if params.project_name:
            results = [e for e in results if e.project_name == params.project_name]
        if params.job_name:
            results = [e for e in results if e.job_name == params.job_name]
        return results[: params.limit]

    async def get_execution_details(self, params: GetExecutionDetailsInput) -> RuntimeExecution:
        for execution in _EXECUTIONS:
            if execution.execution_id == params.execution_id:
                return execution
        raise RuntimeProviderError(f"Unknown execution_id: {params.execution_id}")

    async def detect_failures(self, params: DetectFailuresInput) -> list[RuntimeFailure]:
        return _FAILURES[: params.limit]

    async def get_execution_logs(self, params: GetExecutionLogsInput) -> list[RuntimeLogEvent]:
        logs = _LOGS.get(params.execution_id)
        if logs is None:
            raise RuntimeProviderError(f"Unknown execution_id: {params.execution_id}")
        if params.min_severity is not None:
            min_rank = _SEVERITY_RANK.index(params.min_severity)
            logs = [log for log in logs if _SEVERITY_RANK.index(log.severity) >= min_rank]
        return logs[: params.limit]


provider = MockRuntimeProvider()
