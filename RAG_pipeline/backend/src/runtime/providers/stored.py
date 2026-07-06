"""DB-backed runtime provider (held-data Model 2 skeleton, Slice 2).

Answers the four generic runtime tools from the held runtime-data store instead of
calling a live source, by delegating to the Slice 1 RuntimeStoreRepository. Reads are
project-scoped using the scope carried on RuntimeProviderContext — which originates
from the authenticated request, never from LLM tool arguments.

Source-agnostic: no Talend-specific names. A future sync worker (or any source)
populates the store through the repository write-port; this provider only reads.
"""

from __future__ import annotations

from typing import Any, Mapping

from src.runtime.insights.models import (
    RuntimeFailureAnalysis,
    RuntimeIncidentDetail,
    RuntimeSimilarFailures,
    RuntimeTimelineEvent,
)
from src.runtime.insights.repository import RuntimeInsightRepository
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
    RuntimeEngine,
    RuntimeExecution,
    RuntimeFailure,
    RuntimeHealthSummary,
    RuntimeJob,
    RuntimeJobDependency,
    RuntimeLogEvent,
    RuntimePlan,
    RuntimeSource,
    RuntimeSyncState,
    RuntimeUnhealthySummary,
)
from src.runtime.providers.base import CredentialField
from src.runtime.providers import RuntimeProviderContext, RuntimeProviderError
from src.runtime.store import RuntimeRecordNotFoundError, RuntimeStoreRepository

_TIMELINE_SIGNAL_MAX_CHARS = 200


