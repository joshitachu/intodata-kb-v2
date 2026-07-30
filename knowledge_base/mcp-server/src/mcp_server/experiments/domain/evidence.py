"""Generic evidence-graph contracts with open subject and relationship taxonomies."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Mapping


def _require_nonblank(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-blank string")


def _require_confidence(value: float | None, field_name: str) -> None:
    if value is not None and not 0 <= value <= 1:
        raise ValueError(f"{field_name} must be between 0 and 1")


@dataclass(frozen=True)
class Subject:
    id: str
    type: str
    label: str
    attributes: Mapping[str, object] = field(default_factory=dict)
    tags: frozenset[str] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        _require_nonblank(self.id, "subject id")
        _require_nonblank(self.type, "subject type")
        _require_nonblank(self.label, "subject label")
        if any(not tag.strip() for tag in self.tags):
            raise ValueError("subject tags must be non-blank")


@dataclass(frozen=True)
class EvidenceLocator:
    source_snapshot_item_id: str
    locator: str
    content_sha256: str
    line_start: int | None = None
    line_end: int | None = None

    def __post_init__(self) -> None:
        _require_nonblank(self.source_snapshot_item_id, "source_snapshot_item_id")
        _require_nonblank(self.locator, "locator")
        if len(self.content_sha256) != 64 or any(
            char not in "0123456789abcdef" for char in self.content_sha256
        ):
            raise ValueError("locator content_sha256 must be a lowercase SHA-256 digest")
        if (self.line_start is None) != (self.line_end is None):
            raise ValueError("locator line ranges require both start and end")
        if self.line_start is not None and (self.line_start < 1 or self.line_end is None or self.line_end < self.line_start):
            raise ValueError("locator line ranges must be positive and ordered")


@dataclass(frozen=True)
class Relationship:
    source_subject_id: str
    predicate: str
    target_subject_id: str
    evidence_refs: tuple[str, ...] = ()
    attributes: Mapping[str, object] = field(default_factory=dict)
    confidence: float | None = None

    def __post_init__(self) -> None:
        _require_nonblank(self.source_subject_id, "relationship source_subject_id")
        _require_nonblank(self.predicate, "relationship predicate")
        _require_nonblank(self.target_subject_id, "relationship target_subject_id")
        if any(not reference.strip() for reference in self.evidence_refs):
            raise ValueError("relationship evidence refs must be non-blank")
        _require_confidence(self.confidence, "relationship confidence")


@dataclass(frozen=True)
class EvidenceItem:
    id: str
    source_snapshot_item_id: str
    kind: str
    locator: EvidenceLocator
    subject_ids: tuple[str, ...] = ()
    timestamp: datetime | None = None
    end_timestamp: datetime | None = None
    confidence: float | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_nonblank(self.id, "evidence id")
        _require_nonblank(self.source_snapshot_item_id, "evidence source_snapshot_item_id")
        _require_nonblank(self.kind, "evidence kind")
        if any(not subject_id.strip() for subject_id in self.subject_ids):
            raise ValueError("evidence subject ids must be non-blank")
        if self.timestamp is not None and self.end_timestamp is not None and self.timestamp > self.end_timestamp:
            raise ValueError("evidence timestamp range must be ordered")
        _require_confidence(self.confidence, "evidence confidence")
