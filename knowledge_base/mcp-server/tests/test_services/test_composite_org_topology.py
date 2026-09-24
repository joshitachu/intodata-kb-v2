"""Tests for the deterministic org-level topology merge (composite uploads)."""

from __future__ import annotations

import json
from pathlib import Path

from mcp_server.services.business_topology.composite import (
    build_composite_org_topology,
)
from mcp_server.services.business_topology.models import BusinessTopology

FINGERPRINT = "fp-abc123"


def _partition_topology(
    *, systems: list[dict], streams: list[dict]
) -> dict:
    return {
        "schema_version": "business-topology-v1",
        "scan_fingerprint": FINGERPRINT,
        "metadata": {
            "project_id": "acme",
            "scan_fingerprint": FINGERPRINT,
            "generated_by": "deterministic_topology_fallback",
            "model_name": "deterministic_topology_fallback",
        },
        "systems": systems,
        "integration_solutions": [],
        "business_objects": [],
        "data_streams": streams,
        "delivery_paths": [],
        "controls": [],
        "runtime_observations": [],
        "conflicts": [],
        "risks": [],
        "unknowns": [],
        "artifact_annotations": [],
        "component_annotations": [],
        "lineage_annotations": [],
        "coverage": {
            "total_artifacts": 10,
            "cited_artifacts": 4,
            "total_evidence": 100,
            "cited_evidence": 8,
            "coverage_ratio": 0.08,
            "technical_relationships": len(streams),
            "grounded_technical_relationships": len(streams),
            "notes": [],
        },
    }


def _write_partition(
    root: Path,
    project_id: str,
    partition_name: str,
    topology: dict | None,
    *,
    validated: bool = True,
) -> None:
    topo_dir = root / "output" / project_id / partition_name / "docs" / "_topology"
    topo_dir.mkdir(parents=True, exist_ok=True)
    if topology is not None:
        (topo_dir / "business_topology.json").write_text(
            json.dumps(topology), encoding="utf-8"
        )
    (topo_dir / "validation_report.json").write_text(
        json.dumps({"validated": validated}), encoding="utf-8"
    )


def _scaffold_composite(root: Path, project_id: str = "acme") -> None:
    topo_dir = root / "output" / project_id / "topology"
    topo_dir.mkdir(parents=True, exist_ok=True)
    (topo_dir / "partition_state.json").write_text(
        json.dumps(
            {
                "AppA_part1": {"scan_fingerprint": FINGERPRINT, "doc_count": 5},
                "AppA_part2": {"scan_fingerprint": FINGERPRINT, "doc_count": 5},
                "AppB": {"scan_fingerprint": FINGERPRINT, "doc_count": 5},
                "AppC": {"scan_fingerprint": FINGERPRINT, "doc_count": 5},
            }
        ),
        encoding="utf-8",
    )
    (topo_dir / "cross_project_index.json").write_text(
        json.dumps(
            {
                "project_id": project_id,
                "platform": "talend",
                "subprojects": {
                    "AppA": {"unit_count": 20},
                    "AppB": {"unit_count": 8},
                    "AppC": {"unit_count": 3},
                },
                "application_edges": [
                    {
                        "from_subproject": "AppA",
                        "to_subproject": "AppB",
                        "source_application": "AppA_System",
                        "target_application": "AppB_System",
                        "mechanism": "jms",
                    }
                ],
                "shared_landscape_systems": {},
            }
        ),
        encoding="utf-8",
    )

    # AppA_part1 failed to publish (mirrors werkorder ETTM_part1); part2 succeeds.
    _write_partition(root, project_id, "AppA_part1", None, validated=False)
    _write_partition(
        root,
        project_id,
        "AppA_part2",
        _partition_topology(
            systems=[
                {
                    "id": "sys-a",
                    "name": "AppA_System",
                    "kind": "software_system",
                    "description": "",
                    "status": "inferred",
                    "confidence": 0.4,
                    "evidence_ids": ["landscape-application:a"],
                    "aliases": [],
                    "purpose": "",
                    "owner": "",
                },
                {
                    "id": "sys-maximo",
                    "name": "Maximo",
                    "kind": "data_system",
                    "description": "",
                    "status": "inferred",
                    "confidence": 0.4,
                    "evidence_ids": ["landscape-system:maximo-a"],
                    "aliases": [],
                    "purpose": "",
                    "owner": "",
                },
            ],
            streams=[
                _stream("sys-a", "sys-maximo", "landscape-edge:a-maximo", ["art:1"]),
            ],
        ),
    )
    # AppB shares Maximo (must dedup into ONE org node) and receives the A->B edge.
    _write_partition(
        root,
        project_id,
        "AppB",
        _partition_topology(
            systems=[
                {
                    "id": "sys-b",
                    "name": "AppB_System",
                    "kind": "software_system",
                    "description": "",
                    "status": "inferred",
                    "confidence": 0.4,
                    "evidence_ids": ["landscape-application:b"],
                    "aliases": [],
                    "purpose": "",
                    "owner": "",
                },
                {
                    "id": "sys-maximo-b",
                    "name": "Maximo",
                    "kind": "data_system",
                    "description": "",
                    "status": "inferred",
                    "confidence": 0.4,
                    "evidence_ids": ["landscape-system:maximo-b"],
                    "aliases": [],
                    "purpose": "",
                    "owner": "",
                },
            ],
            streams=[
                _stream("sys-b", "sys-maximo-b", "landscape-edge:b-maximo", ["art:2"]),
            ],
        ),
    )
    # AppC published no topology at all (fully missing -> unknown).
    _write_partition(root, project_id, "AppC", None, validated=False)


