"""One-cycle runtime source sync.

Adapters are source-specific write-side integrations. This service keeps the
product path source-agnostic: adapter output is normalized runtime models, persisted
through the central store, then derived insights are rebuilt from DB rows.
"""

from __future__ import annotations

import datetime
import logging
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from src.runtime.insights.builder import InsightBuildResult, build_runtime_insights_for_execution
from src.runtime.redaction import sanitize_sync_error
from src.runtime.sync.progress import sync_progress
from src.runtime.models import (
    RuntimeArtifact,
    RuntimeConnection,
    RuntimeEngine,
    RuntimeEnvironment,
    RuntimeExecution,
    RuntimeFailure,
    RuntimeJob,
    RuntimeJobDependency,
    RuntimeJobRun,
    RuntimePlan,
    RuntimePlanRun,
    RuntimeComponentMetric,
    RuntimeLogEvent,
    RuntimeSource,
    RuntimeSyncStatus,
    RuntimeWorkspace,
)
from src.runtime.store import RuntimeStoreRepository
from src.runtime.store import RuntimeDuplicateSourceError, RuntimeRecordNotFoundError

if TYPE_CHECKING:
    from src.runtime.insights.semantic import RuntimeSemanticIndexer

# Re-exported: `sanitize_sync_error` moved to src/runtime/redaction.py so the
# documentation-evidence read path can share it without importing the sync
# service (which imports the store, and would cycle).
__all__ = ["sanitize_sync_error"]

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RuntimeSyncBatch:
    """Normalized records returned by one adapter cycle."""

    sources: list[RuntimeSource] = field(default_factory=list)
    environments: list[RuntimeEnvironment] = field(default_factory=list)
    workspaces: list[RuntimeWorkspace] = field(default_factory=list)
    engines: list[RuntimeEngine] = field(default_factory=list)
    jobs: list[RuntimeJob] = field(default_factory=list)
    artifacts: list[RuntimeArtifact] = field(default_factory=list)
    connections: list[RuntimeConnection] = field(default_factory=list)
    job_dependencies: list[RuntimeJobDependency] = field(default_factory=list)
    job_runs: list[RuntimeJobRun] = field(default_factory=list)
    plans: list[RuntimePlan] = field(default_factory=list)
    plan_runs: list[RuntimePlanRun] = field(default_factory=list)
    executions: list[RuntimeExecution] = field(default_factory=list)
    failures: list[RuntimeFailure] = field(default_factory=list)
    log_events: list[RuntimeLogEvent] = field(default_factory=list)
    component_metrics: list[RuntimeComponentMetric] = field(default_factory=list)
    partial: bool = False
    failure_reason_code: str | None = None
    error_summary: str | None = None
    synced_at: datetime.datetime | None = None


@dataclass(frozen=True)
class RuntimeSyncCycleResult:
    source_key: str
    project_id: str
    status: RuntimeSyncStatus
    last_synced_at: datetime.datetime | None
    executions_written: int = 0
    failures_written: int = 0
    log_events_written: int = 0
    component_metrics_written: int = 0
    sources_written: int = 0
    environments_written: int = 0
    workspaces_written: int = 0
    engines_written: int = 0
    jobs_written: int = 0
    artifacts_written: int = 0
    connections_written: int = 0
    job_dependencies_written: int = 0
    job_runs_written: int = 0
    plans_written: int = 0
    plan_runs_written: int = 0
    insights_built: int = 0
    failure_reason_code: str | None = None
    error_summary: str | None = None


class RuntimeSyncAdapter(Protocol):
    """Source adapter contract for one normalized sync cycle."""

    async def fetch_runtime_batch(self, *, project_id: str) -> RuntimeSyncBatch:
        """Return normalized runtime rows for this source/project."""


class _RuntimeSourceAdapter:
    def __init__(self, provider, credentials: dict) -> None:
        self._provider = provider
        self._credentials = credentials

    async def fetch_runtime_batch(self, *, project_id: str) -> RuntimeSyncBatch:
        return await self._provider.sync(credentials=self._credentials, project_id=project_id)


