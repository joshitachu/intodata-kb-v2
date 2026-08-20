"""Tests for the deterministic org-level evidence catalog merge (composite uploads)."""

from __future__ import annotations

import json
from pathlib import Path

from mcp_server.services.business_topology import build_composite_evidence_catalog

FINGERPRINT = "fp-abc123"
PROJECT_ID = "acme"


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n",
        encoding="utf-8",
    )


def _partition_evidence_dir(root: Path, project_id: str, subproject: str) -> Path:
    return root / "output" / project_id / subproject / "docs" / "_topology" / "evidence"


def _org_evidence_dir(root: Path, project_id: str) -> Path:
    return root / "output" / project_id / "topology" / "evidence"


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _scaffold_two_partitions(root: Path) -> None:
    part_a = _partition_evidence_dir(root, PROJECT_ID, "AppA")
    part_b = _partition_evidence_dir(root, PROJECT_ID, "AppB")

    _write_jsonl(
        part_a / "artifacts.jsonl",
        [
            {"id": "art-shared-1", "name": "Shared Artifact", "source": "AppA"},
            {"id": "art-a-2", "name": "AppA Only Artifact"},
        ],
    )
    _write_jsonl(
        part_b / "artifacts.jsonl",
        [
            {"id": "art-shared-1", "name": "Shared Artifact", "source": "AppB"},
            {"id": "art-b-2", "name": "AppB Only Artifact"},
        ],
    )
    _write_jsonl(
        part_a / "lineage.jsonl",
        [{"id": "lin-a-1", "from": "x", "to": "y"}],
    )
    _write_jsonl(
        part_b / "lineage.jsonl",
        [{"id": "lin-b-1", "from": "y", "to": "z"}],
    )


def test_merges_union_deduped_by_id_first_writer_wins(tmp_path: Path) -> None:
    _scaffold_two_partitions(tmp_path)

    result = build_composite_evidence_catalog(tmp_path, PROJECT_ID, FINGERPRINT)
    assert result is True

    org_dir = _org_evidence_dir(tmp_path, PROJECT_ID)
    artifacts_path = org_dir / "artifacts.jsonl"
    assert artifacts_path.is_file()

    artifacts = _read_jsonl(artifacts_path)
    ids = [row["id"] for row in artifacts]
    assert ids == ["art-shared-1", "art-a-2", "art-b-2"]
    # First occurrence (sorted dir order -> AppA before AppB) wins.
    shared = next(row for row in artifacts if row["id"] == "art-shared-1")
    assert shared["source"] == "AppA"

    lineage_path = org_dir / "lineage.jsonl"
    assert lineage_path.is_file()
    lineage_ids = {row["id"] for row in _read_jsonl(lineage_path)}
    assert lineage_ids == {"lin-a-1", "lin-b-1"}

    marker = org_dir / ".fingerprint"
    assert marker.read_text(encoding="utf-8").strip() == f"{FINGERPRINT}:2"


def test_idempotent_same_fingerprint_does_not_rebuild(tmp_path: Path) -> None:
    _scaffold_two_partitions(tmp_path)
    assert build_composite_evidence_catalog(tmp_path, PROJECT_ID, FINGERPRINT) is True

    org_dir = _org_evidence_dir(tmp_path, PROJECT_ID)
    artifacts_before = _read_jsonl(org_dir / "artifacts.jsonl")

    # Mutate a partition file without changing the fingerprint passed in;
    # the marker gate should skip rebuilding entirely.
    _write_jsonl(
        _partition_evidence_dir(tmp_path, PROJECT_ID, "AppA") / "artifacts.jsonl",
        [{"id": "art-shared-1", "name": "Shared Artifact", "source": "AppA"}],
    )

    result = build_composite_evidence_catalog(tmp_path, PROJECT_ID, FINGERPRINT)
    assert result is True

    artifacts_after = _read_jsonl(org_dir / "artifacts.jsonl")
    assert artifacts_after == artifacts_before