def _stream(src: str, dst: str, evidence: str, artifacts: list[str]) -> dict:
    return {
        "id": f"ds-{src}-{dst}",
        "name": f"{src} -> {dst}",
        "description": "",
        "status": "inferred",
        "confidence": 0.4,
        "evidence_ids": [evidence],
        "source_system_id": src,
        "destination_system_id": dst,
        "business_object_ids": [],
        "business_summary": "",
        "integration_solution_ids": [],
        "delivery_path_id": None,
        "control_ids": [],
        "risk_ids": [],
        "unknown_ids": [],
        "runtime_observation_ids": [],
        "conflict_ids": [],
        "artifact_ids": artifacts,
    }


def test_returns_none_when_not_composite(tmp_path: Path) -> None:
    (tmp_path / "output" / "plain").mkdir(parents=True)
    assert build_composite_org_topology("plain", project_root=tmp_path) is None


def test_merges_partitions_into_validated_org_topology(tmp_path: Path) -> None:
    _scaffold_composite(tmp_path)

    topology = build_composite_org_topology("acme", project_root=tmp_path)
    assert topology is not None

    # The result must satisfy the strict backend contract (mirrors frontend Zod).
    revalidated = BusinessTopology.model_validate(topology.model_dump(mode="json"))
    assert revalidated.metadata.generated_by == "composite_org_merge"
    assert revalidated.scan_fingerprint == FINGERPRINT

    names = [system.name for system in topology.systems]
    # Maximo appears once despite two partitions declaring it.
    assert names.count("Maximo") == 1
    maximo = next(system for system in topology.systems if system.name == "Maximo")
    # Its evidence is the union of both partitions' identity citations.
    assert set(maximo.evidence_ids) == {
        "landscape-system:maximo-a",
        "landscape-system:maximo-b",
    }

    system_ids = {system.id for system in topology.systems}
    for stream in topology.data_streams:
        assert stream.source_system_id in system_ids
        assert stream.destination_system_id in system_ids

    # The cross-project application edge (AppA -> AppB via JMS) is present.
    id_by_name = {system.name: system.id for system in topology.systems}
    pairs = {
        (stream.source_system_id, stream.destination_system_id)
        for stream in topology.data_streams
    }
    assert (id_by_name["AppA_System"], id_by_name["AppB_System"]) in pairs

    # One solution per logical sub-project; the fully-missing one is an unknown.
    assert {solution.name for solution in topology.integration_solutions} == {
        "AppA",
        "AppB",
        "AppC",
    }
    assert any("AppC" in unknown.name for unknown in topology.unknowns)
    # AppA published via part2, so it is NOT flagged missing.
    assert not any("AppA:" in unknown.name for unknown in topology.unknowns)

    # Coverage is the honest sum of contributing partitions (AppA_part2 + AppB).
    assert topology.coverage.total_evidence == 200
    assert 0.0 <= topology.coverage.coverage_ratio <= 1.0


