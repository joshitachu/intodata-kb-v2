from __future__ import annotations

from datetime import datetime, timezone

from mcp_server.experiments.domain.evidence import Relationship, Subject
from mcp_server.experiments.domain.scope import (
    ScopePredicate, ScopeSpec, TraversalSpec, select_records, traverse_relationships,
)


def test_scope_accepts_new_open_taxonomies_without_code_change() -> None:
    records = (
        {"source_kind": "shipment_telemetry", "source_role": "runtime_observation", "capabilities": {"events"}, "metadata": {"region": "EU"}},
        {"source_kind": "future_custom_source", "source_role": "policy", "capabilities": {"searchable_text"}, "metadata": {"region": "US"}},
    )
    scope = ScopeSpec(
        include=(ScopePredicate("metadata.region", "equals", "EU"),),
        required_capabilities=frozenset({"events"}),
    )

    assert select_records(records, scope) == (records[0],)
    assert Subject(id="shipment:S1", type="logistics.shipment", label="S1").type == "logistics.shipment"


def test_graph_traversal_is_cycle_safe_and_generic() -> None:
    edges = (
        Relationship("shipment:S1", "has_event", "event:E1"),
        Relationship("event:E1", "owned_by", "team:ops"),
        Relationship("team:ops", "observes", "shipment:S1"),
    )
    found = traverse_relationships(
        start_subject_ids=("shipment:S1",), relationships=edges,
        spec=TraversalSpec(direction="outbound", max_depth=4),
    )

    assert found == {"shipment:S1", "event:E1", "team:ops"}


def test_scope_time_window_is_generic() -> None:
    timestamp = datetime(2026, 7, 27, tzinfo=timezone.utc)
    scope = ScopeSpec(start_at=timestamp, end_at=timestamp)

    assert select_records(({"capabilities": set(), "timestamp": timestamp},), scope)
    assert not select_records(({"capabilities": set()},), scope)
