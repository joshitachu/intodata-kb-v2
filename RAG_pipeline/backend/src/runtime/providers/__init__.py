"""Provider protocol for the generic runtime tool boundary (EP-004 slice 2).

Adapters (Talend TMC, mock, future platforms) implement these four async
methods using the normalized models from runtime/models.py. Callers in
runtime/client.py never see provider-specific operation names — only the
generic tool vocabulary defined in runtime/registry.py.
"""

from __future__ import annotations

from typing import Protocol

from src.runtime.models import (
    DetectFailuresInput,
    GetExecutionDetailsInput,
    GetExecutionLogsInput,
    ListExecutionsInput,
    RuntimeExecution,
    RuntimeFailure,
    RuntimeLogEvent,
)


class RuntimeProviderError(Exception):
    """Raised by provider adapters for controlled provider-side failures
    (not found, upstream error). Caught by runtime/client.py and converted
    into a structured RuntimeToolResult failure."""


class RuntimeProvider(Protocol):
    async def list_executions(self, params: ListExecutionsInput) -> list[RuntimeExecution]: ...

    async def get_execution_details(self, params: GetExecutionDetailsInput) -> RuntimeExecution: ...

    async def detect_failures(self, params: DetectFailuresInput) -> list[RuntimeFailure]: ...

    async def get_execution_logs(self, params: GetExecutionLogsInput) -> list[RuntimeLogEvent]: ...