class StoredRuntimeProvider:
    """Reads held runtime data via the repository, scoped per request."""

    def provider_key(self) -> str:
        return "stored"

    def provider_label(self) -> str:
        return "Stored Runtime Data"

    def credential_schema(self) -> list[CredentialField]:
        return []

    def container_key_field(self) -> str:
        return "workspace_id"

    def static_ref_strategy(self) -> None:
        return None

    async def validate_credentials(self, credentials: Mapping[str, Any]) -> tuple[bool, str | None]:
        return True, None

    async def sync(self, *, credentials: Mapping[str, Any], project_id: str):
        raise RuntimeProviderError("stored runtime provider is read-only and cannot sync a live source")

    @staticmethod
    def _store_repository(context: RuntimeProviderContext | None) -> RuntimeStoreRepository:
        if context is None or context.db is None:
            raise RuntimeProviderError("stored runtime provider requires a database session")
        return RuntimeStoreRepository(context.db)

    @staticmethod
    def _insight_repository(context: RuntimeProviderContext | None) -> RuntimeInsightRepository:
        if context is None or context.db is None:
            raise RuntimeProviderError("stored runtime provider requires a database session")
        return RuntimeInsightRepository(context.db)

    @staticmethod
    def _scope(context: RuntimeProviderContext | None):
        # context is non-None here (checked by _store_repository/_insight_repository)
        return context.project_scope if context is not None else None

    async def list_executions(
        self, params: ListExecutionsInput, context: RuntimeProviderContext | None = None
    ) -> list[RuntimeExecution]:
        repo = self._store_repository(context)
        return await repo.list_executions(self._scope(context), params)

    async def get_execution_details(
        self, params: GetExecutionDetailsInput, context: RuntimeProviderContext | None = None
    ) -> RuntimeExecution:
        repo = self._store_repository(context)
        try:
            return await repo.get_execution_details(self._scope(context), params)
        except RuntimeRecordNotFoundError as e:
            raise RuntimeProviderError(str(e)) from e

    async def detect_failures(
        self, params: DetectFailuresInput, context: RuntimeProviderContext | None = None
    ) -> list[RuntimeFailure]:
        repo = self._store_repository(context)
        return await repo.detect_failures(self._scope(context), params)

    async def get_execution_logs(
        self, params: GetExecutionLogsInput, context: RuntimeProviderContext | None = None
    ) -> list[RuntimeLogEvent]:
        repo = self._store_repository(context)
        try:
            return await repo.get_execution_logs(self._scope(context), params)
        except RuntimeRecordNotFoundError as e:
            raise RuntimeProviderError(str(e)) from e

    async def analyze_execution_failure(
        self, params: AnalyzeExecutionFailureInput, context: RuntimeProviderContext | None = None
    ) -> RuntimeFailureAnalysis:
        repo = self._insight_repository(context)
        scope = self._scope(context)
        try:
            summary = await repo.get_execution_summary(scope, params.execution_id)
        except RuntimeRecordNotFoundError as e:
            raise RuntimeProviderError(str(e)) from e

        incident = None
        evidence = []
        if summary.has_incident and summary.incident_id:
            try:
                incident = await repo.get_incident(scope, summary.incident_id)
                evidence = await repo.get_incident_evidence(scope, summary.incident_id, limit=5)
            except RuntimeRecordNotFoundError:
                pass  # insight rows may be partially built; non-fatal

        return RuntimeFailureAnalysis(
            execution_id=summary.execution_id,
            job_name=summary.job_name,
            state=summary.state,
            started_at=summary.started_at,
            ended_at=summary.ended_at,
            duration_ms=summary.duration_ms,
            warning_count=summary.warning_count,
            error_count=summary.error_count,
            failed_component=summary.failed_component,
            primary_error_code=summary.primary_error_code,
            primary_exception_class=summary.primary_exception_class,
            summary_text=summary.summary_text,
            has_incident=summary.has_incident,
            incident_id=summary.incident_id,
            symptom_summary=incident.symptom_summary if incident else None,
            root_cause_candidate=incident.root_cause_candidate if incident else None,
            confidence=incident.confidence if incident else None,
            cause_origin=incident.cause_origin if incident else None,
            next_checks=incident.next_checks if incident else [],
            evidence=evidence,
            estate_context=summary.estate_context,
        )

    async def get_execution_timeline(
        self, params: GetExecutionTimelineInput, context: RuntimeProviderContext | None = None
    ) -> list[RuntimeTimelineEvent]:
        repo = self._insight_repository(context)
        scope = self._scope(context)
        try:
            events = await repo.get_execution_timeline(
                scope,
                params.execution_id,
                min_severity=params.min_severity.value if params.min_severity else None,
                limit=params.limit,
            )
        except RuntimeRecordNotFoundError as e:
            raise RuntimeProviderError(str(e)) from e
        # Cap signal length so timeline results cannot flood LLM context.
        return [
            event.model_copy(update={"signal": event.signal[:_TIMELINE_SIGNAL_MAX_CHARS]})
            if len(event.signal) > _TIMELINE_SIGNAL_MAX_CHARS
            else event
            for event in events
        ]

    async def get_runtime_incident(
        self, params: GetRuntimeIncidentInput, context: RuntimeProviderContext | None = None
    ) -> RuntimeIncidentDetail:
        repo = self._insight_repository(context)
        scope = self._scope(context)
        try:
            incident = await repo.get_incident(scope, params.incident_id)
        except RuntimeRecordNotFoundError as e:
            raise RuntimeProviderError(str(e)) from e
        evidence = []
        if params.include_evidence:
            try:
                evidence = await repo.get_incident_evidence(scope, params.incident_id, limit=params.evidence_limit)
            except RuntimeRecordNotFoundError:
                pass
        return RuntimeIncidentDetail(incident=incident, evidence=evidence)

    async def find_similar_failures(
        self, params: FindSimilarFailuresInput, context: RuntimeProviderContext | None = None
    ) -> RuntimeSimilarFailures:
        repo = self._insight_repository(context)
        scope = self._scope(context)
        incident_id = params.incident_id
        if incident_id is None and params.execution_id is not None:
            try:
                summary = await repo.get_execution_summary(scope, params.execution_id)
            except RuntimeRecordNotFoundError as e:
                raise RuntimeProviderError(str(e)) from e
            if not summary.has_incident or summary.incident_id is None:
                raise RuntimeProviderError(
                    f"No incident was derived for execution {params.execution_id}"
                )
            incident_id = summary.incident_id
        try:
            return await repo.get_similar_failures_for_incident(scope, incident_id, limit=params.limit)
        except RuntimeRecordNotFoundError as e:
            raise RuntimeProviderError(str(e)) from e

    async def list_runtime_sync_status(
        self, params: ListRuntimeSyncStatusInput, context: RuntimeProviderContext | None = None
    ) -> list[RuntimeSyncState]:
        repo = self._store_repository(context)
        return await repo.list_sync_states(
            self._scope(context),
            source_key=params.source_key,
            project_id=params.project_id,
            limit=params.limit,
        )

    async def list_runtime_health(
        self, params: ListRuntimeHealthInput, context: RuntimeProviderContext | None = None
    ) -> list[RuntimeHealthSummary]:
        repo = self._store_repository(context)
        return await repo.list_health_summaries(
            self._scope(context),
            source_key=params.source_key,
            project_id=params.project_id,
            limit=params.limit,
        )

    async def list_runtime_sources(
        self, params: ListRuntimeInventoryInput, context: RuntimeProviderContext | None = None
    ) -> list[RuntimeSource]:
        repo = self._store_repository(context)
        return await repo.list_sources(
            self._scope(context),
            source_key=params.source_key,
            project_id=params.project_id,
            limit=params.limit,
        )

    async def list_runtime_engines(
        self, params: ListRuntimeEnginesInput, context: RuntimeProviderContext | None = None
    ) -> list[RuntimeEngine]:
        repo = self._store_repository(context)
        return await repo.list_engines(self._scope(context), params)

    async def list_runtime_jobs(
        self, params: ListRuntimeJobsInput, context: RuntimeProviderContext | None = None
    ) -> list[RuntimeJob]:
        repo = self._store_repository(context)
        return await repo.list_jobs(self._scope(context), params)

    async def list_runtime_artifacts(
        self, params: ListRuntimeArtifactsInput, context: RuntimeProviderContext | None = None
    ) -> list[RuntimeArtifact]:
        repo = self._store_repository(context)
        return await repo.list_artifacts(self._scope(context), params)

    async def list_runtime_connections(
        self, params: ListRuntimeConnectionsInput, context: RuntimeProviderContext | None = None
    ) -> list[RuntimeConnection]:
        repo = self._store_repository(context)
        return await repo.list_connections(self._scope(context), params)

    async def list_runtime_dependencies(
        self, params: ListRuntimeDependenciesInput, context: RuntimeProviderContext | None = None
    ) -> list[RuntimeJobDependency]:
        repo = self._store_repository(context)
        return await repo.list_job_dependencies(self._scope(context), params)

    async def get_runtime_dependency_summary(
        self, params: ListRuntimeInventoryInput, context: RuntimeProviderContext | None = None
    ) -> RuntimeDependencySummary:
        repo = self._store_repository(context)
        return await repo.get_dependency_summary(self._scope(context), params)

    async def list_runtime_plans(
        self, params: ListRuntimePlansInput, context: RuntimeProviderContext | None = None
    ) -> list[RuntimePlan]:
        repo = self._store_repository(context)
        return await repo.list_plans(self._scope(context), params)

    async def list_runtime_unhealthy(
        self, params: ListRuntimeInventoryInput, context: RuntimeProviderContext | None = None
    ) -> RuntimeUnhealthySummary:
        repo = self._store_repository(context)
        scope = self._scope(context)
        overview = await repo.get_estate_overview(scope, params)
        sources = await repo.list_sources(
            scope,
            source_key=params.source_key,
            project_id=params.project_id,
            limit=params.limit,
        )
        engine_health = await repo.list_engine_health(
            scope,
            ListRuntimeEnginesInput(
                source_key=params.source_key,
                project_id=params.project_id,
                limit=params.limit,
            ),
        )
        job_health = await repo.list_job_health(
            scope,
            ListRuntimeJobsInput(
                source_key=params.source_key,
                project_id=params.project_id,
                limit=params.limit,
            ),
        )
        plan_health = await repo.list_plan_health(
            scope,
            ListRuntimePlansInput(
                source_key=params.source_key,
                project_id=params.project_id,
                limit=params.limit,
            ),
        )
        return RuntimeUnhealthySummary(
            overview=overview,
            sources=sources,
            unhealthy_engines=[
                item for item in engine_health
                if item.failed_runs > 0 or item.current_failure_streak > 0
            ][: params.limit],
            unhealthy_jobs=[
                item for item in job_health
                if item.failed_runs > 0 or item.current_failure_streak > 0
            ][: params.limit],
            unhealthy_plans=[
                item for item in plan_health
                if item.failed_runs > 0 or item.current_failure_streak > 0
            ][: params.limit],
        )


provider = StoredRuntimeProvider()
