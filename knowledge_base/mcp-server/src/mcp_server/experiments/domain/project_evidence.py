"""Immutable, source-neutral project evidence inventory contracts.

This module is intentionally an inventory and preparation boundary.  It records
what was readable, unreadable, or unsupported without deriving domain facts or
assigning business meaning to any source role.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import hashlib
import json
from pathlib import PurePosixPath
from types import MappingProxyType
from typing import Any, Mapping

PROJECT_EVIDENCE_SCHEMA_VERSION = 3
_MANIFEST_KEYS = frozenset(
    {"counts", "fingerprint_sha256", "limits", "schema_version", "snapshot_id", "sources"}
)
_LIMIT_KEYS = frozenset(
    {"max_prepared_bytes", "max_prepared_records", "max_raw_bytes", "max_raw_files"}
)
_COUNT_KEYS = frozenset({"prepared_bytes", "prepared_records", "raw_bytes", "raw_files"})
_SOURCE_KEYS = frozenset(
    {
        "capabilities",
        "descriptor_id",
        "failure_identity",
        "metadata",
        "prepared_representations",
        "provenance",
        "raw_evidence_references",
        "raw_source_files",
        "source_kind",
        "source_role",
        "status",
        "status_reason",
        "trust_level",
        "usable_snapshot_manifest_sha256",
    }
)
_PROVENANCE_KEYS = frozenset(
    {
        "locator",
        "redaction_receipt_reference",
        "redaction_receipt_sha256",
        "redaction_status",
        "source_version",
    }
)
_RAW_REFERENCE_KEYS = frozenset(
    {
        "item_id",
        "locator",
        "logical_identity",
        "logical_path",
        "media_type",
        "raw_sha256",
        "size_bytes",
    }
)
_RAW_SOURCE_KEYS = frozenset({"logical_identity", "raw_sha256", "size_bytes"})
_PREPARED_KEYS = frozenset(
    {
        "extracted_sha256",
        "item_id",
        "logical_identity",
        "logical_path",
        "media_type",
        "size_bytes",
    }
)


class EvidenceSourceStatus(StrEnum):
    READABLE = "readable"
    UNREADABLE = "unreadable"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True)
class ProjectEvidenceSource:
    """One declared source and its immutable preparation outcome."""

    descriptor_id: str
    source_kind: str
    source_role: str
    provenance_locator: str
    source_version: str | None
    redaction_receipt_reference: str | None
    redaction_receipt_sha256: str | None
    redaction_status: str
    status: EvidenceSourceStatus
    status_reason: str | None
    capabilities: tuple[str, ...]
    trust_level: str
    metadata: Mapping[str, Any]
    raw_evidence_references: tuple[Mapping[str, Any], ...] = ()
    raw_source_files: tuple[Mapping[str, Any], ...] = ()
    prepared_representations: tuple[Mapping[str, Any], ...] = ()
    failure_identity: Mapping[str, Any] | None = None
    usable_snapshot_manifest_sha256: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "capabilities", tuple(self.capabilities))
        object.__setattr__(self, "metadata", _freeze_json(self.metadata))
        object.__setattr__(
            self,
            "raw_evidence_references",
            tuple(_freeze_json(value) for value in self.raw_evidence_references),
        )
        object.__setattr__(
            self,
            "raw_source_files",
            tuple(_freeze_json(value) for value in self.raw_source_files),
        )
        object.__setattr__(
            self,
            "prepared_representations",
            tuple(_freeze_json(value) for value in self.prepared_representations),
        )
        object.__setattr__(
            self,
            "failure_identity",
            None if self.failure_identity is None else _freeze_json(self.failure_identity),
        )

    def manifest_entry(self) -> dict[str, Any]:
        return {
            "capabilities": list(self.capabilities),
            "descriptor_id": self.descriptor_id,
            "failure_identity": _thaw_json(self.failure_identity),
            "metadata": _thaw_json(self.metadata),
            "prepared_representations": _thaw_json(self.prepared_representations),
            "provenance": {
                "locator": self.provenance_locator,
                "redaction_receipt_reference": self.redaction_receipt_reference,
                "redaction_receipt_sha256": self.redaction_receipt_sha256,
                "redaction_status": self.redaction_status,
                "source_version": self.source_version,
            },
            "raw_evidence_references": _thaw_json(self.raw_evidence_references),
            "raw_source_files": _thaw_json(self.raw_source_files),
            "source_kind": self.source_kind,
            "source_role": self.source_role,
            "status": self.status.value,
            "status_reason": self.status_reason,
            "trust_level": self.trust_level,
            "usable_snapshot_manifest_sha256": self.usable_snapshot_manifest_sha256,
        }

    @classmethod
    def from_manifest_entry(cls, entry: Mapping[str, Any]) -> "ProjectEvidenceSource":
        _require_exact_keys(entry, _SOURCE_KEYS, "project evidence source")
        provenance = _require_mapping(entry["provenance"], "source provenance")
        _require_exact_keys(provenance, _PROVENANCE_KEYS, "source provenance")
        status = EvidenceSourceStatus(str(entry["status"]))
        return cls(
            descriptor_id=_require_string(entry["descriptor_id"], "descriptor_id"),
            source_kind=_require_string(entry["source_kind"], "source_kind"),
            source_role=_require_string(entry["source_role"], "source_role"),
            provenance_locator=_require_string(provenance["locator"], "provenance locator"),
            source_version=_optional_string(provenance["source_version"], "source version"),
            redaction_receipt_reference=_optional_string(
                provenance["redaction_receipt_reference"], "redaction receipt reference"
            ),
            redaction_receipt_sha256=_optional_sha(
                provenance["redaction_receipt_sha256"], "redaction receipt sha256"
            ),
            redaction_status=_require_string(provenance["redaction_status"], "redaction status"),
            status=status,
            status_reason=_optional_string(entry["status_reason"], "status reason"),
            capabilities=tuple(_require_string_list(entry["capabilities"], "capabilities")),
            trust_level=_require_string(entry["trust_level"], "trust level"),
            metadata=_require_mapping(entry["metadata"], "source metadata"),
            raw_evidence_references=tuple(
                _require_mapping(value, "raw evidence reference")
                for value in _require_list(entry["raw_evidence_references"], "raw references")
            ),
            raw_source_files=tuple(
                _require_mapping(value, "raw source file")
                for value in _require_list(entry["raw_source_files"], "raw source files")
            ),
            prepared_representations=tuple(
                _require_mapping(value, "prepared representation")
                for value in _require_list(
                    entry["prepared_representations"], "prepared representations"
                )
            ),
            failure_identity=(
                None
                if entry["failure_identity"] is None
                else _require_mapping(entry["failure_identity"], "failure identity")
            ),
            usable_snapshot_manifest_sha256=_optional_sha(
                entry["usable_snapshot_manifest_sha256"], "usable snapshot manifest sha256"
            ),
        )


@dataclass(frozen=True)
class ProjectEvidencePreparedContent:
    """Approved searchable bytes retained separately from raw source references."""

    item_id: str
    extracted_bytes: bytes
    extracted_sha256: str
    media_type: str
    size_bytes: int

    def __post_init__(self) -> None:
        if not self.item_id.strip() or not self.media_type.strip():
            raise ValueError("prepared content identity and media type must not be blank")
        if not isinstance(self.extracted_bytes, bytes):
            raise ValueError("prepared content must be immutable bytes")
        if self.size_bytes != len(self.extracted_bytes):
            raise ValueError("prepared content size does not match bytes")
        if hashlib.sha256(self.extracted_bytes).hexdigest() != self.extracted_sha256:
            raise ValueError("prepared content hash does not match bytes")


@dataclass(frozen=True)
class ProjectEvidenceSnapshot:
    """A frozen project-level manifest plus prepared content for readable sources.

    ``manifest_json`` is canonical immutable state.  ``manifest()`` returns a
    fresh decoded copy so a caller cannot alter future reads or the fingerprint.
    Raw evidence remains represented by locators and hashes only; searchable
    prepared bytes stay inside ``prepared_content_items``.  This is distinct
    from ``SourceSnapshot`` because several established adapters preserve the
    original source media type while producing JSON prepared representations;
    the legacy model correctly requires one media type for both.
    """

    id: str
    schema_version: int
    fingerprint_sha256: str
    manifest_json: bytes
    sources: tuple[ProjectEvidenceSource, ...]
    prepared_content_items: tuple[ProjectEvidencePreparedContent, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "sources", tuple(self.sources))
        object.__setattr__(
            self,
            "prepared_content_items",
            tuple(self.prepared_content_items),
        )
        if not isinstance(self.manifest_json, bytes):
            raise ValueError("project evidence manifest must be immutable bytes")
        manifest = self.manifest()
        if _canonical_json_bytes(manifest) != self.manifest_json:
            raise ValueError("project evidence manifest must be canonical JSON")
        _require_exact_keys(manifest, _MANIFEST_KEYS, "project evidence manifest")
        if (
            manifest.get("snapshot_id") != self.id
            or manifest.get("schema_version") != self.schema_version
            or manifest.get("fingerprint_sha256") != self.fingerprint_sha256
            or self.schema_version != PROJECT_EVIDENCE_SCHEMA_VERSION
        ):
            raise ValueError("project evidence snapshot fields differ from frozen manifest")
        fingerprint_payload = dict(manifest)
        fingerprint_payload.pop("fingerprint_sha256")
        if (
            hashlib.sha256(_canonical_json_bytes(fingerprint_payload)).hexdigest()
            != self.fingerprint_sha256
        ):
            raise ValueError("project evidence manifest fingerprint does not match")
        manifest_sources = _require_list(manifest["sources"], "project evidence sources")
        if [source.manifest_entry() for source in self.sources] != manifest_sources:
            raise ValueError("project evidence public sources differ from frozen manifest")
        limits = _require_mapping(manifest["limits"], "project evidence limits")
        counts = _require_mapping(manifest["counts"], "project evidence counts")
        _require_exact_keys(limits, _LIMIT_KEYS, "project evidence limits")
        _require_exact_keys(counts, _COUNT_KEYS, "project evidence counts")
        for key in _LIMIT_KEYS:
            _require_positive_int(limits[key], key)
        for key in _COUNT_KEYS:
            _require_nonnegative_int(counts[key], key)
        self._validate_source_invariants()
        derived_counts = {
            "prepared_bytes": sum(
                int(value["size_bytes"])
                for source in self.sources
                for value in source.prepared_representations
            ),
            "prepared_records": sum(
                len(source.prepared_representations) for source in self.sources
            ),
            "raw_bytes": sum(_source_raw_bytes(source) for source in self.sources),
            "raw_files": sum(_source_raw_files(source) for source in self.sources),
        }
        if dict(counts) != derived_counts:
            raise ValueError("project evidence manifest counts do not match source inventory")
        if any(
            derived_counts[count_key] > int(limits[limit_key])
            for count_key, limit_key in (
                ("raw_files", "max_raw_files"),
                ("raw_bytes", "max_raw_bytes"),
                ("prepared_records", "max_prepared_records"),
                ("prepared_bytes", "max_prepared_bytes"),
            )
        ):
            raise ValueError("project evidence manifest exceeds declared limits")
        prepared_by_id = {content.item_id: content for content in self.prepared_content_items}
        if len(prepared_by_id) != len(self.prepared_content_items):
            raise ValueError("project evidence prepared content ids must be unique")
        manifest_prepared = {
            str(item["item_id"]): item
            for source in manifest_sources
            if isinstance(source, dict)
            for item in source.get("prepared_representations", [])
            if isinstance(item, dict) and item.get("item_id")
        }
        if set(prepared_by_id) != set(manifest_prepared):
            raise ValueError("prepared content differs from frozen manifest inventory")
        for item_id, content in prepared_by_id.items():
            entry = manifest_prepared[item_id]
            if (
                entry.get("extracted_sha256") != content.extracted_sha256
                or entry.get("media_type") != content.media_type
                or entry.get("size_bytes") != content.size_bytes
            ):
                raise ValueError("prepared content metadata differs from frozen manifest")

    @classmethod
    def from_manifest_json(
        cls,
        *,
        manifest_json: bytes,
        prepared_content_items: tuple[ProjectEvidencePreparedContent, ...],
    ) -> "ProjectEvidenceSnapshot":
        if not isinstance(manifest_json, bytes):
            raise ValueError("project evidence manifest must be immutable bytes")
        try:
            manifest = json.loads(manifest_json)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("project evidence manifest is not valid JSON") from error
        manifest = _require_mapping(manifest, "project evidence manifest")
        _require_exact_keys(manifest, _MANIFEST_KEYS, "project evidence manifest")
        sources = tuple(
            ProjectEvidenceSource.from_manifest_entry(
                _require_mapping(entry, "project evidence source")
            )
            for entry in _require_list(manifest["sources"], "project evidence sources")
        )
        return cls(
            id=_require_string(manifest["snapshot_id"], "snapshot id"),
            schema_version=_require_positive_int(manifest["schema_version"], "schema version"),
            fingerprint_sha256=_require_sha(manifest["fingerprint_sha256"], "snapshot fingerprint"),
            manifest_json=manifest_json,
            sources=sources,
            prepared_content_items=prepared_content_items,
        )

    def _validate_source_invariants(self) -> None:
        descriptor_ids = [source.descriptor_id for source in self.sources]
        if descriptor_ids != sorted(descriptor_ids) or len(descriptor_ids) != len(
            set(descriptor_ids)
        ):
            raise ValueError("project evidence sources must have unique sorted descriptor ids")
        prepared_item_ids: list[str] = []
        prepared_logical_identities: list[str] = []
        prepared_logical_paths: list[str] = []
        for source in self.sources:
            for value, label in (
                (source.descriptor_id, "descriptor id"),
                (source.source_kind, "source kind"),
                (source.source_role, "source role"),
                (source.provenance_locator, "provenance locator"),
                (source.redaction_status, "redaction status"),
                (source.trust_level, "trust level"),
            ):
                _require_string(value, label)
            if source.capabilities != tuple(sorted(set(source.capabilities))):
                raise ValueError("source capabilities must be unique and sorted")
            if source.redaction_receipt_sha256 is not None:
                _require_sha(source.redaction_receipt_sha256, "redaction receipt sha256")
            raw_references_by_id: dict[str, Mapping[str, Any]] = {}
            for reference in source.raw_evidence_references:
                _require_exact_keys(reference, _RAW_REFERENCE_KEYS, "raw evidence reference")
                item_id = _require_string(reference["item_id"], "raw evidence item id")
                if item_id in raw_references_by_id:
                    raise ValueError("raw evidence item ids must be unique within a source")
                raw_references_by_id[item_id] = reference
                _require_safe_logical_path(reference["logical_path"], "raw evidence logical path")
                _require_sha(reference["raw_sha256"], "raw evidence sha256")
                _require_nonnegative_int(reference["size_bytes"], "raw evidence size")
            for raw_source in source.raw_source_files:
                _require_exact_keys(raw_source, _RAW_SOURCE_KEYS, "raw source file")
                _require_sha(raw_source["raw_sha256"], "raw source sha256")
                _require_nonnegative_int(raw_source["size_bytes"], "raw source size")
            for prepared in source.prepared_representations:
                _require_exact_keys(prepared, _PREPARED_KEYS, "prepared representation")
                item_id = _require_string(prepared["item_id"], "prepared item id")
                prepared_item_ids.append(item_id)
                prepared_logical_identities.append(
                    _require_string(
                        prepared["logical_identity"],
                        "prepared logical identity",
                    )
                )
                prepared_logical_paths.append(
                    _require_safe_logical_path(
                        prepared["logical_path"],
                        "prepared logical path",
                    )
                )
                _require_sha(prepared["extracted_sha256"], "prepared sha256")
                _require_nonnegative_int(prepared["size_bytes"], "prepared size")
            if source.status is EvidenceSourceStatus.READABLE:
                if (
                    source.status_reason is not None
                    or source.failure_identity is not None
                    or not source.raw_source_files
                    or not source.prepared_representations
                    or source.usable_snapshot_manifest_sha256 is None
                    or source.redaction_status != "approved"
                    or source.redaction_receipt_sha256 is None
                    or source.redaction_receipt_reference is None
                ):
                    raise ValueError("readable project evidence source has inconsistent state")
            elif (
                not source.status_reason
                or source.raw_evidence_references
                or source.raw_source_files
                or source.prepared_representations
                or source.failure_identity is None
                or source.usable_snapshot_manifest_sha256 is not None
            ):
                raise ValueError("non-readable project evidence source has inconsistent state")
            if source.failure_identity is not None:
                _validate_failure_identity(source.failure_identity)
        if len(prepared_item_ids) != len(set(prepared_item_ids)):
            raise ValueError("project evidence prepared item ids must be globally unique")
        if len(prepared_logical_identities) != len(set(prepared_logical_identities)):
            raise ValueError(
                "project evidence prepared logical identities must be globally unique"
            )
        if len(prepared_logical_paths) != len(set(prepared_logical_paths)):
            raise ValueError("project evidence prepared logical paths must be globally unique")

    def manifest(self) -> dict[str, Any]:
        try:
            value = json.loads(self.manifest_json)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("project evidence manifest is not valid JSON") from error
        if not isinstance(value, dict):
            raise ValueError("project evidence manifest must be an object")
        return value

    def prepared_contents(self) -> tuple[tuple[str, bytes], ...]:
        """Expose only approved prepared representations in stable item order."""
        return tuple(
            (content.item_id, content.extracted_bytes) for content in self.prepared_content_items
        )


def _freeze_json(value: Any) -> Any:
    """Deep-freeze JSON-compatible public state."""
    if isinstance(value, Mapping):
        return MappingProxyType(
            {str(key): _freeze_json(nested) for key, nested in sorted(value.items())}
        )
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json(nested) for nested in value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise ValueError("project evidence state must be JSON-compatible")


def _thaw_json(value: Any) -> Any:
    """Return a defensive JSON-compatible copy of frozen state."""
    if isinstance(value, Mapping):
        return {key: _thaw_json(nested) for key, nested in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(nested) for nested in value]
    return value


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _validate_failure_identity(value: Mapping[str, Any]) -> None:
    status = value.get("identity_status")
    if status == "captured":
        _require_exact_keys(value, frozenset({"files", "identity_status"}), "failure identity")
        files = _require_sequence(value["files"], "failure identity files")
        for item in files:
            entry = _require_mapping(item, "failure identity file")
            _require_exact_keys(
                entry,
                frozenset({"raw_sha256", "reference_id", "size_bytes"}),
                "failure identity file",
            )
            _require_sha(entry["raw_sha256"], "failure raw sha256")
            _require_sha(entry["reference_id"], "failure reference id")
            _require_nonnegative_int(entry["size_bytes"], "failure source size")
        return
    if status == "unavailable":
        _require_exact_keys(
            value,
            frozenset({"identity_status", "opaque_sha256"}),
            "failure identity",
        )
        _require_sha(value["opaque_sha256"], "failure opaque sha256")
        return
    raise ValueError("failure identity has unsupported state")


def _source_raw_files(source: ProjectEvidenceSource) -> int:
    if source.status is EvidenceSourceStatus.READABLE:
        return len(source.raw_source_files)
    if source.failure_identity and source.failure_identity.get("identity_status") == "captured":
        files = source.failure_identity.get("files")
        return len(files) if isinstance(files, tuple) else 0
    return 0


def _source_raw_bytes(source: ProjectEvidenceSource) -> int:
    if source.status is EvidenceSourceStatus.READABLE:
        return sum(int(value["size_bytes"]) for value in source.raw_source_files)
    if source.failure_identity and source.failure_identity.get("identity_status") == "captured":
        files = source.failure_identity.get("files")
        if isinstance(files, tuple):
            return sum(int(value["size_bytes"]) for value in files)
    return 0


def _require_exact_keys(value: Mapping[str, Any], expected: frozenset[str], label: str) -> None:
    if set(value) != expected:
        raise ValueError(f"{label} has unknown or missing fields")


def _require_mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    return dict(value)


def _require_list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be an array")
    return value


def _require_sequence(value: Any, label: str) -> list[Any] | tuple[Any, ...]:
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{label} must be an array")
    return value


def _require_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-blank string")
    return value


def _require_safe_logical_path(value: Any, label: str) -> str:
    logical_path = _require_string(value, label)
    relative = PurePosixPath(logical_path)
    if (
        relative.is_absolute()
        or "\\" in logical_path
        or "\x00" in logical_path
        or any(part in {"", ".", ".."} for part in relative.parts)
    ):
        raise ValueError(f"{label} must be a safe relative POSIX path")
    return logical_path


def _optional_string(value: Any, label: str) -> str | None:
    return None if value is None else _require_string(value, label)


def _require_string_list(value: Any, label: str) -> list[str]:
    values = _require_list(value, label)
    if any(not isinstance(item, str) or not item.strip() for item in values):
        raise ValueError(f"{label} must contain non-blank strings")
    return values


def _require_sha(value: Any, label: str) -> str:
    text = _require_string(value, label)
    if len(text) != 64 or any(character not in "0123456789abcdef" for character in text):
        raise ValueError(f"{label} must be a lowercase SHA-256 digest")
    return text


def _optional_sha(value: Any, label: str) -> str | None:
    return None if value is None else _require_sha(value, label)


def _require_positive_int(value: Any, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError(f"{label} must be a positive integer")
    return value


def _require_nonnegative_int(value: Any, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{label} must be a non-negative integer")
    return value
