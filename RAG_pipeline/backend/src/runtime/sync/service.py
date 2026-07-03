"""One-cycle runtime source sync.

Adapters are source-specific write-side integrations. This service keeps the
product path source-agnostic: adapter output is normalized runtime models, persisted
through the central store, then derived insights are rebuilt from DB rows.
"""

from __future__ import annotations

import datetime
import re
from dataclasses import dataclass, field
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from src.runtime.insights.builder import InsightBuildResult, build_runtime_insights_for_execution
from src.runtime.insights.parser import redact_credentials
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

_MAX_ERROR_SUMMARY = 240
_TOKENISH_RE = re.compile(
    r"(?i)\b(bearer|token|secret|password|passwd|pwd|api[_-]?key)\b\s*[:=]?\s*\S*"
)


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


async def run_runtime_sync_cycle(
    session: AsyncSession,
    *,
    adapter: RuntimeSyncAdapter,
    source_key: str,
    project_id: str,
    now: datetime.datetime | None = None,
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
    for event in batch.log_events:
        await repo.upsert_log_event(project_id, event, source_key, synced_at)
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


def sanitize_sync_error(value: str | None) -> str | None:
    """Bound and redact sync failure text before persistence or API exposure."""
    if not value:
        return None
    compact = " ".join(value.replace("\n", " ").split())
    redacted = redact_credentials(_TOKENISH_RE.sub("<redacted>", compact))
    if len(redacted) > _MAX_ERROR_SUMMARY:
        return redacted[: _MAX_ERROR_SUMMARY - 3] + "..."
    return redacted


def _clean_reason_code(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip())[:80]
    return cleaned or None


def _as_utc(value: datetime.datetime) -> datetime.datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=datetime.UTC)
    return value.astimezone(datetime.UTC)
