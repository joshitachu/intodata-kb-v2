"""Read-only repository for the runtime insight layer.

Project scope is enforced at the query level on every read. No query parameter
or caller-supplied value may widen the authenticated scope.

Scope semantics match the held runtime store:
  None  -> unrestricted (superadmin / view-all)
  []    -> deny all — every method returns empty or raises RuntimeRecordNotFoundError
  [ids] -> restrict to those project_ids

Point lookups (get_execution_summary, get_incident) raise RuntimeRecordNotFoundError
for both unknown and out-of-scope records so callers cannot distinguish the two.

Timeline and evidence reads use a two-step gate: verify the parent record is in scope
(→ 404 if not), then return an empty list if no matching rows exist (→ 200 empty).
"""

from __future__ import annotations

import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.runtime_data import RuntimeExecutionRow, RuntimeLogEventRow
from src.db.models.runtime_insights import (
    RuntimeEnrichedEventRow,
    RuntimeIncidentEvidenceRow,
    RuntimeIncidentRow,
    RuntimeExecutionSummaryRow,
)
from src.runtime.insights.models import (
    RuntimeExecutionSummaryInsight,
    RuntimeIncidentEvidence,
    RuntimeIncidentInsight,
    RuntimeTimelineEvent,
)
from src.runtime.store import ProjectScope, RuntimeRecordNotFoundError

# Severity ordering for min_severity filtering (lowest -> highest).
_SEVERITY_ORDER = ["debug", "info", "warning", "error"]


def _severities_at_or_above(minimum: str) -> list[str]:
    try:
        min_rank = _SEVERITY_ORDER.index(minimum.lower())
    except ValueError:
        return _SEVERITY_ORDER[:]
    return _SEVERITY_ORDER[min_rank:]


def _as_utc(value: datetime.datetime | None) -> datetime.datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=datetime.UTC)
    return value.astimezone(datetime.UTC)


