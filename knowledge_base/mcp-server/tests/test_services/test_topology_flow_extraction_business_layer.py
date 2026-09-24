"""The business layer of the doc-grounded topology lane (Stage A + Stage B).

Before this contract, assembly populated systems, streams, paths and plain
transformations only. Every business rule, control, risk, open question,
condition, key, verdict and owner the documents stated was either not asked
for or folded into `description` prose that no panel renders. These tests pin
that each such fact now lands in its structured field, keeps the documentation
tier (never `observed`), survives `validate_business_topology`, and that a
malformed optional item costs only itself.

All names are synthetic.
"""

from __future__ import annotations

import json
from pathlib import Path

from mcp_server.services.business_topology.assembly import (
    assemble_topology,
    merge_scanner_floor,
)
from mcp_server.services.business_topology.deterministic import (
    build_deterministic_topology,
)
from mcp_server.services.business_topology.doc_evidence import register_doc_evidence
from mcp_server.services.business_topology.evidence import (
    artifact_evidence_id,
    build_business_topology_evidence,
)
from mcp_server.services.business_topology.flow_extraction import (
    parse_flow_extraction,
)
from mcp_server.services.business_topology.pruning import prune_to_valid
from mcp_server.services.business_topology.validation import (
    validate_business_topology,
)

_ROOT = "IMPORT/SHOP"
_DESIGNS = ("Orders_Publish", "Orders_Receive", "Invoice_Export")


def _job(name: str) -> dict:
    return {
        "name": name,
        "job_id": name,
        "source_root": _ROOT,
        "path": f"Shop/{name}.item",
        "artifact_kind": "job",
        "artifact_role": "execution",
        "components": [],
        "connections": [],
    }