def _batch_record_count(batch: RuntimeSyncBatch) -> int:
    """How many records a fetched batch will write, known before persisting."""
    return sum(
        len(getattr(batch, name))
        for name in (
            "executions",
            "failures",
            "log_events",
            "component_metrics",
            "environments",
            "workspaces",
            "engines",
            "jobs",
            "artifacts",
            "connections",
            "job_dependencies",
            "job_runs",
            "plans",
            "plan_runs",
        )
    )


def _record_count(result: RuntimeSyncCycleResult) -> int:
    return (
        result.executions_written
        + result.failures_written
        + result.log_events_written
        + result.component_metrics_written
        + result.sources_written
        + result.environments_written
        + result.workspaces_written
        + result.engines_written
        + result.jobs_written
        + result.artifacts_written
        + result.connections_written
        + result.job_dependencies_written
        + result.job_runs_written
        + result.plans_written
        + result.plan_runs_written
    )


async def run_runtime_source_sync_cycle(
    session: AsyncSession,
    *,
    source_id: int,
    now: datetime.datetime | None = None,
    semantic_indexer: "RuntimeSemanticIndexer | None" = None,
) -> RuntimeSyncCycleResult:
    """Run one source-configured sync cycle and update source health."""

    cycle_at = _as_utc(now or datetime.datetime.now(datetime.UTC))
    from src.runtime.source_registry import get_source_provider

    repo = RuntimeStoreRepository(session)
    source = await repo.get_source_config(source_id, None)
    if not source.enabled:
        raise RuntimeRecordNotFoundError(f"Runtime source {source_id} is disabled")
    provider = get_source_provider(source.source_type)
    if provider is None:
        raise RuntimeRecordNotFoundError(f"Runtime source provider not found: {source.source_type}")
    await repo.record_source_sync_attempt(source_id, cycle_at)
    claimed = sync_progress.begin(source_id, now=cycle_at)
    try:
        credentials, _key_id = await repo.read_credentials(source_id)
        sync_progress.update(source_id, phase="Reading from the source")
        batch = await provider.sync(credentials=credentials, project_id=source.project_id)
        sync_progress.update(
            source_id,
            phase="Saving imported records",
            records=_batch_record_count(batch),
        )
        result = await _persist_source_batch_with_project_map(
            session,
            repo=repo,
            source_id=source_id,
            source_key=source.source_type,
            default_project_id=source.project_id,
            container_key_field=provider.container_key_field(),
            batch=batch,
            cycle_at=cycle_at,
            semantic_indexer=semantic_indexer,
        )
        await repo.prune_expired_log_events(now=cycle_at)
    except Exception as exc:
        if claimed:
            sync_progress.finish(source_id)
        error_summary = sanitize_sync_error(str(exc) or exc.__class__.__name__)
        await repo.record_source_sync_failure(
            source_id,
            failed_at=cycle_at,
            sanitized_error=error_summary,
        )
        return RuntimeSyncCycleResult(
            source_key=source.source_type,
            project_id=source.project_id,
            status=RuntimeSyncStatus.FAILED,
            last_synced_at=None,
            failure_reason_code=exc.__class__.__name__[:80],
            error_summary=error_summary,
        )
    if claimed:
        sync_progress.finish(source_id)
    if result.status == RuntimeSyncStatus.SUCCESS:
        await _refresh_source_identity(repo, source_id=source_id, provider=provider, credentials=credentials, batch=batch)
        await repo.record_source_sync_success(
            source_id,
            succeeded_at=result.last_synced_at or cycle_at,
            records_ingested=_record_count(result),
        )
    else:
        await repo.record_source_sync_failure(
            source_id,
            failed_at=cycle_at,
            sanitized_error=result.error_summary,
        )
        updated = await repo.get_source_config(source_id, None)
        if updated.disabled_reason == "auto-disabled":
            from src.runtime.audit import log_runtime_source_event

            await log_runtime_source_event(
                session,
                user=None,
                action="source_disabled_auto",
                source_id=source_id,
                details={
                    "failure_reason": result.error_summary,
                    "consecutive_failures": updated.health.consecutive_failures if updated.health else None,
                },
            )
    return result


