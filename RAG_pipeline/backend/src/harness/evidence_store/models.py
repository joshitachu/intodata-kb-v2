"""Validated normalized evidence contracts and metadata sanitization."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

_SENSITIVE_KEY = re.compile(
    r"(?:password|passwd|secret|token|credential|authorization|cookie|api[_-]?key)", re.I
)
_MAX_METADATA_BYTES = 8192


class EvidenceSourceClass(str, Enum):
    GENERATED_DOC = "generated_doc"
    INDEXED_DOC = "indexed_doc"
    CURRENT_RUNTIME = "current_runtime"
    SOURCE_TOPOLOGY = "source_topology"
    CONNECTOR = "connector"


class EvidenceSnapshot(BaseModel):
    """Safe evidence captured at retrieval time."""

    model_config = ConfigDict(frozen=True, str_strip_whitespace=True)

    id: int | None = None
    workflow_run_id: int | None = None
    source_class: EvidenceSourceClass
    source_id: str = Field(min_length=1, max_length=255)
    capability_id: str | None = Field(default=None, max_length=160)
    project_ref: str | None = Field(default=None, max_length=255)
    entity_ref: str | None = Field(default=None, max_length=255)
    connector_ref: str | None = Field(default=None, max_length=255)
    title: str = Field(min_length=1, max_length=500)
    summary: str = Field(min_length=1, max_length=4000)
    uri: str | None = Field(default=None, max_length=1000)
    observed_at: datetime | None = None
    retrieved_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    freshness: float = Field(default=0.5, ge=0, le=1)
    confidence: float = Field(default=0.5, ge=0, le=1)
    authority: float = Field(default=0.5, ge=0, le=1)
    score: float | None = Field(default=None, ge=0, le=1)
    claim_key: str | None = Field(default=None, max_length=255)
    claim_value: str | None = Field(default=None, max_length=1000)
    conflict_group: str | None = Field(default=None, max_length=80)
    conflict_refs: list[int] = Field(default_factory=list, max_length=50)
    collected_order: int | None = Field(default=None, ge=1)
    used_order: int | None = Field(default=None, ge=1)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("observed_at", "retrieved_at")
    @classmethod
    def timezone_required(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("evidence timestamps must include a timezone")
        return value

    @field_validator("metadata", mode="before")
    @classmethod
    def sanitize_metadata(cls, value: Any) -> dict[str, Any]:
        clean = _sanitize(value if isinstance(value, dict) else {}, depth=0)
        if (
            len(json.dumps(clean, sort_keys=True, separators=(",", ":")).encode())
            > _MAX_METADATA_BYTES
        ):
            raise ValueError("sanitized evidence metadata exceeds 8192 bytes")
        return clean


def _sanitize(value: Any, *, depth: int) -> Any:
    if depth > 3:
        return "[truncated]"
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for raw_key in sorted(value, key=str)[:50]:
            key = str(raw_key)[:100]
            if _SENSITIVE_KEY.search(key):
                continue
            result[key] = _sanitize(value[raw_key], depth=depth + 1)
        return result
    if isinstance(value, (list, tuple)):
        return [_sanitize(item, depth=depth + 1) for item in value[:50]]
    if isinstance(value, str):
        return value[:1000]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)[:1000]
