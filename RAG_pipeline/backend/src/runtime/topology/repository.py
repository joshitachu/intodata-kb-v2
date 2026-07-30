"""Persistence helpers for bounded scanner topology snapshots and explicit maps."""

from __future__ import annotations

import datetime
import hashlib
import json

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.runtime_topology import (
    RuntimeJobScanMapRow,
    RuntimeStaticTopologySnapshotRow,
)
from src.runtime.topology.models import (
    RuntimeJobScanMap,
    RuntimeJobScanMapCreate,
    RuntimeStaticTopologySnapshotIn,
    RuntimeStaticTopologySnapshotReceipt,
)


class StaticTopologySnapshotConflictError(Exception):
    """A revision was replayed with different static evidence."""


class RuntimeJobScanMapValidationError(Exception):
    """A requested static target does not exist in the selected snapshot."""


def _as_utc(value: datetime.datetime | None) -> datetime.datetime:
    if value is None:
        return datetime.datetime.now(datetime.UTC)
    return value if value.tzinfo is not None else value.replace(tzinfo=datetime.UTC)


def _snapshot_payload(snapshot: RuntimeStaticTopologySnapshotIn) -> dict:
    # Source sequence and revision identify the scanner observation rather than
    # its structural content.  They are compared independently during replay.
    return snapshot.model_dump(
        mode="json", exclude={"project_id", "scan_revision", "source_sequence"}
    )


