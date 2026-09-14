"""The additive structured slots on the business topology.

These fields exist because the same facts used to be concatenated into a
stream's ``description`` -- median 1,386 characters on the reference estate --
where a reader cannot table them, filter them, or compare two streams on them.
Every field is optional, so the point of this module is as much what still
validates without them as what validates with them: seven projects hold
published topologies that predate the slots, and none of them may be rejected.
"""

from __future__ import annotations

import json
from pathlib import Path

from mcp_server.services.business_topology.evidence import (
    build_business_topology_evidence,
)
from mcp_server.services.business_topology.validation import (
    parse_and_validate_business_topology,
)
from tests.test_services.test_business_topology import _write_scan


def _bundle_and_payload(tmp_path: Path) -> tuple[object, dict]:
    """A minimal valid topology with two scanner-backed systems and one stream."""

    scan_root = tmp_path / "scan-output"
    staging = tmp_path / "staging"
    staging.mkdir()
    _write_scan(scan_root)
    (staging / "_analysis").mkdir()
    (staging / "_analysis" / "orders.md").write_text(
        "# Orders\n\nREST GET is observed.", encoding="utf-8"
    )
    # A runtime observation needs typed runtime evidence, so the fixture carries
    # one accepted observed-runtime claim. Without it the measured-facts case
    # below would test the wrong failure.
    (staging / "_phase1").mkdir()
    (staging / "_phase1" / "supplemental.json").write_text(
        json.dumps(
            {
                "claims": [
                    {
                        "claim_id": "runtime-volume",
                        "claim_class": "observed_runtime",
                        "statement": "Message volume was observed.",
                        "evidence_ids": ["runtime:item"],
                        "fact_ids": [],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    bundle = build_business_topology_evidence(
        project_id="demo",
        scan_output_dir=scan_root,
        staging_dir=staging,
    )
    artifact_id = next(iter(bundle.artifact_ids))
    identity_by_scanner_id = {
        record["scanner_id"]: evidence_id
        for evidence_id, record in bundle.evidence_records.items()
        if bundle.evidence_types[evidence_id] == "scanner_system"
    }
    edge_id = next(
        evidence_id
        for evidence_id, evidence_type in bundle.evidence_types.items()
        if evidence_type == "scanner_edge"
    )
    payload = {
        "schema_version": "business-topology-v1",
        "scan_fingerprint": bundle.scan_fingerprint,
        "metadata": {
            "project_id": "demo",
            "scan_fingerprint": bundle.scan_fingerprint,
        },
        "systems": [
            {
                "id": "system:source",
                "name": "Order source",
                "status": "inferred",
                "confidence": 0.7,
                "evidence_ids": [
                    artifact_id,
                    identity_by_scanner_id["system:orders-source"],
                ],
            },
            {
                "id": "system:target",
                "name": "REST consumer",
                "status": "inferred",
                "confidence": 0.7,
                "evidence_ids": [
                    artifact_id,
                    identity_by_scanner_id["system:orders-consumer"],
                ],
            },
        ],
        "data_streams": [
            {
                "id": "stream:orders",
                "name": "Order delivery",
                "status": "inferred",
                "confidence": 0.8,
                "evidence_ids": [edge_id],
                "source_system_id": "system:source",
                "destination_system_id": "system:target",
                "business_summary": "Delivers orders to the REST consumer.",
                "artifact_ids": [artifact_id],
            }
        ],
        "coverage": {
            "total_artifacts": 0,
            "cited_artifacts": 0,
            "total_evidence": 0,
            "cited_evidence": 0,
            "coverage_ratio": 0,
        },
    }
    return bundle, payload


def _validate(payload: dict, bundle: object):
    return parse_and_validate_business_topology(
        json.dumps(payload), bundle=bundle, project_id="demo"
    )


def test_a_topology_without_the_structured_slots_still_validates(
    tmp_path: Path,
) -> None:
    """The regression that matters: published topologies predate these fields."""

    bundle, payload = _bundle_and_payload(tmp_path)
    topology, report = _validate(payload, bundle)

    assert report.validated is True
    assert topology is not None
    stream = topology.data_streams[0]
    assert topology.business_rules == []
    assert stream.contract_width is None
    assert stream.deployment is None
    assert stream.conditions == []
    assert stream.lifecycle == "unknown"
    assert stream.direction_pattern == "unknown"
    assert stream.verdict == ""
    assert stream.document_refs == []


def test_structured_stream_and_transformation_facts_round_trip(
    tmp_path: Path,
) -> None:
    bundle, payload = _bundle_and_payload(tmp_path)
    artifact_id = payload["data_streams"][0]["artifact_ids"][0]
    edge_id = payload["data_streams"][0]["evidence_ids"][0]
    payload["data_streams"][0].update(
        {
            "mechanism": "HTTP POST on the REST facade, then a queue.",
            "generation": "2 -- the message bus",
            "direction_pattern": "push",
            "lifecycle": "active",
            "contract_width": {
                "offered": 400,
                "mapped": 12,
                "written": 5,
                "note": "Header and cost attribution only.",
            },
            "conditions": [
                {
                    "kind": "ordering",
                    "statement": "Status is written last of four calls.",
                    "evidence_note": "Component order in the job.",
                },
                {
                    "kind": "trigger",
                    "statement": "The source publishes on change.",
                    "evidence_note": "Outside the delivered configuration.",
                },
            ],
            "deployment": {
                "supplied_version": "11.3",
                "deployed_version": "11.3",
                "built_at": "2025-08-22",
                "agreement": "equal",
                "note": "Bundle version from the runtime log.",
            },
            "key_form": "six digits, the work order number",
            "verdict": "replace -- the consumer becomes a subscriber",
            "document_refs": ["04_CHAINS.md -- 'Chain 1'"],
        }
    )
    payload["transformations"] = [
        {
            "id": "transform:fields",
            "name": "Field mapping source -> consumer",
            "status": "inferred",
            "confidence": 0.9,
            "evidence_ids": [edge_id],
            "stream_ids": ["stream:orders"],
            "artifact_ids": [artifact_id],
            "kind": "field_mapping",
            "mappings": [
                {
                    "source": "DESCRIPTION",
                    "target": "description",
                    "mapping_type": "derived",
                    "expression": "substring(DESCRIPTION, 1, 50)",
                    "category": "free text",
                    "reaches_target": "yes",
                    "note": "truncated at 50 characters",
                },
                {
                    "source": "WORKTYPE",
                    "target": "serviceType",
                    "mapping_type": "direct",
                    "category": "type",
                    "reaches_target": "no",
                    "note": "produced but never read",
                },
            ],
        },
        {
            "id": "transform:status-values",
            "name": "Value table: source status -> consumer status",
            "status": "inferred",
            "confidence": 0.9,
            "evidence_ids": [edge_id],
            "stream_ids": ["stream:orders"],
            "artifact_ids": [artifact_id],
            "kind": "value_domain",
            "target_field": "status",
            "mappings": [
                {
                    "source": "APPROVED",
                    "target": "In Process",
                    "mapping_type": "value",
                    "reaches_target": "yes",
                }
            ],
        },
    ]
    payload["business_rules"] = [
        {
            "id": "rule:status-collapse",
            "name": "Status collapse",
            "description": "Decides what the consumer can see about progress.",
            "status": "inferred",
            "confidence": 0.9,
            "evidence_ids": [edge_id],
            "kind": "value_mapping",
            "statement": (
                "Thirteen source statuses are projected onto three consumer "
                "statuses on every publication, statelessly."
            ),
            "inputs": ["STATUS"],
            "output": "status",
            "stream_ids": ["stream:orders"],
            "transformation_ids": ["transform:status-values"],
            "owner": "Business, consumer process",
            "owner_question": (
                "Is the three-state view authoritative, or a display convention?"
            ),
        }
    ]
    runtime_evidence_id = next(
        evidence_id
        for evidence_id, evidence_type in bundle.evidence_types.items()
        if evidence_type == "observed_runtime"
    )
    payload["runtime_observations"] = [
        {
            "id": "runtime:volume",
            "name": "Measured volume",
            "status": "observed",
            "confidence": 0.9,
            "evidence_ids": [runtime_evidence_id],
            "stream_ids": ["stream:orders"],
            "observed_behavior": "43,208 distinct business keys in eight months.",
            "observation_window": "2026-01-16 .. 2026-09-09",
            "kind": "volume",
            "source": "message registration",
            "metrics": [
                {
                    "label": "distinct business keys",
                    "value": "43208",
                    "window": "8 months",
                    "source": "message registration",
                }
            ],
        }
    ]
    payload["data_streams"][0]["runtime_observation_ids"] = ["runtime:volume"]

    topology, report = _validate(payload, bundle)

    assert report.validated is True, report.errors
    assert topology is not None
    stream = topology.data_streams[0]
    assert stream.contract_width is not None
    assert (
        stream.contract_width.offered,
        stream.contract_width.mapped,
        stream.contract_width.written,
    ) == (400, 12, 5)
    assert stream.direction_pattern == "push"
    assert stream.lifecycle == "active"
    assert stream.deployment is not None
    assert stream.deployment.agreement == "equal"
    assert [condition.kind for condition in stream.conditions] == [
        "ordering",
        "trigger",
    ]
    assert stream.key_form.startswith("six digits")
    assert stream.document_refs == ["04_CHAINS.md -- 'Chain 1'"]

    kinds = {item.id: item.kind for item in topology.transformations}
    assert kinds == {
        "transform:fields": "field_mapping",
        "transform:status-values": "value_domain",
    }
    value_domain = next(
        item for item in topology.transformations if item.kind == "value_domain"
    )
    assert value_domain.target_field == "status"
    field_mapping = next(
        item for item in topology.transformations if item.kind == "field_mapping"
    )
    assert field_mapping.mappings[0].expression == "substring(DESCRIPTION, 1, 50)"
    assert field_mapping.mappings[0].reaches_target == "yes"
    assert field_mapping.mappings[1].reaches_target == "no"

    rule = topology.business_rules[0]
    assert rule.kind == "value_mapping"
    assert rule.transformation_ids == ["transform:status-values"]
    assert rule.owner_question.endswith("?")
    assert topology.runtime_observations[0].metrics[0].value == "43208"
    assert topology.runtime_observations[0].kind == "volume"


def test_a_business_rule_must_state_something_and_resolve_its_references(
    tmp_path: Path,
) -> None:
    """A rule pointing at nothing is worse than no rule: it looks answered."""

    bundle, payload = _bundle_and_payload(tmp_path)
    edge_id = payload["data_streams"][0]["evidence_ids"][0]
    payload["business_rules"] = [
        {
            "id": "rule:dangling",
            "name": "Dangling rule",
            "status": "inferred",
            "confidence": 0.5,
            "evidence_ids": [edge_id],
            "statement": "A decision is taken somewhere.",
            "stream_ids": ["stream:absent"],
            "transformation_ids": ["transform:absent"],
        }
    ]

    topology, report = _validate(payload, bundle)

    assert topology is None
    assert any("unknown stream_id stream:absent" in error for error in report.errors)
    assert any(
        "unknown transformation_id transform:absent" in error
        for error in report.errors
    )

    payload["business_rules"][0]["stream_ids"] = ["stream:orders"]
    payload["business_rules"][0]["transformation_ids"] = []
    payload["business_rules"][0]["statement"] = "   "
    empty_topology, empty_report = _validate(payload, bundle)

    assert empty_topology is None
    assert any("rule:dangling has no statement" in error for error in empty_report.errors)


def test_contract_width_that_does_not_narrow_is_warned_about(
    tmp_path: Path,
) -> None:
    """A swapped pair of counts argues the opposite of the truth."""

    bundle, payload = _bundle_and_payload(tmp_path)
    payload["data_streams"][0]["contract_width"] = {
        "offered": 5,
        "mapped": 12,
        "written": 400,
    }

    topology, report = _validate(payload, bundle)

    assert topology is not None
    assert report.validated is True
    assert any(
        "contract width is not a narrowing funnel" in warning
        for warning in report.warnings
    )


def test_a_value_domain_without_a_target_field_is_warned_about(
    tmp_path: Path,
) -> None:
    """Unnested, a value table renders beside its field and reads as a rule."""

    bundle, payload = _bundle_and_payload(tmp_path)
    edge_id = payload["data_streams"][0]["evidence_ids"][0]
    artifact_id = payload["data_streams"][0]["artifact_ids"][0]
    payload["transformations"] = [
        {
            "id": "transform:values",
            "name": "Value table",
            "status": "inferred",
            "confidence": 0.9,
            "evidence_ids": [edge_id],
            "stream_ids": ["stream:orders"],
            "artifact_ids": [artifact_id],
            "kind": "value_domain",
            "mappings": [
                {"source": "APPROVED", "target": "In Process", "mapping_type": "value"}
            ],
        }
    ]

    topology, report = _validate(payload, bundle)

    assert topology is not None
    assert any(
        "value-domain claim(s) name no target field" in warning
        for warning in report.warnings
    )
