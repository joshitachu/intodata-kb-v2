"""Freeze a deterministic, multi-source project evidence inventory.

The aggregate supplements ``SourceSnapshotService``.  Established callers keep
its all-readable invariant, while this layer records every declared descriptor,
including bounded identities for unreadable and unsupported sources.  It makes
no semantic or business inference from the captured evidence.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Mapping
from urllib.parse import urlsplit, urlunsplit

from mcp_server.experiments.domain.project_evidence import (
    EvidenceSourceStatus,
    PROJECT_EVIDENCE_SCHEMA_VERSION,
    ProjectEvidencePreparedContent,
    ProjectEvidenceSnapshot,
    ProjectEvidenceSource,
)
from mcp_server.experiments.domain.sources import (
    RedactionState,
    SourceDescriptor,
    SourceLimits,
    SourceSnapshotContent,
    SourceSnapshotItem,
)
from mcp_server.experiments.infrastructure.sources.registry import (
    CapturedRawSource,
    SourceAdapterError,
    SourceBudgetError,
    SourceAdapterRegistry,
    read_regular,
    regular_files,
)
from mcp_server.experiments.infrastructure.storage.canonical_json import canonical_json_bytes


_SENSITIVE_KEY = re.compile(
    r"(?:password|passwd|pwd|secret|token|api[_-]?key|apikey|credential|"
    r"private[_-]?key|clientsecret|accesskey|authorization|auth|bearer|cookie|"
    r"session|csrf|headers?)",
    re.IGNORECASE,
)
_URL_USERINFO = re.compile(r"\b[a-z][a-z0-9+.-]*://[^/\s:@]+:[^/\s@]+@", re.IGNORECASE)
_PRIVATE_KEY = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")
_ASSIGNMENT = re.compile(
    r"(?i)(password|passwd|pwd|secret|token|api[_-]?key|apikey|credential|"
    r"clientsecret|authorization)\s*[:=]\s*[^\s,;&]+"
)
_SENSITIVE_TEXT = re.compile(
    r"(?i)(?:authorization|proxy-authorization|cookie|set-cookie|x-api-key|api[_-]?key|"
    r"password|passwd|pwd|secret|token|credential|bearer|basic)\s*(?::|=|\s)"
)
_HEADER_LINE = re.compile(r"(?m)^[A-Za-z][A-Za-z0-9-]{1,63}:\s*\S+")
_JWT = re.compile(
    r"(?<![A-Za-z0-9_-])[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}(?![A-Za-z0-9_-])"
)
_SAFE_SUFFIX = re.compile(r"^\.[a-z0-9]{1,16}$")
_SCANNER_SEMANTIC_PATHS = frozenset(
    {
        "scan_result.json",
        "scan/files_index.json",
        "scan/lineage.json",
        "scan/manifest.json",
        "scan/meta.json",
    }
)


@dataclass(frozen=True)
class PreparedEvidenceLimits:
    max_records: int = 20_000
    max_bytes: int = 100 * 1024 * 1024

    def __post_init__(self) -> None:
        if (
            not isinstance(self.max_records, int)
            or isinstance(self.max_records, bool)
            or self.max_records < 1
            or not isinstance(self.max_bytes, int)
            or isinstance(self.max_bytes, bool)
            or self.max_bytes < 1
        ):
            raise ValueError("prepared evidence limits must be positive integers")


@dataclass(frozen=True)
class CreateProjectEvidenceSnapshotRequest:
    descriptors: tuple[SourceDescriptor, ...]
    limits: SourceLimits = SourceLimits()
    prepared_limits: PreparedEvidenceLimits = PreparedEvidenceLimits()
    snapshot_id: str | None = None


@dataclass(frozen=True)
class _ReceiptEvidence:
    reference: str | None
    sha256: str | None
    status: str
    approved_bytes: bytes | None
    source_files: tuple[tuple[str, int], ...] = ()


@dataclass(frozen=True)
class _CaptureOutcome:
    source: ProjectEvidenceSource
    prepared: tuple[ProjectEvidencePreparedContent, ...]
    item_ids: tuple[str, ...]
    logical_identities: tuple[str, ...]
    raw_files: int
    raw_bytes: int
    prepared_records: int
    prepared_bytes: int


class ProjectEvidenceSnapshotService:
    """Inventory heterogeneous sources through one bounded trust boundary."""

    def __init__(self, *, registry: SourceAdapterRegistry) -> None:
        self._registry = registry

    def create(self, request: CreateProjectEvidenceSnapshotRequest) -> ProjectEvidenceSnapshot:
        if not request.descriptors:
            raise ValueError("project evidence snapshot requires at least one descriptor")
        descriptor_ids = [descriptor.id for descriptor in request.descriptors]
        if len(descriptor_ids) != len(set(descriptor_ids)):
            raise ValueError("project evidence descriptor ids must be unique")

        sources: list[ProjectEvidenceSource] = []
        prepared_content: list[ProjectEvidencePreparedContent] = []
        global_item_ids: set[str] = set()
        global_logical_identities: set[str] = set()
        raw_files = 0
        raw_bytes = 0
        prepared_records = 0
        prepared_bytes = 0
        for descriptor in sorted(request.descriptors, key=lambda value: value.id):
            outcome = self._capture_descriptor(descriptor, request.limits)
            duplicate_ids = global_item_ids.intersection(outcome.item_ids)
            duplicate_identities = global_logical_identities.intersection(
                outcome.logical_identities
            )
            if duplicate_ids:
                raise ValueError("project evidence has duplicate item ids across descriptors")
            if duplicate_identities:
                raise ValueError(
                    "project evidence has duplicate logical identities across descriptors"
                )
            raw_files += outcome.raw_files
            raw_bytes += outcome.raw_bytes
            prepared_records += outcome.prepared_records
            prepared_bytes += outcome.prepared_bytes
            if raw_files > request.limits.max_files:
                raise ValueError("aggregate project evidence file limit exceeded")
            if raw_bytes > request.limits.max_bytes:
                raise ValueError("aggregate project evidence byte limit exceeded")
            if prepared_records > request.prepared_limits.max_records:
                raise ValueError("aggregate project evidence prepared record limit exceeded")
            if prepared_bytes > request.prepared_limits.max_bytes:
                raise ValueError("aggregate project evidence prepared byte limit exceeded")
            global_item_ids.update(outcome.item_ids)
            global_logical_identities.update(outcome.logical_identities)
            sources.append(outcome.source)
            prepared_content.extend(outcome.prepared)

        manifest_sources = [source.manifest_entry() for source in sources]
        limits = {
            "max_prepared_bytes": request.prepared_limits.max_bytes,
            "max_prepared_records": request.prepared_limits.max_records,
            "max_raw_bytes": request.limits.max_bytes,
            "max_raw_files": request.limits.max_files,
        }
        counts = {
            "prepared_bytes": prepared_bytes,
            "prepared_records": prepared_records,
            "raw_bytes": raw_bytes,
            "raw_files": raw_files,
        }
        identity_payload = {
            "counts": counts,
            "limits": limits,
            "schema_version": PROJECT_EVIDENCE_SCHEMA_VERSION,
            "sources": manifest_sources,
        }
        identity = hashlib.sha256(canonical_json_bytes(identity_payload)).hexdigest()
        snapshot_id = request.snapshot_id or f"project-evidence-{identity[:16]}"
        fingerprint_payload = {
            **identity_payload,
            "snapshot_id": snapshot_id,
        }
        fingerprint = hashlib.sha256(canonical_json_bytes(fingerprint_payload)).hexdigest()
        manifest = {**fingerprint_payload, "fingerprint_sha256": fingerprint}
        return ProjectEvidenceSnapshot(
            id=snapshot_id,
            schema_version=PROJECT_EVIDENCE_SCHEMA_VERSION,
            fingerprint_sha256=fingerprint,
            manifest_json=canonical_json_bytes(manifest),
            sources=tuple(sources),
            prepared_content_items=tuple(
                sorted(prepared_content, key=lambda content: content.item_id)
            ),
        )

    def _capture_descriptor(
        self, descriptor: SourceDescriptor, limits: SourceLimits
    ) -> _CaptureOutcome:
        receipt = _read_redaction_receipt(descriptor)
        matches = self._registry.matching_adapters(descriptor)
        if not matches:
            return self._failure_outcome(
                descriptor,
                limits=limits,
                receipt=receipt,
                status=EvidenceSourceStatus.UNSUPPORTED,
                reason="no_registered_adapter",
            )
        if len(matches) > 1:
            return self._failure_outcome(
                descriptor,
                limits=limits,
                receipt=receipt,
                status=EvidenceSourceStatus.UNSUPPORTED,
                reason="ambiguous_registered_adapter",
            )
        if receipt.status != "approved":
            if bool(descriptor.metadata.get("automatic")):
                # A server-owned workspace is merely declared until an
                # operator/scanner-issued receipt authorizes its content. Do
                # not even hash a potentially multi-gigabyte tree while
                # reporting that unavailable state.
                return _CaptureOutcome(
                    source=_source_entry(
                        descriptor,
                        receipt=receipt,
                        status=EvidenceSourceStatus.UNREADABLE,
                        status_reason=f"redaction_receipt_{receipt.status}",
                        failure_identity=_opaque_failure_identity(
                            descriptor,
                            reason=f"redaction_receipt_{receipt.status}",
                        ),
                    ),
                    prepared=(),
                    item_ids=(),
                    logical_identities=(),
                    raw_files=0,
                    raw_bytes=0,
                    prepared_records=0,
                    prepared_bytes=0,
                )
            return self._failure_outcome(
                descriptor,
                limits=limits,
                receipt=receipt,
                status=EvidenceSourceStatus.UNREADABLE,
                reason=f"redaction_receipt_{receipt.status}",
            )

        adapter = matches[0]
        try:
            capture_with_receipt = getattr(adapter, "snapshot_with_redaction_receipt", None)
            if callable(capture_with_receipt):
                captured = capture_with_receipt(
                    descriptor,
                    limits,
                    receipt.approved_bytes,
                )
            else:
                captured = adapter.snapshot(descriptor, limits)
            snapshot_fingerprint, prepared_content = _validate_aggregate_capture(
                descriptor,
                receipt,
                captured.items,
                captured.contents,
                captured.raw_sources,
            )
        except (SourceAdapterError, ValueError, OSError):
            return self._failure_outcome(
                descriptor,
                limits=limits,
                receipt=receipt,
                status=EvidenceSourceStatus.UNREADABLE,
                reason="adapter_capture_failed",
            )
        source = _source_entry(
            descriptor,
            receipt=receipt,
            status=EvidenceSourceStatus.READABLE,
            status_reason=None,
            items=captured.items,
            contents=captured.contents,
            raw_sources=captured.raw_sources,
            snapshot_fingerprint=snapshot_fingerprint,
        )
        return _CaptureOutcome(
            source=source,
            prepared=prepared_content,
            item_ids=tuple(item.id for item in captured.items),
            logical_identities=tuple(item.logical_identity for item in captured.items),
            raw_files=len(captured.raw_sources),
            raw_bytes=sum(source.size_bytes for source in captured.raw_sources),
            prepared_records=len(prepared_content),
            prepared_bytes=sum(content.size_bytes for content in prepared_content),
        )

    @staticmethod
    def _failure_outcome(
        descriptor: SourceDescriptor,
        *,
        limits: SourceLimits,
        receipt: _ReceiptEvidence,
        status: EvidenceSourceStatus,
        reason: str,
    ) -> _CaptureOutcome:
        failure_identity, file_count, byte_count = _bounded_failure_identity(descriptor, limits)
        return _CaptureOutcome(
            source=_source_entry(
                descriptor,
                receipt=receipt,
                status=status,
                status_reason=reason,
                failure_identity=failure_identity,
            ),
            prepared=(),
            item_ids=(),
            logical_identities=(),
            raw_files=file_count,
            raw_bytes=byte_count,
            prepared_records=0,
            prepared_bytes=0,
        )


def _source_entry(
    descriptor: SourceDescriptor,
    *,
    receipt: _ReceiptEvidence,
    status: EvidenceSourceStatus,
    status_reason: str | None,
    items: tuple[SourceSnapshotItem, ...] = (),
    contents: tuple[SourceSnapshotContent, ...] = (),
    raw_sources: tuple[CapturedRawSource, ...] = (),
    snapshot_fingerprint: str | None = None,
    failure_identity: dict[str, Any] | None = None,
) -> ProjectEvidenceSource:
    metadata = _sanitize_metadata(_json_mapping(descriptor.metadata, "descriptor metadata"))
    source_version = _source_version(metadata)
    raw_references: tuple[dict[str, Any], ...] = ()
    prepared: tuple[dict[str, Any], ...] = ()
    raw_source_files: tuple[dict[str, Any], ...] = ()
    if snapshot_fingerprint is not None:
        content_by_id = {content.item_id: content for content in contents}
        logical_paths = {
            item.id: _safe_prepared_logical_path(descriptor, item.logical_identity)
            for item in items
        }
        raw_references = tuple(
            {
                "item_id": item.id,
                "locator": _sanitize_locator(item.locator),
                "logical_identity": _sanitize_identity(item.logical_identity),
                "logical_path": logical_paths[item.id],
                "media_type": item.media_type,
                "raw_sha256": item.raw_sha256,
                "size_bytes": item.size_bytes,
            }
            for item in sorted(items, key=lambda value: value.logical_identity)
        )
        prepared = tuple(
            {
                "extracted_sha256": item.extracted_sha256,
                "item_id": item.id,
                "logical_identity": _sanitize_identity(item.logical_identity),
                "logical_path": logical_paths[item.id],
                "media_type": content_by_id[item.id].media_type,
                "size_bytes": item.extracted_size_bytes,
            }
            for item in sorted(items, key=lambda value: value.logical_identity)
        )
        raw_source_files = tuple(
            {
                "logical_identity": _sanitize_identity(source.logical_identity),
                "raw_sha256": source.raw_sha256,
                "size_bytes": source.size_bytes,
            }
            for source in sorted(raw_sources, key=lambda value: value.logical_identity)
        )
    return ProjectEvidenceSource(
        descriptor_id=descriptor.id,
        source_kind=descriptor.source_kind,
        source_role=descriptor.source_role,
        provenance_locator=_sanitize_locator(descriptor.provenance_locator),
        source_version=source_version,
        redaction_receipt_reference=receipt.reference,
        redaction_receipt_sha256=receipt.sha256,
        redaction_status=receipt.status,
        status=status,
        status_reason=status_reason,
        capabilities=tuple(sorted(descriptor.capabilities)),
        trust_level=descriptor.trust_level,
        metadata=metadata,
        raw_evidence_references=raw_references,
        raw_source_files=raw_source_files,
        prepared_representations=prepared,
        failure_identity=failure_identity,
        usable_snapshot_manifest_sha256=snapshot_fingerprint,
    )


def _read_redaction_receipt(
    descriptor: SourceDescriptor,
) -> _ReceiptEvidence:
    path = descriptor.redaction_receipt_path
    if path is None:
        return _ReceiptEvidence(None, None, "missing", None)
    path_identity = hashlib.sha256(str(path).encode("utf-8")).hexdigest()
    reference = f"receipt://sha256/{path_identity}"
    try:
        content = read_regular(path, max_bytes=1024 * 1024)
    except SourceAdapterError:
        return _ReceiptEvidence(reference, None, "unreadable", None)
    digest = hashlib.sha256(content).hexdigest()
    source_files = _bound_receipt_source_files(content)
    if source_files is None:
        return _ReceiptEvidence(reference, digest, "invalid", None)
    return _ReceiptEvidence(reference, digest, "approved", content, source_files)


def _bound_receipt_source_files(content: bytes) -> tuple[tuple[str, int], ...] | None:
    """Accept only a closed receipt bound to the exact raw file hash multiset."""

    try:
        decoded = json.loads(content, object_pairs_hook=_no_duplicate_json_keys)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        return None
    if not isinstance(decoded, dict) or set(decoded) != {
        "schema_version",
        "source_files",
        "status",
    }:
        return None
    if decoded["schema_version"] != 1 or decoded["status"] != "approved":
        return None
    values = decoded["source_files"]
    if not isinstance(values, list) or not values:
        return None
    source_files: list[tuple[str, int]] = []
    for value in values:
        if not isinstance(value, dict) or set(value) != {"raw_sha256", "size_bytes"}:
            return None
        digest = value["raw_sha256"]
        size = value["size_bytes"]
        if (
            not isinstance(digest, str)
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
            or not isinstance(size, int)
            or isinstance(size, bool)
            or size < 0
        ):
            return None
        source_files.append((digest, size))
    if source_files != sorted(source_files):
        return None
    return tuple(source_files)


def _no_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, nested in pairs:
        if key in value:
            raise ValueError("duplicate receipt field")
        value[key] = nested
    return value


def _bounded_failure_identity(
    descriptor: SourceDescriptor,
    limits: SourceLimits,
) -> tuple[dict[str, Any], int, int]:
    """Hash bounded raw inputs without retaining path names or source values."""
    files: list[dict[str, Any]] = []
    total_bytes = 0
    roots = (descriptor.source_path, *descriptor.additional_paths)
    try:
        for root_index, root in enumerate(roots):
            root_path = Path(root)
            base = root_path if root_path.is_dir() else root_path.parent
            for path in regular_files(root_path, limits=limits):
                if _same_file(path, descriptor.redaction_receipt_path):
                    continue
                content = read_regular(path, max_bytes=limits.max_bytes)
                total_bytes += len(content)
                if len(files) >= limits.max_files or total_bytes > limits.max_bytes:
                    raise SourceBudgetError("failure identity source budget exceeded")
                try:
                    relative = path.relative_to(base).as_posix()
                except ValueError:
                    relative = path.name
                reference_id = hashlib.sha256(
                    f"{root_index}:{relative}".encode("utf-8")
                ).hexdigest()
                files.append(
                    {
                        "raw_sha256": hashlib.sha256(content).hexdigest(),
                        "reference_id": reference_id,
                        "size_bytes": len(content),
                    }
                )
    except SourceBudgetError:
        # Explicit user-declared sources retain the established fail-closed
        # contract.  Only the server-owned automatic workspace inventory may
        # become an unavailable manifest entry when its bounded walk exceeds
        # the aggregate budget.
        if not bool(descriptor.metadata.get("automatic")):
            raise ValueError("failure identity source budget exceeded")
        opaque = hashlib.sha256(
            canonical_json_bytes(
                {
                    "descriptor_id": descriptor.id,
                    "source_kind": descriptor.source_kind,
                    "source_reference": hashlib.sha256(
                        str(descriptor.source_path).encode("utf-8")
                    ).hexdigest(),
                    "reason": "source_budget_exceeded",
                }
            )
        ).hexdigest()
        return {"identity_status": "unavailable", "opaque_sha256": opaque}, 0, 0
    except (OSError, SourceAdapterError, ValueError):
        opaque = hashlib.sha256(
            canonical_json_bytes(
                {
                    "descriptor_id": descriptor.id,
                    "source_kind": descriptor.source_kind,
                    "source_reference": hashlib.sha256(
                        str(descriptor.source_path).encode("utf-8")
                    ).hexdigest(),
                }
            )
        ).hexdigest()
        return {"identity_status": "unavailable", "opaque_sha256": opaque}, 0, 0
    payload = {"files": files, "identity_status": "captured"}
    return payload, len(files), total_bytes


def _opaque_failure_identity(
    descriptor: SourceDescriptor,
    *,
    reason: str,
) -> dict[str, Any]:
    return {
        "identity_status": "unavailable",
        "opaque_sha256": hashlib.sha256(
            canonical_json_bytes(
                {
                    "descriptor_id": descriptor.id,
                    "reason": reason,
                    "source_kind": descriptor.source_kind,
                    "source_reference": hashlib.sha256(
                        str(descriptor.source_path).encode("utf-8")
                    ).hexdigest(),
                }
            )
        ).hexdigest(),
    }


def _validate_aggregate_capture(
    descriptor: SourceDescriptor,
    receipt: _ReceiptEvidence,
    items: tuple[SourceSnapshotItem, ...],
    contents: tuple[SourceSnapshotContent, ...],
    raw_sources: tuple[CapturedRawSource, ...],
) -> tuple[str, tuple[ProjectEvidencePreparedContent, ...]]:
    """Validate prepared output without weakening ``SourceSnapshot`` invariants."""
    if not items:
        raise ValueError("adapter capture contains no evidence items")
    if not raw_sources:
        raise ValueError("adapter capture contains no raw source accounting")
    identities = [item.logical_identity for item in items]
    item_ids = [item.id for item in items]
    content_ids = [content.item_id for content in contents]
    if any(
        item.source_descriptor_id != descriptor.id
        or item.source_kind != descriptor.source_kind
        or item.source_role != descriptor.source_role
        or item.capabilities != descriptor.capabilities
        or item.trust_level != descriptor.trust_level
        for item in items
    ):
        raise ValueError("adapter capture item ownership differs from descriptor")
    if len(identities) != len(set(identities)):
        raise ValueError("adapter capture has duplicate logical identities")
    if len(item_ids) != len(set(item_ids)) or len(content_ids) != len(set(content_ids)):
        raise ValueError("adapter capture has duplicate item identities")
    if set(item_ids) != set(content_ids):
        raise ValueError("adapter capture items and prepared content do not match")
    raw_identities = [source.logical_identity for source in raw_sources]
    if len(raw_identities) != len(set(raw_identities)):
        raise ValueError("adapter capture has duplicate raw source identities")
    captured_receipt_files = tuple(
        sorted((source.raw_sha256, source.size_bytes) for source in raw_sources)
    )
    if captured_receipt_files != receipt.source_files:
        raise ValueError("redaction receipt is not bound to captured raw sources")
    captured_raw_pairs = set(captured_receipt_files)
    if any((item.raw_sha256, item.size_bytes) not in captured_raw_pairs for item in items):
        raise ValueError("adapter item is not owned by captured raw source accounting")
    content_by_id = {content.item_id: content for content in contents}
    for item in items:
        content = content_by_id[item.id]
        if not isinstance(content.extracted_bytes, bytes):
            raise ValueError("adapter prepared content must be immutable bytes")
        if item.redaction_state is not RedactionState.APPROVED:
            raise ValueError("adapter capture includes non-approved evidence")
        if (
            item.extracted_sha256 != content.extracted_sha256
            or item.extracted_size_bytes != content.size_bytes
        ):
            raise ValueError("adapter capture prepared content does not match item")
    entries = [
        item.manifest_entry() for item in sorted(items, key=lambda value: value.logical_identity)
    ]
    manifest = {
        "items": entries,
        "schema_version": 1,
        "security_state": RedactionState.APPROVED.value,
    }
    fingerprint = hashlib.sha256(canonical_json_bytes(manifest)).hexdigest()
    prepared = tuple(
        ProjectEvidencePreparedContent(
            item_id=content.item_id,
            extracted_bytes=content.extracted_bytes,
            extracted_sha256=content.extracted_sha256,
            media_type=content.media_type,
            size_bytes=content.size_bytes,
        )
        for content in sorted(contents, key=lambda value: value.item_id)
    )
    return fingerprint, prepared


def _json_mapping(value: object, field_name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be a mapping")
    try:
        encoded = canonical_json_bytes(dict(value))
    except (TypeError, ValueError) as error:
        raise ValueError(f"{field_name} must be JSON-compatible") from error
    decoded = json.loads(encoded)
    if not isinstance(decoded, dict):
        raise ValueError(f"{field_name} must be a mapping")
    return decoded


def _source_version(metadata: dict[str, Any]) -> str | None:
    for key in ("source_version", "version"):
        value = metadata.get(key)
        if isinstance(value, (str, int)) and str(value).strip():
            return str(value)
    return None


def _sanitize_metadata(value: dict[str, Any]) -> dict[str, Any]:
    return {
        str(key): _sanitize_metadata_value(str(key), nested)
        for key, nested in sorted(value.items())
    }


def _sanitize_metadata_value(key: str, value: Any) -> Any:
    if _SENSITIVE_KEY.search(key):
        return "[REDACTED]"
    if isinstance(value, dict):
        return _sanitize_metadata(value)
    if isinstance(value, list):
        return [_sanitize_metadata_value("", nested) for nested in value]
    if isinstance(value, str):
        return _sanitize_text(value)
    return value


def _sanitize_text(value: str) -> str:
    stripped = value.strip()
    if (
        _PRIVATE_KEY.search(value)
        or _SENSITIVE_TEXT.search(value)
        or _JWT.search(value)
        or _ASSIGNMENT.search(value)
        or _HEADER_LINE.search(value)
    ):
        return "[REDACTED]"
    if "://" in stripped:
        return _sanitize_locator(stripped)
    if _URL_USERINFO.search(value):
        return "[REDACTED]"
    return value


def _sanitize_locator(value: str) -> str:
    clean = value.strip()
    if _PRIVATE_KEY.search(clean) or _SENSITIVE_TEXT.search(clean) or _JWT.search(clean):
        return f"locator://sha256/{hashlib.sha256(clean.encode('utf-8')).hexdigest()}"
    try:
        parsed = urlsplit(clean)
    except ValueError:
        return f"locator://sha256/{hashlib.sha256(clean.encode('utf-8')).hexdigest()}"
    if not parsed.scheme:
        return f"locator://sha256/{hashlib.sha256(clean.encode('utf-8')).hexdigest()}"
    hostname = parsed.hostname or ""
    hashed_hostname = (
        f"sha256-{hashlib.sha256(hostname.encode('utf-8')).hexdigest()}"
        if hostname
        else ""
    )
    netloc = hashed_hostname
    try:
        if parsed.port is not None:
            netloc = f"{hashed_hostname}:{parsed.port}"
    except ValueError:
        netloc = hashed_hostname
    path = (
        f"/sha256/{hashlib.sha256(parsed.path.encode('utf-8')).hexdigest()}" if parsed.path else ""
    )
    query = (
        f"value_sha256={hashlib.sha256(parsed.query.encode('utf-8')).hexdigest()}"
        if parsed.query
        else ""
    )
    fragment = (
        f"value_sha256={hashlib.sha256(parsed.fragment.encode('utf-8')).hexdigest()}"
        if parsed.fragment
        else ""
    )
    return urlunsplit((parsed.scheme, netloc, path, query, fragment))


def _sanitize_identity(value: str) -> str:
    return f"identity://sha256/{hashlib.sha256(value.encode('utf-8')).hexdigest()}"


def _safe_prepared_logical_path(
    descriptor: SourceDescriptor,
    logical_identity: str,
) -> str:
    """Retain only execution-relevant path shape, never arbitrary source names."""

    prefix = f"{descriptor.id}:"
    if not logical_identity.startswith(prefix):
        opaque = hashlib.sha256(logical_identity.encode("utf-8")).hexdigest()
        return f"prepared/{opaque}.bin"
    raw_relative = logical_identity[len(prefix) :]
    relative = PurePosixPath(raw_relative)
    if (
        not raw_relative
        or relative.is_absolute()
        or "\\" in raw_relative
        or "\x00" in raw_relative
        or any(part in {"", ".", ".."} for part in relative.parts)
    ):
        raise ValueError("prepared logical identity has an unsafe relative path")

    if descriptor.source_kind == "intodoc_scan":
        normalized = relative.as_posix()
        if normalized in _SCANNER_SEMANTIC_PATHS:
            return normalized
        if (
            len(relative.parts) >= 3
            and relative.parts[:2] == ("scan", "jobs")
            and relative.suffix.casefold() == ".json"
        ):
            opaque = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
            return f"scan/jobs/{opaque}.json"

    suffix = relative.suffix.casefold()
    safe_suffix = suffix if _SAFE_SUFFIX.fullmatch(suffix) else ".bin"
    opaque = hashlib.sha256(logical_identity.encode("utf-8")).hexdigest()
    return f"prepared/{opaque}{safe_suffix}"


def _same_file(path: Path, candidate: Path | None) -> bool:
    if candidate is None:
        return False
    try:
        return path.resolve(strict=True) == candidate.resolve(strict=True)
    except OSError:
        return False
