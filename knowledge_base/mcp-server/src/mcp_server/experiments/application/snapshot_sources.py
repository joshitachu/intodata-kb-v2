"""Build deterministic generic source snapshots through trusted adapter ports."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from uuid import uuid4

from mcp_server.experiments.domain.sources import (
    RedactionState, SourceDescriptor, SourceLimits, SourceSnapshot,
)
from mcp_server.experiments.infrastructure.sources.registry import SourceAdapterRegistry


class SourceSnapshotSink(Protocol):
    def save_snapshot(self, snapshot: SourceSnapshot) -> "SourceSnapshotPersistence": ...


@dataclass(frozen=True)
class SourceSnapshotPersistence:
    """Durable content receipt returned only after payloads and index are finalized."""

    snapshot_id: str
    content_relative_paths: dict[str, str]
    manifest_relative_path: str


@dataclass(frozen=True)
class SourceSnapshotCreationResult:
    snapshot: SourceSnapshot
    persistence: SourceSnapshotPersistence | None


@dataclass(frozen=True)
class CreateSourceSnapshotRequest:
    descriptors: tuple[SourceDescriptor, ...]
    limits: SourceLimits = SourceLimits()
    snapshot_id: str | None = None


class SourceSnapshotService:
    """Application service with no ORM, FastAPI, or source-domain assumptions."""

    def __init__(self, *, registry: SourceAdapterRegistry, sink: SourceSnapshotSink | None = None) -> None:
        self._registry = registry
        self._sink = sink

    def create(self, request: CreateSourceSnapshotRequest) -> SourceSnapshotCreationResult:
        if not request.descriptors:
            raise ValueError("source snapshot requires at least one descriptor")
        descriptor_ids = [descriptor.id for descriptor in request.descriptors]
        if len(descriptor_ids) != len(set(descriptor_ids)):
            raise ValueError("source descriptor ids must be unique")
        items = []
        contents = []
        for descriptor in sorted(request.descriptors, key=lambda item: item.id):
            result = self._registry.resolve(descriptor).snapshot(descriptor, request.limits)
            items.extend(result.items)
            contents.extend(result.contents)
        if len(items) > request.limits.max_files or sum(item.size_bytes for item in items) > request.limits.max_bytes:
            raise ValueError("aggregate source file or byte limit exceeded")
        snapshot = SourceSnapshot.build(
            snapshot_id=request.snapshot_id or str(uuid4()), items=tuple(items), contents=tuple(contents)
        )
        if snapshot.security_state is not RedactionState.APPROVED:
            raise ValueError("source snapshot cannot finalize without approved redaction")
        persistence = self._sink.save_snapshot(snapshot) if self._sink is not None else None
        if persistence is not None:
            if persistence.snapshot_id != snapshot.id:
                raise ValueError("source snapshot persistence receipt has the wrong snapshot id")
            if set(persistence.content_relative_paths) != {item.id for item in snapshot.items}:
                raise ValueError("source snapshot persistence receipt is missing content paths")
        return SourceSnapshotCreationResult(snapshot=snapshot, persistence=persistence)
