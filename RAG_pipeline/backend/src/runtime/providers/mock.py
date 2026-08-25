"""Deterministic, credential-free mock runtime provider (EP-004 slice 2).

Enabled via runtime_provider_key="mock". Exists only for tests and local
validation — never reads env secrets or calls the network.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Mapping

from src.runtime.models import (
    DetectFailuresInput,
    GetExecutionDetailsInput,
    GetExecutionLogsInput,
    ListExecutionsInput,
    RuntimeArtifact,
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
    RuntimeSource,
    RuntimeSyncStatus,
    RuntimeWorkspace,
)
from src.runtime.providers.base import (
    CredentialField,
    NoScopeSelectionMixin,
    SingleTenantIdentityMixin,
)
from src.runtime.providers import RuntimeProviderContext, RuntimeProviderError
from src.runtime.sync import RuntimeSyncBatch

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


class MockRuntimeProvider(NoScopeSelectionMixin, SingleTenantIdentityMixin):
    """Deterministic fixture-backed provider. No network, no secrets."""

    def provider_key(self) -> str:
        return "mock"

    def provider_label(self) -> str:
        return "Mock"

    def credential_schema(self) -> list[CredentialField]:
        return []

    def container_key_field(self) -> str:
        return "workspace_id"

    def static_ref_strategy(self) -> None:
        return None

    def physical_source_identity(self, credentials: Mapping[str, Any]) -> str:
        return "mock"

    def source_type_aliases(self) -> set[str]:
        return {self.provider_key()}

    def identity_credential_fields(self) -> set[str]:
        return set()

    async def validate_credentials(self, credentials: Mapping[str, Any]) -> tuple[bool, str | None]:
        return True, None

    async def sync(self, *, credentials: Mapping[str, Any], project_id: str) -> RuntimeSyncBatch:
        return await self.fetch_runtime_batch(project_id=project_id)

    async def list_executions(
        self, params: ListExecutionsInput, context: RuntimeProviderContext | None = None
    ) -> list[RuntimeExecution]:
        results = _EXECUTIONS
        if params.project_name:
            results = [e for e in results if e.project_name == params.project_name]
        if params.job_name:
            results = [e for e in results if e.job_name == params.job_name]
        return results[: params.limit]

    async def get_execution_details(
        self, params: GetExecutionDetailsInput, context: RuntimeProviderContext | None = None
    ) -> RuntimeExecution:
        for execution in _EXECUTIONS:
            if execution.execution_id == params.execution_id:
                return execution
        raise RuntimeProviderError(f"Unknown execution_id: {params.execution_id}")

    async def detect_failures(
        self, params: DetectFailuresInput, context: RuntimeProviderContext | None = None
    ) -> list[RuntimeFailure]:
        return _FAILURES[: params.limit]

    async def get_execution_logs(
        self, params: GetExecutionLogsInput, context: RuntimeProviderContext | None = None
    ) -> list[RuntimeLogEvent]:
        logs = _LOGS.get(params.execution_id)
        if logs is None:
            raise RuntimeProviderError(f"Unknown execution_id: {params.execution_id}")
        if params.min_severity is not None:
            min_rank = _SEVERITY_RANK.index(params.min_severity)
            logs = [log for log in logs if _SEVERITY_RANK.index(log.severity) >= min_rank]
        return logs[: params.limit]

    async def fetch_runtime_batch(self, *, project_id: str) -> RuntimeSyncBatch:
        """Return a deterministic local sync batch for scheduler smoke tests.

        This is intentionally credential-free and network-free. It lets the
        runtime scheduler exercise the same DB write path as live adapters.
        """
        source_key = "mock"
        environment = RuntimeEnvironment(
            source_key=source_key,
            environment_id="mock-env",
            name="Mock Environment",
            last_seen_at=_FIXED_NOW,
        )
        workspace = RuntimeWorkspace(
            source_key=source_key,
            workspace_id="mock-workspace",
            name="Mock Workspace",
            environment_id=environment.environment_id,
            environment_name=environment.name,
            last_seen_at=_FIXED_NOW,
        )
        engine = RuntimeEngine(
            source_key=source_key,
            engine_id="mock-engine",
            name="mock-engine-01",
            status=RuntimeEngineStatus.ONLINE,
            environment_id=environment.environment_id,
            environment_name=environment.name,
            workspace_id=workspace.workspace_id,
            workspace_name=workspace.name,
            last_seen_at=_FIXED_NOW,
        )
        job = RuntimeJob(
            source_key=source_key,
            job_id="mock-nightly-sync",
            name="nightly_sync",
            display_name="Nightly Sync",
            job_type="task",
            status="active",
            environment_id=environment.environment_id,
            environment_name=environment.name,
            workspace_id=workspace.workspace_id,
            workspace_name=workspace.name,
            engine_id=engine.engine_id,
            engine_name=engine.name,
            artifact_name="mock-orders-artifact",
            last_seen_at=_FIXED_NOW,
        )
        artifact = RuntimeArtifact(
            source_key=source_key,
            artifact_id="mock-orders-artifact",
            name="orders_sync_route",
            artifact_type="route",
            version="1.3.0",
            workspace_id=workspace.workspace_id,
            workspace_name=workspace.name,
            owner="platform-team",
            last_seen_at=_FIXED_NOW,
            source_updated_at=_FIXED_NOW,
        )
        connection = RuntimeConnection(
            source_key=source_key,
            connection_id="mock-oracle-prod",
            name="Oracle PROD orders",
            connection_type="oracle",
            workspace_id=workspace.workspace_id,
            workspace_name=workspace.name,
            owner="data-platform",
            last_seen_at=_FIXED_NOW,
            source_updated_at=_FIXED_NOW,
        )
        dependencies = [
            RuntimeJobDependency(
                source_key=source_key,
                job_id=job.job_id,
                job_name=job.name,
                dependency_type="artifact",
                dependency_id=artifact.artifact_id,
                dependency_name=artifact.name,
                dependency_kind=artifact.artifact_type,
                workspace_id=workspace.workspace_id,
                workspace_name=workspace.name,
                last_seen_at=_FIXED_NOW,
            ),
            RuntimeJobDependency(
                source_key=source_key,
                job_id=job.job_id,
                job_name=job.name,
                dependency_type="connection",
                dependency_id=connection.connection_id,
                dependency_name=connection.name,
                dependency_kind=connection.connection_type,
                workspace_id=workspace.workspace_id,
                workspace_name=workspace.name,
                last_seen_at=_FIXED_NOW,
            ),
        ]
        job_runs = [
            RuntimeJobRun(
                source_key=source_key,
                run_id=execution.execution_id,
                job_id=job.job_id,
                job_name=execution.job_name,
                execution_id=execution.execution_id,
                state=execution.state,
                environment_id=environment.environment_id,
                environment_name=environment.name,
                workspace_id=workspace.workspace_id,
                workspace_name=workspace.name,
                engine_id=engine.engine_id,
                engine_name=engine.name,
                started_at=execution.started_at,
                ended_at=execution.ended_at,
                duration_ms=execution.duration_ms,
            )
            for execution in _EXECUTIONS
        ]
        return RuntimeSyncBatch(
            sources=[
                RuntimeSource(
                    project_id=project_id,
                    source_key=source_key,
                    source_type="mock",
                    label="Mock runtime source",
                    status=RuntimeSyncStatus.SUCCESS,
                    is_demo=True,
                    last_synced_at=_FIXED_NOW,
                    last_seen_at=_FIXED_NOW,
                    ingested_at=_FIXED_NOW,
                )
            ],
            environments=[environment],
            workspaces=[workspace],
            engines=[engine],
            jobs=[job],
            artifacts=[artifact],
            connections=[connection],
            job_dependencies=dependencies,
            job_runs=job_runs,
            executions=_EXECUTIONS,
            failures=_FAILURES,
            log_events=[event for events in _LOGS.values() for event in events],
            synced_at=_FIXED_NOW,
        )


provider = MockRuntimeProvider()
