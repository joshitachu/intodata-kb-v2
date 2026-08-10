"""Push-evidence + deterministic-fallback tests for the business-topology step.

Covers the fix for the verified ETTM_part1 failure where the topology step
exhausted its tool-call budget before reading `_topology/evidence/*.jsonl` and
fabricated an evidence-less topology that failed validation.

Three levels:
* unit  — context assembly pushes all critical evidence under a byte cap with no
          tool dependency; JSON extraction survives a model preamble;
* unit  — the deterministic fallback builds a valid, evidence-cited topology
          from scanner edges + identities on a synthetic bundle;
* integration — the deterministic fallback validates against the REAL preserved
          ETTM_part1 evidence bundle on disk (skipped if absent).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mcp_server.config import config
from mcp_server.services.business_topology import (
    BusinessTopologyEvidenceBundle,
    build_business_topology_evidence,
    build_deterministic_topology,
    build_topology_context_files,
    validate_business_topology,
)
from mcp_server.services.business_topology.validation import _strip_json_fence

from tests.test_services.test_business_topology import _write_scan


# --------------------------------------------------------------------------
# Context assembly (PUSH, not PULL)
# --------------------------------------------------------------------------


def _build_bundle(tmp_path: Path) -> BusinessTopologyEvidenceBundle:
    scan_root = tmp_path / "scan-output"
    staging = tmp_path / "staging"
    staging.mkdir()
    _write_scan(scan_root)
    return build_business_topology_evidence(
        project_id="demo",
        scan_output_dir=scan_root,
        staging_dir=staging,
    )


def test_context_files_push_critical_evidence_without_tools(tmp_path: Path) -> None:
    bundle = _build_bundle(tmp_path)
    files = build_topology_context_files(
        bundle, tmp_path / "staging", byte_cap=config.topology_context_evidence_bytes
    )
    names = {path.name for path in files}
    # Every validation-critical file the model previously had to PULL is pushed.
    for required in (
        "factual_evidence_index.json",
        "systems.jsonl",
        "applications.jsonl",
        "edges.jsonl",
        "connections.jsonl",
        "relations.jsonl",
        "artifacts_min.jsonl",
        "_doc_pack_business_topology.md",
    ):
        assert required in names, f"missing pushed evidence: {required}"
    # All entries are real files on disk (a pushed context, not tool handles).
    assert all(path.is_file() for path in files)


def test_trimmed_artifacts_drop_bulk_keep_identity(tmp_path: Path) -> None:
    bundle = _build_bundle(tmp_path)
    files = build_topology_context_files(
        bundle, tmp_path / "staging", byte_cap=config.topology_context_evidence_bytes
    )
    trimmed = next(path for path in files if path.name == "artifacts_min.jsonl")
    rows = [json.loads(line) for line in trimmed.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert rows, "trimmed artifact index must not be empty"
    for row in rows:
        assert row["type"] == "scanner_artifact"
        assert "name" in row and "source_root" in row
        # Bulk fields that dominate byte size are dropped.
        assert "component_ids" not in row
        assert "connections" not in row
        # The trimmed id still resolves against the bundle's artifact ledger.
        assert row["id"] in bundle.artifact_ids


def test_context_cap_never_drops_critical_files(tmp_path: Path) -> None:
    bundle = _build_bundle(tmp_path)
    files = build_topology_context_files(bundle, tmp_path / "staging", byte_cap=1)
    names = {path.name for path in files}
    # Critical files survive an impossibly small cap...
    for required in ("factual_evidence_index.json", "systems.jsonl", "edges.jsonl"):
        assert required in names
    # ...while non-critical bulk is dropped.
    assert "components.jsonl" not in names
    assert "channels.jsonl" not in names


# --------------------------------------------------------------------------
# JSON extraction robustness (model preamble before the object)
# --------------------------------------------------------------------------


def test_strip_json_fence_extracts_object_after_preamble() -> None:
    text = (
        "Given the persistent tool call budget errors, I'll construct the "
        "topology from domain knowledge.\n\n```json\n{\"schema_version\": "
        "\"business-topology-v1\", \"a\": 1}\n```\n"
    )
    extracted = _strip_json_fence(text)
    parsed = json.loads(extracted)
    assert parsed["schema_version"] == "business-topology-v1"


def test_strip_json_fence_extracts_unfenced_embedded_object() -> None:
    text = "Here is the result: {\"schema_version\": \"business-topology-v1\"} done."
    assert json.loads(_strip_json_fence(text))["schema_version"] == "business-topology-v1"


# --------------------------------------------------------------------------
# Deterministic fallback (evidence-only)
# --------------------------------------------------------------------------


def test_deterministic_fallback_from_scanner_edges_validates(tmp_path: Path) -> None:
    bundle = _build_bundle(tmp_path)
    topology = build_deterministic_topology(bundle, project_id="demo")
    report = validate_business_topology(topology, bundle=bundle, project_id="demo")

    assert report.validated, report.errors
    assert topology.data_streams, "fallback must emit at least one directional stream"
    assert topology.systems
    assert topology.metadata.generated_by == "deterministic_topology_fallback"
    # Real, non-placeholder coverage: cited evidence and grounded edges.
    assert topology.coverage.cited_evidence > 0
    assert topology.coverage.grounded_technical_relationships == len(topology.data_streams)
    # Every stream cites its directional edge and both system identities.
    for stream in topology.data_streams:
        assert stream.artifact_ids
        assert len(stream.evidence_ids) >= 3


def test_deterministic_fallback_streams_cite_only_known_evidence(tmp_path: Path) -> None:
    bundle = _build_bundle(tmp_path)
    topology = build_deterministic_topology(bundle, project_id="demo")
    for stream in topology.data_streams:
        assert set(stream.evidence_ids).issubset(bundle.evidence_ids)
        assert set(stream.artifact_ids).issubset(bundle.artifact_ids)


# --------------------------------------------------------------------------
# INTEGRATION: real preserved ETTM_part1 evidence
# --------------------------------------------------------------------------


def _reconstruct_in_memory_bundle(root: Path) -> BusinessTopologyEvidenceBundle:
    """Rebuild the in-memory bundle from preserved evidence files.

    Mirrors ``build_business_topology_evidence``'s in-memory structure (a dict
    keyed by id, so duplicate rows collapse) rather than the strict
    ``load_business_topology_evidence`` reader, matching how the running
    pipeline holds the bundle.
    """

    evidence_dir = root / "evidence"
    overview = json.loads((root / "factual_evidence_index.json").read_text(encoding="utf-8"))
    records: dict[str, dict] = {}
    for path in sorted(evidence_dir.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if isinstance(row, dict) and row.get("id"):
                records[row["id"]] = row
    types = {rid: rec["type"] for rid, rec in records.items()}
    return BusinessTopologyEvidenceBundle(
        root=root,
        overview_path=root / "factual_evidence_index.json",
        starter_pack_path=root / "_doc_pack_business_topology.md",
        evidence_ids_path=evidence_dir / "evidence_ids.json",
        scan_fingerprint=overview["scan_fingerprint"],
        evidence_ids=frozenset(types),
        evidence_types=types,
        evidence_records=records,
        artifact_ids=frozenset(i for i, t in types.items() if t == "scanner_artifact"),
        component_ids=frozenset(i for i, t in types.items() if t == "scanner_component"),
        lineage_ids=frozenset(i for i, t in types.items() if t == "scanner_lineage"),
    )


_ETTM_TOPOLOGY = (
    Path(__file__).resolve().parents[3]
    / "output"
    / "werkorder"
    / "ETTM_part1"
    / "docs"
    / "_topology"
)


@pytest.mark.skipif(
    not (_ETTM_TOPOLOGY / "factual_evidence_index.json").is_file(),
    reason="preserved ETTM_part1 evidence bundle not present",
)
def test_deterministic_fallback_validates_against_real_ettm_evidence() -> None:
    bundle = _reconstruct_in_memory_bundle(_ETTM_TOPOLOGY)
    topology = build_deterministic_topology(bundle, project_id="werkorder")
    report = validate_business_topology(topology, bundle=bundle, project_id="werkorder")

    assert report.validated, report.errors
    assert topology.data_streams, "expected real directional streams from ETTM edges"
    assert topology.systems
    # Non-placeholder coverage against the real 105-artifact / 7167-evidence bundle.
    assert topology.coverage.cited_evidence > 0
    assert topology.coverage.cited_artifacts > 0
    assert topology.coverage.coverage_ratio > 0.0
    assert topology.coverage.grounded_technical_relationships == len(topology.data_streams)
    # Every emitted stream is scanner-grounded and drill-down-capable.
    for stream in topology.data_streams:
        assert stream.artifact_ids
        assert set(stream.evidence_ids).issubset(bundle.evidence_ids)


@pytest.mark.skipif(
    not (_ETTM_TOPOLOGY / "factual_evidence_index.json").is_file(),
    reason="preserved ETTM_part1 evidence bundle not present",
)
def test_pushed_context_contains_evidence_model_said_it_could_not_reach() -> None:
    """The pushed context now inlines exactly the files the failed run could not PULL."""
    bundle = _reconstruct_in_memory_bundle(_ETTM_TOPOLOGY)
    files = build_topology_context_files(
        bundle, _ETTM_TOPOLOGY.parent, byte_cap=config.topology_context_evidence_bytes
    )
    names = {path.name for path in files}
    # The failed output cited inability to access these; they are now pushed.
    for required in (
        "factual_evidence_index.json",
        "systems.jsonl",
        "edges.jsonl",
        "cross_project_index.json",
        "_doc_pack_business_topology.md",
    ):
        assert required in names, f"{required} must be in the pushed context"
    total = sum(path.stat().st_size for path in files)
    # Fits comfortably under the core context hard ceiling (no forced slicing of
    # a JSON synthesis call, which would restart-duplicate output).
    assert total // 4 < config.core_context_hard_ceiling_tokens
