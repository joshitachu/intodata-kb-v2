"""Adapter for immutable, redacted supporting-evidence derivatives."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from mcp_server.experiments.domain.sources import (
    RedactionState,
    SourceDescriptor,
    SourceLimits,
    SourceSnapshotContent,
    SourceSnapshotItem,
)
from mcp_server.experiments.infrastructure.sources.registry import (
    AdapterSnapshotResult,
    CapturedRawSource,
    SourceAdapterError,
    read_regular,
)


_MANIFEST_NAME = "prepared_evidence_manifest.json"
_CONTENT_DIR = "content"


class PreparedDirectoryEvidenceAdapter:
    """Expose only prepared bytes while retaining raw hashes for provenance."""

    id = "prepared-directory-evidence"
    version = "1"

    def __init__(self, *, allowed_roots: tuple[Path, ...]) -> None:
        self._allowed_roots = tuple(Path(root) for root in allowed_roots)

    def supports(self, descriptor: SourceDescriptor) -> bool:
        return descriptor.source_kind == "prepared_directory_evidence"

    def snapshot(self, descriptor: SourceDescriptor, limits: SourceLimits) -> AdapterSnapshotResult:
        root = self._within_allowed_root(descriptor.source_path)
        manifest = self._manifest(root)
        inventory = manifest["inventory"]
        if not isinstance(inventory, list):
            raise SourceAdapterError("prepared evidence manifest inventory is invalid")
        entries = [entry for entry in inventory if isinstance(entry, dict) and entry.get("status") == "prepared"]
        if not entries:
            raise SourceAdapterError("prepared evidence manifest has no prepared entries")
        if len(entries) > limits.max_files:
            raise SourceAdapterError("prepared evidence item limit exceeded")
        items: list[SourceSnapshotItem] = []
        contents: list[SourceSnapshotContent] = []
        raw_sources: list[CapturedRawSource] = []
        total = 0
        seen_content_ids: set[str] = set()
        for entry in sorted(entries, key=lambda value: str(value.get("logical_identity", ""))):
            required = {
                "content_id", "content_sha256", "content_size_bytes", "logical_identity",
                "media_type", "raw_sha256", "size_bytes",
            }
            if not required.issubset(entry):
                raise SourceAdapterError("prepared evidence entry is incomplete")
            content_id = _sha(entry["content_id"], "prepared content id")
            if content_id in seen_content_ids:
                raise SourceAdapterError("prepared evidence content ids are not unique")
            seen_content_ids.add(content_id)
            raw_sha = _sha(entry["raw_sha256"], "prepared raw sha256")
            content_sha = _sha(entry["content_sha256"], "prepared content sha256")
            raw_size = _non_negative_int(entry["size_bytes"], "prepared raw size")
            content_size = _non_negative_int(entry["content_size_bytes"], "prepared content size")
            logical_identity = _text(entry["logical_identity"], "prepared logical identity")
            media_type = _text(entry["media_type"], "prepared media type")
            path = root / _CONTENT_DIR / f"{content_id}.txt"
            content = read_regular(path, max_bytes=content_size)
            if len(content) != content_size or hashlib.sha256(content).hexdigest() != content_sha:
                raise SourceAdapterError("prepared evidence content does not match manifest")
            total += len(content)
            if total > limits.max_bytes:
                raise SourceAdapterError("prepared evidence byte limit exceeded")
            item_id = hashlib.sha256(logical_identity.encode("utf-8")).hexdigest()
            item = SourceSnapshotItem(
                id=item_id,
                source_descriptor_id=descriptor.id,
                logical_identity=logical_identity,
                source_kind=descriptor.source_kind,
                source_role=descriptor.source_role,
                capabilities=descriptor.capabilities,
                locator=f"{descriptor.provenance_locator.rstrip('/')}/prepared/{content_id}",
                raw_sha256=raw_sha,
                extracted_sha256=content_sha,
                media_type=media_type,
                size_bytes=raw_size,
                extracted_size_bytes=content_size,
                trust_level=descriptor.trust_level,
                redaction_state=RedactionState.APPROVED,
                adapter_id=self.id,
                adapter_version=self.version,
                metadata={"prepared": True},
            )
            items.append(item)
            contents.append(
                SourceSnapshotContent(
                    item_id=item_id,
                    extracted_bytes=content,
                    extracted_sha256=content_sha,
                    media_type=media_type,
                    size_bytes=content_size,
                )
            )
            raw_sources.append(CapturedRawSource(logical_identity, raw_sha, raw_size))
        return AdapterSnapshotResult(
            items=tuple(items), contents=tuple(contents), raw_sources=tuple(raw_sources)
        )

    def _within_allowed_root(self, path: Path) -> Path:
        try:
            resolved = Path(path).resolve(strict=True)
        except OSError as exc:
            raise SourceAdapterError("prepared evidence root is unavailable") from exc
        for root in self._allowed_roots:
            try:
                resolved.relative_to(root.resolve(strict=True))
                return resolved
            except (OSError, ValueError):
                continue
        raise SourceAdapterError("prepared evidence root is outside configured storage")

    @staticmethod
    def _manifest(root: Path) -> dict[str, Any]:
        raw = read_regular(root / _MANIFEST_NAME, max_bytes=16 * 1024 * 1024)
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SourceAdapterError("prepared evidence manifest is invalid") from exc
        if not isinstance(value, dict) or value.get("schema_version") != 1 or value.get("status") != "approved":
            raise SourceAdapterError("prepared evidence manifest is not approved")
        return value


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SourceAdapterError(f"{field} is invalid")
    return value


def _sha(value: object, field: str) -> str:
    text = _text(value, field)
    if len(text) != 64 or any(character not in "0123456789abcdef" for character in text):
        raise SourceAdapterError(f"{field} is invalid")
    return text


def _non_negative_int(value: object, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise SourceAdapterError(f"{field} is invalid")
    return value