async def _refresh_source_identity(
    repo: RuntimeStoreRepository,
    *,
    source_id: int,
    provider,
    credentials: dict,
    batch: RuntimeSyncBatch,
) -> None:
    """Keep the stored identity aligned with the tenant the source actually serves.

    The markers an identity is derived from can change on the source side (a new
    TMC environment, say). Without this the identity would freeze at its
    registration-time value, and re-registering the same physical source would
    compute a different identity and slip past the duplicate guard -- mirroring
    every row a second time, which is the failure that guard exists to prevent.
    """
    refreshed = provider.physical_identity_from_batch(credentials, batch)
    if refreshed is None:
        return
    if refreshed == await repo.get_source_physical_identity(source_id):
        return
    try:
        await repo.promote_source_identity(source_id, refreshed)
    except RuntimeDuplicateSourceError:
        logger.warning(
            "Runtime source %s now resolves to an identity another source already holds; "
            "the two are mirroring the same physical source",
            source_id,
        )


async def _persist_source_batch_with_project_map(
    session: AsyncSession,
    *,
    repo: RuntimeStoreRepository,
    source_id: int,
    source_key: str,
    default_project_id: str,
    container_key_field: str,
    batch: RuntimeSyncBatch,
    cycle_at: datetime.datetime,
    semantic_indexer: "RuntimeSemanticIndexer | None" = None,
) -> RuntimeSyncCycleResult:
    synced_at = _as_utc(batch.synced_at or cycle_at)
    status = RuntimeSyncStatus.PARTIAL if batch.partial else RuntimeSyncStatus.SUCCESS
    failure_reason = _clean_reason_code(batch.failure_reason_code) if batch.partial else None
    error_summary = sanitize_sync_error(batch.error_summary) if batch.error_summary else None

    # ``batch.sources`` is deliberately NOT persisted here.
    #
    # A provider's batch declares its own source row because the original
    # deploy-level scheduler (``run_runtime_sync_cycle``) had no registered
    # source to attribute data to -- the declared row WAS the source. This path
    # is the self-service one: the operator already registered a row, it already
    # holds the credentials, health and schedule, and it is the row this cycle
    # was launched from. Upserting the declared row here produced a SECOND,
    # credential-less row per project (``observed:talend_tmc``), which the admin
    # list and the dashboard's connector panel both rendered as if it were a
    # real, polling connection.
    #
    # It never even described this data: the declared key is a provider
    # constant (``talend_tmc``) while every record below is stamped with
    # ``source_key`` -- the registered row's own type. Nothing referenced it.
    for environment in batch.environments:
        await repo.upsert_environment(default_project_id, environment, source_key, synced_at)
    for workspace in batch.workspaces:
        project_id, _unmapped = await repo.resolve_project_id(source_id, workspace.workspace_id)
        await repo.upsert_workspace(project_id, workspace, source_key, synced_at)
    for engine in batch.engines:
        project_id, _unmapped = await repo.resolve_project_id(source_id, _container_key(engine, container_key_field))
        await repo.upsert_engine(project_id, engine, source_key, synced_at)
    for job in batch.jobs:
        project_id, _unmapped = await repo.resolve_project_id(source_id, _container_key(job, container_key_field))
        await repo.upsert_job(project_id, job, source_key, synced_at)
    for artifact in batch.artifacts:
        project_id, _unmapped = await repo.resolve_project_id(source_id, _container_key(artifact, container_key_field))
        await repo.upsert_artifact(project_id, artifact, source_key, synced_at)
    for connection in batch.connections:
        project_id, _unmapped = await repo.resolve_project_id(source_id, _container_key(connection, container_key_field))
        await repo.upsert_connection(project_id, connection, source_key, synced_at)
    for dependency in batch.job_dependencies:
        project_id, _unmapped = await repo.resolve_project_id(source_id, _container_key(dependency, container_key_field))
        await repo.upsert_job_dependency(project_id, dependency, source_key, synced_at)
    for plan in batch.plans:
        project_id, _unmapped = await repo.resolve_project_id(source_id, _container_key(plan, container_key_field))
        await repo.upsert_plan(project_id, plan, source_key, synced_at)
    for execution in batch.executions:
        await repo.upsert_execution(default_project_id, execution, source_key, synced_at)
    for failure in batch.failures:
        await repo.upsert_failure(default_project_id, failure, source_key, synced_at)
    for job_run in batch.job_runs:
        project_id, _unmapped = await repo.resolve_project_id(source_id, _container_key(job_run, container_key_field))
        await repo.upsert_job_run(project_id, job_run, source_key, synced_at)
    for plan_run in batch.plan_runs:
        project_id, _unmapped = await repo.resolve_project_id(source_id, _container_key(plan_run, container_key_field))
        await repo.upsert_plan_run(project_id, plan_run, source_key, synced_at)
    await repo.clear_engine_scope_log_events(
        default_project_id, _engine_scope_execution_ids(batch)
    )
    for event in batch.log_events:
        await repo.upsert_log_event(default_project_id, event, source_key, synced_at)
    for metric in batch.component_metrics:
        await repo.upsert_component_metric(default_project_id, metric, source_key, synced_at)

    built = 0
    for execution in batch.executions:
        result: InsightBuildResult = await build_runtime_insights_for_execution(
            session,
            project_id=default_project_id,
            execution_id=execution.execution_id,
            source_key=source_key,
            ingested_at=synced_at,
            semantic_indexer=semantic_indexer,
        )
        if result.enriched_events or result.had_incident:
            built += 1

    await repo.record_sync_state(
        source_key=source_key,
        project_id=default_project_id,
        status=status,
        last_synced_at=synced_at if status == RuntimeSyncStatus.SUCCESS else None,
        failure_reason_code=failure_reason,
        error_summary=error_summary,
        updated_at=cycle_at,
    )
    return RuntimeSyncCycleResult(
        source_key=source_key,
        project_id=default_project_id,
        status=status,
        last_synced_at=synced_at if status == RuntimeSyncStatus.SUCCESS else None,
        executions_written=len(batch.executions),
        failures_written=len(batch.failures),
        log_events_written=len(batch.log_events),
        component_metrics_written=len(batch.component_metrics),
        # Zero: the declared source row is skipped above, so counting it
        # would inflate "records ingested" by one on every cycle.
        sources_written=0,
        environments_written=len(batch.environments),
        workspaces_written=len(batch.workspaces),
        engines_written=len(batch.engines),
        jobs_written=len(batch.jobs),
        artifacts_written=len(batch.artifacts),
        connections_written=len(batch.connections),
        job_dependencies_written=len(batch.job_dependencies),
        job_runs_written=len(batch.job_runs),
        plans_written=len(batch.plans),
        plan_runs_written=len(batch.plan_runs),
        insights_built=built,
        failure_reason_code=failure_reason,
        error_summary=error_summary,
    )