def _rich_partition_topology() -> dict:
    """A partition topology carrying the business layer the org view must keep.

    Two flows between the SAME pair of systems, distinguished only by business
    object -- the shape that de-duplicating on (source, destination) alone
    silently collapsed into one unlabelled edge.
    """

    topology = _partition_topology(
        systems=[
            {
                "id": "sys-maximo",
                "name": "Maximo",
                "description": "EAM",
                "status": "observed",
                "confidence": 0.8,
                "evidence_ids": ["landscape-system:aaa"],
                "kind": "software_system",
                "aliases": [],
                "purpose": "",
                "owner": "",
            },
            {
                "id": "sys-4ps",
                "name": "4PS",
                "description": "ERP",
                "status": "observed",
                "confidence": 0.8,
                "evidence_ids": ["landscape-system:bbb"],
                "kind": "software_system",
                "aliases": [],
                "purpose": "",
                "owner": "",
            },
        ],
        streams=[],
    )
    workorder = _stream("sys-maximo", "sys-4ps", "landscape-edge:1", ["artifact:1"])
    workorder.update(
        {
            "id": "ds-local-workorder",
            "name": "Maximo -> 4PS: Work Order",
            "status": "observed",
            "confidence": 0.8,
            "business_object_ids": ["bo-local-wo"],
            "protocols": ["REST", "JMS"],
            "delivery_path_id": "path-local-workorder",
        }
    )
    invoice = _stream("sys-maximo", "sys-4ps", "landscape-edge:2", ["artifact:2"])
    invoice.update(
        {
            "id": "ds-local-invoice",
            "name": "Maximo -> 4PS: Invoice",
            "business_object_ids": ["bo-local-inv"],
            "protocols": ["REST"],
        }
    )
    topology["data_streams"] = [workorder, invoice]
    topology["business_objects"] = [
        {
            "id": "bo-local-wo",
            "name": "WorkOrder",
            "description": "",
            "status": "inferred",
            "confidence": 0.7,
            "evidence_ids": ["doc:aaa"],
            "aliases": ["Work order"],
        },
        {
            "id": "bo-local-inv",
            "name": "Invoice",
            "description": "",
            "status": "inferred",
            "confidence": 0.7,
            "evidence_ids": ["doc:bbb"],
            "aliases": [],
        },
    ]
    topology["delivery_paths"] = [
        {
            "id": "path-local-workorder",
            "name": "Maximo to 4PS WorkOrder delivery",
            "description": "REST + JMS",
            "status": "observed",
            "confidence": 0.8,
            "evidence_ids": ["landscape-edge:1"],
            "stream_id": "ds-local-workorder",
            "segments": [
                {
                    "id": "segment:ds-local-workorder:1",
                    "name": "Receive webhook",
                    "description": "",
                    "status": "observed",
                    "confidence": 0.8,
                    "evidence_ids": ["landscape-edge:1"],
                    "sequence": 1,
                    "source_ref": "sys-maximo",
                    "destination_ref": "step:ds-local-workorder:1",
                    "mechanism": "REST webhook",
                    "protocol": "REST",
                    "resource": "WorkOrder XML",
                    "component": "maximoRestService",
                    "artifact_ids": ["artifact:1"],
                    "mode": "event_driven",
                },
                {
                    "id": "segment:ds-local-workorder:2",
                    "name": "Write service order",
                    "description": "",
                    "status": "observed",
                    "confidence": 0.8,
                    "evidence_ids": ["landscape-edge:1"],
                    "sequence": 2,
                    "source_ref": "step:ds-local-workorder:1",
                    "destination_ref": "sys-4ps",
                    "mechanism": "REST API",
                    "protocol": "REST",
                    "resource": "serviceOrders",
                    "component": "maximoFourpsWorkOrder",
                    "artifact_ids": ["artifact:1"],
                    "mode": "direct",
                },
            ],
        }
    ]
    topology["transformations"] = [
        {
            "id": "transform:ds-local-workorder:1",
            "name": "WorkOrder status mapping",
            "description": "",
            "status": "observed",
            "confidence": 0.8,
            "evidence_ids": ["landscape-edge:1"],
            "stream_ids": ["ds-local-workorder"],
            "integration_solution_ids": [],
            "artifact_ids": ["artifact:1"],
            "mappings": [
                {
                    "source": "STATUS=VOORBEREID",
                    "target": "In Process",
                    "mapping_type": "value",
                }
            ],
        }
    ]
    return topology


def test_org_merge_carries_the_business_layer(tmp_path: Path) -> None:
    _scaffold_composite(tmp_path)
    _write_partition(tmp_path, "acme", "AppA_part2", _rich_partition_topology())

    topology = build_composite_org_topology("acme", project_root=tmp_path)
    assert topology is not None

    # Two business flows between the same pair of systems survive as two.
    maximo_to_4ps = [
        stream
        for stream in topology.data_streams
        if stream.source_system_id == "sys-maximo"
        and stream.destination_system_id == "sys-4ps"
    ]
    assert len(maximo_to_4ps) == 2
    assert {stream.name for stream in maximo_to_4ps} == {
        "Maximo -> 4PS: Work Order",
        "Maximo -> 4PS: Invoice",
    }

    assert {item.name for item in topology.business_objects} == {"WorkOrder", "Invoice"}
    assert topology.transformations
    assert topology.transformations[0].mappings[0].target == "In Process"

    # The scanner-backed flow keeps its stronger status instead of being
    # flattened to `inferred`.
    workorder = next(s for s in maximo_to_4ps if "Work Order" in s.name)
    assert workorder.status == "observed"
    assert workorder.protocols == ["REST", "JMS"]

    # The delivery path is re-pointed onto org ids and stays a connected chain
    # anchored to the stream's endpoints.
    assert len(topology.delivery_paths) == 1
    path = topology.delivery_paths[0]
    assert path.stream_id == workorder.id
    assert workorder.delivery_path_id == path.id
    assert [segment.sequence for segment in path.segments] == [1, 2]
    assert path.segments[0].source_ref == "sys-maximo"
    assert path.segments[-1].destination_ref == "sys-4ps"
    assert path.segments[0].destination_ref == path.segments[1].source_ref
    assert path.segments[0].destination_ref.startswith(f"step:{workorder.id}:")

    # The org solution is no longer artifact-less, so L1 drills down to code.
    solution = next(item for item in topology.integration_solutions if item.name == "AppA")
    assert solution.artifact_ids


def test_org_merge_validates_against_the_published_contract(tmp_path: Path) -> None:
    _scaffold_composite(tmp_path)
    _write_partition(tmp_path, "acme", "AppA_part2", _rich_partition_topology())
    topology = build_composite_org_topology("acme", project_root=tmp_path)
    assert topology is not None
    # Round-trips through the served contract (the frontend Zod shape).
    assert BusinessTopology.model_validate(topology.model_dump(mode="json"))


# ---------------------------------------------------------------------------
# Whole business layer, merge rule, real validation, freshness
# ---------------------------------------------------------------------------

import pytest  # noqa: E402

