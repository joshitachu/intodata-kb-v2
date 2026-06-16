"""Tests for the deterministic mock runtime provider (src/runtime/providers/mock.py)."""

import pytest

from src.runtime.models import (
    DetectFailuresInput,
    GetExecutionDetailsInput,
    GetExecutionLogsInput,
    ListExecutionsInput,
    RuntimeExecution,
    RuntimeFailure,
    RuntimeLogEvent,
    RuntimeLogSeverity,
)
from src.runtime.providers import RuntimeProviderError
from src.runtime.providers.mock import provider


class TestMockProviderListExecutions:
    async def test_returns_normalized_executions(self):
        results = await provider.list_executions(ListExecutionsInput())
        assert len(results) >= 1
        assert all(isinstance(r, RuntimeExecution) for r in results)

    async def test_filters_by_job_name(self):
        results = await provider.list_executions(ListExecutionsInput(job_name="nightly_sync"))
        assert results
        assert all(r.job_name == "nightly_sync" for r in results)

    async def test_respects_limit(self):
        results = await provider.list_executions(ListExecutionsInput(limit=1))
        assert len(results) == 1


class TestMockProviderGetExecutionDetails:
    async def test_returns_known_execution(self):
        result = await provider.get_execution_details(GetExecutionDetailsInput(execution_id="mock-exec-1"))
        assert isinstance(result, RuntimeExecution)
        assert result.execution_id == "mock-exec-1"

    async def test_unknown_execution_raises_provider_error(self):
        with pytest.raises(RuntimeProviderError):
            await provider.get_execution_details(GetExecutionDetailsInput(execution_id="does-not-exist"))


class TestMockProviderDetectFailures:
    async def test_returns_normalized_failures(self):
        results = await provider.detect_failures(DetectFailuresInput())
        assert len(results) >= 1
        assert all(isinstance(r, RuntimeFailure) for r in results)


class TestMockProviderGetExecutionLogs:
    async def test_returns_normalized_log_events(self):
        results = await provider.get_execution_logs(GetExecutionLogsInput(execution_id="mock-exec-2"))
        assert len(results) >= 1
        assert all(isinstance(r, RuntimeLogEvent) for r in results)

    async def test_unknown_execution_raises_provider_error(self):
        with pytest.raises(RuntimeProviderError):
            await provider.get_execution_logs(GetExecutionLogsInput(execution_id="does-not-exist"))

    async def test_min_severity_filters_lower_severity_events(self):
        results = await provider.get_execution_logs(
            GetExecutionLogsInput(execution_id="mock-exec-2", min_severity=RuntimeLogSeverity.ERROR)
        )
        assert results
        assert all(r.severity == RuntimeLogSeverity.ERROR for r in results)
