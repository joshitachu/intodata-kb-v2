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