class RuntimeInsightRepository:
    """Async repository for runtime insight reads. All reads are project-scoped."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @staticmethod
    def _denies_all(scope: ProjectScope) -> bool:
        return scope is not None and len(scope) == 0

    @staticmethod
    def _apply_scope(stmt, column, scope: ProjectScope):
        if scope is None:
            return stmt
        return stmt.where(column.in_(scope))

    # -------------------------------------------------------------------------
    # Execution summary
    # -------------------------------------------------------------------------

    async def get_execution_summary(
        self, scope: ProjectScope, execution_id: str
    ) -> RuntimeExecutionSummaryInsight:
        """Return a joined execution summary for an in-scope execution.

        Raises RuntimeRecordNotFoundError if the summary does not exist or is
        out of scope. The insight layer may not have been built for every execution,
        so absence of a summary row is treated as not-found.
        """
        if self._denies_all(scope):
            raise RuntimeRecordNotFoundError(f"Execution not found: {execution_id}")

        stmt = select(RuntimeExecutionSummaryRow).where(
            RuntimeExecutionSummaryRow.execution_id == execution_id
        )
        stmt = self._apply_scope(stmt, RuntimeExecutionSummaryRow.project_id, scope)
        summary_row = (await self._session.execute(stmt)).scalars().first()
        if summary_row is None:
            raise RuntimeRecordNotFoundError(f"Execution not found: {execution_id}")

        exec_row = await self._load_execution(summary_row.project_id, execution_id)
        inc_row = await self._load_incident_by_execution(summary_row.project_id, execution_id)

        return RuntimeExecutionSummaryInsight(
            project_id=summary_row.project_id,
            execution_id=execution_id,
            job_name=summary_row.job_name,
            warning_count=summary_row.warning_count,
            error_count=summary_row.error_count,
            first_warning_at=_as_utc(summary_row.first_warning_at),
            first_error_at=_as_utc(summary_row.first_error_at),
            final_error_at=_as_utc(summary_row.final_error_at),
            failed_component=summary_row.failed_component,
            primary_error_code=summary_row.primary_error_code,
            primary_exception_class=summary_row.primary_exception_class,
            summary_text=summary_row.summary_text,
            evidence_refs=summary_row.evidence_refs,
            state=exec_row.state if exec_row else None,
            started_at=_as_utc(exec_row.started_at) if exec_row else None,
            ended_at=_as_utc(exec_row.ended_at) if exec_row else None,
            duration_ms=exec_row.duration_ms if exec_row else None,
            project_name=exec_row.project_name if exec_row else None,
            incident_id=inc_row.incident_id if inc_row else None,
            has_incident=inc_row is not None,
        )

    # -------------------------------------------------------------------------
    # Timeline
    # -------------------------------------------------------------------------

    async def get_execution_timeline(
        self,
        scope: ProjectScope,
        execution_id: str,
        min_severity: str | None = None,
        limit: int = 200,
    ) -> list[RuntimeTimelineEvent]:
        """Return chronological log events enriched with parsed insight fields.

        Raises RuntimeRecordNotFoundError if the execution is unknown or out of scope.
        Returns an empty list if the execution is in scope but has no matching log lines.
        """
        if self._denies_all(scope):
            raise RuntimeRecordNotFoundError(f"Execution not found: {execution_id}")

        exec_row = await self._load_scoped_execution(scope, execution_id)
        if exec_row is None:
            raise RuntimeRecordNotFoundError(f"Execution not found: {execution_id}")

        project_id = exec_row.project_id

        log_stmt = (
            select(RuntimeLogEventRow)
            .where(
                RuntimeLogEventRow.project_id == project_id,
                RuntimeLogEventRow.execution_id == execution_id,
            )
            .order_by(RuntimeLogEventRow.timestamp.asc())
            .limit(limit)
        )
        if min_severity is not None:
            log_stmt = log_stmt.where(
                RuntimeLogEventRow.severity.in_(_severities_at_or_above(min_severity))
            )
        log_rows = list((await self._session.execute(log_stmt)).scalars().all())

        if not log_rows:
            return []

        log_ids = [row.id for row in log_rows]
        enriched_stmt = select(RuntimeEnrichedEventRow).where(
            RuntimeEnrichedEventRow.project_id == project_id,
            RuntimeEnrichedEventRow.log_event_id.in_(log_ids),
        )
        enriched_rows = (await self._session.execute(enriched_stmt)).scalars().all()
        enriched_map = {row.log_event_id: row for row in enriched_rows}

        return [
            RuntimeTimelineEvent(
                log_event_id=log_row.id,
                timestamp=_as_utc(log_row.timestamp),
                severity=log_row.severity,
                message=log_row.message,
                component_name=enriched_map[log_row.id].component_name if log_row.id in enriched_map else None,
                error_code=enriched_map[log_row.id].error_code if log_row.id in enriched_map else None,
                exception_class=enriched_map[log_row.id].exception_class if log_row.id in enriched_map else None,
                retry_attempt=enriched_map[log_row.id].retry_attempt if log_row.id in enriched_map else None,
                retry_max=enriched_map[log_row.id].retry_max if log_row.id in enriched_map else None,
                confidence=enriched_map[log_row.id].confidence if log_row.id in enriched_map else None,
                message_family=enriched_map[log_row.id].message_family if log_row.id in enriched_map else None,
            )
            for log_row in log_rows
        ]

    # -------------------------------------------------------------------------
    # Incidents
    # -------------------------------------------------------------------------

    async def list_incidents(
        self,
        scope: ProjectScope,
        since: datetime.datetime | None = None,
        until: datetime.datetime | None = None,
        limit: int = 50,
    ) -> list[RuntimeIncidentInsight]:
        """Return scoped incidents ordered by first_bad_at descending."""
        if self._denies_all(scope):
            return []

        stmt = select(RuntimeIncidentRow)
        stmt = self._apply_scope(stmt, RuntimeIncidentRow.project_id, scope)
        if since is not None:
            stmt = stmt.where(RuntimeIncidentRow.first_bad_at >= since)
        if until is not None:
            stmt = stmt.where(RuntimeIncidentRow.first_bad_at <= until)
        stmt = stmt.order_by(RuntimeIncidentRow.first_bad_at.desc()).limit(limit)
        inc_rows = list((await self._session.execute(stmt)).scalars().all())

        if not inc_rows:
            return []

        execution_ids = list({row.execution_id for row in inc_rows})
        exec_stmt = select(RuntimeExecutionRow).where(
            RuntimeExecutionRow.execution_id.in_(execution_ids)
        )
        exec_stmt = self._apply_scope(exec_stmt, RuntimeExecutionRow.project_id, scope)
        exec_rows = (await self._session.execute(exec_stmt)).scalars().all()
        exec_map = {(row.project_id, row.execution_id): row for row in exec_rows}

        return [
            _to_incident_insight(row, exec_map.get((row.project_id, row.execution_id)))
            for row in inc_rows
        ]

    async def get_incident(
        self, scope: ProjectScope, incident_id: str
    ) -> RuntimeIncidentInsight:
        """Return a scoped incident by incident_id.

        Raises RuntimeRecordNotFoundError for unknown or out-of-scope incident_ids.
        """
        if self._denies_all(scope):
            raise RuntimeRecordNotFoundError(f"Incident not found: {incident_id}")

        stmt = select(RuntimeIncidentRow).where(
            RuntimeIncidentRow.incident_id == incident_id
        )
        stmt = self._apply_scope(stmt, RuntimeIncidentRow.project_id, scope)
        row = (await self._session.execute(stmt)).scalars().first()
        if row is None:
            raise RuntimeRecordNotFoundError(f"Incident not found: {incident_id}")

        exec_row = await self._load_execution(row.project_id, row.execution_id)
        return _to_incident_insight(row, exec_row)

    async def get_incident_evidence(
        self, scope: ProjectScope, incident_id: str, limit: int = 20
    ) -> list[RuntimeIncidentEvidence]:
        """Return bounded evidence rows for an in-scope incident.

        Raises RuntimeRecordNotFoundError if the incident is unknown or out of scope.
        Returns an empty list if the incident exists but has no evidence rows.
        """
        if self._denies_all(scope):
            raise RuntimeRecordNotFoundError(f"Incident not found: {incident_id}")

        inc_stmt = select(RuntimeIncidentRow).where(
            RuntimeIncidentRow.incident_id == incident_id
        )
        inc_stmt = self._apply_scope(inc_stmt, RuntimeIncidentRow.project_id, scope)
        inc_row = (await self._session.execute(inc_stmt)).scalars().first()
        if inc_row is None:
            raise RuntimeRecordNotFoundError(f"Incident not found: {incident_id}")

        ev_stmt = (
            select(RuntimeIncidentEvidenceRow)
            .where(
                RuntimeIncidentEvidenceRow.project_id == inc_row.project_id,
                RuntimeIncidentEvidenceRow.incident_id == incident_id,
            )
            .limit(limit)
        )
        ev_rows = list((await self._session.execute(ev_stmt)).scalars().all())

        if not ev_rows:
            return []

        log_ids = [ev.log_event_id for ev in ev_rows]
        log_stmt = select(RuntimeLogEventRow).where(RuntimeLogEventRow.id.in_(log_ids))
        log_rows = (await self._session.execute(log_stmt)).scalars().all()
        log_map = {row.id: row for row in log_rows}

        result: list[RuntimeIncidentEvidence] = []
        for ev in ev_rows:
            log_row = log_map.get(ev.log_event_id)
            if log_row is None:
                continue
            result.append(
                RuntimeIncidentEvidence(
                    log_event_id=ev.log_event_id,
                    evidence_role=ev.evidence_role,
                    excerpt=ev.excerpt,
                    timestamp=_as_utc(log_row.timestamp),
                    severity=log_row.severity,
                )
            )
        return result

    # -------------------------------------------------------------------------
    # Private load helpers
    # -------------------------------------------------------------------------

    async def _load_execution(
        self, project_id: str, execution_id: str
    ) -> RuntimeExecutionRow | None:
        stmt = select(RuntimeExecutionRow).where(
            RuntimeExecutionRow.project_id == project_id,
            RuntimeExecutionRow.execution_id == execution_id,
        )
        return (await self._session.execute(stmt)).scalars().first()

    async def _load_scoped_execution(
        self, scope: ProjectScope, execution_id: str
    ) -> RuntimeExecutionRow | None:
        stmt = select(RuntimeExecutionRow).where(
            RuntimeExecutionRow.execution_id == execution_id
        )
        stmt = self._apply_scope(stmt, RuntimeExecutionRow.project_id, scope)
        return (await self._session.execute(stmt)).scalars().first()

    async def _load_incident_by_execution(
        self, project_id: str, execution_id: str
    ) -> RuntimeIncidentRow | None:
        stmt = select(RuntimeIncidentRow).where(
            RuntimeIncidentRow.project_id == project_id,
            RuntimeIncidentRow.execution_id == execution_id,
        )
        return (await self._session.execute(stmt)).scalars().first()


# -------------------------------------------------------------------------
# Row-to-model helpers
# -------------------------------------------------------------------------


def _to_incident_insight(
    row: RuntimeIncidentRow,
    exec_row: RuntimeExecutionRow | None,
) -> RuntimeIncidentInsight:
    return RuntimeIncidentInsight(
        project_id=row.project_id,
        incident_id=row.incident_id,
        execution_id=row.execution_id,
        signature_hash=row.signature_hash,
        symptom_summary=row.symptom_summary,
        root_cause_candidate=row.root_cause_candidate,
        confidence=row.confidence,
        first_bad_at=_as_utc(row.first_bad_at),
        last_healthy_at=_as_utc(row.last_healthy_at),
        next_checks=row.next_checks or [],
        job_name=exec_row.job_name if exec_row else None,
        state=exec_row.state if exec_row else None,
    )
