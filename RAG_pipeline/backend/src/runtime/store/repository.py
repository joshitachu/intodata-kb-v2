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

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.runtime_data import (
    RuntimeExecutionRow,
    RuntimeFailureRow,
    RuntimeLogEventRow,
    RuntimeSyncStateRow,
)
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
from src.runtime.store import ProjectScope, RuntimeRecordNotFoundError

# Severity ordering for min_severity filtering (low -> high).
_SEVERITY_ORDER: list[RuntimeLogSeverity] = list(RuntimeLogSeverity)


def _severities_at_or_above(minimum: RuntimeLogSeverity) -> list[str]:
    min_rank = _SEVERITY_ORDER.index(minimum)
    return [s.value for s in _SEVERITY_ORDER[min_rank:]]


def log_event_fingerprint(event: RuntimeLogEvent) -> str:
    """Deterministic fingerprint for log idempotency (no natural unique id upstream)."""
    raw = f"{event.timestamp.isoformat()}|{event.severity.value}|{event.message}"
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
        if params.since:
            stmt = stmt.where(RuntimeExecutionRow.started_at >= params.since)
        if params.until:
            stmt = stmt.where(RuntimeExecutionRow.started_at <= params.until)
        stmt = stmt.order_by(RuntimeExecutionRow.started_at.desc()).limit(params.limit)
        rows = (await self._session.execute(stmt)).scalars().all()
        return [_to_execution(row) for row in rows]

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
        stmt = stmt.order_by(RuntimeFailureRow.failed_at.desc()).limit(params.limit)
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
        stmt = stmt.order_by(RuntimeLogEventRow.timestamp.asc()).limit(params.limit)
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
        values = (await self._session.execute(stmt)).scalars().all()
        if not values:
            return None
        return _as_utc(max(values))

    # --- write-port (idempotent) ---

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
        row.error_summary = failure.error_summary
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
            RuntimeLogEventRow.event_fingerprint == fingerprint,
        )
        row = (await self._session.execute(stmt)).scalars().first()
        if row is None:
            row = RuntimeLogEventRow(
                project_id=project_id,
                execution_id=event.execution_id,
                event_fingerprint=fingerprint,
            )
            self._session.add(row)
        row.timestamp = event.timestamp
        row.severity = event.severity.value
        row.message = event.message
        row.source_key = source_key
        row.ingested_at = ingested_at
        await self._session.flush()

    async def update_sync_state(
        self, source_key: str, project_id: str, last_synced_at: datetime.datetime
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
    )


def _to_log_event(row: RuntimeLogEventRow) -> RuntimeLogEvent:
    return RuntimeLogEvent(
        execution_id=row.execution_id,
        timestamp=_as_utc(row.timestamp),
        severity=RuntimeLogSeverity(row.severity),
        message=row.message,
    )
