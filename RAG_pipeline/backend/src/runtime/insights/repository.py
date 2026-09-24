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

Free-text fields are redacted on the way out as well as on the way in — see
"Read-path redaction" below for which ones and why.
"""

from __future__ import annotations

import datetime
import hashlib
from collections.abc import Sequence

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.runtime_data import (
    RuntimeEngineRow,
    RuntimeExecutionRow,
    RuntimeJobRow,
    RuntimeJobRunRow,
    RuntimeLogEventRow,
    RuntimePlanRow,
    RuntimePlanRunRow,
    RuntimeSourceRow,
)
from src.db.models.runtime_insights import (
    RuntimeEnrichedEventRow,
    RuntimeErrorSignatureRow,
    RuntimeIncidentEvidenceRow,
    RuntimeIncidentRow,
    RuntimeExecutionSummaryRow,
)
from src.runtime.insights.models import (
    RuntimeAlertReadyMetadata,
    RuntimeExecutionSummaryInsight,
    RuntimeFailureEstateContext,
    RuntimeIncidentEvidence,
    RuntimeIncidentInsight,
    RuntimeSimilarFailures,
    RuntimeSimilarIncident,
    RuntimeTimelineEvent,
)
from src.runtime.insights.parser import redact_log_text
from src.runtime.store import ProjectScope, RuntimeRecordNotFoundError

# Severity ordering for min_severity filtering (lowest -> highest).
_SEVERITY_ORDER = ["debug", "info", "warning", "error"]

#: How far back the plan-run window reaches when linking an execution to the plan
#: run that contains it. `runtime_plan_runs` has no execution_id column -- the link
#: is inside `step_results` -- so the match is a bounded scan rather than a lookup.
_PLAN_RUN_MATCH_WINDOW = 50


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


# ---------------------------------------------------------------------------
# Read-path redaction
# ---------------------------------------------------------------------------
#
# The write path is the primary fix, but it only cleans rows written AFTER it
# lands. Every field below already exists on disk in its pre-fix form, and every
# one of them is read straight into an LLM tool result by
# `providers/stored.py` (`analyze_execution_failure`, `get_runtime_incident`,
# `find_similar_failures`). Redacting on read is the only protection that reaches
# data already stored without a migration, and it stays valuable afterwards as
# defense-in-depth against a future write path that forgets.
#
# Safe to layer because `redact_log_text` is idempotent: applied to a row the
# fixed write path produced, it returns that row's text byte for byte.
#
# Covered: `causal_message` (the runtime's own sentence), `root_cause_candidate`
# (a catalog template with a `{host}` slot, or a quoted runtime sentence),
# `next_checks` (same templates) and `excerpt` (a slice of a raw provider line).
#
# Deliberately NOT redacted here: `summary_text` (execution and signature) and
# `symptom_summary`. Those are composed by `builder._build_summary_text` /
# `_build_symptom_summary` from structured fields alone -- job name, component,
# error code, exception class -- and never carry provider prose or an address.
# Running the entity pass over them would risk a lowercase dotted job name being
# read as an FQDN in exchange for nothing.


def _redacted(value: str | None) -> str | None:
    """Redact one stored free-text field on its way out of the repository."""
    return redact_log_text(value)


def _redacted_list(values: list | None) -> list[str]:
    """Redact a stored list of free-text checks (``next_checks``)."""
    return [redact_log_text(str(item)) or "" for item in (values or [])]


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
        estate_context = await self._load_estate_context(summary_row.project_id, execution_id, exec_row)

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
            causal_message=_redacted(summary_row.causal_message),
            evidence_refs=summary_row.evidence_refs,
            state=exec_row.state if exec_row else None,
            started_at=_as_utc(exec_row.started_at) if exec_row else None,
            ended_at=_as_utc(exec_row.ended_at) if exec_row else None,
            duration_ms=exec_row.duration_ms if exec_row else None,
            project_name=exec_row.project_name if exec_row else None,
            incident_id=inc_row.incident_id if inc_row else None,
            has_incident=inc_row is not None,
            estate_context=estate_context,
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
                signal=_build_timeline_signal(enriched_map.get(log_row.id), log_row.severity),
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
        offset: int = 0,
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
        stmt = stmt.order_by(
            RuntimeIncidentRow.first_bad_at.desc(), RuntimeIncidentRow.id.desc()
        ).offset(offset).limit(limit)
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

        # One batched estate lookup for the whole page. Per-incident lookups made
        # this endpoint issue six queries an incident -- 300 for a 50-row page --
        # one of which read the 50 newest `runtime_plan_runs` rows WITH their
        # `step_results` JSON, so a page of 50 de-TOASTed and deserialized that
        # same JSON 50 times over.
        contexts = await self._load_estate_contexts(
            [
                (row.project_id, row.execution_id, exec_map.get((row.project_id, row.execution_id)))
                for row in inc_rows
            ]
        )
        return [
            _to_incident_insight(
                row,
                exec_map.get((row.project_id, row.execution_id)),
                contexts[(row.project_id, row.execution_id)],
            )
            for row in inc_rows
        ]

    async def count_incidents(
        self,
        scope: ProjectScope,
        since: datetime.datetime | None = None,
        until: datetime.datetime | None = None,
    ) -> int:
        if self._denies_all(scope):
            return 0
        stmt = select(func.count()).select_from(RuntimeIncidentRow)
        stmt = self._apply_scope(stmt, RuntimeIncidentRow.project_id, scope)
        if since is not None:
            stmt = stmt.where(RuntimeIncidentRow.first_bad_at >= since)
        if until is not None:
            stmt = stmt.where(RuntimeIncidentRow.first_bad_at <= until)
        return int((await self._session.execute(stmt)).scalar_one())

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
        estate_context = await self._load_estate_context(row.project_id, row.execution_id, exec_row)
        return _to_incident_insight(row, exec_row, estate_context)

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
                    excerpt=_redacted(ev.excerpt),
                    timestamp=_as_utc(log_row.timestamp),
                    severity=log_row.severity,
                )
            )
        return result

    # -------------------------------------------------------------------------
    # Similar failures
    # -------------------------------------------------------------------------

    async def get_similar_failures_for_incident(
        self, scope: ProjectScope, incident_id: str, limit: int = 10
    ) -> RuntimeSimilarFailures:
        """Return recurrence info for an in-scope incident.

        Raises RuntimeRecordNotFoundError if incident_id is unknown or out of scope.
        Returns occurrence_count=1, is_recurring=False with an empty similar list when
        no signature hash is stored on the incident.
        """
        if self._denies_all(scope):
            raise RuntimeRecordNotFoundError(f"Incident not found: {incident_id}")

        inc_stmt = select(RuntimeIncidentRow).where(
            RuntimeIncidentRow.incident_id == incident_id
        )
        inc_stmt = self._apply_scope(inc_stmt, RuntimeIncidentRow.project_id, scope)
        parent_row = (await self._session.execute(inc_stmt)).scalars().first()
        if parent_row is None:
            raise RuntimeRecordNotFoundError(f"Incident not found: {incident_id}")

        project_id = parent_row.project_id
        signature_hash = parent_row.signature_hash

        if signature_hash is None:
            fingerprint = incident_id
            return RuntimeSimilarFailures(
                incident_id=incident_id,
                execution_id=parent_row.execution_id,
                recurrence=RuntimeAlertReadyMetadata(
                    signature_hash=None,
                    signature_summary=None,
                    occurrence_count=1,
                    similar_count=0,
                    first_seen_at=_as_utc(parent_row.first_bad_at),
                    last_seen_at=_as_utc(parent_row.first_bad_at),
                    is_recurring=False,
                    alert_fingerprint=fingerprint,
                    dedupe_key=fingerprint,
                    alert_title="Unknown failure pattern",
                    alert_reason="No signature could be derived for this incident.",
                ),
                similar_incidents=[],
            )

        sig_stmt = select(RuntimeErrorSignatureRow).where(
            RuntimeErrorSignatureRow.project_id == project_id,
            RuntimeErrorSignatureRow.signature_hash == signature_hash,
        )
        sig_row = (await self._session.execute(sig_stmt)).scalars().first()

        similar_stmt = (
            select(RuntimeIncidentRow)
            .where(
                RuntimeIncidentRow.project_id == project_id,
                RuntimeIncidentRow.signature_hash == signature_hash,
                RuntimeIncidentRow.incident_id != incident_id,
            )
            .order_by(RuntimeIncidentRow.first_bad_at.desc())
            .limit(limit)
        )
        similar_rows = list((await self._session.execute(similar_stmt)).scalars().all())

        similar_exec_map: dict[str, RuntimeExecutionRow] = {}
        if similar_rows:
            exec_ids = [row.execution_id for row in similar_rows]
            exec_stmt = select(RuntimeExecutionRow).where(
                RuntimeExecutionRow.execution_id.in_(exec_ids),
                RuntimeExecutionRow.project_id == project_id,
            )
            exec_rows = (await self._session.execute(exec_stmt)).scalars().all()
            similar_exec_map = {row.execution_id: row for row in exec_rows}

        similar_incidents = [
            RuntimeSimilarIncident(
                incident_id=row.incident_id,
                execution_id=row.execution_id,
                job_name=similar_exec_map[row.execution_id].job_name
                if row.execution_id in similar_exec_map
                else None,
                state=similar_exec_map[row.execution_id].state
                if row.execution_id in similar_exec_map
                else None,
                first_bad_at=_as_utc(row.first_bad_at),
                symptom_summary=row.symptom_summary,
                root_cause_candidate=_redacted(row.root_cause_candidate),
                confidence=row.confidence,
            )
            for row in similar_rows
        ]

        occurrence_count = sig_row.occurrence_count if sig_row else 1
        similar_count = len(similar_incidents)
        is_recurring = occurrence_count > 1
        alert_fingerprint = hashlib.sha256(
            f"{project_id}:{signature_hash}".encode()
        ).hexdigest()[:32]
        dedupe_key = f"{project_id[:8]}:{signature_hash[:16]}"
        sig_summary = sig_row.summary_text if sig_row else None
        alert_title = sig_summary or "Unknown failure pattern"
        alert_reason = (
            f"Seen {occurrence_count} time(s) in this project."
            if is_recurring
            else "First occurrence of this failure pattern in this project."
        )

        return RuntimeSimilarFailures(
            incident_id=incident_id,
            execution_id=parent_row.execution_id,
            recurrence=RuntimeAlertReadyMetadata(
                signature_hash=signature_hash,
                signature_summary=sig_summary,
                occurrence_count=occurrence_count,
                similar_count=similar_count,
                first_seen_at=_as_utc(sig_row.first_seen_at) if sig_row else None,
                last_seen_at=_as_utc(sig_row.last_seen_at) if sig_row else None,
                is_recurring=is_recurring,
                alert_fingerprint=alert_fingerprint,
                dedupe_key=dedupe_key,
                alert_title=alert_title,
                alert_reason=alert_reason,
            ),
            similar_incidents=similar_incidents,
        )

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

    async def _load_estate_context(
        self,
        project_id: str,
        execution_id: str,
        exec_row: RuntimeExecutionRow | None,
    ) -> RuntimeFailureEstateContext:
        """The estate context for a single execution.

        Delegates to the batch loader so the point lookup and the list page
        resolve the estate by exactly one rule.
        """
        contexts = await self._load_estate_contexts([(project_id, execution_id, exec_row)])
        return contexts[(project_id, execution_id)]

    async def _load_estate_contexts(
        self,
        requests: Sequence[tuple[str, str, RuntimeExecutionRow | None]],
    ) -> dict[tuple[str, str], RuntimeFailureEstateContext]:
        """Estate context for many executions, in a fixed number of queries.

        Each request is `(project_id, execution_id, exec_row)`; the result is
        keyed by `(project_id, execution_id)` and always has an entry for every
        request, because an execution with no inventory at all still gets an
        all-None context.

        The resolution order is the same chain a single lookup walked -- job run,
        then job, then engine, then source, then plan run, then plan -- because
        each step's key comes from the one before it. What changed is that every
        step now runs once for the whole batch instead of once per execution.
        """
        if not requests:
            return {}
        keys = [(project_id, execution_id) for project_id, execution_id, _ in requests]
        exec_rows = {key: exec_row for key, (_, _, exec_row) in zip(keys, requests, strict=True)}
        project_ids = sorted({project_id for project_id, _ in keys})
        execution_ids = sorted({execution_id for _, execution_id in keys})

        job_runs = await self._batch_job_runs(project_ids, execution_ids)
        # A job run is the authority on which source owns the execution; the
        # execution row is the fallback, exactly as in the single lookup.
        source_keys = {
            key: (job_runs[key].source_key if key in job_runs else None)
            or (exec_rows[key].source_key if exec_rows[key] else None)
            for key in keys
        }
        jobs = await self._batch_jobs(keys, job_runs, exec_rows, source_keys)
        engines = await self._batch_engines(keys, job_runs, jobs, source_keys)
        sources = await self._batch_sources(project_ids, source_keys)
        plan_runs = await self._batch_plan_runs(keys, source_keys)
        plans = await self._batch_plans(keys, plan_runs, source_keys)

        return {
            key: self._build_estate_context(
                source_key=source_keys[key],
                exec_row=exec_rows[key],
                job_run=job_runs.get(key),
                job=jobs.get(key),
                engine=engines.get(key),
                source=sources.get(key),
                plan_run=plan_runs.get(key),
                plan=plans.get(key),
            )
            for key in keys
        }

    async def _batch_job_runs(
        self, project_ids: Sequence[str], execution_ids: Sequence[str]
    ) -> dict[tuple[str, str], RuntimeJobRunRow]:
        stmt = (
            select(RuntimeJobRunRow)
            .where(
                RuntimeJobRunRow.project_id.in_(project_ids),
                RuntimeJobRunRow.execution_id.in_(execution_ids),
            )
            # `execution_id` is not unique per project, and the single lookup took
            # whatever the unordered scan returned first. Ordering by `id` states
            # that choice instead of inheriting it from the query plan.
            .order_by(RuntimeJobRunRow.id.asc())
        )
        return self._first_by_key(
            (await self._session.execute(stmt)).scalars().all(),
            lambda row: (row.project_id, row.execution_id),
        )

    async def _batch_jobs(
        self,
        keys: Sequence[tuple[str, str]],
        job_runs: dict[tuple[str, str], RuntimeJobRunRow],
        exec_rows: dict[tuple[str, str], RuntimeExecutionRow | None],
        source_keys: dict[tuple[str, str], str | None],
    ) -> dict[tuple[str, str], RuntimeJobRow]:
        """Jobs matched by id where a run named one, and by name otherwise.

        Both lookups in one statement: the single-lookup version chose between
        them per execution, so a batch that only supported one would silently
        lose the estate for every execution the other branch covered.
        """
        wanted_job_ids = {
            job_runs[key].job_id for key in keys if key in job_runs and source_keys[key]
        }
        wanted_names = {
            exec_rows[key].job_name
            for key in keys
            if key not in job_runs and exec_rows[key] is not None and source_keys[key]
        }
        if not wanted_job_ids and not wanted_names:
            return {}
        alternatives = []
        if wanted_job_ids:
            alternatives.append(RuntimeJobRow.job_id.in_(sorted(wanted_job_ids)))
        if wanted_names:
            alternatives.append(RuntimeJobRow.name.in_(sorted(wanted_names)))
        stmt = (
            select(RuntimeJobRow)
            .where(
                RuntimeJobRow.project_id.in_(
                    sorted({project_id for project_id, _ in keys})
                ),
                RuntimeJobRow.source_key.in_(
                    sorted({value for value in source_keys.values() if value})
                ),
                or_(*alternatives),
            )
            .order_by(RuntimeJobRow.id.asc())
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        by_id = self._first_by_key(
            rows, lambda row: (row.project_id, row.source_key, row.job_id)
        )
        by_name = self._first_by_key(
            rows, lambda row: (row.project_id, row.source_key, row.name)
        )
        resolved: dict[tuple[str, str], RuntimeJobRow] = {}
        for key in keys:
            project_id, _ = key
            source_key = source_keys[key]
            if source_key is None:
                continue
            job_run = job_runs.get(key)
            if job_run is not None:
                match = by_id.get((project_id, source_key, job_run.job_id))
            elif exec_rows[key] is not None:
                match = by_name.get((project_id, source_key, exec_rows[key].job_name))
            else:
                match = None
            if match is not None:
                resolved[key] = match
        return resolved

    async def _batch_engines(
        self,
        keys: Sequence[tuple[str, str]],
        job_runs: dict[tuple[str, str], RuntimeJobRunRow],
        jobs: dict[tuple[str, str], RuntimeJobRow],
        source_keys: dict[tuple[str, str], str | None],
    ) -> dict[tuple[str, str], RuntimeEngineRow]:
        engine_ids: dict[tuple[str, str], str | None] = {}
        for key in keys:
            job_run = job_runs.get(key)
            job = jobs.get(key)
            engine_ids[key] = _first_present(
                job_run.engine_id if job_run else None,
                job.engine_id if job else None,
            )
        wanted = {
            engine_id
            for key, engine_id in engine_ids.items()
            if engine_id is not None and source_keys[key]
        }
        if not wanted:
            return {}
        stmt = (
            select(RuntimeEngineRow)
            .where(
                RuntimeEngineRow.project_id.in_(
                    sorted({project_id for project_id, _ in keys})
                ),
                RuntimeEngineRow.source_key.in_(
                    sorted({value for value in source_keys.values() if value})
                ),
                RuntimeEngineRow.engine_id.in_(sorted(wanted)),
            )
            .order_by(RuntimeEngineRow.id.asc())
        )
        by_key = self._first_by_key(
            (await self._session.execute(stmt)).scalars().all(),
            lambda row: (row.project_id, row.source_key, row.engine_id),
        )
        return {
            key: match
            for key in keys
            if (source_key := source_keys[key])
            and engine_ids[key] is not None
            and (match := by_key.get((key[0], source_key, engine_ids[key]))) is not None
        }

    async def _batch_sources(
        self, project_ids: Sequence[str], source_keys: dict[tuple[str, str], str | None]
    ) -> dict[tuple[str, str], RuntimeSourceRow]:
        """The source row behind each incident, keyed the way the incidents are.

        Matched on `runtime_sources.estate_key`, because `project_ids` here are
        row STAMPS taken off the incident rows and the estate key is the same
        stamp on this table. For a source registered against a project the
        estate key is that project's id, so this is the join it has always been;
        for an ownerless estate it is the only value the two sides share.
        """
        wanted = sorted({value for value in source_keys.values() if value})
        if not wanted:
            return {}
        stmt = (
            select(RuntimeSourceRow)
            .where(
                RuntimeSourceRow.estate_key.in_(project_ids),
                RuntimeSourceRow.source_key.in_(wanted),
            )
            .order_by(RuntimeSourceRow.id.asc())
        )
        by_key = self._first_by_key(
            (await self._session.execute(stmt)).scalars().all(),
            lambda row: (row.estate_key, row.source_key),
        )
        return {
            key: match
            for key, source_key in source_keys.items()
            if source_key and (match := by_key.get((key[0], source_key))) is not None
        }

    async def _batch_plan_runs(
        self,
        keys: Sequence[tuple[str, str]],
        source_keys: dict[tuple[str, str], str | None],
    ) -> dict[tuple[str, str], RuntimePlanRunRow]:
        """Plan run owning each execution, from one window per (project, source).

        `runtime_plan_runs` has no execution_id column -- the link lives inside
        the `step_results` JSON -- so this still reads the same bounded window of
        the newest `_PLAN_RUN_MATCH_WINDOW` rows and scans it. What it no longer
        does is read that window once per execution: on one mirrored tenant the
        window's JSON averages 23 kB a row, so a 50-incident page used to pull
        roughly 57 MB of it out of TOAST to answer 50 questions about the same
        50 rows. The query, its ordering and the first-match rule are unchanged,
        so the answer is the same row it always was.
        """
        pairs = sorted(
            {(project_id, source_key)
             for (project_id, _), source_key in source_keys.items()
             if source_key}
        )
        resolved: dict[tuple[str, str], RuntimePlanRunRow] = {}
        for project_id, source_key in pairs:
            wanted = {
                execution_id
                for (row_project_id, execution_id) in keys
                if row_project_id == project_id and source_keys[(project_id, execution_id)] == source_key
            }
            stmt = (
                select(RuntimePlanRunRow)
                .where(
                    RuntimePlanRunRow.project_id == project_id,
                    RuntimePlanRunRow.source_key == source_key,
                )
                .order_by(RuntimePlanRunRow.started_at.desc())
                .limit(_PLAN_RUN_MATCH_WINDOW)
            )
            for row in (await self._session.execute(stmt)).scalars().all():
                for step in row.step_results or []:
                    if not isinstance(step, dict):
                        continue
                    execution_id = step.get("execution_id")
                    if execution_id in wanted:
                        # First match in window order wins, as before.
                        resolved.setdefault((project_id, execution_id), row)
        return resolved

    async def _batch_plans(
        self,
        keys: Sequence[tuple[str, str]],
        plan_runs: dict[tuple[str, str], RuntimePlanRunRow],
        source_keys: dict[tuple[str, str], str | None],
    ) -> dict[tuple[str, str], RuntimePlanRow]:
        wanted = {
            plan_runs[key].plan_id
            for key in keys
            if key in plan_runs and source_keys[key]
        }
        if not wanted:
            return {}
        stmt = (
            select(RuntimePlanRow)
            .where(
                RuntimePlanRow.project_id.in_(
                    sorted({project_id for project_id, _ in keys})
                ),
                RuntimePlanRow.source_key.in_(
                    sorted({value for value in source_keys.values() if value})
                ),
                RuntimePlanRow.plan_id.in_(sorted(wanted)),
            )
            .order_by(RuntimePlanRow.id.asc())
        )
        by_key = self._first_by_key(
            (await self._session.execute(stmt)).scalars().all(),
            lambda row: (row.project_id, row.source_key, row.plan_id),
        )
        return {
            key: match
            for key in keys
            if (source_key := source_keys[key])
            and key in plan_runs
            and (match := by_key.get((key[0], source_key, plan_runs[key].plan_id))) is not None
        }

    @staticmethod
    def _first_by_key(rows, key_of):
        """Index rows by key, keeping the first occurrence.

        A batched fetch has to reproduce `.first()` on the per-key query it
        replaces, which is "the first row the scan returned" -- so later
        duplicates are discarded rather than overwriting.
        """
        indexed: dict = {}
        for row in rows:
            indexed.setdefault(key_of(row), row)
        return indexed

    def _build_estate_context(
        self,
        *,
        source_key: str | None,
        exec_row: RuntimeExecutionRow | None,
        job_run: RuntimeJobRunRow | None,
        job: RuntimeJobRow | None,
        engine: RuntimeEngineRow | None,
        source: RuntimeSourceRow | None,
        plan_run: RuntimePlanRunRow | None,
        plan: RuntimePlanRow | None,
    ) -> RuntimeFailureEstateContext:
        """Fold the resolved inventory rows into one sanitized context."""
        environment_id = _first_present(
            job_run.environment_id if job_run else None,
            job.environment_id if job else None,
            engine.environment_id if engine else None,
            plan_run.environment_id if plan_run else None,
            plan.environment_id if plan else None,
        )
        environment_name = _first_present(
            job_run.environment_name if job_run else None,
            job.environment_name if job else None,
            engine.environment_name if engine else None,
            plan_run.environment_name if plan_run else None,
            plan.environment_name if plan else None,
        )
        workspace_id = _first_present(
            job_run.workspace_id if job_run else None,
            job.workspace_id if job else None,
            engine.workspace_id if engine else None,
            plan_run.workspace_id if plan_run else None,
            plan.workspace_id if plan else None,
        )
        workspace_name = _first_present(
            job_run.workspace_name if job_run else None,
            job.workspace_name if job else None,
            engine.workspace_name if engine else None,
            plan_run.workspace_name if plan_run else None,
            plan.workspace_name if plan else None,
        )

        return RuntimeFailureEstateContext(
            source_key=source_key,
            source_label=source.label if source else None,
            source_type=source.source_type if source else None,
            source_status=source.status if source else None,
            source_is_demo=bool(source.is_demo) if source else None,
            source_last_synced_at=_as_utc(source.last_synced_at) if source else None,
            source_last_seen_at=_as_utc(source.last_seen_at) if source else None,
            environment_id=environment_id,
            environment_name=environment_name,
            workspace_id=workspace_id,
            workspace_name=workspace_name,
            engine_id=_first_present(
                job_run.engine_id if job_run else None,
                job.engine_id if job else None,
                engine.engine_id if engine else None,
            ),
            engine_name=_first_present(
                job_run.engine_name if job_run else None,
                job.engine_name if job else None,
                engine.name if engine else None,
            ),
            engine_status=engine.status if engine else None,
            engine_version=engine.version if engine else None,
            engine_last_seen_at=_as_utc(engine.last_seen_at) if engine else None,
            job_id=_first_present(job_run.job_id if job_run else None, job.job_id if job else None),
            job_name=_first_present(
                job_run.job_name if job_run else None,
                job.name if job else None,
                exec_row.job_name if exec_row else None,
            ),
            job_display_name=job.display_name if job else None,
            job_run_id=job_run.run_id if job_run else None,
            plan_id=_first_present(plan_run.plan_id if plan_run else None, plan.plan_id if plan else None),
            plan_name=_first_present(plan_run.plan_name if plan_run else None, plan.name if plan else None),
            plan_display_name=plan.display_name if plan else None,
            plan_run_id=plan_run.run_id if plan_run else None,
        )


# -------------------------------------------------------------------------
# Row-to-model helpers
# -------------------------------------------------------------------------


def _to_incident_insight(
    row: RuntimeIncidentRow,
    exec_row: RuntimeExecutionRow | None,
    estate_context: RuntimeFailureEstateContext,
) -> RuntimeIncidentInsight:
    return RuntimeIncidentInsight(
        project_id=row.project_id,
        incident_id=row.incident_id,
        execution_id=row.execution_id,
        signature_hash=row.signature_hash,
        symptom_summary=row.symptom_summary,
        root_cause_candidate=_redacted(row.root_cause_candidate),
        causal_message=_redacted(row.causal_message),
        termination_component=_redacted(row.termination_component),
        confidence=row.confidence,
        cause_origin=row.cause_origin,
        pattern_id=row.pattern_id,
        first_bad_at=_as_utc(row.first_bad_at),
        last_healthy_at=_as_utc(row.last_healthy_at),
        next_checks=_redacted_list(row.next_checks),
        job_name=exec_row.job_name if exec_row else None,
        state=exec_row.state if exec_row else None,
        estate_context=estate_context,
    )


def _first_present(*values: str | None) -> str | None:
    for value in values:
        if value:
            return value
    return None


def _build_timeline_signal(enriched: RuntimeEnrichedEventRow | None, severity: str) -> str:
    """Return a bounded structured signal, never the raw log message body."""
    if enriched is None:
        return f"{severity} event"

    parts: list[str] = []
    if enriched.component_name:
        parts.append(f"component={enriched.component_name}")
    if enriched.error_code:
        parts.append(f"error_code={enriched.error_code}")
    if enriched.exception_class:
        parts.append(f"exception={enriched.exception_class}")
    if enriched.retry_attempt is not None and enriched.retry_max is not None:
        parts.append(f"retry={enriched.retry_attempt}/{enriched.retry_max}")
    if enriched.timeout_ms is not None:
        parts.append(f"timeout_ms={enriched.timeout_ms}")
    if enriched.message_family:
        parts.append(f"family={enriched.message_family}")
    if not parts:
        return f"{severity} event"
    return "; ".join(parts[:6])
