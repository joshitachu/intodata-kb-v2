"""Trusted adapter for approved IntoDoc scan outputs and redaction receipts."""

from __future__ import annotations

import json
from pathlib import Path

from mcp_server.experiments.domain.sources import RedactionState, SourceDescriptor, SourceLimits
from mcp_server.experiments.infrastructure.sources.registry import (
    AdapterSnapshotResult, SourceAdapterError, capture_snapshot_item, read_regular, regular_files,
)


class IntodocScanSourceAdapter:
    id = "intodoc-scan"
    version = "1"

    def supports(self, descriptor: SourceDescriptor) -> bool:
        return descriptor.source_kind == "intodoc_scan"

    def snapshot(self, descriptor: SourceDescriptor, limits: SourceLimits) -> AdapterSnapshotResult:
        receipt = descriptor.redaction_receipt_path
        if receipt is None or not self._redaction_approved(receipt):
            raise SourceAdapterError("IntoDoc source snapshot requires an approved redaction receipt")
        roots = (descriptor.source_path, *descriptor.additional_paths)
        files: list[tuple[Path, Path]] = []
        for root in roots:
            root = Path(root)
            for path in regular_files(root, limits=limits):
                files.append((path, root if root.is_dir() else path.parent))
        if len(files) > limits.max_files:
            raise SourceAdapterError("source file limit exceeded")
        seen = set()
        captured = []
        total_bytes = 0
        for path, root in sorted(files, key=lambda entry: entry[0].as_posix()):
            if path in seen:
                continue
            seen.add(path)
            total_bytes += path.stat().st_size
            if total_bytes > limits.max_bytes:
                raise SourceAdapterError("source byte limit exceeded")
            captured.append(capture_snapshot_item(
                descriptor=descriptor, path=path, root=root, adapter_id=self.id,
                adapter_version=self.version, redaction_state=RedactionState.APPROVED,
            ))
        return AdapterSnapshotResult(
            items=tuple(item for item, _ in captured),
            contents=tuple(content for _, content in captured),
        )

    @staticmethod
    def _redaction_approved(receipt: Path) -> bool:
        try:
            content = read_regular(receipt, max_bytes=1024 * 1024)
            decoded = json.loads(content)
        except (json.JSONDecodeError, SourceAdapterError):
            return False
        if not isinstance(decoded, dict):
            return False
        if decoded.get("approved") is True or decoded.get("status") == "approved":
            return True
        # IntoDoc's established report format predates an explicit approval
        # boolean. A completed standard/strict report with a bounded summary is
        # the scanner-issued receipt that redaction ran; permissive/off reports
        # are never accepted.
        level = decoded.get("redaction_level")
        total = decoded.get("total_redactions")
        summary = decoded.get("summary")
        return (
            level in {"standard", "strict"}
            and isinstance(total, int)
            and not isinstance(total, bool)
            and total >= 0
            and isinstance(summary, dict)
            and all(
                isinstance(key, str)
                and isinstance(value, int)
                and not isinstance(value, bool)
                and value >= 0
                for key, value in summary.items()
            )
            and sum(summary.values()) == total
        )
