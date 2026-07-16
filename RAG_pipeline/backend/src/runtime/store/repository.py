"""Repository for the held runtime-data store.

Reads return the normalized runtime models from src/runtime/models.py and are always
project-scoped. The scope is an explicit argument supplied by backend code — never by
LLM/tool arguments — so authorization cannot be widened from chat input.

The write-port performs dialect-portable, idempotent upserts (select-then-update/insert
rather than Postgres-only ON CONFLICT) so the same repository runs under SQLite in
unit tests and Postgres in production.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import math
import uuid
from collections import defaultdict

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.runtime_data import (
    RuntimeArtifactRow,
    RuntimeComponentMetricRow,
    RuntimeConnectionRow,
    RuntimeEngineRow,
    RuntimeEnvironmentRow,
    RuntimeExecutionRow,
    RuntimeFailureRow,
    RuntimeJobRow,
    RuntimeJobDependencyRow,
    RuntimeJobRunRow,
    RuntimeLogEventRow,
    RuntimePlanRow,
    RuntimePlanRunRow,
    RuntimeSourceRow,
    RuntimeSourceCredentialsRow,
    RuntimeSourceHealthRow,
    RuntimeWorkspaceProjectMapRow,
    RuntimeSyncStateRow,
    RuntimeWorkspaceRow,
)
from src.db.models.runtime_insights import RuntimeIncidentRow
from src.runtime.models import (
    DetectFailuresInput,
    GetExecutionDetailsInput,
    GetExecutionLogsInput,
    ListExecutionsInput,
    ListRuntimeArtifactsInput,
    ListRuntimeConnectionsInput,
    ListRuntimeDependenciesInput,
    ListRuntimeEnginesInput,
    ListRuntimeInventoryInput,
    ListRuntimeJobRunsInput,
    ListRuntimeJobsInput,
    ListRuntimePlanRunsInput,
    ListRuntimePlansInput,
    RuntimeArtifact,
    RuntimeConnection,
    RuntimeDependencySummary,
    RuntimeExecution,
    RuntimeEngine,
    RuntimeEngineHealth,
    RuntimeEngineStatus,
    RuntimeEnvironment,
    RuntimeEstateOverview,
    RuntimeExecutionState,
    RuntimeExtendedMetadataAvailability,
    RuntimeFailure,
    RuntimeHealthSummary,
    RuntimeJob,
    RuntimeJobDependency,
    RuntimeJobHealth,
    RuntimeJobRun,
    RuntimeComponentMetric,
    RuntimeLogEvent,
    RuntimeLogSeverity,
    RuntimePlan,
    RuntimePlanHealth,
    RuntimePlanRun,
    RuntimePlanRunStepResult,
    RuntimePlanStep,
    RuntimeSource,
    RuntimeSourceHealth,
    RuntimeSourceRead,
    RuntimeSourceUpdate,
    RuntimeWorkspaceProjectMap,
    RuntimeSyncState,
    RuntimeSyncStatus,
    RuntimeWorkspace,
)
from src.runtime.secrets import (
    current_key_id,
    decrypt_and_rotate_if_needed,
    encrypt,
)
from src.runtime.insights.parser import redact_credentials
from src.runtime.store import ProjectScope, RuntimeDuplicateSourceError, RuntimeRecordNotFoundError

# Severity ordering for min_severity filtering (low -> high).
_SEVERITY_ORDER: list[RuntimeLogSeverity] = list(RuntimeLogSeverity)


def _severities_at_or_above(minimum: RuntimeLogSeverity) -> list[str]:
    min_rank = _SEVERITY_ORDER.index(minimum)
    return [s.value for s in _SEVERITY_ORDER[min_rank:]]


def log_event_fingerprint(event: RuntimeLogEvent) -> str:
    """Deterministic fingerprint for log idempotency (no natural unique id upstream)."""
    raw = f"{event.timestamp.isoformat()}|{event.severity.value}|{event.line_ordinal}|{event.message}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:64]


def _as_utc(value: datetime.datetime | None) -> datetime.datetime | None:
    """Return a timezone-aware UTC datetime. Postgres returns tz-aware values; SQLite
    (used in tests) returns naive ones. Runtime timestamps are stored as UTC, so the
    repository normalizes both backends to the same tz-aware UTC contract."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=datetime.UTC)
    return value.astimezone(datetime.UTC)


