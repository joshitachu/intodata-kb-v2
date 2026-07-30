from __future__ import annotations

import json
from pathlib import Path

import pytest

from mcp_server.experiments.domain.sources import SourceDescriptor, SourceLimits
from mcp_server.experiments.infrastructure.sources.registry import SourceAdapterError
from mcp_server.experiments.infrastructure.sources.runtime_export import (
    RuntimeExportAdapter,
    RuntimeExportLimits,
)


FIXTURE_ROOT = Path(__file__).parents[1] / "fixtures" / "agentic_experiments" / "logistics_operations"


def _descriptor(path: Path) -> SourceDescriptor:
    return SourceDescriptor(
        id="logistics-runtime",
        source_kind="runtime_export",
        source_role="runtime_observation",
        source_path=path,
        provenance_locator="fixture://logistics/runtime",
        capabilities=frozenset({"runtime_events", "timestamped_records"}),
        trust_level="governed_fixture",
    )


def test_jsonl_events_have_deterministic_record_and_timestamp_provenance() -> None:
    adapter = RuntimeExportAdapter()
    result = adapter.snapshot(_descriptor(FIXTURE_ROOT / "runtime_events.jsonl"), SourceLimits())

    assert len(result.items) == 2
    first = result.items[0]
    assert first.logical_identity.endswith("#record=event_id:evt-100")
    assert "timestamp=2026-07-27T10%3A00%3A00Z" in first.locator
    assert first.metadata["record"]["event"] == "loaded"
    assert first.redaction_state.value == "approved"


@pytest.mark.parametrize(
    ("name", "payload", "message"),
    [
        ("non-object.json", "[]", "root must be an object"),
        ("malformed.jsonl", "{bad}\n", "malformed"),
        ("duplicate.jsonl", '{"id":"same"}\n{"id":"same"}\n', "duplicate logical identity"),
    ],
)
def test_runtime_export_rejects_malformed_nonobject_and_duplicate_records(
    tmp_path: Path, name: str, payload: str, message: str
) -> None:
    path = tmp_path / name
    path.write_text(payload, encoding="utf-8")

    with pytest.raises(SourceAdapterError, match=message):
        RuntimeExportAdapter().snapshot(_descriptor(path), SourceLimits())


def test_runtime_export_rejects_deep_values_and_huge_jsonl_lines(tmp_path: Path) -> None:
    deep_path = tmp_path / "deep.json"
    value: object = {"id": "deep"}
    for _ in range(4):
        value = {"nested": value}
    deep_path.write_text(json.dumps(value), encoding="utf-8")
    huge_path = tmp_path / "huge.jsonl"
    huge_path.write_text('{"id":"big","value":"' + "x" * 32 + '"}\n', encoding="utf-8")

    with pytest.raises(SourceAdapterError, match="depth"):
        RuntimeExportAdapter(limits=RuntimeExportLimits(max_depth=2)).snapshot(
            _descriptor(deep_path), SourceLimits()
        )
    with pytest.raises(SourceAdapterError, match="line"):
        RuntimeExportAdapter(limits=RuntimeExportLimits(max_line_bytes=16)).snapshot(
            _descriptor(huge_path), SourceLimits()
        )