from mcp_server.services.business_topology.composite import (  # noqa: E402
    build_validated_composite_org_topology,
    resolve_current_partitions,
)

CURRENT = "fp-current"


def _write_state(root: Path, entries: dict, project_id: str = "acme") -> None:
    topo_dir = root / "output" / project_id / "topology"
    topo_dir.mkdir(parents=True, exist_ok=True)
    (topo_dir / "partition_state.json").write_text(json.dumps(entries), encoding="utf-8")


def _write_ledger(
    root: Path, partition: str, records: list[dict], project_id: str = "acme"
) -> None:
    """A partition evidence ledger in the exact on-disk shape Phase 3.7 writes."""

    topo_dir = root / "output" / project_id / partition / "docs" / "_topology"
    evidence_dir = topo_dir / "evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    (topo_dir / "factual_evidence_index.json").write_text(
        json.dumps({"scan_fingerprint": CURRENT}), encoding="utf-8"
    )
    (evidence_dir / "evidence_ids.json").write_text(
        json.dumps({"evidence": {r["id"]: r["type"] for r in records}}), encoding="utf-8"
    )
    (evidence_dir / "records.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in records), encoding="utf-8"
    )


def _claim(id_: str, evidence: list[str], **extra) -> dict:
    return {
        "id": id_,
        "name": extra.pop("name", id_),
        "description": extra.pop("description", ""),
        "status": extra.pop("status", "inferred"),
        "confidence": extra.pop("confidence", 0.7),
        "evidence_ids": evidence,
        **extra,
    }


def _ledger_records(suffix: str = "") -> list[dict]:
    return [
        {"id": "landscape-system:mx", "type": "scanner_system", "name": "Maximo", "scanner_id": "system:mx"},
        {"id": "landscape-system:4ps", "type": "scanner_system", "name": "4PS", "scanner_id": "system:4ps"},
        {
            "id": "landscape-edge:mx-4ps",
            "type": "scanner_edge",
            "source_identity_evidence_id": "landscape-system:mx",
            "destination_identity_evidence_id": "landscape-system:4ps",
            "artifact_names": ["WO_Job"],
        },
        {"id": "artifact:wo", "type": "scanner_artifact", "name": "WO_Job", "job_id": "WO_Job"},
        {"id": "component:c1", "type": "scanner_component", "name": "tMap_1"},
        {"id": "lineage:l1", "type": "scanner_lineage", "job_name": "WO_Job"},
        {"id": "runtime:obs1", "type": "observed_runtime", "statement": "48 runs/day"},
        {"id": "claim:intended", "type": "intended_documented", "statement": "Only approved"},
    ]


