"""Adapter for already-frozen, allowlisted legacy run artifacts."""

from __future__ import annotations

from pathlib import Path

from mcp_server.experiments.domain.sources import RedactionState, SourceDescriptor, SourceLimits
from mcp_server.experiments.infrastructure.sources.registry import (
    AdapterSnapshotResult,
    CapturedRawSource,
    SourceAdapterError,
    capture_snapshot_item,
    ensure_within,
    regular_files,
)


class LegacyRunArtifactAdapter:
    id = "legacy-run-artifact"
    version = "1"

    def __init__(self, *, allowed_roots: tuple[Path, ...]) -> None:
        self._allowed_roots = allowed_roots

    def supports(self, descriptor: SourceDescriptor) -> bool:
        return descriptor.source_kind == "legacy_run_artifact"

    def snapshot(self, descriptor: SourceDescriptor, limits: SourceLimits) -> AdapterSnapshotResult:
        root = ensure_within(descriptor.source_path, self._allowed_roots)
        if not self._allowed_roots:
            raise SourceAdapterError("legacy run adapter has no configured frozen roots")
        files = regular_files(root, limits=limits)
        if any(path.suffix.lower() not in {".json", ".md"} for path in files):
            raise SourceAdapterError(
                "legacy run adapter permits only frozen Markdown and JSON artifacts"
            )
        captured = tuple(
            capture_snapshot_item(
                descriptor=descriptor,
                path=path,
                root=root,
                adapter_id=self.id,
                adapter_version=self.version,
                redaction_state=RedactionState.APPROVED,
            )
            for path in files
        )
        return AdapterSnapshotResult(
            items=tuple(item for item, _ in captured),
            contents=tuple(content for _, content in captured),
            raw_sources=tuple(
                CapturedRawSource(item.logical_identity, item.raw_sha256, item.size_bytes)
                for item, _ in captured
            ),
        )