class RuntimeStoreRepository:
    """Async repository over the runtime-data tables, bound to one session."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # --- scope helpers ---

    @staticmethod
    def _denies_all(scope: ProjectScope) -> bool:
        """An empty (non-None) scope means the caller may see nothing."""
        return scope is not None and len(scope) == 0

    @staticmethod
    def _apply_scope(stmt, column, scope: ProjectScope):
        if scope is None:
            return stmt
        return stmt.where(column.in_(scope))

    # --- reads (project-scoped) ---

    async def list_executions(
        self, scope: ProjectScope, params: ListExecutionsInput
    ) -> list[RuntimeExecution]:
        if self._denies_all(scope):
            return []
        stmt = select(RuntimeExecutionRow)
        stmt = self._apply_scope(stmt, RuntimeExecutionRow.project_id, scope)
        if params.project_name:
            stmt = stmt.where(RuntimeExecutionRow.project_name == params.project_name)
        if params.job_name:
            stmt = stmt.where(RuntimeExecutionRow.job_name == params.job_name)
        if params.state is not None:
            stmt = stmt.where(RuntimeExecutionRow.state == params.state.value)
        if params.since:
            stmt = stmt.where(RuntimeExecutionRow.started_at >= params.since)
        if params.until:
            stmt = stmt.where(RuntimeExecutionRow.started_at <= params.until)
        stmt = stmt.order_by(RuntimeExecutionRow.started_at.desc(), RuntimeExecutionRow.id.desc()).offset(params.offset).limit(params.limit)
        rows = (await self._session.execute(stmt)).scalars().all()
        # Defensive read-side protection for historical mirrored writes. A source
        # owns its execution ids, so an identical (source_key, execution_id) is
        # one physical event even if an old mirror still exists temporarily.
        seen: set[tuple[str, str]] = set()
        return [
            _to_execution(row)
            for row in rows
            if not ((key := (row.source_key, row.execution_id)) in seen or seen.add(key))
        ]

    async def get_execution_details(
        self, scope: ProjectScope, params: GetExecutionDetailsInput
    ) -> RuntimeExecution:
        if self._denies_all(scope):
            raise RuntimeRecordNotFoundError(f"Unknown execution_id: {params.execution_id}")
        stmt = select(RuntimeExecutionRow).where(
            RuntimeExecutionRow.execution_id == params.execution_id
        )
        stmt = self._apply_scope(stmt, RuntimeExecutionRow.project_id, scope)
        row = (await self._session.execute(stmt)).scalars().first()
        if row is None:
            raise RuntimeRecordNotFoundError(f"Unknown execution_id: {params.execution_id}")
        return _to_execution(row)

    async def detect_failures(
        self, scope: ProjectScope, params: DetectFailuresInput
    ) -> list[RuntimeFailure]:
        if self._denies_all(scope):
            return []
        stmt = select(RuntimeFailureRow)
        stmt = self._apply_scope(stmt, RuntimeFailureRow.project_id, scope)
        if params.since:
            stmt = stmt.where(RuntimeFailureRow.failed_at >= params.since)
        if params.until:
            stmt = stmt.where(RuntimeFailureRow.failed_at <= params.until)
        stmt = stmt.order_by(RuntimeFailureRow.failed_at.desc(), RuntimeFailureRow.id.desc()).offset(params.offset).limit(params.limit)
        rows = (await self._session.execute(stmt)).scalars().all()
        return [_to_failure(row) for row in rows]

    async def get_execution_logs(
        self, scope: ProjectScope, params: GetExecutionLogsInput
    ) -> list[RuntimeLogEvent]:
        if self._denies_all(scope):
            raise RuntimeRecordNotFoundError(f"Unknown execution_id: {params.execution_id}")
        stmt = select(RuntimeLogEventRow).where(
            RuntimeLogEventRow.execution_id == params.execution_id
        )
        stmt = self._apply_scope(stmt, RuntimeLogEventRow.project_id, scope)
        if params.min_severity is not None:
            stmt = stmt.where(
                RuntimeLogEventRow.severity.in_(_severities_at_or_above(params.min_severity))
            )
        stmt = stmt.order_by(RuntimeLogEventRow.timestamp.asc(), RuntimeLogEventRow.line_ordinal.asc(), RuntimeLogEventRow.id.asc()).offset(params.offset).limit(params.limit)
        rows = (await self._session.execute(stmt)).scalars().all()
        if not rows:
            raise RuntimeRecordNotFoundError(f"Unknown execution_id: {params.execution_id}")
        return [_to_log_event(row) for row in rows]

    async def get_freshness(
        self, scope: ProjectScope, source_key: str, project_id: str | None = None
    ) -> datetime.datetime | None:
        """Latest in-scope watermark for a source (and optional project) — the "as of"."""
        if self._denies_all(scope):
            return None
        stmt = select(RuntimeSyncStateRow.last_synced_at).where(
            RuntimeSyncStateRow.source_key == source_key
        )
        stmt = self._apply_scope(stmt, RuntimeSyncStateRow.project_id, scope)
        if project_id is not None:
            stmt = stmt.where(RuntimeSyncStateRow.project_id == project_id)
        values = [value for value in (await self._session.execute(stmt)).scalars().all() if value]
        if not values:
            return None
        return _as_utc(max(values))

    async def count_executions(self, scope: ProjectScope, params: ListExecutionsInput) -> int:
        if self._denies_all(scope):
            return 0
        stmt = select(RuntimeExecutionRow.source_key, RuntimeExecutionRow.execution_id)
        stmt = self._apply_scope(stmt, RuntimeExecutionRow.project_id, scope)
        if params.project_name:
            stmt = stmt.where(RuntimeExecutionRow.project_name == params.project_name)
        if params.job_name:
            stmt = stmt.where(RuntimeExecutionRow.job_name == params.job_name)
        if params.state is not None:
            stmt = stmt.where(RuntimeExecutionRow.state == params.state.value)
        if params.since:
            stmt = stmt.where(RuntimeExecutionRow.started_at >= params.since)
        if params.until:
            stmt = stmt.where(RuntimeExecutionRow.started_at <= params.until)
        deduped = stmt.distinct().subquery()
        return int((await self._session.execute(select(func.count()).select_from(deduped))).scalar_one())

    async def count_failures(self, scope: ProjectScope, params: DetectFailuresInput) -> int:
        if self._denies_all(scope):
            return 0
        stmt = select(func.count()).select_from(RuntimeFailureRow)
        stmt = self._apply_scope(stmt, RuntimeFailureRow.project_id, scope)
        if params.since:
            stmt = stmt.where(RuntimeFailureRow.failed_at >= params.since)
        if params.until:
            stmt = stmt.where(RuntimeFailureRow.failed_at <= params.until)
        return int((await self._session.execute(stmt)).scalar_one())

    async def count_execution_logs(self, scope: ProjectScope, params: GetExecutionLogsInput) -> int:
        if self._denies_all(scope):
            return 0
        stmt = select(func.count()).select_from(RuntimeLogEventRow).where(RuntimeLogEventRow.execution_id == params.execution_id)
        stmt = self._apply_scope(stmt, RuntimeLogEventRow.project_id, scope)
        if params.min_severity is not None:
            stmt = stmt.where(RuntimeLogEventRow.severity.in_(_severities_at_or_above(params.min_severity)))
        return int((await self._session.execute(stmt)).scalar_one())

    async def prune_expired_log_events(self, *, now: datetime.datetime | None = None, retention_days: int = 90) -> int:
        cutoff = (now or datetime.datetime.now(datetime.UTC)) - datetime.timedelta(days=retention_days)
        result = await self._session.execute(delete(RuntimeLogEventRow).where(RuntimeLogEventRow.timestamp < cutoff))
        await self._session.flush()
        return int(result.rowcount or 0)

    async def search_log_events(
        self, scope: ProjectScope, *, query: str, project_id: str | None = None,
        execution_id: str | None = None, since: datetime.datetime | None = None,
        until: datetime.datetime | None = None, limit: int = 20, snippet_chars: int = 300,
    ) -> list[dict[str, object]]:
        """Search redacted raw logs with bounded snippets; scope is always server-derived."""
        if self._denies_all(scope) or not query.strip():
            return []
        stmt = select(RuntimeLogEventRow)
        stmt = self._apply_scope(stmt, RuntimeLogEventRow.project_id, scope)
        if project_id is not None:
            stmt = stmt.where(RuntimeLogEventRow.project_id == project_id)
        if execution_id is not None:
            stmt = stmt.where(RuntimeLogEventRow.execution_id == execution_id)
        if since is not None:
            stmt = stmt.where(RuntimeLogEventRow.timestamp >= since)
        if until is not None:
            stmt = stmt.where(RuntimeLogEventRow.timestamp <= until)
        dialect = self._session.bind.dialect.name if self._session.bind is not None else ""
        if dialect == "postgresql":
            vector = func.to_tsvector("simple", RuntimeLogEventRow.message)
            tsquery = func.plainto_tsquery("simple", query)
            stmt = stmt.where(
                vector.op("@@")(tsquery) | RuntimeLogEventRow.message.op("%")(query)
            ).order_by(
                func.ts_rank_cd(vector, tsquery).desc(),
                func.similarity(RuntimeLogEventRow.message, query).desc(),
                RuntimeLogEventRow.timestamp.desc(),
                RuntimeLogEventRow.id.desc(),
            )
        else:
            stmt = stmt.where(RuntimeLogEventRow.message.ilike(f"%{query}%")).order_by(RuntimeLogEventRow.timestamp.desc(), RuntimeLogEventRow.id.desc())
        rows = (
            await self._session.execute(stmt.limit(min(max(limit * 5, limit), 500)))
        ).scalars().all()
        results: dict[tuple[str, str, str, str], dict[str, object]] = {}
        for row in rows:
            safe_message = redact_credentials(row.message)
            key = (row.project_id, row.execution_id, row.severity, safe_message)
            existing = results.get(key)
            if existing is not None:
                existing["occurrence_count"] = int(existing["occurrence_count"]) + 1
                continue
            results[key] = {
                "project_id": row.project_id,
                "execution_id": row.execution_id,
                "timestamp": _as_utc(row.timestamp),
                "severity": row.severity,
                "line_ordinal": row.line_ordinal,
                "snippet": safe_message[:snippet_chars],
                "occurrence_count": 1,
            }
        return list(results.values())[:limit]

    async def list_sync_states(
        self,
        scope: ProjectScope,
        *,
        source_key: str | None = None,
        project_id: str | None = None,
        limit: int = 100,
    ) -> list[RuntimeSyncState]:
        """Return scoped source/project sync freshness and health rows."""
        if self._denies_all(scope):
            return []
        stmt = select(RuntimeSyncStateRow)
        stmt = self._apply_scope(stmt, RuntimeSyncStateRow.project_id, scope)
        if source_key is not None:
            stmt = stmt.where(RuntimeSyncStateRow.source_key == source_key)
        if project_id is not None:
            stmt = stmt.where(RuntimeSyncStateRow.project_id == project_id)
        stmt = stmt.order_by(RuntimeSyncStateRow.updated_at.desc(), RuntimeSyncStateRow.id.desc()).limit(limit)
        rows = (await self._session.execute(stmt)).scalars().all()
        return [_to_sync_state(row) for row in rows]

    async def list_health_summaries(
        self,
        scope: ProjectScope,
        *,
        project_id: str | None = None,
        source_key: str | None = None,
        limit: int = 50,
    ) -> list[RuntimeHealthSummary]:
        """Return DB-derived health rollups grouped by project/source/job."""
        if self._denies_all(scope):
            return []
        exec_stmt = select(RuntimeExecutionRow)
        exec_stmt = self._apply_scope(exec_stmt, RuntimeExecutionRow.project_id, scope)
        if project_id is not None:
            exec_stmt = exec_stmt.where(RuntimeExecutionRow.project_id == project_id)
        if source_key is not None:
            exec_stmt = exec_stmt.where(RuntimeExecutionRow.source_key == source_key)
        exec_rows = list((await self._session.execute(exec_stmt)).scalars().all())
        if not exec_rows:
            return []

        metric_stmt = select(RuntimeComponentMetricRow)
        metric_stmt = self._apply_scope(metric_stmt, RuntimeComponentMetricRow.project_id, scope)
        if project_id is not None:
            metric_stmt = metric_stmt.where(RuntimeComponentMetricRow.project_id == project_id)
        if source_key is not None:
            metric_stmt = metric_stmt.where(RuntimeComponentMetricRow.source_key == source_key)
        metric_rows = list((await self._session.execute(metric_stmt)).scalars().all())

        incident_stmt = select(RuntimeIncidentRow)
        incident_stmt = self._apply_scope(incident_stmt, RuntimeIncidentRow.project_id, scope)
        if project_id is not None:
            incident_stmt = incident_stmt.where(RuntimeIncidentRow.project_id == project_id)
        incident_rows = list((await self._session.execute(incident_stmt)).scalars().all())

        exec_by_id = {(r.project_id, r.execution_id): r for r in exec_rows}
        grouped: dict[tuple[str, str, str], list[RuntimeExecutionRow]] = defaultdict(list)
        for row in exec_rows:
            grouped[(row.project_id, row.source_key, row.job_name)].append(row)

        metrics_by_group: dict[tuple[str, str, str], list[RuntimeComponentMetricRow]] = defaultdict(list)
        for metric in metric_rows:
            exec_row = exec_by_id.get((metric.project_id, metric.execution_id))
            if exec_row is None:
                continue
            metrics_by_group[(exec_row.project_id, exec_row.source_key, exec_row.job_name)].append(metric)

        incidents_by_group: dict[tuple[str, str, str], list[RuntimeIncidentRow]] = defaultdict(list)
        for incident in incident_rows:
            exec_row = exec_by_id.get((incident.project_id, incident.execution_id))
            if exec_row is None:
                continue
            incidents_by_group[(exec_row.project_id, exec_row.source_key, exec_row.job_name)].append(incident)

        summaries = [
            _to_health_summary(
                project_id=pid,
                source_key=src,
                job_name=job,
                executions=rows,
                metrics=metrics_by_group.get((pid, src, job), []),
                incidents=incidents_by_group.get((pid, src, job), []),
            )
            for (pid, src, job), rows in grouped.items()
        ]
        summaries.sort(
            key=lambda item: (
                item.failure_rate,
                item.current_failure_streak,
                item.recurrence_count or 0,
                item.last_failure_at or datetime.datetime.min.replace(tzinfo=datetime.UTC),
            ),
            reverse=True,
        )
        return summaries[:limit]

    async def list_sources(
        self,
        scope: ProjectScope,
        *,
        source_key: str | None = None,
        project_id: str | None = None,
        limit: int = 100,
    ) -> list[RuntimeSource]:
        """Return scoped runtime source inventory rows."""
        if self._denies_all(scope):
            return []
        stmt = select(RuntimeSourceRow)
        stmt = self._apply_scope(stmt, RuntimeSourceRow.project_id, scope)
        if project_id is not None:
            stmt = stmt.where(RuntimeSourceRow.project_id == project_id)
        if source_key is not None:
            stmt = stmt.where(RuntimeSourceRow.source_key == source_key)
        stmt = stmt.order_by(RuntimeSourceRow.last_seen_at.desc().nullslast(), RuntimeSourceRow.id.desc()).limit(limit)
        rows = (await self._session.execute(stmt)).scalars().all()
        return [_to_source(row) for row in rows]

    # --- source management (credentials never returned) ---

    async def create_source_config(
        self,
        *,
        project_id: str,
        source_type: str,
        label: str,
        physical_identity: str | None = None,
        sync_interval_seconds: int = 60,
    ) -> RuntimeSourceRead:
        physical_identity = physical_identity or f"local:{source_type}:{uuid.uuid4().hex}"
        existing = (await self._session.execute(select(RuntimeSourceRow.id).where(
            RuntimeSourceRow.physical_identity == physical_identity,
        ))).scalar_one_or_none()
        if existing is not None:
            raise RuntimeDuplicateSourceError("That physical runtime source is already registered")
        now = datetime.datetime.now(datetime.UTC)
        source_key = f"{source_type}-{uuid.uuid4().hex[:12]}"
        row = RuntimeSourceRow(
            project_id=project_id,
            source_key=source_key,
            source_type=source_type,
            physical_identity=physical_identity,
            label=label,
            status=RuntimeSyncStatus.SUCCESS.value,
            enabled=True,
            sync_interval_seconds=max(sync_interval_seconds, 30),
            auto_disable_threshold=3,
            is_demo=1 if source_type == "mock" else 0,
            ingested_at=now,
            last_seen_at=now,
        )
        self._session.add(row)
        await self._session.flush()
        health = RuntimeSourceHealthRow(source_id=row.id)
        self._session.add(health)
        await self._session.flush()
        return _to_source_read(row, health)

    async def get_source_config(self, source_id: int, scope: ProjectScope) -> RuntimeSourceRead:
        row = await self._get_source_row(source_id, scope)
        health = await self._get_source_health_row(row.id)
        return _to_source_read(row, health)

    async def promote_source_identity(self, source_id: int, physical_identity: str) -> None:
        """Replace a migration-era legacy identity after credentials are verified."""
        row = await self._get_source_row(source_id, None)
        collision = (await self._session.execute(select(RuntimeSourceRow.id).where(
            RuntimeSourceRow.physical_identity == physical_identity,
            RuntimeSourceRow.id != source_id,
        ))).scalar_one_or_none()
        if collision is not None:
            raise RuntimeDuplicateSourceError("That physical runtime source is already registered")
        row.physical_identity = physical_identity
        await self._session.flush()

    async def get_source_physical_identity(self, source_id: int) -> str:
        return (await self._get_source_row(source_id, None)).physical_identity

    async def find_source_id_by_physical_identity(self, physical_identity: str) -> int | None:
        return (await self._session.execute(select(RuntimeSourceRow.id).where(
            RuntimeSourceRow.physical_identity == physical_identity,
        ))).scalar_one_or_none()

    async def list_legacy_source_ids(self, source_types: set[str]) -> list[int]:
        if not source_types:
            return []
        return list((await self._session.execute(select(RuntimeSourceRow.id).where(
            RuntimeSourceRow.source_type.in_(source_types),
            RuntimeSourceRow.physical_identity.startswith("legacy:"),
        ))).scalars().all())

    async def list_source_configs(
        self,
        scope: ProjectScope,
        *,
        project_id: str | None = None,
        limit: int = 100,
    ) -> list[RuntimeSourceRead]:
        if self._denies_all(scope):
            return []
        stmt = select(RuntimeSourceRow)
        stmt = self._apply_scope(stmt, RuntimeSourceRow.project_id, scope)
        if project_id is not None:
            stmt = stmt.where(RuntimeSourceRow.project_id == project_id)
        stmt = stmt.order_by(RuntimeSourceRow.ingested_at.desc(), RuntimeSourceRow.id.desc()).limit(limit)
        rows = list((await self._session.execute(stmt)).scalars().all())
        health_rows = {
            row.source_id: row
            for row in (
                await self._session.execute(
                    select(RuntimeSourceHealthRow).where(
                        RuntimeSourceHealthRow.source_id.in_([row.id for row in rows] or [-1])
                    )
                )
            ).scalars().all()
        }
        return [_to_source_read(row, health_rows.get(row.id)) for row in rows]

    async def update_source_config(
        self,
        source_id: int,
        scope: ProjectScope,
        update: RuntimeSourceUpdate,
    ) -> RuntimeSourceRead:
        row = await self._get_source_row(source_id, scope)
        if update.label is not None:
            row.label = update.label
        if update.enabled is not None:
            row.enabled = update.enabled
            row.disabled_reason = None if update.enabled else "manual"
            if update.enabled:
                health = await self._get_source_health_row(row.id)
                if health is not None:
                    health.consecutive_failures = 0
        if update.sync_interval_seconds is not None:
            row.sync_interval_seconds = max(update.sync_interval_seconds, 30)
        await self._session.flush()
        return _to_source_read(row, await self._get_source_health_row(row.id))

    async def delete_source_config(self, source_id: int, scope: ProjectScope) -> None:
        row = await self._get_source_row(source_id, scope)
        await self._session.execute(delete(RuntimeSourceCredentialsRow).where(RuntimeSourceCredentialsRow.source_id == row.id))
        await self._session.execute(delete(RuntimeSourceHealthRow).where(RuntimeSourceHealthRow.source_id == row.id))
        await self._session.execute(delete(RuntimeWorkspaceProjectMapRow).where(RuntimeWorkspaceProjectMapRow.source_id == row.id))
        await self._session.delete(row)
        await self._session.flush()

    async def add_container_mapping(
        self,
        *,
        source_id: int,
        container_key: str,
        container_name: str | None,
        project_id: str,
        created_by: str | None,
    ) -> RuntimeWorkspaceProjectMap:
        now = datetime.datetime.now(datetime.UTC)
        stmt = select(RuntimeWorkspaceProjectMapRow).where(
            RuntimeWorkspaceProjectMapRow.source_id == source_id,
            RuntimeWorkspaceProjectMapRow.container_key == container_key,
        )
        row = (await self._session.execute(stmt)).scalars().first()
        if row is None:
            row = RuntimeWorkspaceProjectMapRow(
                source_id=source_id,
                container_key=container_key,
                created_at=now,
                created_by=created_by,
            )
            self._session.add(row)
        row.container_name = container_name
        row.project_id = project_id
        await self._session.flush()
        return _to_workspace_project_map(row)

    async def list_mappings(self, source_id: int) -> list[RuntimeWorkspaceProjectMap]:
        rows = (
            await self._session.execute(
                select(RuntimeWorkspaceProjectMapRow)
                .where(RuntimeWorkspaceProjectMapRow.source_id == source_id)
                .order_by(RuntimeWorkspaceProjectMapRow.container_key.asc())
            )
        ).scalars().all()
        return [_to_workspace_project_map(row) for row in rows]

    async def delete_mapping(self, source_id: int, container_key: str) -> None:
        await self._session.execute(
            delete(RuntimeWorkspaceProjectMapRow).where(
                RuntimeWorkspaceProjectMapRow.source_id == source_id,
                RuntimeWorkspaceProjectMapRow.container_key == container_key,
            )
        )
        await self._session.flush()

    async def resolve_project_id(self, source_id: int, container_key: str | None) -> tuple[str, bool]:
        source = await self._get_source_row(source_id, None)
        if container_key:
            row = (
                await self._session.execute(
                    select(RuntimeWorkspaceProjectMapRow).where(
                        RuntimeWorkspaceProjectMapRow.source_id == source_id,
                        RuntimeWorkspaceProjectMapRow.container_key == container_key,
                    )
                )
            ).scalars().first()
            if row is not None:
                return row.project_id, False
        return source.project_id, True

    async def write_credentials(
        self,
        *,
        source_id: int,
        plaintext_payload: dict,
        credential_type: str,
        rotated_by: str | None,
    ) -> None:
        now = datetime.datetime.now(datetime.UTC)
        payload = json.dumps(plaintext_payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
        key_id = current_key_id()
        encrypted_payload = encrypt(payload, key_id)
        stmt = select(RuntimeSourceCredentialsRow).where(RuntimeSourceCredentialsRow.source_id == source_id)
        row = (await self._session.execute(stmt)).scalars().first()
        if row is None:
            row = RuntimeSourceCredentialsRow(
                source_id=source_id,
                credential_type=credential_type,
                created_at=now,
            )
            self._session.add(row)
        row.encrypted_payload = encrypted_payload
        row.encryption_key_id = key_id
        row.rotated_at = now
        row.rotated_by = rotated_by
        await self._session.flush()

    async def read_credentials(self, source_id: int) -> tuple[dict, str]:
        stmt = select(RuntimeSourceCredentialsRow).where(RuntimeSourceCredentialsRow.source_id == source_id)
        row = (await self._session.execute(stmt)).scalars().first()
        if row is None:
            raise RuntimeRecordNotFoundError(f"Credentials not found for runtime source {source_id}")
        result = decrypt_and_rotate_if_needed(row.encrypted_payload, row.encryption_key_id)
        if result.rotated:
            row.encrypted_payload = result.ciphertext
            row.encryption_key_id = result.encryption_key_id
            row.rotated_at = datetime.datetime.now(datetime.UTC)
            await self._session.flush()
        return json.loads(result.plaintext.decode("utf-8")), row.encryption_key_id

    async def _get_source_row(self, source_id: int, scope: ProjectScope) -> RuntimeSourceRow:
        if self._denies_all(scope):
            raise RuntimeRecordNotFoundError(f"Runtime source not found: {source_id}")
        stmt = select(RuntimeSourceRow).where(RuntimeSourceRow.id == source_id)
        stmt = self._apply_scope(stmt, RuntimeSourceRow.project_id, scope)
        row = (await self._session.execute(stmt)).scalars().first()
        if row is None:
            raise RuntimeRecordNotFoundError(f"Runtime source not found: {source_id}")
        return row

    async def _get_source_health_row(self, source_id: int) -> RuntimeSourceHealthRow | None:
        return (
            await self._session.execute(
                select(RuntimeSourceHealthRow).where(RuntimeSourceHealthRow.source_id == source_id)
            )
        ).scalars().first()

    async def record_source_sync_attempt(self, source_id: int, attempted_at: datetime.datetime) -> None:
        health = await self._ensure_source_health_row(source_id)
        health.last_attempt_at = attempted_at
        await self._session.flush()

    async def record_source_sync_success(
        self,
        source_id: int,
        *,
        succeeded_at: datetime.datetime,
        records_ingested: int,
    ) -> RuntimeSourceRead:
        row = await self._get_source_row(source_id, None)
        health = await self._ensure_source_health_row(source_id)
        health.last_success_at = succeeded_at
        health.consecutive_failures = 0
        health.last_failure_sanitized = None
        health.total_cycles += 1
        health.total_records_ingested += records_ingested
        row.last_synced_at = succeeded_at
        row.last_seen_at = succeeded_at
        row.status = RuntimeSyncStatus.SUCCESS.value
        row.enabled = True
        row.disabled_reason = None
        await self._session.flush()
        return _to_source_read(row, health)

    async def record_source_sync_failure(
        self,
        source_id: int,
        *,
        failed_at: datetime.datetime,
        sanitized_error: str | None,
    ) -> RuntimeSourceRead:
        row = await self._get_source_row(source_id, None)
        health = await self._ensure_source_health_row(source_id)
        health.last_failure_at = failed_at
        health.consecutive_failures += 1
        health.last_failure_sanitized = sanitized_error
        health.total_cycles += 1
        row.status = RuntimeSyncStatus.FAILED.value
        if health.consecutive_failures >= row.auto_disable_threshold:
            row.enabled = False
            row.disabled_reason = "auto-disabled"
        await self._session.flush()
        return _to_source_read(row, health)

    async def _ensure_source_health_row(self, source_id: int) -> RuntimeSourceHealthRow:
        row = await self._get_source_health_row(source_id)
        if row is None:
            row = RuntimeSourceHealthRow(source_id=source_id)
            self._session.add(row)
            await self._session.flush()
        return row

    async def list_engines(
        self,
        scope: ProjectScope,
        params: ListRuntimeEnginesInput,
    ) -> list[RuntimeEngine]:
        """Return scoped runtime engines with source/environment/workspace labels."""
        if self._denies_all(scope):
            return []
        stmt = select(RuntimeEngineRow)
        stmt = self._apply_scope(stmt, RuntimeEngineRow.project_id, scope)
        if params.project_id is not None:
            stmt = stmt.where(RuntimeEngineRow.project_id == params.project_id)
        if params.source_key is not None:
            stmt = stmt.where(RuntimeEngineRow.source_key == params.source_key)
        if params.status is not None:
            stmt = stmt.where(RuntimeEngineRow.status == params.status.value)
        stmt = stmt.order_by(RuntimeEngineRow.name.asc(), RuntimeEngineRow.id.asc()).offset(params.offset).limit(params.limit)
        rows = (await self._session.execute(stmt)).scalars().all()
        return [_to_engine(row) for row in rows]

    async def count_engines(
        self, scope: ProjectScope, params: ListRuntimeEnginesInput
    ) -> int:
        if self._denies_all(scope):
            return 0
        stmt = select(func.count()).select_from(RuntimeEngineRow)
        stmt = self._apply_scope(stmt, RuntimeEngineRow.project_id, scope)
        if params.project_id is not None:
            stmt = stmt.where(RuntimeEngineRow.project_id == params.project_id)
        if params.source_key is not None:
            stmt = stmt.where(RuntimeEngineRow.source_key == params.source_key)
        if params.status is not None:
            stmt = stmt.where(RuntimeEngineRow.status == params.status.value)
        return int((await self._session.execute(stmt)).scalar_one())

    async def list_engine_health(
        self,
        scope: ProjectScope,
        params: ListRuntimeEnginesInput,
    ) -> list[RuntimeEngineHealth]:
        """Return DB-derived health grouped by runtime engine."""
        if self._denies_all(scope):
            return []
        run_stmt = select(RuntimeJobRunRow)
        run_stmt = self._apply_scope(run_stmt, RuntimeJobRunRow.project_id, scope)
        engine_stmt = select(RuntimeEngineRow)
        engine_stmt = self._apply_scope(engine_stmt, RuntimeEngineRow.project_id, scope)

        if params.project_id is not None:
            run_stmt = run_stmt.where(RuntimeJobRunRow.project_id == params.project_id)
            engine_stmt = engine_stmt.where(RuntimeEngineRow.project_id == params.project_id)
        if params.source_key is not None:
            run_stmt = run_stmt.where(RuntimeJobRunRow.source_key == params.source_key)
            engine_stmt = engine_stmt.where(RuntimeEngineRow.source_key == params.source_key)
        if params.status is not None:
            engine_stmt = engine_stmt.where(RuntimeEngineRow.status == params.status.value)

        run_rows = list((await self._session.execute(run_stmt)).scalars().all())
        engine_rows = list((await self._session.execute(engine_stmt)).scalars().all())
        engines_by_key = {
            (row.project_id, row.source_key, row.engine_id): row
            for row in engine_rows
        }
        allowed_engine_keys = set(engines_by_key)

        grouped: dict[tuple[str, str, str], list[RuntimeJobRunRow]] = defaultdict(list)
        for row in run_rows:
            if row.engine_id is None:
                continue
            key = (row.project_id, row.source_key, row.engine_id)
            if allowed_engine_keys and key not in allowed_engine_keys:
                continue
            grouped[key].append(row)

        summaries = [
            _to_engine_health(
                project_id=pid,
                source_key=source_key,
                engine_id=engine_id,
                runs=rows,
                engine=engines_by_key.get((pid, source_key, engine_id)),
            )
            for (pid, source_key, engine_id), rows in grouped.items()
        ]
        summaries.sort(
            key=lambda item: (
                item.failure_rate,
                item.current_failure_streak,
                item.latest_run_at or datetime.datetime.min.replace(tzinfo=datetime.UTC),
            ),
            reverse=True,
        )
        return summaries[: params.limit]

    async def list_jobs(
        self,
        scope: ProjectScope,
        params: ListRuntimeJobsInput,
    ) -> list[RuntimeJob]:
        """Return scoped current-state runtime jobs/tasks."""
        if self._denies_all(scope):
            return []
        stmt = select(RuntimeJobRow)
        stmt = self._apply_scope(stmt, RuntimeJobRow.project_id, scope)
        if params.project_id is not None:
            stmt = stmt.where(RuntimeJobRow.project_id == params.project_id)
        if params.source_key is not None:
            stmt = stmt.where(RuntimeJobRow.source_key == params.source_key)
        if params.environment_id is not None:
            stmt = stmt.where(RuntimeJobRow.environment_id == params.environment_id)
        if params.workspace_id is not None:
            stmt = stmt.where(RuntimeJobRow.workspace_id == params.workspace_id)
        if params.engine_id is not None:
            stmt = stmt.where(RuntimeJobRow.engine_id == params.engine_id)
        if params.status is not None:
            stmt = stmt.where(RuntimeJobRow.status == params.status)
        stmt = stmt.order_by(RuntimeJobRow.name.asc(), RuntimeJobRow.id.asc()).offset(params.offset).limit(params.limit)
        rows = (await self._session.execute(stmt)).scalars().all()
        return [_to_job(row) for row in rows]

    async def count_jobs(self, scope: ProjectScope, params: ListRuntimeJobsInput) -> int:
        if self._denies_all(scope):
            return 0
        stmt = select(func.count()).select_from(RuntimeJobRow)
        stmt = self._apply_scope(stmt, RuntimeJobRow.project_id, scope)
        if params.project_id is not None:
            stmt = stmt.where(RuntimeJobRow.project_id == params.project_id)
        if params.source_key is not None:
            stmt = stmt.where(RuntimeJobRow.source_key == params.source_key)
        if params.environment_id is not None:
            stmt = stmt.where(RuntimeJobRow.environment_id == params.environment_id)
        if params.workspace_id is not None:
            stmt = stmt.where(RuntimeJobRow.workspace_id == params.workspace_id)
        if params.engine_id is not None:
            stmt = stmt.where(RuntimeJobRow.engine_id == params.engine_id)
        if params.status is not None:
            stmt = stmt.where(RuntimeJobRow.status == params.status)
        return int((await self._session.execute(stmt)).scalar_one())

    async def list_artifacts(
        self,
        scope: ProjectScope,
        params: ListRuntimeArtifactsInput,
    ) -> list[RuntimeArtifact]:
        """Return optional artifact/package metadata from the runtime mirror."""
        if self._denies_all(scope):
            return []
        stmt = select(RuntimeArtifactRow)
        stmt = self._apply_scope(stmt, RuntimeArtifactRow.project_id, scope)
        if params.project_id is not None:
            stmt = stmt.where(RuntimeArtifactRow.project_id == params.project_id)
        if params.source_key is not None:
            stmt = stmt.where(RuntimeArtifactRow.source_key == params.source_key)
        if params.workspace_id is not None:
            stmt = stmt.where(RuntimeArtifactRow.workspace_id == params.workspace_id)
        if params.artifact_type is not None:
            stmt = stmt.where(RuntimeArtifactRow.artifact_type == params.artifact_type)
        stmt = stmt.order_by(RuntimeArtifactRow.name.asc(), RuntimeArtifactRow.id.asc()).offset(params.offset).limit(params.limit)
        rows = (await self._session.execute(stmt)).scalars().all()
        return [_to_artifact(row) for row in rows]

    async def list_connections(
        self,
        scope: ProjectScope,
        params: ListRuntimeConnectionsInput,
    ) -> list[RuntimeConnection]:
        """Return optional connection metadata without parameters or credentials."""
        if self._denies_all(scope):
            return []
        stmt = select(RuntimeConnectionRow)
        stmt = self._apply_scope(stmt, RuntimeConnectionRow.project_id, scope)
        if params.project_id is not None:
            stmt = stmt.where(RuntimeConnectionRow.project_id == params.project_id)
        if params.source_key is not None:
            stmt = stmt.where(RuntimeConnectionRow.source_key == params.source_key)
        if params.workspace_id is not None:
            stmt = stmt.where(RuntimeConnectionRow.workspace_id == params.workspace_id)
        if params.connection_type is not None:
            stmt = stmt.where(RuntimeConnectionRow.connection_type == params.connection_type)
        stmt = stmt.order_by(RuntimeConnectionRow.name.asc(), RuntimeConnectionRow.id.asc()).offset(params.offset).limit(params.limit)
        rows = (await self._session.execute(stmt)).scalars().all()
        return [_to_connection(row) for row in rows]

    async def list_job_dependencies(
        self,
        scope: ProjectScope,
        params: ListRuntimeDependenciesInput,
    ) -> list[RuntimeJobDependency]:
        """Return job-to-artifact/connection dependency edges."""
        if self._denies_all(scope):
            return []
        stmt = select(RuntimeJobDependencyRow)
        stmt = self._apply_scope(stmt, RuntimeJobDependencyRow.project_id, scope)
        if params.project_id is not None:
            stmt = stmt.where(RuntimeJobDependencyRow.project_id == params.project_id)
        if params.source_key is not None:
            stmt = stmt.where(RuntimeJobDependencyRow.source_key == params.source_key)
        if params.dependency_type is not None:
            stmt = stmt.where(RuntimeJobDependencyRow.dependency_type == params.dependency_type)
        if params.dependency_id is not None:
            stmt = stmt.where(RuntimeJobDependencyRow.dependency_id == params.dependency_id)
        if params.job_id is not None:
            stmt = stmt.where(RuntimeJobDependencyRow.job_id == params.job_id)
        stmt = stmt.order_by(RuntimeJobDependencyRow.job_name.asc(), RuntimeJobDependencyRow.id.asc()).offset(params.offset).limit(params.limit)
        rows = (await self._session.execute(stmt)).scalars().all()
        return [_to_job_dependency(row) for row in rows]

    async def get_dependency_summary(
        self,
        scope: ProjectScope,
        params: ListRuntimeInventoryInput,
    ) -> RuntimeDependencySummary:
        """Return availability and hygiene facts for optional extended metadata."""
        artifacts = await self.list_artifacts(
            scope,
            ListRuntimeArtifactsInput(
                project_id=params.project_id,
                source_key=params.source_key,
                limit=params.limit,
            ),
        )
        connections = await self.list_connections(
            scope,
            ListRuntimeConnectionsInput(
                project_id=params.project_id,
                source_key=params.source_key,
                limit=params.limit,
            ),
        )
        dependencies = await self.list_job_dependencies(
            scope,
            ListRuntimeDependenciesInput(
                project_id=params.project_id,
                source_key=params.source_key,
                limit=params.limit,
            ),
        )
        return RuntimeDependencySummary(
            availability=RuntimeExtendedMetadataAvailability(
                artifacts_supported=bool(artifacts),
                connections_supported=bool(connections),
                schedules_supported=False,
                promotions_supported=False,
                clusters_supported=False,
                unavailable=["schedules", "promotions", "clusters"],
            ),
            artifacts_total=len(artifacts),
            connections_total=len(connections),
            dependencies_total=len(dependencies),
            stale_artifacts=[item for item in artifacts if item.source_updated_at is None][: params.limit],
            unowned_artifacts=[item for item in artifacts if not item.owner][: params.limit],
            unowned_connections=[item for item in connections if not item.owner][: params.limit],
        )

    async def list_job_runs(
        self,
        scope: ProjectScope,
        params: ListRuntimeJobRunsInput,
    ) -> list[RuntimeJobRun]:
        """Return scoped runtime job/task run facts."""
        if self._denies_all(scope):
            return []
        stmt = select(RuntimeJobRunRow)
        stmt = self._apply_scope(stmt, RuntimeJobRunRow.project_id, scope)
        if params.project_id is not None:
            stmt = stmt.where(RuntimeJobRunRow.project_id == params.project_id)
        if params.source_key is not None:
            stmt = stmt.where(RuntimeJobRunRow.source_key == params.source_key)
        if params.job_id is not None:
            stmt = stmt.where(RuntimeJobRunRow.job_id == params.job_id)
        if params.engine_id is not None:
            stmt = stmt.where(RuntimeJobRunRow.engine_id == params.engine_id)
        if params.state is not None:
            stmt = stmt.where(RuntimeJobRunRow.state == params.state.value)
        if params.since is not None:
            stmt = stmt.where(RuntimeJobRunRow.started_at >= params.since)
        if params.until is not None:
            stmt = stmt.where(RuntimeJobRunRow.started_at <= params.until)
        stmt = stmt.order_by(RuntimeJobRunRow.started_at.desc(), RuntimeJobRunRow.id.desc()).offset(params.offset).limit(params.limit)
        rows = (await self._session.execute(stmt)).scalars().all()
        return [_to_job_run(row) for row in rows]

    async def list_job_health(
        self,
        scope: ProjectScope,
        params: ListRuntimeJobsInput,
    ) -> list[RuntimeJobHealth]:
        """Return DB-derived health grouped by runtime job."""
        if self._denies_all(scope):
            return []
        run_stmt = select(RuntimeJobRunRow)
        run_stmt = self._apply_scope(run_stmt, RuntimeJobRunRow.project_id, scope)
        job_stmt = select(RuntimeJobRow)
        job_stmt = self._apply_scope(job_stmt, RuntimeJobRow.project_id, scope)

        if params.project_id is not None:
            run_stmt = run_stmt.where(RuntimeJobRunRow.project_id == params.project_id)
            job_stmt = job_stmt.where(RuntimeJobRow.project_id == params.project_id)
        if params.source_key is not None:
            run_stmt = run_stmt.where(RuntimeJobRunRow.source_key == params.source_key)
            job_stmt = job_stmt.where(RuntimeJobRow.source_key == params.source_key)
        if params.environment_id is not None:
            run_stmt = run_stmt.where(RuntimeJobRunRow.environment_id == params.environment_id)
            job_stmt = job_stmt.where(RuntimeJobRow.environment_id == params.environment_id)
        if params.workspace_id is not None:
            run_stmt = run_stmt.where(RuntimeJobRunRow.workspace_id == params.workspace_id)
            job_stmt = job_stmt.where(RuntimeJobRow.workspace_id == params.workspace_id)
        if params.engine_id is not None:
            run_stmt = run_stmt.where(RuntimeJobRunRow.engine_id == params.engine_id)
            job_stmt = job_stmt.where(RuntimeJobRow.engine_id == params.engine_id)

        run_rows = list((await self._session.execute(run_stmt)).scalars().all())
        job_rows = list((await self._session.execute(job_stmt)).scalars().all())
        jobs_by_key = {(row.project_id, row.source_key, row.job_id): row for row in job_rows}

        grouped: dict[tuple[str, str, str], list[RuntimeJobRunRow]] = defaultdict(list)
        for row in run_rows:
            grouped[(row.project_id, row.source_key, row.job_id)].append(row)

        summaries = [
            _to_job_health(
                project_id=pid,
                source_key=source_key,
                job_id=job_id,
                runs=rows,
                job=jobs_by_key.get((pid, source_key, job_id)),
            )
            for (pid, source_key, job_id), rows in grouped.items()
        ]
        summaries.sort(
            key=lambda item: (
                item.failure_rate,
                item.current_failure_streak,
                item.latest_run_at or datetime.datetime.min.replace(tzinfo=datetime.UTC),
            ),
            reverse=True,
        )
        return summaries[: params.limit]

    async def list_plans(
        self,
        scope: ProjectScope,
        params: ListRuntimePlansInput,
    ) -> list[RuntimePlan]:
        """Return scoped current-state runtime plans."""
        if self._denies_all(scope):
            return []
        stmt = select(RuntimePlanRow)
        stmt = self._apply_scope(stmt, RuntimePlanRow.project_id, scope)
        if params.project_id is not None:
            stmt = stmt.where(RuntimePlanRow.project_id == params.project_id)
        if params.source_key is not None:
            stmt = stmt.where(RuntimePlanRow.source_key == params.source_key)
        if params.environment_id is not None:
            stmt = stmt.where(RuntimePlanRow.environment_id == params.environment_id)
        if params.workspace_id is not None:
            stmt = stmt.where(RuntimePlanRow.workspace_id == params.workspace_id)
        if params.status is not None:
            stmt = stmt.where(RuntimePlanRow.status == params.status)
        stmt = stmt.order_by(RuntimePlanRow.name.asc(), RuntimePlanRow.id.asc()).offset(params.offset).limit(params.limit)
        rows = (await self._session.execute(stmt)).scalars().all()
        return [_to_plan(row) for row in rows]

    async def count_plans(self, scope: ProjectScope, params: ListRuntimePlansInput) -> int:
        if self._denies_all(scope):
            return 0
        stmt = select(func.count()).select_from(RuntimePlanRow)
        stmt = self._apply_scope(stmt, RuntimePlanRow.project_id, scope)
        if params.project_id is not None:
            stmt = stmt.where(RuntimePlanRow.project_id == params.project_id)
        if params.source_key is not None:
            stmt = stmt.where(RuntimePlanRow.source_key == params.source_key)
        if params.environment_id is not None:
            stmt = stmt.where(RuntimePlanRow.environment_id == params.environment_id)
        if params.workspace_id is not None:
            stmt = stmt.where(RuntimePlanRow.workspace_id == params.workspace_id)
        if params.status is not None:
            stmt = stmt.where(RuntimePlanRow.status == params.status)
        return int((await self._session.execute(stmt)).scalar_one())

    async def list_plan_runs(
        self,
        scope: ProjectScope,
        params: ListRuntimePlanRunsInput,
    ) -> list[RuntimePlanRun]:
        """Return scoped runtime plan-run facts."""
        if self._denies_all(scope):
            return []
        stmt = select(RuntimePlanRunRow)
        stmt = self._apply_scope(stmt, RuntimePlanRunRow.project_id, scope)
        if params.project_id is not None:
            stmt = stmt.where(RuntimePlanRunRow.project_id == params.project_id)
        if params.source_key is not None:
            stmt = stmt.where(RuntimePlanRunRow.source_key == params.source_key)
        if params.plan_id is not None:
            stmt = stmt.where(RuntimePlanRunRow.plan_id == params.plan_id)
        if params.state is not None:
            stmt = stmt.where(RuntimePlanRunRow.state == params.state.value)
        if params.since is not None:
            stmt = stmt.where(RuntimePlanRunRow.started_at >= params.since)
        if params.until is not None:
            stmt = stmt.where(RuntimePlanRunRow.started_at <= params.until)
        stmt = stmt.order_by(RuntimePlanRunRow.started_at.desc(), RuntimePlanRunRow.id.desc()).offset(params.offset).limit(params.limit)
        rows = (await self._session.execute(stmt)).scalars().all()
        return [_to_plan_run(row) for row in rows]

    async def list_plan_health(
        self,
        scope: ProjectScope,
        params: ListRuntimePlansInput,
    ) -> list[RuntimePlanHealth]:
        """Return DB-derived health grouped by runtime plan."""
        if self._denies_all(scope):
            return []
        run_stmt = select(RuntimePlanRunRow)
        run_stmt = self._apply_scope(run_stmt, RuntimePlanRunRow.project_id, scope)
        plan_stmt = select(RuntimePlanRow)
        plan_stmt = self._apply_scope(plan_stmt, RuntimePlanRow.project_id, scope)

        if params.project_id is not None:
            run_stmt = run_stmt.where(RuntimePlanRunRow.project_id == params.project_id)
            plan_stmt = plan_stmt.where(RuntimePlanRow.project_id == params.project_id)
        if params.source_key is not None:
            run_stmt = run_stmt.where(RuntimePlanRunRow.source_key == params.source_key)
            plan_stmt = plan_stmt.where(RuntimePlanRow.source_key == params.source_key)
        if params.environment_id is not None:
            run_stmt = run_stmt.where(RuntimePlanRunRow.environment_id == params.environment_id)
            plan_stmt = plan_stmt.where(RuntimePlanRow.environment_id == params.environment_id)
        if params.workspace_id is not None:
            run_stmt = run_stmt.where(RuntimePlanRunRow.workspace_id == params.workspace_id)
            plan_stmt = plan_stmt.where(RuntimePlanRow.workspace_id == params.workspace_id)

        run_rows = list((await self._session.execute(run_stmt)).scalars().all())
        plan_rows = list((await self._session.execute(plan_stmt)).scalars().all())
        plans_by_key = {(row.project_id, row.source_key, row.plan_id): row for row in plan_rows}

        grouped: dict[tuple[str, str, str], list[RuntimePlanRunRow]] = defaultdict(list)
        for row in run_rows:
            grouped[(row.project_id, row.source_key, row.plan_id)].append(row)

        summaries = [
            _to_plan_health(
                project_id=pid,
                source_key=source_key,
                plan_id=plan_id,
                runs=rows,
                plan=plans_by_key.get((pid, source_key, plan_id)),
            )
            for (pid, source_key, plan_id), rows in grouped.items()
        ]
        summaries.sort(
            key=lambda item: (
                item.failure_rate,
                item.current_failure_streak,
                item.latest_run_at or datetime.datetime.min.replace(tzinfo=datetime.UTC),
            ),
            reverse=True,
        )
        return summaries[: params.limit]

    async def get_estate_overview(
        self,
        scope: ProjectScope,
        params: ListRuntimeInventoryInput,
    ) -> RuntimeEstateOverview:
        """Return compact scoped counts for the runtime operations overview."""
        if self._denies_all(scope):
            return RuntimeEstateOverview(
                project_id=params.project_id,
                sources_total=0,
                sources_failed=0,
                sources_partial=0,
                sources_demo=0,
                engines_total=0,
                engines_online=0,
                engines_degraded=0,
                engines_offline=0,
                engines_unknown=0,
                environments_total=0,
                workspaces_total=0,
            )

        source_stmt = select(RuntimeSourceRow)
        source_stmt = self._apply_scope(source_stmt, RuntimeSourceRow.project_id, scope)
        engine_stmt = select(RuntimeEngineRow)
        engine_stmt = self._apply_scope(engine_stmt, RuntimeEngineRow.project_id, scope)
        env_stmt = select(RuntimeEnvironmentRow)
        env_stmt = self._apply_scope(env_stmt, RuntimeEnvironmentRow.project_id, scope)
        workspace_stmt = select(RuntimeWorkspaceRow)
        workspace_stmt = self._apply_scope(workspace_stmt, RuntimeWorkspaceRow.project_id, scope)

        if params.project_id is not None:
            source_stmt = source_stmt.where(RuntimeSourceRow.project_id == params.project_id)
            engine_stmt = engine_stmt.where(RuntimeEngineRow.project_id == params.project_id)
            env_stmt = env_stmt.where(RuntimeEnvironmentRow.project_id == params.project_id)
            workspace_stmt = workspace_stmt.where(RuntimeWorkspaceRow.project_id == params.project_id)
        if params.source_key is not None:
            source_stmt = source_stmt.where(RuntimeSourceRow.source_key == params.source_key)
            engine_stmt = engine_stmt.where(RuntimeEngineRow.source_key == params.source_key)
            env_stmt = env_stmt.where(RuntimeEnvironmentRow.source_key == params.source_key)
            workspace_stmt = workspace_stmt.where(RuntimeWorkspaceRow.source_key == params.source_key)

        sources = list((await self._session.execute(source_stmt)).scalars().all())
        engines = list((await self._session.execute(engine_stmt)).scalars().all())
        environments = list((await self._session.execute(env_stmt)).scalars().all())
        workspaces = list((await self._session.execute(workspace_stmt)).scalars().all())
        synced_values = [_as_utc(row.last_synced_at) for row in sources if row.last_synced_at]
        engine_health = await self.list_engine_health(
            scope,
            ListRuntimeEnginesInput(
                project_id=params.project_id,
                source_key=params.source_key,
                limit=params.limit,
            ),
        )
        job_health = await self.list_job_health(
            scope,
            ListRuntimeJobsInput(
                project_id=params.project_id,
                source_key=params.source_key,
                limit=params.limit,
            ),
        )
        plan_health = await self.list_plan_health(
            scope,
            ListRuntimePlansInput(
                project_id=params.project_id,
                source_key=params.source_key,
                limit=params.limit,
            ),
        )
        freshness_cutoff = datetime.datetime.now(datetime.UTC) - datetime.timedelta(minutes=5)

        return RuntimeEstateOverview(
            project_id=params.project_id,
            sources_total=len(sources),
            sources_failed=sum(1 for row in sources if row.status == RuntimeSyncStatus.FAILED.value),
            sources_partial=sum(1 for row in sources if row.status == RuntimeSyncStatus.PARTIAL.value),
            sources_demo=sum(1 for row in sources if row.is_demo),
            engines_total=len(engines),
            engines_online=sum(1 for row in engines if row.status == RuntimeEngineStatus.ONLINE.value),
            engines_degraded=sum(1 for row in engines if row.status == RuntimeEngineStatus.DEGRADED.value),
            engines_offline=sum(1 for row in engines if row.status == RuntimeEngineStatus.OFFLINE.value),
            engines_unknown=sum(
                1
                for row in engines
                if row.status in {RuntimeEngineStatus.UNKNOWN.value, RuntimeEngineStatus.STALE.value}
            ),
            engines_unhealthy=sum(
                1
                for row in engine_health
                if row.failed_runs > 0 or row.current_failure_streak > 0
            ),
            jobs_unhealthy=sum(
                1
                for row in job_health
                if row.failed_runs > 0 or row.current_failure_streak > 0
            ),
            plans_unhealthy=sum(
                1
                for row in plan_health
                if row.failed_runs > 0 or row.current_failure_streak > 0
            ),
            sources_stale=sum(
                1
                for row in sources
                if row.status in {RuntimeSyncStatus.FAILED.value, RuntimeSyncStatus.PARTIAL.value}
                or (row.last_synced_at is not None and _as_utc(row.last_synced_at) < freshness_cutoff)
            ),
            environments_total=len(environments),
            workspaces_total=len(workspaces),
            last_synced_at=max(synced_values) if synced_values else None,
        )

    # --- write-port (idempotent) ---

    async def upsert_source(
        self,
        project_id: str,
        source: RuntimeSource,
        ingested_at: datetime.datetime,
    ) -> None:
        stmt = select(RuntimeSourceRow).where(
            RuntimeSourceRow.project_id == project_id,
            RuntimeSourceRow.source_key == source.source_key,
        )
        row = (await self._session.execute(stmt)).scalars().first()
        if row is None:
            row = RuntimeSourceRow(project_id=project_id, source_key=source.source_key)
            self._session.add(row)
        row.source_type = source.source_type
        row.physical_identity = f"observed:{source.source_key}"
        row.label = source.label
        row.status = source.status.value
        row.is_demo = 1 if source.is_demo else 0
        row.last_synced_at = source.last_synced_at
        row.last_seen_at = source.last_seen_at
        row.ingested_at = ingested_at
        await self._session.flush()

    async def upsert_environment(
        self,
        project_id: str,
        environment: RuntimeEnvironment,
        source_key: str,
        ingested_at: datetime.datetime,
    ) -> None:
        stmt = select(RuntimeEnvironmentRow).where(
            RuntimeEnvironmentRow.project_id == project_id,
            RuntimeEnvironmentRow.source_key == source_key,
            RuntimeEnvironmentRow.environment_id == environment.environment_id,
        )
        row = (await self._session.execute(stmt)).scalars().first()
        if row is None:
            row = RuntimeEnvironmentRow(
                project_id=project_id,
                source_key=source_key,
                environment_id=environment.environment_id,
            )
            self._session.add(row)
        row.name = environment.name
        row.description = environment.description
        row.last_seen_at = environment.last_seen_at
        row.ingested_at = ingested_at
        await self._session.flush()

    async def upsert_workspace(
        self,
        project_id: str,
        workspace: RuntimeWorkspace,
        source_key: str,
        ingested_at: datetime.datetime,
    ) -> None:
        stmt = select(RuntimeWorkspaceRow).where(
            RuntimeWorkspaceRow.project_id == project_id,
            RuntimeWorkspaceRow.source_key == source_key,
            RuntimeWorkspaceRow.workspace_id == workspace.workspace_id,
        )
        row = (await self._session.execute(stmt)).scalars().first()
        if row is None:
            row = RuntimeWorkspaceRow(
                project_id=project_id,
                source_key=source_key,
                workspace_id=workspace.workspace_id,
            )
            self._session.add(row)
        row.name = workspace.name
        row.environment_id = workspace.environment_id
        row.environment_name = workspace.environment_name
        row.owner = workspace.owner
        row.workspace_type = workspace.workspace_type
        row.last_seen_at = workspace.last_seen_at
        row.ingested_at = ingested_at
        await self._session.flush()

    async def upsert_engine(
        self,
        project_id: str,
        engine: RuntimeEngine,
        source_key: str,
        ingested_at: datetime.datetime,
    ) -> None:
        stmt = select(RuntimeEngineRow).where(
            RuntimeEngineRow.project_id == project_id,
            RuntimeEngineRow.source_key == source_key,
            RuntimeEngineRow.engine_id == engine.engine_id,
        )
        row = (await self._session.execute(stmt)).scalars().first()
        if row is None:
            row = RuntimeEngineRow(
                project_id=project_id,
                source_key=source_key,
                engine_id=engine.engine_id,
            )
            self._session.add(row)
        row.name = engine.name
        row.status = engine.status.value
        row.raw_status = engine.raw_status
        row.availability = engine.availability
        row.version = engine.version
        row.environment_id = engine.environment_id
        row.environment_name = engine.environment_name
        row.workspace_id = engine.workspace_id
        row.workspace_name = engine.workspace_name
        row.last_seen_at = engine.last_seen_at
        row.source_updated_at = engine.source_updated_at
        row.ingested_at = ingested_at
        await self._session.flush()

    async def upsert_job(
        self,
        project_id: str,
        job: RuntimeJob,
        source_key: str,
        ingested_at: datetime.datetime,
    ) -> None:
        stmt = select(RuntimeJobRow).where(
            RuntimeJobRow.project_id == project_id,
            RuntimeJobRow.source_key == source_key,
            RuntimeJobRow.job_id == job.job_id,
        )
        row = (await self._session.execute(stmt)).scalars().first()
        if row is None:
            row = RuntimeJobRow(
                project_id=project_id,
                source_key=source_key,
                job_id=job.job_id,
            )
            self._session.add(row)
        row.name = job.name
        row.display_name = job.display_name
        row.job_type = job.job_type
        row.status = job.status
        row.environment_id = job.environment_id
        row.environment_name = job.environment_name
        row.workspace_id = job.workspace_id
        row.workspace_name = job.workspace_name
        row.engine_id = job.engine_id
        row.engine_name = job.engine_name
        row.owner = job.owner
        row.artifact_name = job.artifact_name
        row.last_seen_at = job.last_seen_at
        row.source_updated_at = job.source_updated_at
        row.ingested_at = ingested_at
        await self._session.flush()

    async def upsert_artifact(
        self,
        project_id: str,
        artifact: RuntimeArtifact,
        source_key: str,
        ingested_at: datetime.datetime,
    ) -> None:
        stmt = select(RuntimeArtifactRow).where(
            RuntimeArtifactRow.project_id == project_id,
            RuntimeArtifactRow.source_key == source_key,
            RuntimeArtifactRow.artifact_id == artifact.artifact_id,
        )
        row = (await self._session.execute(stmt)).scalars().first()
        if row is None:
            row = RuntimeArtifactRow(
                project_id=project_id,
                source_key=source_key,
                artifact_id=artifact.artifact_id,
            )
            self._session.add(row)
        row.name = artifact.name
        row.artifact_type = artifact.artifact_type
        row.version = artifact.version
        row.workspace_id = artifact.workspace_id
        row.workspace_name = artifact.workspace_name
        row.owner = artifact.owner
        row.last_seen_at = artifact.last_seen_at
        row.source_updated_at = artifact.source_updated_at
        row.ingested_at = ingested_at
        await self._session.flush()

    async def upsert_connection(
        self,
        project_id: str,
        connection: RuntimeConnection,
        source_key: str,
        ingested_at: datetime.datetime,
    ) -> None:
        stmt = select(RuntimeConnectionRow).where(
            RuntimeConnectionRow.project_id == project_id,
            RuntimeConnectionRow.source_key == source_key,
            RuntimeConnectionRow.connection_id == connection.connection_id,
        )
        row = (await self._session.execute(stmt)).scalars().first()
        if row is None:
            row = RuntimeConnectionRow(
                project_id=project_id,
                source_key=source_key,
                connection_id=connection.connection_id,
            )
            self._session.add(row)
        row.name = connection.name
        row.connection_type = connection.connection_type
        row.workspace_id = connection.workspace_id
        row.workspace_name = connection.workspace_name
        row.owner = connection.owner
        row.last_seen_at = connection.last_seen_at
        row.source_updated_at = connection.source_updated_at
        row.ingested_at = ingested_at
        await self._session.flush()

    async def upsert_job_dependency(
        self,
        project_id: str,
        dependency: RuntimeJobDependency,
        source_key: str,
        ingested_at: datetime.datetime,
    ) -> None:
        stmt = select(RuntimeJobDependencyRow).where(
            RuntimeJobDependencyRow.project_id == project_id,
            RuntimeJobDependencyRow.source_key == source_key,
            RuntimeJobDependencyRow.job_id == dependency.job_id,
            RuntimeJobDependencyRow.dependency_type == dependency.dependency_type,
            RuntimeJobDependencyRow.dependency_id == dependency.dependency_id,
        )
        row = (await self._session.execute(stmt)).scalars().first()
        if row is None:
            row = RuntimeJobDependencyRow(
                project_id=project_id,
                source_key=source_key,
                job_id=dependency.job_id,
                dependency_type=dependency.dependency_type,
                dependency_id=dependency.dependency_id,
            )
            self._session.add(row)
        row.job_name = dependency.job_name
        row.dependency_name = dependency.dependency_name
        row.dependency_kind = dependency.dependency_kind
        row.workspace_id = dependency.workspace_id
        row.workspace_name = dependency.workspace_name
        row.last_seen_at = dependency.last_seen_at
        row.ingested_at = ingested_at
        await self._session.flush()

    async def upsert_job_run(
        self,
        project_id: str,
        run: RuntimeJobRun,
        source_key: str,
        ingested_at: datetime.datetime,
    ) -> None:
        stmt = select(RuntimeJobRunRow).where(
            RuntimeJobRunRow.project_id == project_id,
            RuntimeJobRunRow.source_key == source_key,
            RuntimeJobRunRow.run_id == run.run_id,
        )
        row = (await self._session.execute(stmt)).scalars().first()
        if row is None:
            row = RuntimeJobRunRow(
                project_id=project_id,
                source_key=source_key,
                run_id=run.run_id,
            )
            self._session.add(row)
        row.job_id = run.job_id
        row.job_name = run.job_name
        row.execution_id = run.execution_id
        row.state = run.state.value
        row.environment_id = run.environment_id
        row.environment_name = run.environment_name
        row.workspace_id = run.workspace_id
        row.workspace_name = run.workspace_name
        row.engine_id = run.engine_id
        row.engine_name = run.engine_name
        row.started_at = run.started_at
        row.ended_at = run.ended_at
        row.duration_ms = run.duration_ms
        row.ingested_at = ingested_at
        await self._session.flush()

    async def upsert_plan(
        self,
        project_id: str,
        plan: RuntimePlan,
        source_key: str,
        ingested_at: datetime.datetime,
    ) -> None:
        stmt = select(RuntimePlanRow).where(
            RuntimePlanRow.project_id == project_id,
            RuntimePlanRow.source_key == source_key,
            RuntimePlanRow.plan_id == plan.plan_id,
        )
        row = (await self._session.execute(stmt)).scalars().first()
        if row is None:
            row = RuntimePlanRow(
                project_id=project_id,
                source_key=source_key,
                plan_id=plan.plan_id,
            )
            self._session.add(row)
        row.name = plan.name
        row.display_name = plan.display_name
        row.status = plan.status
        row.environment_id = plan.environment_id
        row.environment_name = plan.environment_name
        row.workspace_id = plan.workspace_id
        row.workspace_name = plan.workspace_name
        row.owner = plan.owner
        row.step_count = plan.step_count
        row.steps = [step.model_dump(mode="json") for step in plan.steps]
        row.last_seen_at = plan.last_seen_at
        row.source_updated_at = plan.source_updated_at
        row.ingested_at = ingested_at
        await self._session.flush()

    async def upsert_plan_run(
        self,
        project_id: str,
        run: RuntimePlanRun,
        source_key: str,
        ingested_at: datetime.datetime,
    ) -> None:
        stmt = select(RuntimePlanRunRow).where(
            RuntimePlanRunRow.project_id == project_id,
            RuntimePlanRunRow.source_key == source_key,
            RuntimePlanRunRow.run_id == run.run_id,
        )
        row = (await self._session.execute(stmt)).scalars().first()
        if row is None:
            row = RuntimePlanRunRow(
                project_id=project_id,
                source_key=source_key,
                run_id=run.run_id,
            )
            self._session.add(row)
        row.plan_id = run.plan_id
        row.plan_name = run.plan_name
        row.state = run.state.value
        row.environment_id = run.environment_id
        row.environment_name = run.environment_name
        row.workspace_id = run.workspace_id
        row.workspace_name = run.workspace_name
        row.started_at = run.started_at
        row.ended_at = run.ended_at
        row.duration_ms = run.duration_ms
        row.step_results = [step.model_dump(mode="json") for step in run.step_results]
        row.ingested_at = ingested_at
        await self._session.flush()

    async def upsert_execution(
        self,
        project_id: str,
        execution: RuntimeExecution,
        source_key: str,
        ingested_at: datetime.datetime,
    ) -> None:
        stmt = select(RuntimeExecutionRow).where(
            RuntimeExecutionRow.project_id == project_id,
            RuntimeExecutionRow.execution_id == execution.execution_id,
        )
        row = (await self._session.execute(stmt)).scalars().first()
        if row is None:
            row = RuntimeExecutionRow(project_id=project_id, execution_id=execution.execution_id)
            self._session.add(row)
        row.job_name = execution.job_name
        row.project_name = execution.project_name
        row.state = execution.state.value
        row.started_at = execution.started_at
        row.ended_at = execution.ended_at
        row.duration_ms = execution.duration_ms
        row.source_key = source_key
        row.ingested_at = ingested_at
        await self._session.flush()

    async def upsert_failure(
        self,
        project_id: str,
        failure: RuntimeFailure,
        source_key: str,
        ingested_at: datetime.datetime,
    ) -> None:
        stmt = select(RuntimeFailureRow).where(
            RuntimeFailureRow.project_id == project_id,
            RuntimeFailureRow.execution_id == failure.execution_id,
        )
        row = (await self._session.execute(stmt)).scalars().first()
        if row is None:
            row = RuntimeFailureRow(project_id=project_id, execution_id=failure.execution_id)
            self._session.add(row)
        row.job_name = failure.job_name
        row.failed_at = failure.failed_at
        row.error_summary = redact_credentials(failure.error_summary)[:300]
        row.error_detail = redact_credentials(failure.error_detail) if failure.error_detail else None
        row.source_key = source_key
        row.ingested_at = ingested_at
        await self._session.flush()

    async def upsert_log_event(
        self,
        project_id: str,
        event: RuntimeLogEvent,
        source_key: str,
        ingested_at: datetime.datetime,
    ) -> None:
        fingerprint = log_event_fingerprint(event)
        stmt = select(RuntimeLogEventRow).where(
            RuntimeLogEventRow.project_id == project_id,
            RuntimeLogEventRow.execution_id == event.execution_id,
            RuntimeLogEventRow.line_ordinal == event.line_ordinal,
            RuntimeLogEventRow.event_fingerprint == fingerprint,
        )
        row = (await self._session.execute(stmt)).scalars().first()
        if row is None:
            row = RuntimeLogEventRow(
                project_id=project_id,
                execution_id=event.execution_id,
                event_fingerprint=fingerprint,
                line_ordinal=event.line_ordinal,
            )
            self._session.add(row)
        row.timestamp = event.timestamp
        row.severity = event.severity.value
        row.message = redact_credentials(event.message)
        row.line_ordinal = event.line_ordinal
        row.source_key = source_key
        row.ingested_at = ingested_at
        await self._session.flush()

    async def upsert_component_metric(
        self,
        project_id: str,
        metric: RuntimeComponentMetric,
        source_key: str,
        ingested_at: datetime.datetime,
    ) -> None:
        stmt = select(RuntimeComponentMetricRow).where(
            RuntimeComponentMetricRow.project_id == project_id,
            RuntimeComponentMetricRow.execution_id == metric.execution_id,
            RuntimeComponentMetricRow.component_key == metric.component_key,
            RuntimeComponentMetricRow.source_key == source_key,
        )
        row = (await self._session.execute(stmt)).scalars().first()
        if row is None:
            row = RuntimeComponentMetricRow(
                project_id=project_id,
                execution_id=metric.execution_id,
                component_key=metric.component_key,
                source_key=source_key,
            )
            self._session.add(row)
        row.component_type = metric.component_type
        row.source_connector_type = metric.source_connector_type
        row.target_connector_type = metric.target_connector_type
        row.rows_total = metric.rows_total
        row.rows_rejected = metric.rows_rejected
        row.duration_ms = metric.duration_ms
        row.measured_at = metric.measured_at
        row.ingested_at = ingested_at
        await self._session.flush()

    async def update_sync_state(
        self, source_key: str, project_id: str, last_synced_at: datetime.datetime
    ) -> None:
        await self.record_sync_state(
            source_key=source_key,
            project_id=project_id,
            status=RuntimeSyncStatus.SUCCESS,
            last_synced_at=last_synced_at,
            failure_reason_code=None,
            error_summary=None,
            updated_at=last_synced_at,
        )

    async def record_sync_state(
        self,
        *,
        source_key: str,
        project_id: str,
        status: RuntimeSyncStatus,
        last_synced_at: datetime.datetime | None,
        failure_reason_code: str | None,
        error_summary: str | None,
        updated_at: datetime.datetime,
    ) -> None:
        stmt = select(RuntimeSyncStateRow).where(
            RuntimeSyncStateRow.source_key == source_key,
            RuntimeSyncStateRow.project_id == project_id,
        )
        row = (await self._session.execute(stmt)).scalars().first()
        if row is None:
            row = RuntimeSyncStateRow(source_key=source_key, project_id=project_id)
            self._session.add(row)
        row.last_synced_at = last_synced_at
        row.status = status.value
        row.failure_reason_code = failure_reason_code
        row.error_summary = error_summary
        row.updated_at = updated_at
        await self._session.flush()


def _to_execution(row: RuntimeExecutionRow) -> RuntimeExecution:
    return RuntimeExecution(
        execution_id=row.execution_id,
        job_name=row.job_name,
        project_name=row.project_name,
        state=RuntimeExecutionState(row.state),
        started_at=_as_utc(row.started_at),
        ended_at=_as_utc(row.ended_at),
        duration_ms=row.duration_ms,
    )


def _to_failure(row: RuntimeFailureRow) -> RuntimeFailure:
    return RuntimeFailure(
        execution_id=row.execution_id,
        job_name=row.job_name,
        failed_at=_as_utc(row.failed_at),
        error_summary=row.error_summary,
        error_detail=row.error_detail,
    )


def _to_log_event(row: RuntimeLogEventRow) -> RuntimeLogEvent:
    return RuntimeLogEvent(
        execution_id=row.execution_id,
        timestamp=_as_utc(row.timestamp),
        severity=RuntimeLogSeverity(row.severity),
        message=row.message,
        line_ordinal=row.line_ordinal,
    )


def _to_sync_state(row: RuntimeSyncStateRow) -> RuntimeSyncState:
    return RuntimeSyncState(
        source_key=row.source_key,
        project_id=row.project_id,
        status=RuntimeSyncStatus(row.status),
        last_synced_at=_as_utc(row.last_synced_at),
        failure_reason_code=row.failure_reason_code,
        error_summary=row.error_summary,
        updated_at=_as_utc(row.updated_at),
    )


def _to_source(row: RuntimeSourceRow) -> RuntimeSource:
    return RuntimeSource(
        project_id=row.project_id,
        source_key=row.source_key,
        source_type=row.source_type,
        label=row.label,
        status=RuntimeSyncStatus(row.status),
        is_demo=bool(row.is_demo),
        last_synced_at=_as_utc(row.last_synced_at),
        last_seen_at=_as_utc(row.last_seen_at),
        ingested_at=_as_utc(row.ingested_at),
    )


def _to_source_health(row: RuntimeSourceHealthRow | None) -> RuntimeSourceHealth | None:
    if row is None:
        return None
    return RuntimeSourceHealth(
        last_attempt_at=_as_utc(row.last_attempt_at),
        last_success_at=_as_utc(row.last_success_at),
        last_failure_at=_as_utc(row.last_failure_at),
        consecutive_failures=row.consecutive_failures,
        last_failure_sanitized=row.last_failure_sanitized,
        total_cycles=row.total_cycles,
        total_records_ingested=row.total_records_ingested,
    )


def _to_source_read(row: RuntimeSourceRow, health: RuntimeSourceHealthRow | None) -> RuntimeSourceRead:
    return RuntimeSourceRead(
        id=row.id,
        project_id=row.project_id,
        source_key=row.source_key,
        source_type=row.source_type,
        label=row.label,
        status=row.status,
        enabled=row.enabled,
        sync_interval_seconds=row.sync_interval_seconds,
        auto_disable_threshold=row.auto_disable_threshold,
        disabled_reason=row.disabled_reason,
        is_demo=bool(row.is_demo),
        last_synced_at=_as_utc(row.last_synced_at),
        last_seen_at=_as_utc(row.last_seen_at),
        ingested_at=_as_utc(row.ingested_at),
        health=_to_source_health(health),
    )


def _to_workspace_project_map(row: RuntimeWorkspaceProjectMapRow) -> RuntimeWorkspaceProjectMap:
    return RuntimeWorkspaceProjectMap(
        id=row.id,
        source_id=row.source_id,
        container_key=row.container_key,
        container_name=row.container_name,
        project_id=row.project_id,
        created_at=_as_utc(row.created_at),
        created_by=row.created_by,
    )


def _to_engine(row: RuntimeEngineRow) -> RuntimeEngine:
    return RuntimeEngine(
        engine_id=row.engine_id,
        source_key=row.source_key,
        name=row.name,
        status=RuntimeEngineStatus(row.status),
        raw_status=row.raw_status,
        availability=row.availability,
        version=row.version,
        environment_id=row.environment_id,
        environment_name=row.environment_name,
        workspace_id=row.workspace_id,
        workspace_name=row.workspace_name,
        last_seen_at=_as_utc(row.last_seen_at),
        source_updated_at=_as_utc(row.source_updated_at),
    )


def _to_job(row: RuntimeJobRow) -> RuntimeJob:
    return RuntimeJob(
        job_id=row.job_id,
        source_key=row.source_key,
        name=row.name,
        display_name=row.display_name,
        job_type=row.job_type,
        status=row.status,
        environment_id=row.environment_id,
        environment_name=row.environment_name,
        workspace_id=row.workspace_id,
        workspace_name=row.workspace_name,
        engine_id=row.engine_id,
        engine_name=row.engine_name,
        owner=row.owner,
        artifact_name=row.artifact_name,
        last_seen_at=_as_utc(row.last_seen_at),
        source_updated_at=_as_utc(row.source_updated_at),
    )


def _to_artifact(row: RuntimeArtifactRow) -> RuntimeArtifact:
    return RuntimeArtifact(
        artifact_id=row.artifact_id,
        source_key=row.source_key,
        name=row.name,
        artifact_type=row.artifact_type,
        version=row.version,
        workspace_id=row.workspace_id,
        workspace_name=row.workspace_name,
        owner=row.owner,
        last_seen_at=_as_utc(row.last_seen_at),
        source_updated_at=_as_utc(row.source_updated_at),
    )


def _to_connection(row: RuntimeConnectionRow) -> RuntimeConnection:
    return RuntimeConnection(
        connection_id=row.connection_id,
        source_key=row.source_key,
        name=row.name,
        connection_type=row.connection_type,
        workspace_id=row.workspace_id,
        workspace_name=row.workspace_name,
        owner=row.owner,
        last_seen_at=_as_utc(row.last_seen_at),
        source_updated_at=_as_utc(row.source_updated_at),
    )


def _to_job_dependency(row: RuntimeJobDependencyRow) -> RuntimeJobDependency:
    return RuntimeJobDependency(
        job_id=row.job_id,
        job_name=row.job_name,
        source_key=row.source_key,
        dependency_type=row.dependency_type,
        dependency_id=row.dependency_id,
        dependency_name=row.dependency_name,
        dependency_kind=row.dependency_kind,
        workspace_id=row.workspace_id,
        workspace_name=row.workspace_name,
        last_seen_at=_as_utc(row.last_seen_at),
    )


def _to_job_run(row: RuntimeJobRunRow) -> RuntimeJobRun:
    return RuntimeJobRun(
        run_id=row.run_id,
        job_id=row.job_id,
        job_name=row.job_name,
        source_key=row.source_key,
        execution_id=row.execution_id,
        state=RuntimeExecutionState(row.state),
        environment_id=row.environment_id,
        environment_name=row.environment_name,
        workspace_id=row.workspace_id,
        workspace_name=row.workspace_name,
        engine_id=row.engine_id,
        engine_name=row.engine_name,
        started_at=_as_utc(row.started_at),
        ended_at=_as_utc(row.ended_at),
        duration_ms=row.duration_ms,
    )


def _to_plan(row: RuntimePlanRow) -> RuntimePlan:
    steps = []
    for raw in row.steps or []:
        if isinstance(raw, dict):
            steps.append(RuntimePlanStep.model_validate(raw))
    return RuntimePlan(
        plan_id=row.plan_id,
        source_key=row.source_key,
        name=row.name,
        display_name=row.display_name,
        status=row.status,
        environment_id=row.environment_id,
        environment_name=row.environment_name,
        workspace_id=row.workspace_id,
        workspace_name=row.workspace_name,
        owner=row.owner,
        step_count=row.step_count,
        steps=steps,
        last_seen_at=_as_utc(row.last_seen_at),
        source_updated_at=_as_utc(row.source_updated_at),
    )


def _to_plan_run(row: RuntimePlanRunRow) -> RuntimePlanRun:
    step_results = []
    for raw in row.step_results or []:
        if isinstance(raw, dict):
            step_results.append(RuntimePlanRunStepResult.model_validate(raw))
    return RuntimePlanRun(
        run_id=row.run_id,
        plan_id=row.plan_id,
        plan_name=row.plan_name,
        source_key=row.source_key,
        state=RuntimeExecutionState(row.state),
        environment_id=row.environment_id,
        environment_name=row.environment_name,
        workspace_id=row.workspace_id,
        workspace_name=row.workspace_name,
        started_at=_as_utc(row.started_at),
        ended_at=_as_utc(row.ended_at),
        duration_ms=row.duration_ms,
        step_results=step_results,
    )


def _percentile(values: list[int], percentile: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = math.ceil((percentile / 100.0) * len(ordered)) - 1
    return ordered[max(0, min(rank, len(ordered) - 1))]


def _to_health_summary(
    *,
    project_id: str,
    source_key: str,
    job_name: str,
    executions: list[RuntimeExecutionRow],
    metrics: list[RuntimeComponentMetricRow],
    incidents: list[RuntimeIncidentRow],
) -> RuntimeHealthSummary:
    ordered = sorted(executions, key=lambda row: _as_utc(row.started_at), reverse=True)
    failed = [row for row in ordered if row.state == RuntimeExecutionState.FAILED.value]
    streak = 0
    for row in ordered:
        if row.state == RuntimeExecutionState.FAILED.value:
            streak += 1
        else:
            break
    durations = [row.duration_ms for row in ordered if row.duration_ms is not None]
    rows_total_values = [row.rows_total or 0 for row in metrics if row.rows_total is not None]
    rows_rejected_values = [
        row.rows_rejected or 0 for row in metrics if row.rows_rejected is not None
    ]
    rows_total = sum(rows_total_values) if rows_total_values else None
    rows_rejected = sum(rows_rejected_values) if rows_rejected_values else None
    reject_ratio = (
        rows_rejected / rows_total
        if rows_total is not None and rows_total > 0 and rows_rejected is not None
        else None
    )
    recurrence_count = None
    signature_hash = None
    if incidents:
        latest = max(incidents, key=lambda row: _as_utc(row.first_bad_at) or datetime.datetime.min.replace(tzinfo=datetime.UTC))
        signature_hash = latest.signature_hash
        recurrence_count = sum(1 for row in incidents if row.signature_hash == signature_hash)
    return RuntimeHealthSummary(
        project_id=project_id,
        source_key=source_key,
        job_name=job_name,
        total_executions=len(ordered),
        failed_executions=len(failed),
        failure_rate=(len(failed) / len(ordered)) if ordered else 0.0,
        current_failure_streak=streak,
        last_failure_at=_as_utc(failed[0].started_at) if failed else None,
        last_execution_at=_as_utc(ordered[0].started_at) if ordered else None,
        p50_duration_ms=_percentile(durations, 50),
        p95_duration_ms=_percentile(durations, 95),
        rows_total=rows_total,
        rows_rejected=rows_rejected,
        reject_ratio=reject_ratio,
        recurrence_count=recurrence_count,
        signature_hash=signature_hash,
    )


def _to_job_health(
    *,
    project_id: str,
    source_key: str,
    job_id: str,
    runs: list[RuntimeJobRunRow],
    job: RuntimeJobRow | None,
) -> RuntimeJobHealth:
    ordered = sorted(runs, key=lambda row: _as_utc(row.started_at), reverse=True)
    failed = [row for row in ordered if row.state == RuntimeExecutionState.FAILED.value]
    streak = 0
    for row in ordered:
        if row.state == RuntimeExecutionState.FAILED.value:
            streak += 1
        else:
            break
    latest = ordered[0] if ordered else None
    fallback_name = latest.job_name if latest is not None else job_id
    return RuntimeJobHealth(
        project_id=project_id,
        source_key=source_key,
        job_id=job_id,
        job_name=job.name if job is not None else fallback_name,
        environment_name=(job.environment_name if job is not None else None)
        or (latest.environment_name if latest is not None else None),
        workspace_name=(job.workspace_name if job is not None else None)
        or (latest.workspace_name if latest is not None else None),
        engine_name=(job.engine_name if job is not None else None)
        or (latest.engine_name if latest is not None else None),
        total_runs=len(ordered),
        failed_runs=len(failed),
        failure_rate=(len(failed) / len(ordered)) if ordered else 0.0,
        current_failure_streak=streak,
        latest_run_at=_as_utc(latest.started_at) if latest is not None else None,
        latest_state=RuntimeExecutionState(latest.state) if latest is not None else None,
    )


def _to_engine_health(
    *,
    project_id: str,
    source_key: str,
    engine_id: str,
    runs: list[RuntimeJobRunRow],
    engine: RuntimeEngineRow | None,
) -> RuntimeEngineHealth:
    ordered = sorted(runs, key=lambda row: _as_utc(row.started_at), reverse=True)
    failed = [row for row in ordered if row.state == RuntimeExecutionState.FAILED.value]
    streak = 0
    for row in ordered:
        if row.state == RuntimeExecutionState.FAILED.value:
            streak += 1
        else:
            break
    latest = ordered[0] if ordered else None
    durations = [row.duration_ms for row in ordered if row.duration_ms is not None]
    fallback_name = latest.engine_name if latest is not None and latest.engine_name else engine_id
    return RuntimeEngineHealth(
        project_id=project_id,
        source_key=source_key,
        engine_id=engine_id,
        engine_name=engine.name if engine is not None else fallback_name,
        engine_status=RuntimeEngineStatus(engine.status) if engine is not None else None,
        environment_name=(engine.environment_name if engine is not None else None)
        or (latest.environment_name if latest is not None else None),
        workspace_name=(engine.workspace_name if engine is not None else None)
        or (latest.workspace_name if latest is not None else None),
        total_runs=len(ordered),
        failed_runs=len(failed),
        failure_rate=(len(failed) / len(ordered)) if ordered else 0.0,
        current_failure_streak=streak,
        latest_run_at=_as_utc(latest.started_at) if latest is not None else None,
        latest_state=RuntimeExecutionState(latest.state) if latest is not None else None,
        p50_duration_ms=_percentile(durations, 50),
        p95_duration_ms=_percentile(durations, 95),
    )


def _to_plan_health(
    *,
    project_id: str,
    source_key: str,
    plan_id: str,
    runs: list[RuntimePlanRunRow],
    plan: RuntimePlanRow | None,
) -> RuntimePlanHealth:
    ordered = sorted(runs, key=lambda row: _as_utc(row.started_at), reverse=True)
    failed = [row for row in ordered if row.state == RuntimeExecutionState.FAILED.value]
    streak = 0
    for row in ordered:
        if row.state == RuntimeExecutionState.FAILED.value:
            streak += 1
        else:
            break
    latest = ordered[0] if ordered else None
    fallback_name = latest.plan_name if latest is not None else plan_id
    return RuntimePlanHealth(
        project_id=project_id,
        source_key=source_key,
        plan_id=plan_id,
        plan_name=plan.name if plan is not None else fallback_name,
        environment_name=(plan.environment_name if plan is not None else None)
        or (latest.environment_name if latest is not None else None),
        workspace_name=(plan.workspace_name if plan is not None else None)
        or (latest.workspace_name if latest is not None else None),
        total_runs=len(ordered),
        failed_runs=len(failed),
        failure_rate=(len(failed) / len(ordered)) if ordered else 0.0,
        current_failure_streak=streak,
        latest_run_at=_as_utc(latest.started_at) if latest is not None else None,
        latest_finish_at=_as_utc(latest.ended_at) if latest is not None else None,
        latest_state=RuntimeExecutionState(latest.state) if latest is not None else None,
    )