def _full_partition_topology(project_id: str = "acme") -> dict:
    """One partition exercising every collection and every stream field."""

    topology = _partition_topology(systems=[], streams=[])
    topology["metadata"]["project_id"] = project_id
    topology["scan_fingerprint"] = CURRENT
    topology["metadata"]["scan_fingerprint"] = CURRENT
    topology["systems"] = [
        _claim(
            "sys-mx",
            ["landscape-system:mx"],
            name="Maximo",
            status="observed",
            confidence=0.9,
            kind="software_system",
            aliases=["IBM Maximo"],
            purpose="Enterprise asset management",
            owner="Asset management",
        ),
        _claim("sys-4ps", ["landscape-system:4ps"], name="4PS", kind="software_system"),
    ]
    topology["integration_solutions"] = [
        _claim("sol-local", ["artifact:wo"], name="AppA runtime", platform="talend", role="execution", artifact_ids=["artifact:wo"]),
    ]
    topology["business_objects"] = [
        _claim("bo-local-wo", ["artifact:wo"], name="WorkOrder", aliases=["Work order"]),
    ]
    topology["data_streams"] = [
        _claim(
            "ds-local-wo",
            ["landscape-edge:mx-4ps"],
            name="Maximo -> 4PS: Work Order",
            status="observed",
            source_system_id="sys-mx",
            destination_system_id="sys-4ps",
            business_object_ids=["bo-local-wo"],
            business_summary="Approved work orders become 4PS service orders.",
            integration_solution_ids=["sol-local"],
            protocols=["REST"],
            requiredness="required",
            delivery_path_id="path-local-wo",
            control_ids=["ctl-1"],
            risk_ids=["risk-1"],
            unknown_ids=["unk-1"],
            runtime_observation_ids=["obs-1"],
            conflict_ids=["conf-1"],
            artifact_ids=["artifact:wo"],
            mechanism="REST webhook",
            generation="gen2",
            direction_pattern="push",
            lifecycle="active",
            contract_width={"offered": 40, "mapped": 12, "written": 5, "note": "tMap_1"},
            conditions=[{"kind": "filter", "statement": "Only STATUS=APPR", "evidence_note": "tFilterRow_1"}],
            deployment={"supplied_version": "0.3", "deployed_version": "0.3", "agreement": "equal"},
            key_form="WONUM",
            verdict="keep",
            document_refs=["03_DATA_FLOWS.md"],
        )
    ]
    topology["delivery_paths"] = [
        _claim(
            "path-local-wo",
            ["landscape-edge:mx-4ps"],
            stream_id="ds-local-wo",
            segments=[
                _claim(
                    "segment:ds-local-wo:1",
                    ["landscape-edge:mx-4ps"],
                    sequence=1,
                    source_ref="sys-mx",
                    destination_ref="sys-4ps",
                    protocol="REST",
                    component="tRESTClient_1",
                    artifact_ids=["artifact:wo"],
                )
            ],
        )
    ]
    topology["transformations"] = [
        _claim(
            "tr-1",
            ["artifact:wo"],
            stream_ids=["ds-local-wo"],
            artifact_ids=["artifact:wo"],
            kind="value_domain",
            target_field="STATUS",
            mappings=[{"source": "APPR", "target": "Released", "mapping_type": "value"}],
        )
    ]
    topology["business_rules"] = [
        _claim(
            "rule-1",
            ["artifact:wo"],
            statement="Only approved work orders are sent",
            stream_ids=["ds-local-wo"],
            transformation_ids=["tr-1"],
            owner="Operations",
            kind="selection",
        )
    ]
    topology["controls"] = [
        _claim("ctl-1", ["artifact:wo"], stream_ids=["ds-local-wo"], control_type="retry")
    ]
    topology["risks"] = [
        _claim("risk-1", ["artifact:wo"], stream_ids=["ds-local-wo"], severity="high")
    ]
    topology["unknowns"] = [
        _claim(
            "unk-1",
            ["artifact:wo"],
            status="not_established",
            stream_ids=["ds-local-wo"],
            question="Who owns the 4PS mapping?",
        )
    ]
    topology["runtime_observations"] = [
        _claim(
            "obs-1",
            ["runtime:obs1"],
            status="observed",
            stream_ids=["ds-local-wo"],
            delivery_path_ids=["path-local-wo"],
            observed_behavior="Runs every 30 minutes",
            kind="cadence",
            metrics=[{"label": "runs", "value": "48", "window": "1d", "source": "TMC"}],
        )
    ]
    topology["conflicts"] = [
        _claim(
            "conf-1",
            ["artifact:wo", "claim:intended"],
            conflict_type="supporting_vs_implementation",
            subject_refs=["ds-local-wo", "rule-1"],
            implementation_evidence_ids=["artifact:wo"],
            supporting_evidence_ids=["claim:intended"],
        )
    ]
    topology["artifact_annotations"] = [
        _claim("ann-1", ["artifact:wo"], artifact_id="artifact:wo", business_role="publisher")
    ]
    topology["component_annotations"] = [
        _claim("comp-1", ["component:c1"], component_id="component:c1", human_explanation="Maps status")
    ]
    topology["lineage_annotations"] = [
        _claim(
            "lin-1",
            ["lineage:l1"],
            lineage_id="lineage:l1",
            business_meaning="Status lineage",
            business_object_id="bo-local-wo",
        )
    ]
    return topology


def _scaffold_full(root: Path) -> None:
    _write_state(root, {"AppA": {"scan_fingerprint": CURRENT}, "__meta__": {"shared_artifact_digest": "x"}})
    _write_partition(root, "acme", "AppA", _full_partition_topology())
    _write_ledger(root, "AppA", _ledger_records())


def test_reserved_meta_entry_is_not_a_partition(tmp_path: Path) -> None:
    _scaffold_full(tmp_path)
    topology = build_composite_org_topology(
        "acme", project_root=tmp_path, scan_fingerprint=CURRENT
    )
    assert topology is not None
    assert [solution.name for solution in topology.integration_solutions] == ["AppA"]
    assert not any("meta" in unknown.id for unknown in topology.unknowns)


def test_composite_preserves_every_stream_field_and_collection(tmp_path: Path) -> None:
    _scaffold_full(tmp_path)
    topology = build_composite_org_topology(
        "acme", project_root=tmp_path, scan_fingerprint=CURRENT
    )
    assert topology is not None
    [stream] = topology.data_streams
    assert stream.id == "ds-maximo--4ps-workorder"
    assert (stream.mechanism, stream.generation, stream.direction_pattern) == (
        "REST webhook",
        "gen2",
        "push",
    )
    assert (stream.lifecycle, stream.key_form, stream.verdict, stream.requiredness) == (
        "active",
        "WONUM",
        "keep",
        "required",
    )
    assert stream.contract_width is not None
    assert (stream.contract_width.offered, stream.contract_width.mapped, stream.contract_width.written) == (40, 12, 5)
    assert stream.conditions[0].statement == "Only STATUS=APPR"
    assert stream.deployment is not None and stream.deployment.agreement == "equal"
    assert stream.document_refs == ["03_DATA_FLOWS.md"]

    # Collections travel, ids re-namespaced per partition ...
    assert [c.id for c in topology.controls] == ["appa:ctl-1"]
    assert [r.id for r in topology.risks] == ["appa:risk-1"]
    assert topology.risks[0].severity == "high"
    assert "appa:unk-1" in {u.id for u in topology.unknowns}
    assert [o.id for o in topology.runtime_observations] == ["appa:obs-1"]
    assert topology.runtime_observations[0].metrics[0].value == "48"
    assert [c.id for c in topology.conflicts] == ["appa:conf-1"]
    assert [a.id for a in topology.artifact_annotations] == ["appa:ann-1"]
    assert [a.id for a in topology.component_annotations] == ["appa:comp-1"]
    assert [a.id for a in topology.lineage_annotations] == ["appa:lin-1"]

    # ... and every reference is re-pointed onto org ids.
    assert stream.control_ids == ["appa:ctl-1"]
    assert stream.risk_ids == ["appa:risk-1"]
    assert stream.unknown_ids == ["appa:unk-1"]
    assert stream.runtime_observation_ids == ["appa:obs-1"]
    assert stream.conflict_ids == ["appa:conf-1"]
    assert topology.controls[0].stream_ids == [stream.id]
    observation = topology.runtime_observations[0]
    assert observation.stream_ids == [stream.id]
    assert observation.delivery_path_ids == [stream.delivery_path_id]
    [rule] = topology.business_rules
    assert rule.stream_ids == [stream.id]
    assert rule.transformation_ids == [topology.transformations[0].id]
    assert set(topology.conflicts[0].subject_refs) == {stream.id, rule.id}
    assert topology.lineage_annotations[0].business_object_id == "bo-workorder"

    # System purpose / owner / aliases / status survive the merge.
    maximo = next(s for s in topology.systems if s.name == "Maximo")
    assert maximo.purpose == "Enterprise asset management"
    assert maximo.owner == "Asset management"
    assert maximo.aliases == ["IBM Maximo"]
    assert (maximo.status, maximo.confidence) == ("observed", 0.9)

    # The partition's own solution evidence folds into the org solution.
    [solution] = topology.integration_solutions
    assert solution.role == "execution"
    assert "artifact:wo" in solution.evidence_ids


