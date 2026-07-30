from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import hashlib
from pathlib import Path

import pytest

from mcp_server.experiments.domain.evidence import EvidenceItem, EvidenceLocator, Relationship, Subject
from mcp_server.experiments.domain.scope import ScopePredicate, ScopeSpec
from mcp_server.experiments.domain.sources import (
    RedactionState, SourceDescriptor, SourceSnapshot, SourceSnapshotItem,
    SourceSnapshotContent,
)


def _locator(**overrides: object) -> EvidenceLocator:
    values: dict[str, object] = {
        "source_snapshot_item_id": "source-1",
        "locator": "sheet:Events,row:2",
        "content_sha256": "a" * 64,
        "line_start": 1,
        "line_end": 2,
    }
    values.update(overrides)
    return EvidenceLocator(**values)  # type: ignore[arg-type]


def test_open_evidence_contracts_reject_invalid_identifiers_ranges_and_confidence() -> None:
    with pytest.raises(ValueError, match="subject type"):
        Subject(id="shipment:S1", type="", label="Shipment")
    with pytest.raises(ValueError, match="SHA-256"):
        _locator(content_sha256="bad")
    with pytest.raises(ValueError, match="positive and ordered"):
        _locator(line_start=3, line_end=2)
    with pytest.raises(ValueError, match="relationship predicate"):
        Relationship("source", "", "target")
    with pytest.raises(ValueError, match="between 0 and 1"):
        Relationship("source", "has_event", "target", confidence=1.1)
    with pytest.raises(ValueError, match="timestamp range"):
        EvidenceItem(
            id="event:E1", source_snapshot_item_id="source-1", kind="runtime_event", locator=_locator(),
            timestamp=datetime(2026, 7, 28, tzinfo=timezone.utc),
            end_timestamp=datetime(2026, 7, 27, tzinfo=timezone.utc),
        )


def test_scope_construction_rejects_invalid_policy_predicates_and_bounds() -> None:
    with pytest.raises(ValueError, match="unsupported generic scope predicate"):
        ScopePredicate("source_kind", "regex", "anything")
    with pytest.raises(ValueError, match="time range"):
        ScopeSpec(
            start_at=datetime(2026, 7, 28, tzinfo=timezone.utc),
            end_at=datetime(2026, 7, 27, tzinfo=timezone.utc),
        )
    with pytest.raises(ValueError, match="non-blank and unique"):
        ScopeSpec(requested_output_ids=("runbook", "runbook"))
    with pytest.raises(ValueError, match="must not overlap"):
        ScopeSpec(required_capabilities=frozenset({"events"}), optional_capabilities=frozenset({"events"}))
    with pytest.raises(ValueError, match="unknown_policy"):
        ScopeSpec(unknown_policy="guess")


def test_source_descriptor_and_snapshot_reject_empty_capabilities_and_duplicate_item_ids(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="capabilities"):
        SourceDescriptor(
            id="source", source_kind="future_source", source_role="policy", source_path=tmp_path,
            provenance_locator="fixture://source",
        )
    with pytest.raises(ValueError, match="at least one"):
        SourceSnapshot.build(snapshot_id="empty", items=(), contents=())
    item = SourceSnapshotItem(
        id="same-id", source_descriptor_id="source", logical_identity="source:file-a",
        source_kind="future_source", source_role="policy", capabilities=frozenset({"text"}),
        locator="fixture://source/file-a", raw_sha256=hashlib.sha256(b"x").hexdigest(), extracted_sha256=hashlib.sha256(b"x").hexdigest(),
        media_type="text/plain", size_bytes=1, extracted_size_bytes=1, trust_level="trusted",
        redaction_state=RedactionState.APPROVED, adapter_id="test", adapter_version="1",
    )
    with pytest.raises(ValueError, match="duplicate item ids"):
        SourceSnapshot.build(
            snapshot_id="duplicate", items=(item, replace(item, logical_identity="source:file-b")),
            contents=(SourceSnapshotContent(
                item_id="same-id", extracted_bytes=b"x", extracted_sha256=hashlib.sha256(b"x").hexdigest(),
                media_type="text/plain", size_bytes=1,
            ),),
        )
