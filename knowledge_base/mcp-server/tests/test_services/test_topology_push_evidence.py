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
    parse_and_validate_business_topology,
    parse_validate_repair_business_topology,
    repair_business_topology,
    validate_business_topology,
)
from mcp_server.services.business_topology.models import (
    BusinessDataStream,
    BusinessSystem,
    BusinessUnknown,
    IntegrationSolution,
)
from mcp_server.services.business_topology.prompt import business_topology_prompt
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
        "technical_facts_min.jsonl",
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


def test_compact_technical_facts_are_bounded_and_structured(tmp_path: Path) -> None:
    bundle = _build_bundle(tmp_path)
    files = build_topology_context_files(
        bundle, tmp_path / "staging", byte_cap=config.topology_context_evidence_bytes
    )
    technical = next(path for path in files if path.name == "technical_facts_min.jsonl")
    assert technical.stat().st_size <= 100_000
    rows = [
        json.loads(line)
        for line in technical.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert {row["type"] for row in rows} >= {"scanner_endpoint", "scanner_lineage"}


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
    assert topology.data_streams[0].protocols == ["REST"]
    assert topology.transformations
    assert topology.transformations[0].mappings[0].mapping_type == "unknown"


def test_generated_protocols_are_canonicalized_and_deduplicated(tmp_path: Path) -> None:
    bundle = _build_bundle(tmp_path)
    topology = build_deterministic_topology(bundle, project_id="demo")
    topology.data_streams[0].protocols = ["rest", "REST", " jms "]
    parsed, report = parse_and_validate_business_topology(
        topology.model_dump_json(), bundle=bundle, project_id="demo"
    )
    assert report.validated, report.errors
    assert parsed is not None
    assert parsed.data_streams[0].protocols == ["REST", "JMS"]


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


# --------------------------------------------------------------------------
# Targeted deterministic repair (verified werkorder failure shapes)
# --------------------------------------------------------------------------


def _valid_base(tmp_path: Path):
    bundle = _build_bundle(tmp_path)
    topology = build_deterministic_topology(bundle, project_id="demo")
    report = validate_business_topology(topology, bundle=bundle, project_id="demo")
    assert report.validated and topology.data_streams
    return bundle, topology


def _identity_id(bundle: BusinessTopologyEvidenceBundle) -> str:
    return next(
        eid for eid, etype in sorted(bundle.evidence_types.items())
        if etype == "scanner_system"
    )


def test_repair_normalizes_solution_missing_self_citation(tmp_path: Path) -> None:
    """Verified ESB_part1 attempt-01 shape: valid artifact_ids, none cited."""
    bundle, topology = _valid_base(tmp_path)
    artifact_id = next(iter(bundle.artifact_ids))
    topology.integration_solutions.append(
        IntegrationSolution(
            id="int-sol-local",
            name="Local codebase",
            status="inferred",
            confidence=0.6,
            evidence_ids=[_identity_id(bundle)],
            artifact_ids=[artifact_id],
        )
    )
    broken = validate_business_topology(topology, bundle=bundle, project_id="demo")
    assert any("does not cite one of its scanner artifact_ids" in e for e in broken.errors)

    repaired, notes = repair_business_topology(topology, bundle=bundle)
    report = validate_business_topology(repaired, bundle=bundle, project_id="demo")
    assert report.validated, report.errors
    solution = repaired.integration_solutions[-1]
    assert artifact_id in solution.evidence_ids
    assert any("citation normalization" in note for note in notes)


def test_repair_downgrades_sibling_solution_without_local_artifacts(tmp_path: Path) -> None:
    """Verified shape: solutions for sibling sub-projects have no local artifacts."""
    bundle, topology = _valid_base(tmp_path)
    topology.integration_solutions.append(
        IntegrationSolution(
            id="int-sol-sibling",
            name="Sibling sub-project",
            status="inferred",
            confidence=0.6,
            evidence_ids=[_identity_id(bundle)],
            artifact_ids=[],
        )
    )
    broken = validate_business_topology(topology, bundle=bundle, project_id="demo")
    assert any("has no scanner artifact_ids" in e for e in broken.errors)

    repaired, _ = repair_business_topology(topology, bundle=bundle)
    report = validate_business_topology(repaired, bundle=bundle, project_id="demo")
    assert report.validated, report.errors
    assert repaired.integration_solutions[-1].status == "not_established"


def test_repair_drops_fabricated_artifact_citations(tmp_path: Path) -> None:
    """Verified ETTM_part2 shape: `artifact:` IDs minted from identity hashes."""
    bundle, topology = _valid_base(tmp_path)
    stream = topology.data_streams[0]
    stream.artifact_ids.append("artifact:deadbeefdeadbeefdead")
    stream.evidence_ids.append("artifact:deadbeefdeadbeefdead")
    broken = validate_business_topology(topology, bundle=bundle, project_id="demo")
    assert any("unknown artifact_id" in e or "unknown evidence" in e for e in broken.errors)

    repaired, notes = repair_business_topology(topology, bundle=bundle)
    report = validate_business_topology(repaired, bundle=bundle, project_id="demo")
    assert report.validated, report.errors
    kept = repaired.data_streams[0]
    assert "artifact:deadbeefdeadbeefdead" not in kept.artifact_ids
    assert "artifact:deadbeefdeadbeefdead" not in kept.evidence_ids
    assert kept.artifact_ids, "real artifacts must survive the drop"
    assert any("unknown" in note for note in notes)


def test_repair_backfills_stream_artifacts_from_directional_evidence(tmp_path: Path) -> None:
    """Verified ESB_part1 shape: stream cites a real edge but artifact_ids=[]."""
    bundle, topology = _valid_base(tmp_path)
    topology.data_streams[0].artifact_ids = []
    broken = validate_business_topology(topology, bundle=bundle, project_id="demo")
    assert any("has no scanner artifact_ids for technical traceability" in e for e in broken.errors)

    repaired, notes = repair_business_topology(topology, bundle=bundle)
    report = validate_business_topology(repaired, bundle=bundle, project_id="demo")
    assert report.validated, report.errors
    assert repaired.data_streams[0].artifact_ids
    assert set(repaired.data_streams[0].artifact_ids).issubset(bundle.artifact_ids)
    assert any("backfilled" in note for note in notes)


def test_repair_drops_ungrounded_stream_and_prunes_references(tmp_path: Path) -> None:
    bundle, topology = _valid_base(tmp_path)
    systems = topology.systems
    topology.data_streams.append(
        BusinessDataStream(
            id="ds-fabricated",
            name="Fabricated flow",
            status="inferred",
            confidence=0.5,
            evidence_ids=[_identity_id(bundle)],  # identity only: no direction
            source_system_id=systems[0].id,
            destination_system_id=systems[-1].id,
            business_summary="not grounded",
            artifact_ids=sorted(bundle.artifact_ids)[:1],
        )
    )
    topology.unknowns.append(
        BusinessUnknown(
            id="unk-1",
            name="references the fabricated stream",
            status="proposed",
            confidence=0.3,
            evidence_ids=[_identity_id(bundle)],
            stream_ids=["ds-fabricated"],
            question="does this survive?",
        )
    )
    broken = validate_business_topology(topology, bundle=bundle, project_id="demo")
    assert any("ds-fabricated has no directional scanner evidence" in e for e in broken.errors)

    repaired, _ = repair_business_topology(topology, bundle=bundle)
    report = validate_business_topology(repaired, bundle=bundle, project_id="demo")
    assert report.validated, report.errors
    assert all(stream.id != "ds-fabricated" for stream in repaired.data_streams)
    assert repaired.data_streams, "grounded streams must survive"
    assert repaired.unknowns[-1].stream_ids == []


def test_repair_downgrades_unsupported_system_promotion(tmp_path: Path) -> None:
    """Verified ESB_part1 attempt-02 shape (sys-smartflow)."""
    bundle, topology = _valid_base(tmp_path)
    endpoint_id = next(
        eid for eid, etype in sorted(bundle.evidence_types.items())
        if etype == "scanner_endpoint"
    )
    topology.systems.append(
        BusinessSystem(
            id="sys-promoted",
            name="Promoted from an endpoint",
            status="inferred",
            confidence=0.5,
            evidence_ids=[endpoint_id],
        )
    )
    broken = validate_business_topology(topology, bundle=bundle, project_id="demo")
    assert any("unsupported system promotion" in e for e in broken.errors)

    repaired, _ = repair_business_topology(topology, bundle=bundle)
    report = validate_business_topology(repaired, bundle=bundle, project_id="demo")
    assert report.validated, report.errors
    promoted = next(s for s in repaired.systems if s.id == "sys-promoted")
    assert promoted.kind == "unresolved"
    assert promoted.status == "not_established"


def test_parse_validate_repair_rejects_streamless_repair(tmp_path: Path) -> None:
    """When every stream is unrepairable the RAW errors must drive the retry."""
    bundle, topology = _valid_base(tmp_path)
    for stream in topology.data_streams:
        stream.evidence_ids = [_identity_id(bundle)]  # strip directional evidence
    text = topology.model_dump_json()
    parsed, report, notes = parse_validate_repair_business_topology(
        text, bundle=bundle, project_id="demo"
    )
    assert parsed is None
    assert notes == []
    assert any("has no directional scanner evidence" in e for e in report.errors)


def test_parse_reports_empty_output_distinctly(tmp_path: Path) -> None:
    """Verified shape: a provider-level failure (402) leaves empty output text."""
    bundle = _build_bundle(tmp_path)
    parsed, report = parse_and_validate_business_topology(
        "", bundle=bundle, project_id="demo"
    )
    assert parsed is None
    assert "no output" in report.errors[0]


def test_prompt_states_mechanical_grounding_contract() -> None:
    prompt = business_topology_prompt(project_id="demo", scan_fingerprint="f" * 64)
    assert "MECHANICAL VALIDATION CONTRACT" in prompt
    assert "sibling sub-projects" in prompt
    assert "verbatim" in prompt
    assert "ENRICHMENT CONTRACT" in prompt
    assert "technical_facts_min.jsonl" in prompt
    assert "Do not leave structured facts trapped" in prompt


# --------------------------------------------------------------------------
# Deterministic fallback enrichment (solutions, identity systems, unknowns)
# --------------------------------------------------------------------------


def test_enriched_fallback_still_validates_on_synthetic_bundle(tmp_path: Path) -> None:
    bundle, topology = _valid_base(tmp_path)
    # Synthetic scan has no applications and no cross-partition edges, so the
    # enrichment layers stay empty rather than inventing anything.
    assert topology.integration_solutions == []
    assert topology.unknowns == []


# --------------------------------------------------------------------------
# INTEGRATION: real preserved EnterpriseServiceBus_part1 run artifacts
# --------------------------------------------------------------------------


_ESB1_DOCS = (
    Path(__file__).resolve().parents[3]
    / "output"
    / "werkorder"
    / "EnterpriseServiceBus_part1"
    / "docs"
)
_ESB1_TOPOLOGY = _ESB1_DOCS / "_topology"
_ESB1_ATTEMPT01 = (
    _ESB1_DOCS
    / "_debug"
    / "agent_tasks"
    / "business_topology"
    / "business_topology_ef3f69690c620ae6"
    / "attempts"
    / "01"
    / "output.md"
)


@pytest.mark.skipif(
    not (_ESB1_TOPOLOGY / "factual_evidence_index.json").is_file()
    or not _ESB1_ATTEMPT01.is_file(),
    reason="preserved ESB_part1 run artifacts not present",
)
def test_real_esb_part1_attempt01_output_repairs_to_valid() -> None:
    """The REAL failed LLM output (3 mechanical errors) must now be salvaged.

    ESB_part1 attempt 01 produced a 12-system / 1-stream topology rejected
    wholesale over: a solution not citing its own artifact_ids, a stream with
    empty artifact_ids, and the dependent segment error. The targeted repair
    must recover it instead of discarding the entire LLM synthesis.
    """

    bundle = _reconstruct_in_memory_bundle(_ESB1_TOPOLOGY)
    raw = _ESB1_ATTEMPT01.read_text(encoding="utf-8")
    topology, report, notes = parse_validate_repair_business_topology(
        raw, bundle=bundle, project_id="werkorder"
    )
    assert topology is not None and report.validated, report.errors
    assert len(topology.data_streams) >= 1
    assert len(topology.systems) >= 10
    assert notes, "acceptance must be via repair, with an audit trail"
    assert any("int_talend_esb" in note for note in notes)
    assert any("stream_erp_to_esb" in note for note in notes)
    # The repaired topology still cites only evidence from the pushed bundle.
    assert all(
        set(stream.evidence_ids).issubset(bundle.evidence_ids)
        and set(stream.artifact_ids).issubset(bundle.artifact_ids)
        for stream in topology.data_streams
    )


@pytest.mark.skipif(
    not (_ESB1_TOPOLOGY / "factual_evidence_index.json").is_file(),
    reason="preserved ESB_part1 evidence bundle not present",
)
def test_enriched_fallback_on_real_esb_part1_bundle() -> None:
    """Enrichment: local solutions, identity systems, cross-partition unknowns.

    The pre-enrichment fallback published 8 systems / 6 streams / 0 solutions /
    0 unknowns and cited 11 of 118 artifacts, silently dropping the 19 of 25
    directional edges implemented by sibling partitions.
    """

    bundle = _reconstruct_in_memory_bundle(_ESB1_TOPOLOGY)
    topology = build_deterministic_topology(bundle, project_id="werkorder")
    report = validate_business_topology(topology, bundle=bundle, project_id="werkorder")

    assert report.validated, report.errors
    assert len(topology.data_streams) >= 6, "stream floor must not shrink"
    assert len(topology.systems) > 8, "identity systems beyond edge participants"
    assert topology.integration_solutions, "local applications become solutions"
    for solution in topology.integration_solutions:
        assert solution.artifact_ids
        assert set(solution.artifact_ids).intersection(solution.evidence_ids)
    assert topology.unknowns, "cross-partition edges are captured, not dropped"
    for unknown in topology.unknowns:
        assert set(unknown.evidence_ids).issubset(bundle.evidence_ids)
    # Every local artifact is now cited (owned by its application's solution).
    assert topology.coverage.cited_artifacts == topology.coverage.total_artifacts