def test_composite_validates_for_real_against_partition_ledgers(tmp_path: Path) -> None:
    _scaffold_full(tmp_path)
    result = build_validated_composite_org_topology(
        "acme", project_root=tmp_path, scan_fingerprint=CURRENT
    )
    assert result is not None
    assert result.report.errors == []
    assert result.validated
    assert result.dropped_ids == []
    assert result.topology is not None
    assert len(result.topology.conflicts) == 1
    assert len(result.topology.runtime_observations) == 1
    # Coverage is recomputed against the org bundle, not summed from partitions.
    assert result.topology.coverage.total_evidence == len(_ledger_records())

    topo_dir = tmp_path / "output" / "acme" / "topology"
    result.write(topo_dir)
    report = json.loads((topo_dir / "validation_report.json").read_text(encoding="utf-8"))
    assert report["validated"] is True
    assert report["generated_by"] == "composite_org_merge"
    assert report["pruned_count"] == 0
    assert (topo_dir / "business_topology.json").is_file()


def _broken_partition(description: str = "") -> dict:
    topology = _full_partition_topology()
    topology["data_streams"].append(
        _claim(
            "ds-local-bad",
            ["evidence:not-in-any-ledger"],
            description=description,
            source_system_id="sys-4ps",
            destination_system_id="sys-mx",
            business_summary="Asserted without scanner evidence",
            artifact_ids=["artifact:wo"],
        )
    )
    return topology


def test_claim_local_defect_is_pruned_and_reported(tmp_path: Path) -> None:
    _write_state(tmp_path, {"AppA": {"scan_fingerprint": CURRENT}})
    _write_partition(tmp_path, "acme", "AppA", _broken_partition())
    _write_ledger(tmp_path, "AppA", _ledger_records())

    result = build_validated_composite_org_topology(
        "acme", project_root=tmp_path, scan_fingerprint=CURRENT
    )
    assert result is not None
    assert result.validated
    assert result.dropped_ids == ["ds-4ps--maximo"]
    assert result.topology is not None
    assert [s.id for s in result.topology.data_streams] == ["ds-maximo--4ps-workorder"]
    assert any("dropped" in warning for warning in result.report.warnings)
    assert result.report_payload()["pruned_claim_ids"] == ["ds-4ps--maximo"]


def test_global_defect_leaves_the_merge_unvalidated_and_unwritten(tmp_path: Path) -> None:
    _write_state(tmp_path, {"AppA": {"scan_fingerprint": CURRENT}})
    topology = _full_partition_topology()
    topology["systems"][1]["description"] = "reached at jdbc://svc:hunter2@db.internal"
    _write_partition(tmp_path, "acme", "AppA", topology)
    _write_ledger(tmp_path, "AppA", _ledger_records())
    topo_dir = tmp_path / "output" / "acme" / "topology"
    (topo_dir / "business_topology.json").write_text("{}", encoding="utf-8")

    result = build_validated_composite_org_topology(
        "acme", project_root=tmp_path, scan_fingerprint=CURRENT
    )
    assert result is not None
    assert not result.validated
    assert result.topology is None
    assert any("secret-like" in error for error in result.report.errors)

    result.write(topo_dir)
    report = json.loads((topo_dir / "validation_report.json").read_text(encoding="utf-8"))
    assert report["validated"] is False
    assert report["errors"]
    # A stale served artifact is removed, never left to be mistaken for current.
    assert not (topo_dir / "business_topology.json").exists()


