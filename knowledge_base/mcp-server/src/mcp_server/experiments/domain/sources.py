"""Open-taxonomy source snapshot contracts, independent of any source domain."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
import hashlib
import json
from pathlib import Path
from typing import Mapping


class RedactionState(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


def _sha(value: str, field_name: str) -> None:
    if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise ValueError(f"{field_name} must be a lowercase SHA-256 digest")


@dataclass(frozen=True)
class SourceLimits:
    max_files: int = 1_000
    max_bytes: int = 100 * 1024 * 1024

    def __post_init__(self) -> None:
        if self.max_files < 1 or self.max_bytes < 1:
            raise ValueError("source limits must be positive")


@dataclass(frozen=True)
class SourceDescriptor:
    """A trusted adapter input; open strings deliberately avoid domain enums."""

    id: str
    source_kind: str
    source_role: str
    source_path: Path
    provenance_locator: str
    capabilities: frozenset[str] = field(default_factory=frozenset)
    additional_paths: tuple[Path, ...] = ()
    redaction_receipt_path: Path | None = None
    trust_level: str = "trusted"
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for field_name in ("id", "source_kind", "source_role", "provenance_locator", "trust_level"):
            if not str(getattr(self, field_name)).strip():
                raise ValueError(f"{field_name} must not be blank")
        if not isinstance(self.source_path, Path):
            raise ValueError("source_path must be a pathlib.Path")
        if not self.capabilities or any(not capability.strip() for capability in self.capabilities):
            raise ValueError("source capabilities must contain non-blank values")
        if any(not isinstance(path, Path) for path in self.additional_paths):
            raise ValueError("additional_paths must contain pathlib.Path values")
        if self.redaction_receipt_path is not None and not isinstance(self.redaction_receipt_path, Path):
            raise ValueError("redaction_receipt_path must be a pathlib.Path or None")


@dataclass(frozen=True)
class SourceSnapshotItem:
    id: str
    source_descriptor_id: str
    logical_identity: str
    source_kind: str
    source_role: str
    capabilities: frozenset[str]
    locator: str
    raw_sha256: str
    extracted_sha256: str
    media_type: str
    size_bytes: int
    extracted_size_bytes: int
    trust_level: str
    redaction_state: RedactionState
    adapter_id: str
    adapter_version: str
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for field_name in (
            "id", "source_descriptor_id", "logical_identity", "source_kind", "source_role",
            "locator", "media_type", "trust_level", "adapter_id", "adapter_version",
        ):
            if not str(getattr(self, field_name)).strip():
                raise ValueError(f"{field_name} must not be blank")
        _sha(self.raw_sha256, "raw_sha256")
        _sha(self.extracted_sha256, "extracted_sha256")
        if self.size_bytes < 0 or self.extracted_size_bytes < 0:
            raise ValueError("source item byte counts must not be negative")

    def manifest_entry(self) -> dict[str, object]:
        return {
            "adapter_id": self.adapter_id,
            "adapter_version": self.adapter_version,
            "capabilities": sorted(self.capabilities),
            "extracted_sha256": self.extracted_sha256,
            "extracted_size_bytes": self.extracted_size_bytes,
            "id": self.id,
            "locator": self.locator,
            "logical_identity": self.logical_identity,
            "media_type": self.media_type,
            "metadata": dict(self.metadata),
            "raw_sha256": self.raw_sha256,
            "redaction_state": self.redaction_state.value,
            "size_bytes": self.size_bytes,
            "source_descriptor_id": self.source_descriptor_id,
            "source_kind": self.source_kind,
            "source_role": self.source_role,
            "trust_level": self.trust_level,
        }


@dataclass(frozen=True)
class SourceSnapshotContent:
    """Approved extracted bytes captured at snapshot time, never raw source secrets."""

    item_id: str
    extracted_bytes: bytes
    extracted_sha256: str
    media_type: str
    size_bytes: int

    def __post_init__(self) -> None:
        if not self.item_id.strip() or not self.media_type.strip():
            raise ValueError("source snapshot content identifiers must not be blank")
        _sha(self.extracted_sha256, "content extracted_sha256")
        if self.size_bytes != len(self.extracted_bytes):
            raise ValueError("content size_bytes must match extracted bytes")
        if hashlib.sha256(self.extracted_bytes).hexdigest() != self.extracted_sha256:
            raise ValueError("content extracted bytes do not match extracted_sha256")


@dataclass(frozen=True)
class SourceSnapshot:
    id: str
    schema_version: int
    items: tuple[SourceSnapshotItem, ...]
    contents: tuple[SourceSnapshotContent, ...]
    manifest_sha256: str
    security_state: RedactionState

    @classmethod
    def build(
        cls, *, snapshot_id: str, items: tuple[SourceSnapshotItem, ...],
        contents: tuple[SourceSnapshotContent, ...],
    ) -> "SourceSnapshot":
        if not items:
            raise ValueError("source snapshot must contain at least one item")
        identities = [item.logical_identity for item in items]
        if len(identities) != len(set(identities)):
            raise ValueError("source snapshot has duplicate logical identities")
        item_ids = [item.id for item in items]
        if len(item_ids) != len(set(item_ids)):
            raise ValueError("source snapshot has duplicate item ids")
        content_ids = [content.item_id for content in contents]
        if len(content_ids) != len(set(content_ids)):
            raise ValueError("source snapshot has duplicate content item ids")
        if set(item_ids) != set(content_ids):
            raise ValueError("source snapshot items and contents must have a one-to-one identity mapping")
        content_by_id = {content.item_id: content for content in contents}
        for item in items:
            content = content_by_id[item.id]
            if (
                item.extracted_sha256 != content.extracted_sha256
                or item.extracted_size_bytes != content.size_bytes
                or item.media_type != content.media_type
            ):
                raise ValueError("source snapshot item and approved content metadata differ")
            if item.redaction_state is not RedactionState.APPROVED:
                raise ValueError("source snapshot cannot include content without approved redaction")
        entries = [item.manifest_entry() for item in sorted(items, key=lambda item: item.logical_identity)]
        state = RedactionState.APPROVED if all(
            item.redaction_state is RedactionState.APPROVED for item in items
        ) else RedactionState.REJECTED
        manifest = {"items": entries, "schema_version": 1, "security_state": state.value}
        canonical = json.dumps(
            manifest, ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")
        return cls(
            id=snapshot_id,
            schema_version=1,
            items=tuple(sorted(items, key=lambda item: item.logical_identity)),
            contents=tuple(sorted(contents, key=lambda content: content.item_id)),
            manifest_sha256=hashlib.sha256(canonical).hexdigest(),
            security_state=state,
        )

    def manifest(self) -> dict[str, object]:
        return {
            "items": [item.manifest_entry() for item in self.items],
            "schema_version": self.schema_version,
            "security_state": self.security_state.value,
            "snapshot_id": self.id,
        }
