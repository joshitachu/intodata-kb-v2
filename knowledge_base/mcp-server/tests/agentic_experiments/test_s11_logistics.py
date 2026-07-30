from __future__ import annotations

from pathlib import Path

from mcp_server.experiments.domain.sources import SourceDescriptor, SourceLimits
from mcp_server.experiments.infrastructure.sources.runtime_export import RuntimeExportAdapter
from mcp_server.experiments.infrastructure.sources.tabular import TabularFileAdapter


FIXTURE_ROOT = Path(__file__).parents[1] / "fixtures" / "agentic_experiments" / "logistics_operations"


def test_logistics_fixture_needs_no_logistics_specific_core_fields() -> None:
    shipment_descriptor = SourceDescriptor(
        id="shipments",
        source_kind="tabular_file",
        source_role="operational_mapping",
        source_path=FIXTURE_ROOT / "status_mapping.csv",
        provenance_locator="fixture://logistics/status-mapping",
        capabilities=frozenset({"tabular_data", "mapping_reference"}),
        trust_level="governed_fixture",
    )
    runtime_descriptor = SourceDescriptor(
        id="events",
        source_kind="runtime_export",
        source_role="runtime_observation",
        source_path=FIXTURE_ROOT / "runtime_events.jsonl",
        provenance_locator="fixture://logistics/events",
        capabilities=frozenset({"timestamped_records", "runtime_events"}),
        trust_level="governed_fixture",
    )

    mapping = TabularFileAdapter().snapshot(shipment_descriptor, SourceLimits()).items
    events = RuntimeExportAdapter().snapshot(runtime_descriptor, SourceLimits()).items

    assert (FIXTURE_ROOT / "warehouse_transport_sop.md").is_file()
    assert mapping[0].source_role == "operational_mapping"
    assert events[0].source_role == "runtime_observation"
    assert all(item.trust_level == "governed_fixture" for item in (*mapping, *events))
    assert all(item.redaction_state.value == "approved" for item in (*mapping, *events))
