"""Bounded JSON and JSONL runtime-export adapter with canonical record receipts."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

from mcp_server.experiments.domain.sources import (
    RedactionState,
    SourceDescriptor,
    SourceLimits,
    SourceSnapshotContent,
    SourceSnapshotItem,
)
from mcp_server.experiments.infrastructure.sources.registry import (
    AdapterSnapshotResult,
    SourceAdapterError,
    read_regular,
)


@dataclass(frozen=True, slots=True)
class RuntimeExportLimits:
    max_file_bytes: int = 8 * 1024 * 1024
    max_records: int = 20_000
    max_line_bytes: int = 128 * 1024
    max_value_chars: int = 32_000
    max_depth: int = 16
    max_container_items: int = 1_000

    def __post_init__(self) -> None:
        if any(
            value < 1
            for value in (
                self.max_file_bytes,
                self.max_records,
                self.max_line_bytes,
                self.max_value_chars,
                self.max_depth,
                self.max_container_items,
            )
        ):
            raise ValueError("runtime export limits must be positive")


class RuntimeExportAdapter:
    id = "runtime-export"
    version = "1"

    def __init__(self, *, limits: RuntimeExportLimits = RuntimeExportLimits()) -> None:
        self._limits = limits

    def supports(self, descriptor: SourceDescriptor) -> bool:
        return descriptor.source_kind == "runtime_export"

    def snapshot(self, descriptor: SourceDescriptor, limits: SourceLimits) -> AdapterSnapshotResult:
        suffix = descriptor.source_path.suffix.casefold()
        raw = read_regular(
            descriptor.source_path, max_bytes=min(limits.max_bytes, self._limits.max_file_bytes)
        )
        if suffix == ".json":
            records = (self._parse_json_object(raw),)
            media_type = "application/json"
        elif suffix == ".jsonl":
            records = self._parse_jsonl_objects(raw)
            media_type = "application/x-ndjson"
        else:
            raise SourceAdapterError("runtime export adapter supports only .json and .jsonl files")
        if len(records) > self._limits.max_records:
            raise SourceAdapterError("runtime export record limit exceeded")
        captured = self._record_items(
            descriptor=descriptor, raw=raw, media_type=media_type, records=records
        )
        return AdapterSnapshotResult(
            items=tuple(item for item, _ in captured),
            contents=tuple(content for _, content in captured),
        )

    def _parse_json_object(self, raw: bytes) -> dict[str, object]:
        try:
            decoded = json.loads(raw.decode("utf-8"), object_pairs_hook=self._no_duplicate_keys)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
            raise SourceAdapterError("runtime JSON is malformed") from error
        if not isinstance(decoded, dict):
            raise SourceAdapterError("runtime JSON root must be an object")
        return self._normalise_value(decoded, depth=0)

    def _parse_jsonl_objects(self, raw: bytes) -> tuple[dict[str, object], ...]:
        records: list[dict[str, object]] = []
        for line_number, line in enumerate(raw.splitlines(), start=1):
            if not line.strip():
                continue
            if len(line) > self._limits.max_line_bytes:
                raise SourceAdapterError("runtime JSONL line exceeds byte limit")
            try:
                decoded = json.loads(line.decode("utf-8"), object_pairs_hook=self._no_duplicate_keys)
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
                raise SourceAdapterError(f"runtime JSONL line {line_number} is malformed") from error
            if not isinstance(decoded, dict):
                raise SourceAdapterError("runtime JSONL records must be objects")
            records.append(self._normalise_value(decoded, depth=0))
            if len(records) > self._limits.max_records:
                raise SourceAdapterError("runtime export record limit exceeded")
        if not records:
            raise SourceAdapterError("runtime JSONL contains no object records")
        return tuple(records)

    @staticmethod
    def _no_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON object key")
            result[key] = value
        return result

    def _normalise_value(self, value: Any, *, depth: int) -> Any:
        if depth > self._limits.max_depth:
            raise SourceAdapterError("runtime export nesting depth exceeded")
        if value is None or isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            if isinstance(value, float) and (value != value or value in (float("inf"), float("-inf"))):
                raise SourceAdapterError("runtime export contains non-finite number")
            return value
        if isinstance(value, str):
            if len(value) > self._limits.max_value_chars:
                raise SourceAdapterError("runtime export string exceeds character limit")
            return value
        if isinstance(value, list):
            if len(value) > self._limits.max_container_items:
                raise SourceAdapterError("runtime export array item limit exceeded")
            return [self._normalise_value(item, depth=depth + 1) for item in value]
        if isinstance(value, dict):
            if len(value) > self._limits.max_container_items:
                raise SourceAdapterError("runtime export object item limit exceeded")
            normalised: dict[str, object] = {}
            for key in sorted(value):
                if not isinstance(key, str) or not key:
                    raise SourceAdapterError("runtime export object keys must be non-blank strings")
                if len(key) > self._limits.max_value_chars:
                    raise SourceAdapterError("runtime export object key exceeds character limit")
                normalised[key] = self._normalise_value(value[key], depth=depth + 1)
            return normalised
        raise SourceAdapterError("runtime export contains unsupported JSON value")

    def _record_items(
        self,
        *,
        descriptor: SourceDescriptor,
        raw: bytes,
        media_type: str,
        records: tuple[dict[str, object], ...],
    ) -> tuple[tuple[SourceSnapshotItem, SourceSnapshotContent], ...]:
        raw_sha = hashlib.sha256(raw).hexdigest()
        seen: set[str] = set()
        captured = []
        for position, record in enumerate(records, start=1):
            record_key = self._record_key(record, position)
            logical_identity = f"{descriptor.id}:{descriptor.source_path.name}#record={record_key}"
            if logical_identity in seen:
                raise SourceAdapterError("runtime export has duplicate logical identity")
            seen.add(logical_identity)
            timestamp_field, timestamp = self._timestamp(record)
            canonical = json.dumps(record, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
            locator = f"{descriptor.provenance_locator}#record={quote(record_key, safe='')}"
            if timestamp is not None:
                locator += f"&timestamp={quote(timestamp, safe='')}"
            metadata = {
                "format": "jsonl" if media_type == "application/x-ndjson" else "json",
                "record_index": position,
                "record": record,
                "timestamp": timestamp,
                "timestamp_field": timestamp_field,
            }
            item = SourceSnapshotItem(
                    id=hashlib.sha256(logical_identity.encode("utf-8")).hexdigest(),
                    source_descriptor_id=descriptor.id,
                    logical_identity=logical_identity,
                    source_kind=descriptor.source_kind,
                    source_role=descriptor.source_role,
                    capabilities=descriptor.capabilities,
                    locator=locator,
                    raw_sha256=raw_sha,
                    extracted_sha256=hashlib.sha256(canonical).hexdigest(),
                    media_type=media_type,
                    size_bytes=len(raw),
                    extracted_size_bytes=len(canonical),
                    trust_level=descriptor.trust_level,
                    redaction_state=RedactionState.APPROVED,
                    adapter_id=self.id,
                    adapter_version=self.version,
                metadata=metadata,
            )
            captured.append(
                (
                    item,
                    SourceSnapshotContent(
                        item_id=item.id,
                        extracted_bytes=canonical,
                        extracted_sha256=item.extracted_sha256,
                        media_type="application/json",
                        size_bytes=len(canonical),
                    ),
                )
            )
        return tuple(captured)

    @staticmethod
    def _record_key(record: dict[str, object], position: int) -> str:
        for field_name in ("id", "record_id", "event_id"):
            value = record.get(field_name)
            if isinstance(value, (str, int)) and str(value).strip():
                return f"{field_name}:{value}"
        return str(position)

    @staticmethod
    def _timestamp(record: dict[str, object]) -> tuple[str | None, str | None]:
        for field_name in ("timestamp", "event_time", "occurred_at"):
            value = record.get(field_name)
            if isinstance(value, str) and value.strip():
                return field_name, value
        return None, None
