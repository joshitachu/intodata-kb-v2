"""Adapter for explicitly governed, already-redacted directory fixtures."""

from __future__ import annotations

from pathlib import Path

from mcp_server.experiments.domain.sources import RedactionState, SourceDescriptor, SourceLimits
from mcp_server.experiments.infrastructure.sources.registry import (
    AdapterSnapshotResult, SourceAdapterError, capture_snapshot_item, ensure_within, regular_files,
)


class DirectoryEvidenceAdapter:
    id = "governed-directory"
    version = "1"

    def __init__(self, *, allowed_roots: tuple[Path, ...]) -> None:
        self._allowed_roots = allowed_roots

    def supports(self, descriptor: SourceDescriptor) -> bool:
        return descriptor.source_kind == "directory_evidence"

    def snapshot(self, descriptor: SourceDescriptor, limits: SourceLimits) -> AdapterSnapshotResult:
        if not self._allowed_roots:
            raise SourceAdapterError("governed directory adapter has no configured roots")
        root = ensure_within(descriptor.source_path, self._allowed_roots)
        files = regular_files(root, limits=limits)
        captured = tuple(capture_snapshot_item(
            descriptor=descriptor, path=path, root=root, adapter_id=self.id,
            adapter_version=self.version, redaction_state=RedactionState.APPROVED,
        ) for path in files)
        return AdapterSnapshotResult(
            items=tuple(item for item, _ in captured),
            contents=tuple(content for _, content in captured),
        )
