"""Provider protocol for the generic runtime tool boundary (EP-004 slice 2).

Adapters (Talend TMC, mock, future platforms) implement these four async
methods using the normalized models from runtime/models.py. Callers in
runtime/client.py never see provider-specific operation names — only the
generic tool vocabulary defined in runtime/registry.py.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from src.runtime.insights.models import (
    RuntimeFailureAnalysis,
    RuntimeIncidentDetail,
    RuntimeSimilarFailures,
    RuntimeTimelineEvent,
)
from src.runtime.models import (
    AnalyzeExecutionFailureInput,
    DetectFailuresInput,
    FindSimilarFailuresInput,
    GetExecutionDetailsInput,
    GetExecutionLogsInput,
    GetExecutionTimelineInput,
    GetRuntimeIncidentInput,
    ListExecutionsInput,
    ListRuntimeArtifactsInput,
    ListRuntimeConnectionsInput,
    ListRuntimeDependenciesInput,
    ListRuntimeEnginesInput,
    ListRuntimeHealthInput,
    ListRuntimeInventoryInput,
    ListRuntimeJobsInput,
    ListRuntimePlansInput,
    ListRuntimeSyncStatusInput,
    RuntimeArtifact,
    RuntimeConnection,
    RuntimeDependencySummary,
    RuntimeExecution,
    RuntimeEngine,
    RuntimeJob,
    RuntimeJobDependency,
    RuntimePlan,
    RuntimeSource,
    RuntimeFailure,
    RuntimeHealthSummary,
    RuntimeLogEvent,
    RuntimeSyncState,
    RuntimeUnhealthySummary,
)
from src.runtime.store import ProjectScope


class RuntimeProviderError(Exception):
    """Raised by provider adapters for controlled provider-side failures
    (not found, upstream error). Caught by runtime/client.py and converted
    into a structured RuntimeToolResult failure."""


@dataclass
class RuntimeProviderContext:
    """Per-call execution context threaded from the authenticated request.

    `project_scope` is the RBAC allow-list (None=all, []=none, [ids]=restrict),
    sourced from backend code only — never from LLM tool arguments. It has no
    default so an unscoped context cannot be constructed by accident. `db` is the
    request's AsyncSession, required by the DB-backed `stored` provider and
    ignored by network/fixture providers."""

    project_scope: ProjectScope
    db: AsyncSession | None = None


class RuntimeProvider(Protocol):
    async def list_executions(
        self, params: ListExecutionsInput, context: RuntimeProviderContext | None = None
    ) -> list[RuntimeExecution]: ...

    async def get_execution_details(
        self, params: GetExecutionDetailsInput, context: RuntimeProviderContext | None = None
    ) -> RuntimeExecution: ...

    async def detect_failures(
        self, params: DetectFailuresInput, context: RuntimeProviderContext | None = None
    ) -> list[RuntimeFailure]: ...

    async def get_execution_logs(
        self, params: GetExecutionLogsInput, context: RuntimeProviderContext | None = None
    ) -> list[RuntimeLogEvent]: ...

    async def analyze_execution_failure(
        self, params: AnalyzeExecutionFailureInput, context: RuntimeProviderContext | None = None
    ) -> RuntimeFailureAnalysis: ...

    async def get_execution_timeline(
        self, params: GetExecutionTimelineInput, context: RuntimeProviderContext | None = None
    ) -> list[RuntimeTimelineEvent]: ...

    async def get_runtime_incident(
        self, params: GetRuntimeIncidentInput, context: RuntimeProviderContext | None = None
    ) -> RuntimeIncidentDetail: ...

    async def find_similar_failures(
        self, params: FindSimilarFailuresInput, context: RuntimeProviderContext | None = None
    ) -> RuntimeSimilarFailures: ...

    async def list_runtime_sync_status(
        self, params: ListRuntimeSyncStatusInput, context: RuntimeProviderContext | None = None
    ) -> list[RuntimeSyncState]: ...

    async def list_runtime_health(
        self, params: ListRuntimeHealthInput, context: RuntimeProviderContext | None = None
    ) -> list[RuntimeHealthSummary]: ...

    async def list_runtime_sources(
        self, params: ListRuntimeInventoryInput, context: RuntimeProviderContext | None = None
    ) -> list[RuntimeSource]: ...

    async def list_runtime_engines(
        self, params: ListRuntimeEnginesInput, context: RuntimeProviderContext | None = None
    ) -> list[RuntimeEngine]: ...

    async def list_runtime_jobs(
        self, params: ListRuntimeJobsInput, context: RuntimeProviderContext | None = None
    ) -> list[RuntimeJob]: ...

    async def list_runtime_artifacts(
        self, params: ListRuntimeArtifactsInput, context: RuntimeProviderContext | None = None
    ) -> list[RuntimeArtifact]: ...

    async def list_runtime_connections(
        self, params: ListRuntimeConnectionsInput, context: RuntimeProviderContext | None = None
    ) -> list[RuntimeConnection]: ...

    async def list_runtime_dependencies(
        self, params: ListRuntimeDependenciesInput, context: RuntimeProviderContext | None = None
    ) -> list[RuntimeJobDependency]: ...

    async def get_runtime_dependency_summary(
        self, params: ListRuntimeInventoryInput, context: RuntimeProviderContext | None = None
    ) -> RuntimeDependencySummary: ...

    async def list_runtime_plans(
        self, params: ListRuntimePlansInput, context: RuntimeProviderContext | None = None
    ) -> list[RuntimePlan]: ...

    async def list_runtime_unhealthy(
        self, params: ListRuntimeInventoryInput, context: RuntimeProviderContext | None = None
    ) -> RuntimeUnhealthySummary: ...
