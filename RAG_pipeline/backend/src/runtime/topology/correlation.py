"""Deterministic execution-to-static-topology correlation.

No names are fuzzed.  A result is only ``matched`` when the runtime task has
an explicit mapping, its reported component exactly normalizes to a scanned
component, and an explicit field violation matches a static lineage fact.
"""

from __future__ import annotations

from collections import deque

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.runtime_data import RuntimeExecutionRow, RuntimeJobRunRow, RuntimeLogEventRow
from src.db.models.runtime_insights import RuntimeEnrichedEventRow, RuntimeExecutionSummaryRow
from src.runtime.store import ProjectScope, RuntimeRecordNotFoundError
from src.runtime.topology.models import (
    RuntimeCorrelationImpact,
    RuntimeExecutionCorrelation,
    RuntimeFieldViolation,
    RuntimeStaticLineageEvidence,
)
from src.runtime.topology.repository import RuntimeTopologyRepository


def _normal(value: str | None) -> str:
    return (value or "").strip().casefold()


class RuntimeCorrelationService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._topology = RuntimeTopologyRepository(session)

    async def correlate_execution(
        self, scope: ProjectScope, execution_id: str
    ) -> RuntimeExecutionCorrelation:
        execution = await self._scoped_execution(scope, execution_id)
        if execution is None:
            raise RuntimeRecordNotFoundError(f"Execution not found: {execution_id}")

        job_run = await self._job_run(execution.project_id, execution_id)
        runtime_job_id = job_run.job_id if job_run else None
        source_key = job_run.source_key if job_run else execution.source_key
        summary = await self._summary(execution.project_id, execution_id)
        field_violation, field_evidence_id = await self._field_violation(
            execution.project_id, execution_id, summary.failed_component if summary else None,
        )
        evidence_ids = self._evidence_ids(summary.evidence_refs if summary else None)
        if field_evidence_id is not None and field_evidence_id not in evidence_ids:
            evidence_ids.append(field_evidence_id)

        base = dict(
            execution_id=execution_id,
            project_id=execution.project_id,
            runtime_job_id=runtime_job_id,
            runtime_job_name=execution.job_name,
            failed_component=summary.failed_component if summary else None,
            field_violation=field_violation,
            runtime_evidence_log_event_ids=sorted(evidence_ids),
        )
        if runtime_job_id is None:
            return RuntimeExecutionCorrelation(
                status="unmatched", component_match="not_reported",
                **base,
            )

        mapping = await self._topology.get_job_scan_map(
            execution.project_id, source_key, runtime_job_id
        )
        snapshot = (
            await self._topology.snapshot_for_revision(execution.project_id, mapping.scan_revision)
            if mapping is not None and mapping.scan_revision
            else await self._topology.latest_snapshot(execution.project_id)
        )
        if snapshot is None or mapping is None:
            return RuntimeExecutionCorrelation(
                status="unmatched", component_match="not_reported",
                scan_revision=snapshot.scan_revision if snapshot else None,
                **base,
            )
        payload = snapshot.payload or {}
        jobs = {
            str(job.get("static_job_id") or ""): job
            for job in payload.get("jobs", [])
            if isinstance(job, dict)
        }
        static_job = jobs.get(mapping.static_job_id)
        if static_job is None:
            return RuntimeExecutionCorrelation(
                status="unmatched", component_match="not_reported",
                scan_revision=snapshot.scan_revision,
                **base,
            )

        component_match = self._component_match(
            static_job, summary.failed_component if summary else None
        )
        lineage = self._lineage_matches(
            payload.get("lineage", []),
            static_job_id=mapping.static_job_id,
            failed_component=summary.failed_component if summary else None,
            field_name=field_violation.field_name if field_violation else None,
        )
        control_impacts = self._impacts(
            payload.get("dependencies", []), jobs, mapping.static_job_id, "control_flow"
        )
        data_impacts = self._impacts(
            payload.get("dependencies", []), jobs, mapping.static_job_id, "data_lineage"
        )
        status = "matched" if component_match == "exact_normalized" and field_violation and lineage else "partial"
        return RuntimeExecutionCorrelation(
            status=status,
            scan_revision=snapshot.scan_revision,
            static_job_id=mapping.static_job_id,
            static_job_name=str(static_job.get("name") or mapping.static_job_id),
            static_source_root=str(static_job.get("source_root") or ""),
            component_match=component_match,
            lineage_evidence=lineage,
            control_flow_impacts=control_impacts,
            data_lineage_impacts=data_impacts,
            **base,
        )

    @staticmethod
    def _evidence_ids(raw_refs: object) -> list[int]:
        """Normalize historical JSON evidence safely; bools are never IDs."""
        if not isinstance(raw_refs, list):
            return []
        result: set[int] = set()
        for value in raw_refs:
            if isinstance(value, bool):
                continue
            if isinstance(value, int):
                result.add(value)
            elif isinstance(value, str) and value.isdecimal():
                result.add(int(value))
        return sorted(result)

    async def _scoped_execution(self, scope: ProjectScope, execution_id: str) -> RuntimeExecutionRow | None:
        if scope is not None and not scope:
            return None
        stmt = select(RuntimeExecutionRow).where(RuntimeExecutionRow.execution_id == execution_id)
        if scope is not None:
            stmt = stmt.where(RuntimeExecutionRow.project_id.in_(scope))
        return (await self._session.execute(stmt)).scalars().first()

    async def _job_run(self, project_id: str, execution_id: str) -> RuntimeJobRunRow | None:
        stmt = select(RuntimeJobRunRow).where(
            RuntimeJobRunRow.project_id == project_id,
            RuntimeJobRunRow.execution_id == execution_id,
        )
        return (await self._session.execute(stmt)).scalars().first()

    async def _summary(self, project_id: str, execution_id: str) -> RuntimeExecutionSummaryRow | None:
        stmt = select(RuntimeExecutionSummaryRow).where(
            RuntimeExecutionSummaryRow.project_id == project_id,
            RuntimeExecutionSummaryRow.execution_id == execution_id,
        )
        return (await self._session.execute(stmt)).scalars().first()

    async def _field_violation(
        self, project_id: str, execution_id: str, failed_component: str | None
    ) -> tuple[RuntimeFieldViolation | None, int | None]:
        stmt = (
            select(RuntimeEnrichedEventRow, RuntimeLogEventRow)
            .join(RuntimeLogEventRow, RuntimeLogEventRow.id == RuntimeEnrichedEventRow.log_event_id)
            .where(
                RuntimeEnrichedEventRow.project_id == project_id,
                RuntimeEnrichedEventRow.execution_id == execution_id,
                RuntimeEnrichedEventRow.field_name.is_not(None),
            )
            .order_by(RuntimeLogEventRow.timestamp.desc(), RuntimeLogEventRow.id.desc())
        )
        rows = (await self._session.execute(stmt)).all()
        if not rows:
            return None, None
        # Prefer an explicit field error emitted by the already selected failed
        # component.  Fall back to the latest explicit field error, never a
        # component inferred from a nearby line.
        selected = next(
            (item for item in rows if failed_component and _normal(item[0].component_name) == _normal(failed_component)),
            rows[0],
        )
        enriched, raw = selected
        return RuntimeFieldViolation(
            field_name=enriched.field_name,
            kind=enriched.field_violation_kind or "unknown",
            expected_type=enriched.expected_type,
            evidence_log_event_id=raw.id,
        ), raw.id

    @staticmethod
    def _component_match(static_job: dict, failed_component: str | None) -> str:
        if not failed_component:
            return "not_reported"
        static_names = {
            _normal(str(component.get("unique_name") or ""))
            for component in static_job.get("components", [])
            if isinstance(component, dict)
        }
        return "exact_normalized" if _normal(failed_component) in static_names else "unmatched"

    @staticmethod
    def _lineage_matches(
        entries: object,
        *,
        static_job_id: str,
        failed_component: str | None,
        field_name: str | None,
    ) -> list[RuntimeStaticLineageEvidence]:
        if not field_name or not isinstance(entries, list):
            return []
        component = _normal(failed_component)
        field = _normal(field_name)
        result: list[RuntimeStaticLineageEvidence] = []
        for entry in entries:
            if not isinstance(entry, dict) or entry.get("job_id") != static_job_id:
                continue
            component_matches = component and component in {
                _normal(str(entry.get("source_component") or "")),
                _normal(str(entry.get("target_component") or "")),
            }
            field_matches = field in {
                _normal(str(entry.get("source_column") or "")),
                _normal(str(entry.get("target_column") or "")),
            }
            if not component_matches or not field_matches:
                continue
            result.append(RuntimeStaticLineageEvidence(
                job_id=static_job_id,
                source_component=str(entry.get("source_component") or ""),
                target_component=str(entry.get("target_component") or ""),
                source_table=str(entry.get("source_table") or ""),
                source_column=str(entry.get("source_column") or ""),
                target_table=str(entry.get("target_table") or ""),
                target_column=str(entry.get("target_column") or ""),
                confidence=str(entry.get("confidence") or "opaque"),
                transformation_types=[str(value) for value in entry.get("transformation_types", [])][:10],
                expressions=[str(value)[:1_000] for value in entry.get("expressions", [])][:10],
                evidence_id=str(entry.get("evidence_id") or ""),
            ))
        return result[:100]

    @staticmethod
    def _impacts(
        entries: object,
        jobs: dict[str, dict],
        start: str,
        dependency_type: str,
    ) -> list[RuntimeCorrelationImpact]:
        if not isinstance(entries, list):
            return []
        adjacency: dict[str, list[str]] = {}
        for entry in entries:
            if not isinstance(entry, dict) or entry.get("dependency_type") != dependency_type:
                continue
            source = str(entry.get("source_job_id") or "")
            target = str(entry.get("target_job_id") or "")
            if not source or not target:
                continue
            # Both impact lists follow the explicitly supplied edge direction.
            # For control flow this is caller -> invoked child; reverse
            # dependents are a distinct future concept and must never be
            # silently presented as downstream impact.
            adjacency.setdefault(source, []).append(target)
        seen = {start}
        queue: deque[tuple[str, int]] = deque([(start, 0)])
        result: list[RuntimeCorrelationImpact] = []
        while queue:
            current, distance = queue.popleft()
            for target in adjacency.get(current, []):
                if target in seen:
                    continue
                seen.add(target)
                job = jobs.get(target, {})
                result.append(RuntimeCorrelationImpact(
                    static_job_id=target,
                    job_name=str(job.get("name") or target),
                    source_root=str(job.get("source_root") or ""),
                    distance=distance + 1,
                    dependency_type=dependency_type,
                ))
                queue.append((target, distance + 1))
        return result[:500]
