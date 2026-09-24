"""get_topology_node on a business stream carries the overlay's facts about it.

The graph edge holds identity and wiring only; rules, conditions, contract
width, controls, risks, open questions and runtime measurements live in the
overlay's own collections. Before `business_facts`, a question about the rules
on one exchange could only be answered with the overview's rule COUNT.
"""

from __future__ import annotations

import copy
from typing import Any

import pytest

from src.topology.graph import EDGE_KEY_SEPARATOR
from src.topology.models import GetTopologyNodeInput
from src.topology.tools import get_topology_node
from tests.test_topology.conftest import json_transport, make_provider

STREAM_ID = "stream:assetcheck-to-log-0"
EDGE_KEY = f"business-stream{EDGE_KEY_SEPARATOR}{STREAM_ID}"

pytestmark = pytest.mark.asyncio


def _enriched(landscape: dict[str, Any]) -> dict[str, Any]:
    payload = copy.deepcopy(landscape)
    business = payload["business_topology"]
    stream = next(item for item in business["data_streams"] if item["id"] == STREAM_ID)
    stream.update(
        {
            "mechanism": "Talend DI job: tDBInput -> tMap -> tDBOutput",
            "lifecycle": "active",
            "direction_pattern": "pull",
            "contract_width": {"offered": None, "mapped": 12, "written": 5, "note": "lineage"},
            "conditions": [{"kind": "filter", "statement": "status == 'OPEN'"}],
            "risk_ids": ["risk:1"],
            "runtime_observation_ids": ["rt:1"],
        }
    )
    business["business_rules"] = [
        {
            "id": "rule:1",
            "name": "Open orders only",
            "status": "inferred",
            "confidence": 0.6,
            "evidence_ids": [],
            "kind": "selection",
            "statement": "Only open orders are sent.",
            "stream_ids": [STREAM_ID],
            "owner_question": "Should closed orders also be sent?",
        }
    ]
    business["risks"] = [
        {"id": "risk:1", "name": "No retry", "status": "inferred", "confidence": 0.5,
         "evidence_ids": [], "stream_ids": [], "severity": "high"}
    ]
    business["runtime_observations"] = [
        {"id": "rt:1", "name": "Reliability", "status": "observed", "confidence": 0.9,
         "evidence_ids": [], "stream_ids": [], "observed_behavior": "2 failures in 100 runs",
         "kind": "reliability",
         "metrics": [{"label": "failure rate", "value": "2", "unit": "%", "window": "90d"}]}
    ]
    return payload


async def _relationship(payload: dict[str, Any]) -> dict[str, Any]:
    result = await get_topology_node(
        GetTopologyNodeInput(project_id="alitalend", name_or_id=EDGE_KEY),
        provider=make_provider(json_transport(payload)),
        access_token=None,
    )
    assert result.content["matched_entity"] == "relationship"
    return result.content


async def test_business_stream_relationship_carries_overlay_facts(alitalend) -> None:
    content = await _relationship(_enriched(alitalend))
    facts = content["business_facts"]

    assert facts["stream_id"] == STREAM_ID
    assert facts["mechanism"].startswith("Talend DI job")
    assert facts["lifecycle"] == "active"
    assert facts["contract_width"]["written"] == 5
    assert facts["conditions"][0]["kind"] == "filter"
    assert facts["business_rules"][0]["owner_question"] == "Should closed orders also be sent?"
    assert facts["risks"][0]["severity"] == "high"
    assert facts["runtime_observations"][0]["metrics"][0]["label"] == "failure rate"
    # The edge now states the stream's own mechanism, not its protocol list.
    assert content["relationship"]["mechanism"].startswith("Talend DI job")


async def test_rules_and_conflicts_link_as_the_canvas_links_them(alitalend) -> None:
    """Rules via a stream's transformations; conflicts via subject_refs."""
    payload = _enriched(alitalend)
    business = payload["business_topology"]
    business["transformations"] = [
        {"id": "tr:1", "name": "Status mapping", "status": "inferred", "confidence": 0.6,
         "evidence_ids": [], "stream_ids": [STREAM_ID], "kind": "value_domain", "mappings": []}
    ]
    business["business_rules"] = [
        {"id": "rule:via-transformation", "name": "Status precedence", "status": "inferred",
         "confidence": 0.6, "evidence_ids": [], "kind": "precedence",
         "statement": "Closed wins over open.", "stream_ids": [],
         "transformation_ids": ["tr:1"]}
    ]
    business["conflicts"] = [
        {"id": "conflict:1", "name": "Docs disagree", "status": "inferred", "confidence": 0.5,
         "evidence_ids": [], "conflict_type": "supporting_sources_disagree",
         "subject_refs": [STREAM_ID]}
    ]

    facts = (await _relationship(payload))["business_facts"]

    assert [rule["name"] for rule in facts["business_rules"]] == ["Status precedence"]
    assert [conflict["name"] for conflict in facts["conflicts"]] == ["Docs disagree"]


async def test_absent_facts_are_omitted_not_reported_as_unknown(alitalend) -> None:
    content = await _relationship(alitalend)
    facts = content["business_facts"]

    assert facts["stream_id"] == STREAM_ID
    for absent in ("lifecycle", "direction_pattern", "contract_width", "business_rules", "risks"):
        assert absent not in facts