def _container_key(record, field_name: str) -> str | None:
    value = getattr(record, field_name, None)
    return value if isinstance(value, str) and value else None


def _engine_scope_execution_ids(batch) -> list[str]:
    """Deployments whose rolling tail this batch re-read.

    Only these get their previous snapshot dropped. An empty list is the
    normal case for a provider that emits no deployment logs, and it makes
    `clear_engine_scope_log_events` a no-op rather than a table-wide delete.
    """
    return list(
        dict.fromkeys(
            event.execution_id
            for event in batch.log_events
            if getattr(event, "scope", None) == "engine" and event.execution_id
        )
    )


async def run_runtime_sync_cycle(
    session: AsyncSession,
    *,
    adapter: RuntimeSyncAdapter,
    source_key: str,
    project_id: str,
    now: datetime.datetime | None = None,
    semantic_indexer: "RuntimeSemanticIndexer | None" = None,
) -> RuntimeSyncCycleResult:
    """Run one adapter cycle and persist normalized runtime data.

    Scheduling, backoff, and adapter discovery are intentionally out of scope.
    Tests and future workers can call this function once per configured source/project.
    """
    cycle_at = _as_utc(now or datetime.datetime.now(datetime.UTC))
    repo = RuntimeStoreRepository(session)

    try:
        batch = await adapter.fetch_runtime_batch(project_id=project_id)
    except Exception as exc:  # noqa: BLE001 - boundary sanitizes any adapter failure
        error_summary = sanitize_sync_error(str(exc) or exc.__class__.__name__)
        await repo.record_sync_state(
            source_key=source_key,
            project_id=project_id,
            status=RuntimeSyncStatus.FAILED,
            last_synced_at=None,
            failure_reason_code=exc.__class__.__name__[:80],
            error_summary=error_summary,
            updated_at=cycle_at,
        )
        return RuntimeSyncCycleResult(
            source_key=source_key,
            project_id=project_id,
            status=RuntimeSyncStatus.FAILED,
            last_synced_at=None,
            failure_reason_code=exc.__class__.__name__[:80],
            error_summary=error_summary,
        )

    synced_at = _as_utc(batch.synced_at or cycle_at)
    status = RuntimeSyncStatus.PARTIAL if batch.partial else RuntimeSyncStatus.SUCCESS
    failure_reason = _clean_reason_code(batch.failure_reason_code) if batch.partial else None
    error_summary = sanitize_sync_error(batch.error_summary) if batch.error_summary else None

    for source in batch.sources:
        await repo.upsert_source(project_id, source, synced_at)
    for environment in batch.environments:
        await repo.upsert_environment(project_id, environment, source_key, synced_at)
    for workspace in batch.workspaces:
        await repo.upsert_workspace(project_id, workspace, source_key, synced_at)
    for engine in batch.engines:
        await repo.upsert_engine(project_id, engine, source_key, synced_at)
    for job in batch.jobs:
        await repo.upsert_job(project_id, job, source_key, synced_at)
    for artifact in batch.artifacts:
        await repo.upsert_artifact(project_id, artifact, source_key, synced_at)
    for connection in batch.connections:
        await repo.upsert_connection(project_id, connection, source_key, synced_at)
    for dependency in batch.job_dependencies:
        await repo.upsert_job_dependency(project_id, dependency, source_key, synced_at)
    for plan in batch.plans:
        await repo.upsert_plan(project_id, plan, source_key, synced_at)
    for execution in batch.executions:
        await repo.upsert_execution(project_id, execution, source_key, synced_at)
    for failure in batch.failures:
        await repo.upsert_failure(project_id, failure, source_key, synced_at)
    for job_run in batch.job_runs:
        await repo.upsert_job_run(project_id, job_run, source_key, synced_at)
    for plan_run in batch.plan_runs:
        await repo.upsert_plan_run(project_id, plan_run, source_key, synced_at)
    await repo.clear_engine_scope_log_events(project_id, _engine_scope_execution_ids(batch))
    for event in batch.log_events:
        await repo.upsert_log_event(project_id, event, source_key, synced_at)
    await repo.prune_expired_log_events(now=cycle_at)
    for metric in batch.component_metrics:
        await repo.upsert_component_metric(project_id, metric, source_key, synced_at)

    built = 0
    for execution in batch.executions:
        result: InsightBuildResult = await build_runtime_insights_for_execution(
            session,
            project_id=project_id,
            execution_id=execution.execution_id,
            source_key=source_key,
            ingested_at=synced_at,
            semantic_indexer=semantic_indexer,
        )
        if result.enriched_events or result.had_incident:
            built += 1

    await repo.record_sync_state(
        source_key=source_key,
        project_id=project_id,
        status=status,
        last_synced_at=synced_at if status == RuntimeSyncStatus.SUCCESS else None,
        failure_reason_code=failure_reason,
        error_summary=error_summary,
        updated_at=cycle_at,
    )

    return RuntimeSyncCycleResult(
        source_key=source_key,
        project_id=project_id,
        status=status,
        last_synced_at=synced_at if status == RuntimeSyncStatus.SUCCESS else None,
        executions_written=len(batch.executions),
        failures_written=len(batch.failures),
        log_events_written=len(batch.log_events),
        component_metrics_written=len(batch.component_metrics),
        sources_written=len(batch.sources),
        environments_written=len(batch.environments),
        workspaces_written=len(batch.workspaces),
        engines_written=len(batch.engines),
        jobs_written=len(batch.jobs),
        artifacts_written=len(batch.artifacts),
        connections_written=len(batch.connections),
        job_dependencies_written=len(batch.job_dependencies),
        job_runs_written=len(batch.job_runs),
        plans_written=len(batch.plans),
        plan_runs_written=len(batch.plan_runs),
        insights_built=built,
        failure_reason_code=failure_reason,
        error_summary=error_summary,
    )


def _clean_reason_code(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip())[:80]
    return cleaned or None


def _as_utc(value: datetime.datetime) -> datetime.datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=datetime.UTC)
    return value.astimezone(datetime.UTC)