def _payload_hash(payload: dict) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class RuntimeTopologyRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def upsert_snapshot(
        self,
        snapshot: RuntimeStaticTopologySnapshotIn,
        *,
        received_at: datetime.datetime | None = None,
    ) -> RuntimeStaticTopologySnapshotReceipt:
        now = _as_utc(received_at)
        payload = _snapshot_payload(snapshot)
        payload_hash = _payload_hash(payload)
        by_revision = select(RuntimeStaticTopologySnapshotRow).where(
            RuntimeStaticTopologySnapshotRow.project_id == snapshot.project_id,
            RuntimeStaticTopologySnapshotRow.scan_revision == snapshot.scan_revision,
        )
        row = (await self._session.execute(by_revision)).scalars().first()
        by_sequence = select(RuntimeStaticTopologySnapshotRow).where(
            RuntimeStaticTopologySnapshotRow.project_id == snapshot.project_id,
            RuntimeStaticTopologySnapshotRow.source_sequence == snapshot.source_sequence,
        )
        sequence_row = (await self._session.execute(by_sequence)).scalars().first()
        if row is None:
            row = sequence_row
        if row is not None:
            if (
                row.scan_revision != snapshot.scan_revision
                or row.source_sequence != snapshot.source_sequence
                or row.payload_hash != payload_hash
            ):
                raise StaticTopologySnapshotConflictError(
                    "A different scanner revision, generation, or payload already exists"
                )
            # Idempotent replay: do not change received_at, payload, or the
            # latest-snapshot ordering.  Delayed older generations are retained
            # but can never become latest.
            return RuntimeStaticTopologySnapshotReceipt(
                project_id=row.project_id,
                scan_revision=row.scan_revision,
                source_sequence=row.source_sequence,
                received_at=_as_utc(row.received_at),
                jobs=len(snapshot.jobs),
                dependencies=len(snapshot.dependencies),
                lineage=len(snapshot.lineage),
            )
        row = RuntimeStaticTopologySnapshotRow(
            project_id=snapshot.project_id,
            scan_revision=snapshot.scan_revision,
            source_sequence=snapshot.source_sequence,
            platform=snapshot.platform,
            payload_hash=payload_hash,
            payload=payload,
            received_at=now,
        )
        self._session.add(row)
        await self._session.flush()
        return RuntimeStaticTopologySnapshotReceipt(
            project_id=row.project_id,
            scan_revision=row.scan_revision,
            source_sequence=row.source_sequence,
            received_at=_as_utc(row.received_at),
            jobs=len(snapshot.jobs),
            dependencies=len(snapshot.dependencies),
            lineage=len(snapshot.lineage),
        )

    async def latest_snapshot(self, project_id: str) -> RuntimeStaticTopologySnapshotRow | None:
        stmt = (
            select(RuntimeStaticTopologySnapshotRow)
            .where(RuntimeStaticTopologySnapshotRow.project_id == project_id)
            .order_by(
                RuntimeStaticTopologySnapshotRow.source_sequence.desc(),
                RuntimeStaticTopologySnapshotRow.id.desc(),
            )
            .limit(1)
        )
        return (await self._session.execute(stmt)).scalars().first()

    async def snapshot_for_revision(
        self, project_id: str, scan_revision: str
    ) -> RuntimeStaticTopologySnapshotRow | None:
        stmt = select(RuntimeStaticTopologySnapshotRow).where(
            RuntimeStaticTopologySnapshotRow.project_id == project_id,
            RuntimeStaticTopologySnapshotRow.scan_revision == scan_revision,
        )
        return (await self._session.execute(stmt)).scalars().first()

    async def upsert_job_scan_map(
        self,
        mapping: RuntimeJobScanMapCreate,
        *,
        created_by: str | None,
        created_at: datetime.datetime | None = None,
    ) -> RuntimeJobScanMap:
        snapshot = await self._snapshot_for_mapping(
            mapping.project_id, mapping.scan_revision
        )
        if snapshot is None:
            raise RuntimeJobScanMapValidationError(
                "No static topology snapshot is available for this mapping"
            )
        jobs = (snapshot.payload or {}).get("jobs", [])
        if not any(
            isinstance(job, dict) and job.get("static_job_id") == mapping.static_job_id
            for job in jobs
        ):
            raise RuntimeJobScanMapValidationError(
                "The requested static job does not exist in the selected snapshot"
            )
        stmt = select(RuntimeJobScanMapRow).where(
            RuntimeJobScanMapRow.project_id == mapping.project_id,
            RuntimeJobScanMapRow.source_key == mapping.source_key,
            RuntimeJobScanMapRow.runtime_job_id == mapping.runtime_job_id,
        )
        row = (await self._session.execute(stmt)).scalars().first()
        if row is None:
            row = RuntimeJobScanMapRow(
                project_id=mapping.project_id,
                source_key=mapping.source_key,
                runtime_job_id=mapping.runtime_job_id,
                static_job_id=mapping.static_job_id,
                scan_revision=mapping.scan_revision,
                created_at=_as_utc(created_at),
                created_by=created_by,
            )
            self._session.add(row)
        else:
            row.static_job_id = mapping.static_job_id
            row.scan_revision = mapping.scan_revision
            row.created_by = created_by
        await self._session.flush()
        return _to_map(row)

    async def _snapshot_for_mapping(
        self, project_id: str, scan_revision: str | None
    ) -> RuntimeStaticTopologySnapshotRow | None:
        if scan_revision:
            return await self.snapshot_for_revision(project_id, scan_revision)
        return await self.latest_snapshot(project_id)

    async def list_job_scan_maps(self, project_id: str) -> list[RuntimeJobScanMap]:
        stmt = (
            select(RuntimeJobScanMapRow)
            .where(RuntimeJobScanMapRow.project_id == project_id)
            .order_by(RuntimeJobScanMapRow.source_key, RuntimeJobScanMapRow.runtime_job_id)
        )
        return [_to_map(row) for row in (await self._session.execute(stmt)).scalars().all()]

    async def get_job_scan_map(
        self, project_id: str, source_key: str, runtime_job_id: str
    ) -> RuntimeJobScanMapRow | None:
        stmt = select(RuntimeJobScanMapRow).where(
            RuntimeJobScanMapRow.project_id == project_id,
            RuntimeJobScanMapRow.source_key == source_key,
            RuntimeJobScanMapRow.runtime_job_id == runtime_job_id,
        )
        return (await self._session.execute(stmt)).scalars().first()

    async def delete_job_scan_map(
        self, project_id: str, source_key: str, runtime_job_id: str
    ) -> bool:
        result = await self._session.execute(
            delete(RuntimeJobScanMapRow).where(
                RuntimeJobScanMapRow.project_id == project_id,
                RuntimeJobScanMapRow.source_key == source_key,
                RuntimeJobScanMapRow.runtime_job_id == runtime_job_id,
            )
        )
        return bool(result.rowcount)


def _to_map(row: RuntimeJobScanMapRow) -> RuntimeJobScanMap:
    return RuntimeJobScanMap(
        id=row.id,
        project_id=row.project_id,
        source_key=row.source_key,
        runtime_job_id=row.runtime_job_id,
        static_job_id=row.static_job_id,
        scan_revision=row.scan_revision,
        created_at=_as_utc(row.created_at),
        created_by=row.created_by,
    )