def test_missing_org_evidence_is_never_reported_as_validated(tmp_path: Path) -> None:
    _write_state(tmp_path, {"AppA": {"scan_fingerprint": CURRENT}})
    _write_partition(tmp_path, "acme", "AppA", _full_partition_topology())

    result = build_validated_composite_org_topology(
        "acme", project_root=tmp_path, scan_fingerprint=CURRENT
    )
    assert result is not None
    assert not result.validated
    assert "evidence catalogue is unavailable" in result.report.errors[0]


def _stream_variant(**fields) -> dict:
    topology = _rich_partition_topology()
    topology["data_streams"] = [topology["data_streams"][0]]
    topology["data_streams"][0].update(fields)
    topology["transformations"] = []
    return topology


def _scaffold_two(root: Path, first: dict, second: dict) -> None:
    _write_state(
        root,
        {
            "AppA": {"scan_fingerprint": FINGERPRINT},
            "AppB": {"scan_fingerprint": FINGERPRINT},
        },
    )
    _write_partition(root, "acme", "AppA", first)
    _write_partition(root, "acme", "AppB", second)


def test_multi_partition_merge_rule(tmp_path: Path) -> None:
    first = _stream_variant(
        status="inferred",
        protocols=["REST"],
        verdict="",
        lifecycle="unknown",
        mechanism="REST webhook",
        document_refs=["A.md"],
        conditions=[{"kind": "filter", "statement": "Only APPR"}],
        artifact_ids=["artifact:1"],
        contract_width={"offered": 40, "mapped": 12, "written": 5},
    )
    second = _stream_variant(
        status="observed",
        protocols=["JMS"],
        verdict="retire",
        lifecycle="duplicate",
        mechanism="JMS queue",
        document_refs=["B.md", "A.md"],
        conditions=[{"kind": "filter", "statement": "only appr"}],
        artifact_ids=["artifact:2"],
        contract_width={"offered": 10, "mapped": 3, "written": 2},
    )
    _scaffold_two(tmp_path, first, second)
    topology = build_composite_org_topology("acme", project_root=tmp_path)
    assert topology is not None
    [stream] = topology.data_streams
    assert stream.status == "observed"  # stronger status wins
    assert stream.protocols == ["REST", "JMS"]  # lists unioned
    assert stream.document_refs == ["A.md", "B.md"]
    assert len(stream.conditions) == 1  # de-duplicated case-insensitively
    assert stream.mechanism == "REST webhook"  # first established scalar kept
    assert stream.verdict == "retire"  # empty scalar filled from the next partition
    assert stream.lifecycle == "duplicate"  # `unknown` filled
    assert set(stream.integration_solution_ids) == {"int-sol-appa", "int-sol-appb"}
    # Disjoint implementing designs: widths are summed and the note says so.
    assert stream.contract_width is not None
    assert (stream.contract_width.offered, stream.contract_width.mapped, stream.contract_width.written) == (50, 15, 7)
    assert "Summed" in stream.contract_width.note


def test_overlapping_designs_keep_first_width_and_record_disagreement(tmp_path: Path) -> None:
    first = _stream_variant(artifact_ids=["artifact:1"], contract_width={"offered": 40, "mapped": 12, "written": 5})
    second = _stream_variant(artifact_ids=["artifact:1"], contract_width={"offered": 41, "mapped": 12, "written": 5})
    _scaffold_two(tmp_path, first, second)
    topology = build_composite_org_topology("acme", project_root=tmp_path)
    assert topology is not None
    width = topology.data_streams[0].contract_width
    assert width is not None
    assert width.offered == 40
    assert "AppB reports offered/mapped/written 41/12/5" in width.note


def test_single_width_is_taken_verbatim(tmp_path: Path) -> None:
    first = _stream_variant(artifact_ids=["artifact:1"], contract_width={"offered": 40, "mapped": 12, "written": 5, "note": "n"})
    second = _stream_variant(artifact_ids=["artifact:2"])
    _scaffold_two(tmp_path, first, second)
    topology = build_composite_org_topology("acme", project_root=tmp_path)
    assert topology is not None
    width = topology.data_streams[0].contract_width
    assert width is not None and (width.offered, width.note) == (40, "n")


# --- freshness: whole-scan vs per-partition fingerprint ----------------------


def test_org_fingerprint_is_the_current_scan_not_an_arbitrary_partition(tmp_path: Path) -> None:
    """partition_state.scan_fingerprint is the digest a partition was LAST
    published from; a reused partition legitimately keeps an older one. The old
    ``sorted(fingerprints)[0]`` picked whichever sorted first."""

    _write_state(
        tmp_path,
        {
            # "aaa..." sorts first: the old rule would have stamped the org view
            # with this stale digest.
            "AppA": {"scan_fingerprint": "aaa-older-scan", "partition_fingerprint": "pA"},
            "AppB": {"scan_fingerprint": CURRENT, "partition_fingerprint": "pB"},
        },
    )
    _write_partition(tmp_path, "acme", "AppA", _rich_partition_topology())
    _write_partition(tmp_path, "acme", "AppB", _rich_partition_topology())

    # AppA changed since it was published -> excluded, reported, and the org
    # view is still stamped with the CURRENT scan.
    stale = build_composite_org_topology(
        "acme", project_root=tmp_path, scan_fingerprint=CURRENT, current_partitions=set()
    )
    assert stale is not None
    assert stale.scan_fingerprint == CURRENT
    assert {s.name for s in stale.integration_solutions if s.status != "not_established"} == {"AppB"}
    assert any("older scan" in unknown.description for unknown in stale.unknowns)

    # AppA unchanged per the skip rule -> current despite its older digest.
    reused = build_composite_org_topology(
        "acme", project_root=tmp_path, scan_fingerprint=CURRENT, current_partitions={"AppA"}
    )
    assert reused is not None
    assert {s.name for s in reused.integration_solutions if s.status != "not_established"} == {"AppA", "AppB"}