def test_rebuilds_on_fingerprint_change(tmp_path: Path) -> None:
    _scaffold_two_partitions(tmp_path)
    assert build_composite_evidence_catalog(tmp_path, PROJECT_ID, FINGERPRINT) is True

    org_dir = _org_evidence_dir(tmp_path, PROJECT_ID)

    # Add a new row to a partition file, then rebuild with a new fingerprint.
    part_a_artifacts = _partition_evidence_dir(tmp_path, PROJECT_ID, "AppA") / "artifacts.jsonl"
    rows = _read_jsonl(part_a_artifacts)
    rows.append({"id": "art-a-3", "name": "New AppA Artifact"})
    _write_jsonl(part_a_artifacts, rows)

    new_fingerprint = "fp-def456"
    result = build_composite_evidence_catalog(tmp_path, PROJECT_ID, new_fingerprint)
    assert result is True

    artifacts_after = _read_jsonl(org_dir / "artifacts.jsonl")
    ids_after = {row["id"] for row in artifacts_after}
    assert "art-a-3" in ids_after

    marker = org_dir / ".fingerprint"
    assert marker.read_text(encoding="utf-8").strip() == f"{new_fingerprint}:2"


def test_derives_unique_cross_partition_file_trigger_job_relation(tmp_path: Path) -> None:
    trigger_dir = _partition_evidence_dir(tmp_path, PROJECT_ID, "EnterpriseServiceBus")
    job_dir = _partition_evidence_dir(tmp_path, PROJECT_ID, "RHM")
    _write_jsonl(trigger_dir / "artifacts.jsonl", [{
        "id": "artifact:trigger",
        "name": "trigger_OnFile_RHM2Maximo_RealizationMachine",
        "job_id": "trigger_OnFile_RHM2Maximo_RealizationMachine",
        "source_root": "ENTERPRISESERVICEBUS",
        "artifact_kind": "route",
    }])
    _write_jsonl(job_dir / "artifacts.jsonl", [{
        "id": "artifact:job",
        "name": "RHM2Maximo_RealizationMachine",
        "job_id": "RHM2Maximo_RealizationMachine",
        "source_root": "RHM",
        "artifact_kind": "job",
    }])

    assert build_composite_evidence_catalog(tmp_path, PROJECT_ID, FINGERPRINT) is True

    relations = _read_jsonl(_org_evidence_dir(tmp_path, PROJECT_ID) / "relations.jsonl")
    assert relations == [{
        "id": relations[0]["id"],
        "type": "derived_relation",
        "source": "trigger_OnFile_RHM2Maximo_RealizationMachine",
        "target": "RHM2Maximo_RealizationMachine",
        "relation_type": "file_trigger_invokes_job",
        "resolution": "unique_exact_name",
        "provenance": "trigger_OnFile exact suffix",
    }]


def test_does_not_derive_file_trigger_relation_for_ambiguous_job_name(tmp_path: Path) -> None:
    trigger_dir = _partition_evidence_dir(tmp_path, PROJECT_ID, "EnterpriseServiceBus")
    _write_jsonl(trigger_dir / "artifacts.jsonl", [{
        "id": "artifact:trigger", "name": "trigger_OnFile_ProcessOrders",
        "artifact_kind": "route",
    }])
    for partition, artifact_id in (("AppA", "artifact:a"), ("AppB", "artifact:b")):
        _write_jsonl(_partition_evidence_dir(tmp_path, PROJECT_ID, partition) / "artifacts.jsonl", [{
            "id": artifact_id, "name": "ProcessOrders", "artifact_kind": "job",
        }])

    assert build_composite_evidence_catalog(tmp_path, PROJECT_ID, FINGERPRINT) is True
    assert _read_jsonl(_org_evidence_dir(tmp_path, PROJECT_ID) / "relations.jsonl") == []


def test_no_partitions_found_returns_false(tmp_path: Path) -> None:
    # No output/{pid}/*/docs/_topology/evidence directories at all.
    (tmp_path / "output" / PROJECT_ID).mkdir(parents=True)

    result = build_composite_evidence_catalog(tmp_path, PROJECT_ID, FINGERPRINT)
    assert result is False

    topology_dir = tmp_path / "output" / PROJECT_ID / "topology"
    assert not topology_dir.exists()
