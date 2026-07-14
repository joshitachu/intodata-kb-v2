"""Run-scoped repository for normalized harness evidence."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.harness_evidence import HarnessEvidenceRow

from .models import EvidenceSnapshot


class HarnessEvidenceRepository:
    def __init__(self, db: AsyncSession):
        self._db = db

    async def collect(
        self, workflow_run_id: int, items: list[EvidenceSnapshot]
    ) -> list[EvidenceSnapshot]:
        if not items:
            return []
        next_order = (
            await self._db.scalar(
                select(func.max(HarnessEvidenceRow.collected_order)).where(
                    HarnessEvidenceRow.workflow_run_id == workflow_run_id
                )
            )
            or 0
        ) + 1
        rows = []
        for offset, item in enumerate(items):
            data = item.model_dump(
                exclude={"id", "workflow_run_id", "collected_order", "used_order", "metadata"}
            )
            row = HarnessEvidenceRow(
                workflow_run_id=workflow_run_id,
                collected_order=next_order + offset,
                used_order=None,
                safe_metadata=item.metadata,
                **data,
            )
            self._db.add(row)
            rows.append(row)
        await self._db.flush()
        return [_snapshot(row) for row in rows]

    async def list_collected(self, workflow_run_id: int) -> list[EvidenceSnapshot]:
        rows = (
            await self._db.scalars(
                select(HarnessEvidenceRow)
                .where(HarnessEvidenceRow.workflow_run_id == workflow_run_id)
                .order_by(HarnessEvidenceRow.collected_order)
            )
        ).all()
        return [_snapshot(row) for row in rows]

    async def mark_used(
        self, workflow_run_id: int, evidence_ids: list[int]
    ) -> list[EvidenceSnapshot]:
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("used evidence ids must be unique")
        by_id: dict[int, HarnessEvidenceRow] = {}
        if evidence_ids:
            rows = (
                await self._db.scalars(
                    select(HarnessEvidenceRow).where(
                        HarnessEvidenceRow.workflow_run_id == workflow_run_id,
                        HarnessEvidenceRow.id.in_(evidence_ids),
                    )
                )
            ).all()
            by_id = {row.id: row for row in rows}
            if set(by_id) != set(evidence_ids):
                raise ValueError("evidence must belong to the workflow run")
        await self._db.execute(
            update(HarnessEvidenceRow)
            .where(HarnessEvidenceRow.workflow_run_id == workflow_run_id)
            .values(used_order=None)
        )
        if evidence_ids:
            for order, evidence_id in enumerate(evidence_ids, 1):
                by_id[evidence_id].used_order = order
        await self._db.flush()
        return await self.list_used(workflow_run_id)

    async def list_used(self, workflow_run_id: int) -> list[EvidenceSnapshot]:
        rows = (
            await self._db.scalars(
                select(HarnessEvidenceRow)
                .where(
                    HarnessEvidenceRow.workflow_run_id == workflow_run_id,
                    HarnessEvidenceRow.used_order.is_not(None),
                )
                .order_by(HarnessEvidenceRow.used_order)
            )
        ).all()
        return [_snapshot(row) for row in rows]

    async def persist_conflicts(self, workflow_run_id: int, items: list[EvidenceSnapshot]) -> None:
        for item in items:
            if item.id is None:
                raise ValueError("conflict annotations require persisted evidence")
            await self._db.execute(
                update(HarnessEvidenceRow)
                .where(
                    HarnessEvidenceRow.workflow_run_id == workflow_run_id,
                    HarnessEvidenceRow.id == item.id,
                )
                .values(conflict_group=item.conflict_group, conflict_refs=item.conflict_refs)
            )
        await self._db.flush()


def _snapshot(row: HarnessEvidenceRow) -> EvidenceSnapshot:
    return EvidenceSnapshot(
        id=row.id,
        workflow_run_id=row.workflow_run_id,
        source_class=row.source_class,
        source_id=row.source_id,
        capability_id=row.capability_id,
        project_ref=row.project_ref,
        entity_ref=row.entity_ref,
        connector_ref=row.connector_ref,
        title=row.title,
        summary=row.summary,
        uri=row.uri,
        observed_at=_as_utc(row.observed_at),
        retrieved_at=_as_utc(row.retrieved_at),
        freshness=row.freshness,
        confidence=row.confidence,
        authority=row.authority,
        score=row.score,
        claim_key=row.claim_key,
        claim_value=row.claim_value,
        conflict_group=row.conflict_group,
        conflict_refs=row.conflict_refs or [],
        collected_order=row.collected_order,
        used_order=row.used_order,
        metadata=row.safe_metadata or {},
    )


def _as_utc(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value
