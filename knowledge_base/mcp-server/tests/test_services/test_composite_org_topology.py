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