def _write_scan(root: Path) -> None:
    """Three systems, two directional edges, and lineage on the second edge."""

    (root / "scan").mkdir(parents=True)
    (root / "scan_result.json").write_text(
        json.dumps(
            {
                "platform": "talend",
                "jobs": [_job(name) for name in _DESIGNS],
                "metadata_connections": [],
            }
        ),
        encoding="utf-8",
    )
    (root / "scan" / "manifest.json").write_text(
        json.dumps(
            {
                "source_roots": [{"id": _ROOT, "project_id": "project-id"}],
                "artifact_relations": [],
                "artifact_resources": [],
                "artifact_endpoints": [],
                "artifact_channels": [],
            }
        ),
        encoding="utf-8",
    )
    (root / "scan" / "lineage.json").write_text(
        json.dumps(
            {
                "entries": [
                    {
                        "job_name": "Invoice_Export",
                        "source_column": "AMOUNT",
                        "target_column": "total",
                        "transformations": [{"transformation_type": "direct"}],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    systems = [
        {"id": f"system:{key}", "name": name, "kind": "software_system", "source_roots": [_ROOT]}
        for key, name in (
            ("orderhub", "OrderHub"),
            ("warehouse", "Warehouse"),
            ("billing", "Billing"),
        )
    ]
    edges = [
        {
            "source": "system:orderhub",
            "target": "system:warehouse",
            "dependency_type": "data",
            "mechanism": "rest",
            "direction": "out",
            "provenance": [{"artifact_id": "Orders_Publish"}],
        },
        {
            "source": "system:warehouse",
            "target": "system:billing",
            "dependency_type": "data",
            "mechanism": "file",
            "direction": "out",
            "provenance": [{"artifact_id": "Invoice_Export"}],
        },
    ]
    (root / "scan" / "landscape.json").write_text(
        json.dumps({"systems": systems, "applications": [], "edges": edges}),
        encoding="utf-8",
    )


def _write_docs(staging: Path) -> None:
    (staging / "02_FLOWS.md").write_text(
        "# Flows\n\n"
        "## Order release\n\nOrders are released to the warehouse.\n\n"
        "## Order rules\n\nThe later delivery date wins.\n\n"
        "## Safeguards\n\nMessages are schema-validated.\n\n"
        "## Open points\n\nThe outbound consumer is unknown.\n",
        encoding="utf-8",
    )


def _write_runtime_claim(staging: Path, artifact_id: str) -> None:
    """One typed runtime claim recorded against a real design."""

    (staging / "_phase1").mkdir()
    (staging / "_phase1" / "runtime.json").write_text(
        json.dumps(
            {
                "claims": [
                    {
                        "claim_id": "c1",
                        "claim_class": "observed_runtime",
                        "statement": "The design ran 40 times last month.",
                        "evidence_ids": [artifact_id],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )


def _bundles(tmp_path: Path, *, runtime_claim: bool = False):
    scan_root = tmp_path / "scan-output"
    staging = tmp_path / "staging"
    staging.mkdir()
    _write_scan(scan_root)
    _write_docs(staging)
    if runtime_claim:
        _write_runtime_claim(
            staging, artifact_evidence_id({"source_root": _ROOT, "job_id": "Orders_Publish"})
        )
    bundle = build_business_topology_evidence(
        project_id="demo", scan_output_dir=scan_root, staging_dir=staging
    )
    doc_bundle, doc_records = register_doc_evidence(bundle, staging)
    return bundle, doc_bundle, doc_records


_FLOW_NAME = "OrderHub -> Warehouse: Order release"

_EXTRACTION = {
    "subproject": "Shop",
    "systems": [
        {
            "name": "OrderHub",
            "role": "Order management",
            "kind": "application",
            "aliases": ["OH", "Order Hub"],
            "owner": "Sales operations",
        },
        {"name": "Warehouse", "role": "Stock and shipping", "kind": "application"},
    ],
    "flows": [
        {
            "name": _FLOW_NAME,
            "source_system": "Order Hub",
            "target_system": "Warehouse",
            "business_object": "Order",
            "carries": "Released orders with their lines",
            "mechanism": "REST push",
            "generation": "second-generation ESB",
            "key_form": "order number + line number",
            "verdict": "keep -- the only release path",
            "implementing_designs": ["Orders_Publish", "Orders_Receive"],
            "conditions": [
                {
                    "kind": "trigger",
                    "statement": "Runs when an order is released.",
                    "evidence": "02_FLOWS.md - 'Order release'",
                },
                {"kind": "not-a-kind", "statement": "Only for open orders."},
            ],
            "transformations": [
                {
                    "name": "Order status domain",
                    "kind": "value_domain",
                    "target_field": "status",
                    "implementing_designs": ["Orders_Receive"],
                    "mappings": [
                        {
                            "source": "REL",
                            "target": "Released",
                            "mapping_type": "value",
                            "expression": "decode(STATUS, 'REL', 'Released')",
                            "category": "status",
                            "reaches_target": True,
                            "note": "only value in use",
                        }
                    ],
                }
            ],
            "business_rules": [
                {
                    "name": "Latest date wins",
                    "kind": "precedence",
                    "statement": "When both dates exist, the later delivery date wins.",
                    "inputs": ["requested_date", "promised_date"],
                    "output": "delivery_date",
                    "owner": "Logistics",
                    "owner_question": "Is the later date still correct?",
                    "transformation": "Order status domain",
                    "evidence": "02_FLOWS.md - 'Order rules'",
                }
            ],
            "confidence": "high",
            "evidence": "02_FLOWS.md - 'Order release'",
        }
    ],
    "controls": [
        {
            "name": "Schema validation",
            "description": "Incoming order messages are validated.",
            "control_type": "validation",
            "flows": [_FLOW_NAME],
            "evidence": "02_FLOWS.md - 'Safeguards'",
        }
    ],
    "risks": [
        {
            "name": "Silent rejects",
            "description": "Rejected orders are not reported.",
            "severity": "HIGH",
            "flows": [_FLOW_NAME, "a flow that does not exist"],
            "evidence": "02_FLOWS.md - 'Safeguards'",
        }
    ],
    "open_questions": [
        {
            "question": "Who consumes the outbound file?",
            "flows": [],
            "evidence": "02_FLOWS.md - 'Open points'",
        }
    ],
    "conflicts": [
        {
            "description": "Documented as disabled, but runtime shows it running.",
            "conflict_type": "runtime_vs_implementation",
            "flows": [_FLOW_NAME],
            "evidence": "02_FLOWS.md - 'Order release'",
        }
    ],
}


def _assemble(tmp_path: Path, payload: dict | None = None, *, runtime_claim: bool = False):
    scanner_bundle, doc_bundle, doc_records = _bundles(tmp_path, runtime_claim=runtime_claim)
    extraction, dropped = parse_flow_extraction(
        json.dumps(payload or _EXTRACTION), subproject="Shop"
    )
    assert extraction is not None
    topology, notes = assemble_topology(
        extraction,
        bundle=doc_bundle,
        project_id="demo",
        doc_records=doc_records,
        subproject="Shop",
    )
    return scanner_bundle, doc_bundle, topology, notes, dropped


def _stream(topology):
    return next(item for item in topology.data_streams if "order" in item.id)


def test_business_layer_validates_end_to_end(tmp_path: Path) -> None:
    _, doc_bundle, topology, _, _ = _assemble(tmp_path)
    report = validate_business_topology(topology, bundle=doc_bundle, project_id="demo")
    assert report.validated, report.errors
    assert topology.business_rules
    assert topology.controls
    assert topology.risks
    assert topology.unknowns


def test_stream_structured_facts_are_fields_not_prose(tmp_path: Path) -> None:
    _, _, topology, _, _ = _assemble(tmp_path)
    stream = _stream(topology)
    assert stream.mechanism == "REST push"
    assert stream.generation == "second-generation ESB"
    assert stream.key_form == "order number + line number"
    assert stream.verdict == "keep -- the only release path"
    assert stream.document_refs == ["02_FLOWS.md - 'Order release'"]
    # The regression: mechanism used to be appended to both prose fields.
    assert "REST push" not in stream.description
    assert "mechanism" not in stream.business_summary.lower()
    assert stream.description == "Released orders with their lines"
    # Protocols are still derived from the structured mechanism.
    assert "REST" in stream.protocols


def test_conditions_keep_their_kind_and_unknown_kinds_are_downgraded(tmp_path: Path) -> None:
    _, _, topology, _, _ = _assemble(tmp_path)
    conditions = _stream(topology).conditions
    assert [condition.kind for condition in conditions] == ["trigger", "unknown"]
    assert conditions[0].statement == "Runs when an order is released."
    assert conditions[0].evidence_note == "02_FLOWS.md - 'Order release'"
    # A condition without its own citation inherits the flow's.
    assert conditions[1].evidence_note == "02_FLOWS.md - 'Order release'"


def test_transformation_kind_target_field_and_mapping_detail(tmp_path: Path) -> None:
    _, _, topology, _, _ = _assemble(tmp_path)
    transformation = topology.transformations[0]
    assert transformation.kind == "value_domain"
    assert transformation.target_field == "status"
    mapping = transformation.mappings[0]
    assert mapping.expression == "decode(STATUS, 'REL', 'Released')"
    assert mapping.category == "status"
    assert mapping.reaches_target == "yes"
    assert mapping.note == "only value in use"


def test_business_rule_is_cross_linked_and_capped_at_the_doc_tier(tmp_path: Path) -> None:
    _, doc_bundle, topology, _, _ = _assemble(tmp_path)
    stream = _stream(topology)
    assert stream.status == "observed", "fixture: the flow itself is scanner-backed"
    rule = topology.business_rules[0]
    assert rule.kind == "precedence"
    assert rule.statement == "When both dates exist, the later delivery date wins."
    assert rule.inputs == ["requested_date", "promised_date"]
    assert rule.output == "delivery_date"
    assert rule.owner == "Logistics"
    assert rule.owner_question == "Is the later date still correct?"
    assert rule.stream_ids == [stream.id]
    assert rule.transformation_ids == [topology.transformations[0].id]
    # A scanner edge proves the exchange, not the rule the prose describes.
    assert rule.status == "inferred"
    assert rule.confidence <= 0.6
    assert rule.evidence_ids
    assert all(
        doc_bundle.evidence_types[item] == "documentation_derived" for item in rule.evidence_ids
    )


def test_controls_risks_and_unknowns_link_back_to_streams(tmp_path: Path) -> None:
    _, _, topology, notes, _ = _assemble(tmp_path)
    stream = _stream(topology)
    control = topology.controls[0]
    risk = topology.risks[0]
    assert control.control_type == "validation"
    assert control.stream_ids == [stream.id]
    assert control.status != "observed"
    assert stream.control_ids == [control.id]
    assert risk.severity == "high"
    assert risk.stream_ids == [stream.id]
    assert stream.risk_ids == [risk.id]
    assert any("a flow that does not exist" in note for note in notes)

    question = next(item for item in topology.unknowns if "outbound" in item.question)
    assert question.status == "not_established"
    assert question.stream_ids == []
    assert question.evidence_ids


def test_system_aliases_and_owner_are_published(tmp_path: Path) -> None:
    _, _, topology, _, _ = _assemble(tmp_path)
    orderhub = next(item for item in topology.systems if item.name == "OrderHub")
    assert orderhub.owner == "Sales operations"
    # Declared aliases plus the spelling a flow used ("Order Hub").
    assert "OH" in orderhub.aliases
    assert "Order Hub" in orderhub.aliases
    assert "OrderHub" not in orderhub.aliases
    # The alias still folds: one node, not two.
    assert sum(item.name == "OrderHub" for item in topology.systems) == 1


def test_documented_conflict_without_typed_evidence_becomes_an_open_question(
    tmp_path: Path,
) -> None:
    _, doc_bundle, topology, notes, _ = _assemble(tmp_path)
    assert topology.conflicts == []
    stream = _stream(topology)
    question = next(item for item in topology.unknowns if "disabled" in item.question)
    assert question.stream_ids == [stream.id]
    assert question.id in stream.unknown_ids
    assert any("published as an open question" in note for note in notes)
    report = validate_business_topology(topology, bundle=doc_bundle, project_id="demo")
    assert report.validated, report.errors


def test_documented_conflict_with_typed_runtime_evidence_is_a_conflict(
    tmp_path: Path,
) -> None:
    _, doc_bundle, topology, _, _ = _assemble(tmp_path, runtime_claim=True)
    assert len(topology.conflicts) == 1
    conflict = topology.conflicts[0]
    stream = _stream(topology)
    assert conflict.conflict_type == "runtime_vs_implementation"
    assert conflict.subject_refs == [stream.id]
    assert stream.conflict_ids == [conflict.id]
    assert set(conflict.implementation_evidence_ids) <= set(stream.artifact_ids)
    assert all(
        doc_bundle.evidence_types[item] == "observed_runtime"
        for item in conflict.supporting_evidence_ids
    )
    report = validate_business_topology(topology, bundle=doc_bundle, project_id="demo")
    assert report.validated, report.errors


def test_a_malformed_optional_item_drops_only_itself(tmp_path: Path) -> None:
    payload = json.loads(json.dumps(_EXTRACTION))
    flow = payload["flows"][0]
    flow["business_rules"].append({"kind": "precedence", "inputs": ["x"]})  # no statement
    flow["business_rules"].append({"statement": "   "})  # blank statement
    flow["business_rules"].append("not an object")
    flow["transformations"][0]["mappings"].append({"source": {"nested": "object"}})
    payload["risks"].append({"name": "", "severity": "low"})
    payload["controls"].append({"description": "no name"})

    _, doc_bundle, topology, _, dropped = _assemble(tmp_path, payload)
    # Everything well-formed survived.
    assert len(topology.data_streams) >= 1
    assert len(topology.business_rules) == 1
    assert len(topology.transformations[0].mappings) == 1
    assert len(topology.risks) == 1
    assert len(topology.controls) == 1
    # Each bad item is reported individually.
    assert sum("rule" in note for note in dropped) == 3
    assert any("mapping" in note for note in dropped)
    assert any(note.startswith("risk") for note in dropped)
    assert any(note.startswith("control") for note in dropped)
    report = validate_business_topology(topology, bundle=doc_bundle, project_id="demo")
    assert report.validated, report.errors


def test_a_malformed_flow_field_drops_only_that_flow() -> None:
    payload = json.loads(json.dumps(_EXTRACTION))
    broken = json.loads(json.dumps(payload["flows"][0]))
    broken["name"] = "broken"
    broken["implementing_designs"] = {"not": "a list"}
    broken["carries"] = ["not", "a", "string"]
    payload["flows"].append(broken)
    extraction, dropped = parse_flow_extraction(json.dumps(payload), subproject="Shop")
    assert extraction is not None
    assert [flow.name for flow in extraction.flows] == [_FLOW_NAME]
    assert any("broken" in note for note in dropped)
    # The top-level findings are untouched by the broken flow.
    assert extraction.controls and extraction.risks


def test_a_rule_that_fails_validation_is_pruned_alone(tmp_path: Path) -> None:
    _, doc_bundle, topology, _, _ = _assemble(tmp_path)
    rule = topology.business_rules[0]
    rule.transformation_ids = ["transform:does-not-exist"]
    topology, report, dropped = prune_to_valid(topology, bundle=doc_bundle, project_id="demo")
    assert report.validated, report.errors
    assert dropped == [rule.id]
    assert topology.data_streams and topology.controls and topology.risks


def test_rule_prose_is_redacted_at_the_copy_boundary(tmp_path: Path) -> None:
    payload = json.loads(json.dumps(_EXTRACTION))
    payload["flows"][0]["business_rules"][0]["statement"] = (
        "Reads from mssql://svc_user:Hunter2Secret@db.internal/orders first."
    )
    payload["controls"][0]["description"] = "Called with Bearer Hunter2SecretTokenValue"
    payload["open_questions"][0]["question"] = (
        "Is mssql://svc:Hunter2Secret@db.internal/x still used?"
    )
    _, doc_bundle, topology, _, _ = _assemble(tmp_path, payload)
    dumped = topology.model_dump_json()
    assert "Hunter2Secret" not in dumped
    report = validate_business_topology(topology, bundle=doc_bundle, project_id="demo")
    assert report.validated, report.errors


def test_scanner_floor_keeps_its_lineage_transformations(tmp_path: Path) -> None:
    """The floor's field mappings travel with the stream it adds."""

    scanner_bundle, doc_bundle, doc_records = _bundles(tmp_path)
    deterministic = build_deterministic_topology(scanner_bundle, project_id="demo")
    floor_transforms = [
        item
        for item in deterministic.transformations
        if item.mappings and item.mappings[0].target == "total"
    ]
    assert floor_transforms, "fixture must give the floor a lineage transformation"

    extraction, _ = parse_flow_extraction(json.dumps(_EXTRACTION), subproject="Shop")
    assert extraction is not None
    topology, _ = assemble_topology(
        extraction,
        bundle=doc_bundle,
        project_id="demo",
        doc_records=doc_records,
        subproject="Shop",
    )
    notes = merge_scanner_floor(topology, deterministic)

    added = next(
        stream
        for stream in topology.data_streams
        if stream.id in {ref for item in floor_transforms for ref in item.stream_ids}
    )
    kept = [item for item in topology.transformations if added.id in item.stream_ids]
    assert kept and kept[0].mappings[0].source == "AMOUNT"
    # The deterministic topology is not shared with the published one.
    assert kept[0] is not floor_transforms[0]
    assert any("scanner-lineage transformation" in note for note in notes)
    report = validate_business_topology(topology, bundle=doc_bundle, project_id="demo")
    assert report.validated, report.errors


def test_scanner_floor_keeps_unknowns_for_adopted_or_no_stream(tmp_path: Path) -> None:
    from mcp_server.services.business_topology.models import BusinessUnknown

    scanner_bundle, doc_bundle, doc_records = _bundles(tmp_path)
    deterministic = build_deterministic_topology(scanner_bundle, project_id="demo")
    floor_stream = next(
        stream
        for stream in deterministic.data_streams
        if "billing" in stream.destination_system_id
    )
    edge_id = next(
        item for item in floor_stream.evidence_ids if item.startswith("landscape-edge")
    )
    attached = BusinessUnknown(
        id="unk-attached",
        name="attached",
        status="not_established",
        confidence=0.2,
        evidence_ids=[edge_id],
        stream_ids=[floor_stream.id],
        question="Which invoice number is used?",
    )
    orphaned = BusinessUnknown(
        id="unk-orphaned",
        name="orphaned",
        status="not_established",
        confidence=0.2,
        evidence_ids=[edge_id],
        stream_ids=["ds-not-adopted"],
        question="About a stream that is not adopted.",
    )
    landscape = BusinessUnknown(
        id="unk-landscape",
        name="landscape",
        status="not_established",
        confidence=0.2,
        evidence_ids=[edge_id],
        question="Which sibling implements this edge?",
    )
    deterministic.unknowns = [attached, orphaned, landscape]
    floor_stream.unknown_ids = ["unk-attached"]

    extraction, _ = parse_flow_extraction(json.dumps(_EXTRACTION), subproject="Shop")
    assert extraction is not None
    topology, _ = assemble_topology(
        extraction,
        bundle=doc_bundle,
        project_id="demo",
        doc_records=doc_records,
        subproject="Shop",
    )
    merge_scanner_floor(topology, deterministic)

    kept = {item.id for item in topology.unknowns}
    assert {"unk-attached", "unk-landscape"} <= kept
    assert "unk-orphaned" not in kept
    adopted = next(item for item in topology.data_streams if item.id == floor_stream.id)
    assert adopted.unknown_ids == ["unk-attached"]
    report = validate_business_topology(topology, bundle=doc_bundle, project_id="demo")
    assert report.validated, report.errors


def test_prompt_asks_for_the_business_layer_in_ascii() -> None:
    from mcp_server.services.business_topology.prompt import (
        business_topology_prompt,
        flow_extraction_prompt,
    )

    stage_a = flow_extraction_prompt(subproject="Shop", documents=["02_FLOWS.md"])
    stage_a.encode("ascii")
    for token in (
        "business_rules",
        "conditions",
        "key_form",
        "verdict",
        "generation",
        "controls",
        "risks",
        "open_questions",
        "conflicts",
        "target_field",
        "reaches_target",
        '"owner"',
    ):
        assert token in stage_a, token
    # The model is still not asked for bookkeeping.
    assert "evidence_ids" not in stage_a
    assert "artifact:" not in stage_a

    lane_two = business_topology_prompt(project_id="demo", scan_fingerprint="f" * 64)
    assert "BUSINESS LAYER CONTRACT" in lane_two
    for token in ("business_rules", "owner_question", "key_form", "verdict", "not_established"):
        assert token in lane_two, token