def test_no_caller_fingerprint_uses_only_an_unambiguous_recorded_digest(tmp_path: Path) -> None:
    _write_state(
        tmp_path,
        {"AppA": {"scan_fingerprint": "fp-1"}, "AppB": {"scan_fingerprint": "fp-2"}},
    )
    _write_partition(tmp_path, "acme", "AppA", _rich_partition_topology())
    topology = build_composite_org_topology("acme", project_root=tmp_path)
    assert topology is not None
    assert topology.scan_fingerprint == ""


def test_resolve_current_partitions_uses_the_skip_rule(tmp_path: Path, monkeypatch) -> None:
    from types import SimpleNamespace

    import mcp_server.services.partition_orchestrator as orchestrator

    _write_state(
        tmp_path,
        {
            "AppA": {"scan_fingerprint": CURRENT},
            "AppB": {"scan_fingerprint": "older", "partition_fingerprint": "pB"},
            "AppC": {"scan_fingerprint": "older", "partition_fingerprint": "pC"},
            "__meta__": {"shared_artifact_digest": "x"},
        },
    )
    monkeypatch.setattr(
        orchestrator,
        "build_generation_plan",
        lambda project_id, resume=True: SimpleNamespace(
            decisions=[
                SimpleNamespace(name="AppB", reason=orchestrator.REASON_UNCHANGED),
                SimpleNamespace(name="AppC", reason=orchestrator.REASON_CHANGED),
            ]
        ),
    )
    assert resolve_current_partitions(
        "acme", project_root=tmp_path, scan_fingerprint=CURRENT
    ) == {"AppA", "AppB"}


def test_all_partitions_stale_is_reported_as_stale(tmp_path: Path) -> None:
    _write_state(tmp_path, {"AppA": {"scan_fingerprint": "older"}})
    _write_partition(tmp_path, "acme", "AppA", _rich_partition_topology())
    result = build_validated_composite_org_topology(
        "acme", project_root=tmp_path, scan_fingerprint=CURRENT, current_partitions=set()
    )
    assert result is not None
    assert result.all_stale
    assert not result.validated
    assert result.stale_partitions == ["AppA"]


@pytest.mark.parametrize("grounded", [True, False])
def test_cross_project_edge_cites_scanner_evidence_or_is_not_asserted(
    tmp_path: Path, grounded: bool
) -> None:
    _scaffold_full(tmp_path)
    topo_dir = tmp_path / "output" / "acme" / "topology"
    (topo_dir / "cross_project_index.json").write_text(
        json.dumps(
            {
                "subprojects": {"AppA": {"unit_count": 1}},
                "application_edges": [
                    {"source_application": "AppA_App", "target_application": "Maximo", "mechanism": "jms"}
                ],
            }
        ),
        encoding="utf-8",
    )
    records = _ledger_records()
    if grounded:
        records += [
            {"id": "landscape-application:a", "type": "scanner_application", "name": "AppA_App", "scanner_id": "talend-project:a"},
            {
                "id": "landscape-edge:a-mx",
                "type": "scanner_edge",
                "source_identity_evidence_id": "landscape-application:a",
                "destination_identity_evidence_id": "landscape-system:mx",
                "artifact_names": ["WO_Job"],
            },
        ]
    _write_ledger(tmp_path, "AppA", records)

    result = build_validated_composite_org_topology(
        "acme", project_root=tmp_path, scan_fingerprint=CURRENT
    )
    assert result is not None and result.validated, result.report.errors
    assert result.topology is not None
    edge = [s for s in result.topology.data_streams if s.name == "AppA_App -> Maximo"]
    if grounded:
        assert edge and edge[0].evidence_ids == ["landscape-edge:a-mx"]
        assert edge[0].artifact_ids == ["artifact:wo"]
    else:
        assert not edge
        assert any("not asserted" in note for note in result.topology.coverage.notes)
    assert result.dropped_ids == []


def test_rejected_partition_system_is_not_promoted(tmp_path: Path) -> None:
    topology = _rich_partition_topology()
    topology["systems"][1]["status"] = "rejected"
    _write_state(tmp_path, {"AppA": {"scan_fingerprint": FINGERPRINT}})
    _write_partition(tmp_path, "acme", "AppA", topology)
    merged = build_composite_org_topology("acme", project_root=tmp_path)
    assert merged is not None
    assert next(s for s in merged.systems if s.name == "4PS").status == "rejected"


def test_unknown_current_scan_is_not_validated(tmp_path: Path) -> None:
    _scaffold_full(tmp_path)
    result = build_validated_composite_org_topology(
        "acme", project_root=tmp_path, scan_fingerprint=""
    )
    assert result is not None and not result.validated
    assert result.report.errors == ["current scan fingerprint is unavailable"]
