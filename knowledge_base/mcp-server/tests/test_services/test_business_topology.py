from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from mcp_server.providers.custom.models import FactsManifest, HarnessRunState
from mcp_server.providers.custom.agent_runtime import (
    AgentPhase,
    AgentTaskContract,
    ScopedEvidenceTools,
)
from mcp_server.providers.custom.pipeline.phases.phase37_business_topology import (
    Phase37BusinessTopology,
)
from mcp_server.services.business_topology.evidence import (
    artifact_evidence_id,
    build_business_topology_evidence,
    component_evidence_id,
    compute_scan_fingerprint,
    lineage_evidence_id,
)
from mcp_server.services.business_topology.validation import (
    parse_and_validate_business_topology,
)


def _write_scan(root: Path) -> None:
    (root / "scan").mkdir(parents=True)
    (root / "scan_result.json").write_text(
        json.dumps(
            {
                "platform": "talend",
                "jobs": [
                    {
                        "name": "ReadOrders",
                        "job_id": "ReadOrders",
                        "source_root": "IMPORT/PROJECT",
                        "path": "Orders/ReadOrders.item",
                        "artifact_kind": "job",
                        "artifact_role": "execution",
                        "components": [
                            {
                                "unique_name": "tRESTClient_1",
                                "component_type": "tRESTClient",
                                "params": [
                                    {"name": "METHOD", "value": "GET"},
                                    {"name": "PASSWORD", "value": "hidden"},
                                ],
                            }
                        ],
                        "connections": [],
                    }
                ],
                "metadata_connections": [],
            }
        ),
        encoding="utf-8",
    )
    (root / "scan" / "manifest.json").write_text(
        json.dumps(
            {
                "source_roots": [{"id": "IMPORT/PROJECT", "project_id": "project-id"}],
                "artifact_relations": [],
                "artifact_resources": [],
                "artifact_endpoints": [
                    {
                        "artifact": "ReadOrders",
                        "endpoint_type": "rest",
                        "direction": "output",
                        "component_name": "tRESTClient_1",
                    }
                ],
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
                        "job_name": "ReadOrders",
                        "source_table": "Orders",
                        "source_column": "OrderId",
                        "target_table": "Output",
                        "target_column": "OrderId",
                        "confidence": "certain",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (root / "scan" / "landscape.json").write_text(
        json.dumps(
            {
                "systems": [
                    {
                        "id": "system:orders-source",
                        "name": "Orders source",
                        "kind": "database",
                        "source_roots": ["IMPORT/PROJECT"],
                    },
                    {
                        "id": "system:orders-consumer",
                        "name": "Orders consumer",
                        "kind": "rest_api",
                        "source_roots": ["IMPORT/PROJECT"],
                    },
                ],
                "applications": [],
                "edges": [
                    {
                        "source": "system:orders-source",
                        "target": "system:orders-consumer",
                        "dependency_type": "data",
                        "mechanism": "rest",
                        "direction": "out",
                        "provenance": [{"artifact_id": "ReadOrders"}],
                    }
                ],
                "semantic_topology": {"systems": [{"name": "guessed"}]},
            }
        ),
        encoding="utf-8",
    )


def test_fingerprint_ignores_removed_semantic_overlay(tmp_path: Path) -> None:
    _write_scan(tmp_path)
    first = compute_scan_fingerprint(tmp_path)
    landscape_path = tmp_path / "scan" / "landscape.json"
    landscape = json.loads(landscape_path.read_text(encoding="utf-8"))
    landscape["semantic_topology"] = {"systems": [{"name": "different guess"}]}
    landscape_path.write_text(json.dumps(landscape), encoding="utf-8")
    assert compute_scan_fingerprint(tmp_path) == first


def test_topology_tools_cannot_read_generated_writer_prose(tmp_path: Path) -> None:
    staging = tmp_path / "staging"
    (staging / "_analysis").mkdir(parents=True)
    (staging / "01_ARCHITECTURE_MAP.md").write_text(
        "WRITER_ONLY_SENTINEL", encoding="utf-8"
    )
    (staging / "_analysis" / "accepted.md").write_text(
        "ACCEPTED_ANALYSIS_SENTINEL", encoding="utf-8"
    )
    facts = FactsManifest(
        project_id="demo",
        platform="generic",
        canonical_unit_label="unit",
        unit_count=0,
    )
    tools = ScopedEvidenceTools(
        staging,
        facts,
        AgentTaskContract(
            phase=AgentPhase.BUSINESS_TOPOLOGY,
            target_id="topology",
            expected_artifact="business topology JSON",
        ),
    )

    assert "01_ARCHITECTURE_MAP.md" not in tools.list_artifacts()
    assert "outside business-topology evidence boundary" in tools.read_artifact(
        "01_ARCHITECTURE_MAP.md"
    )
    assert "ACCEPTED_ANALYSIS_SENTINEL" in tools.read_artifact(
        "_analysis/accepted.md"
    )
    assert "WRITER_ONLY_SENTINEL" not in tools.search_evidence("WRITER_ONLY_SENTINEL")


def test_evidence_is_factual_and_validation_resolves_ids(tmp_path: Path) -> None:
    scan_root = tmp_path / "scan-output"
    staging = tmp_path / "staging"
    staging.mkdir()
    _write_scan(scan_root)
    (staging / "01_ARCHITECTURE_MAP.md").write_text("# Architecture\nOrders are retrieved.", encoding="utf-8")
    (staging / "_analysis").mkdir()
    (staging / "_analysis" / "orders.md").write_text("# Orders\nREST GET is observed.", encoding="utf-8")
    (staging / "_analysis" / "_claims").mkdir()
    (staging / "_analysis" / "_claims" / "orders.jsonl").write_text(
        json.dumps(
            {
                "record_type": "claim",
                "claim_id": "claim:orders-read",
                "group": "Orders",
                "task_id": "Orders__task_1__analysis",
                "concern": "analysis",
                "section": "DATA_FLOWS",
                "kind": "relationship",
                "unit_names": ["ReadOrders"],
                "related_units": [],
                "statement": "ReadOrders retrieves order data through REST.",
                "evidence_refs": ["scan/jobs/ReadOrders.json"],
                "confidence": "supported",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    bundle = build_business_topology_evidence(
        project_id="demo",
        scan_output_dir=scan_root,
        staging_dir=staging,
    )
    overview = json.loads(bundle.overview_path.read_text(encoding="utf-8"))
    assert overview["semantic_derivations"] == []
    assert "guessed" not in bundle.overview_path.read_text(encoding="utf-8")
    assert "Orders are retrieved" not in bundle.starter_pack_path.read_text(encoding="utf-8")
    component_text = (bundle.root / "evidence" / "components.jsonl").read_text(encoding="utf-8")
    assert '"name": "METHOD"' in component_text
    assert '"value": "GET"' in component_text
    assert "hidden" not in component_text
    claim_text = (bundle.root / "evidence" / "phase2_claims.jsonl").read_text(
        encoding="utf-8"
    )
    assert "claim:orders-read" in claim_text
    assert bundle.evidence_types["claim:orders-read"] == "phase2_claim"
    assert overview["counts"]["phase2_claims"] == 1

    artifact_id = next(iter(bundle.artifact_ids))
    endpoint_id = next(
        evidence_id
        for evidence_id, evidence_type in bundle.evidence_types.items()
        if evidence_type == "scanner_endpoint"
    )
    identity_by_scanner_id = {
        record["scanner_id"]: evidence_id
        for evidence_id, record in bundle.evidence_records.items()
        if bundle.evidence_types[evidence_id] == "scanner_system"
    }
    source_identity_id = identity_by_scanner_id["system:orders-source"]
    target_identity_id = identity_by_scanner_id["system:orders-consumer"]
    edge_id = next(
        evidence_id
        for evidence_id, evidence_type in bundle.evidence_types.items()
        if evidence_type == "scanner_edge"
    )
    assert "document:01_ARCHITECTURE_MAP.md" not in bundle.evidence_ids
    raw_scan = json.loads((scan_root / "scan_result.json").read_text(encoding="utf-8"))
    raw_lineage = json.loads((scan_root / "scan" / "lineage.json").read_text(encoding="utf-8"))
    assert artifact_evidence_id(raw_scan["jobs"][0]) == artifact_id
    assert component_evidence_id(artifact_id, raw_scan["jobs"][0]["components"][0]) in bundle.component_ids
    assert lineage_evidence_id(raw_lineage["entries"][0]) in bundle.lineage_ids
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
                "description": "Proposed source identity",
                "status": "proposed",
                "confidence": 0.7,
                "evidence_ids": [source_identity_id],
                "kind": "software_system",
                "aliases": [],
            },
            {
                "id": "system:target",
                "name": "Order consumer",
                "description": "Proposed destination identity",
                "status": "proposed",
                "confidence": 0.7,
                "evidence_ids": [target_identity_id],
                "kind": "software_system",
                "aliases": [],
            },
        ],
        "integration_solutions": [],
        "business_objects": [],
        "data_streams": [
            {
                "id": "stream:orders",
                "name": "Order transfer",
                "description": "Order data is transferred",
                "status": "inferred",
                "confidence": 0.7,
                "evidence_ids": [edge_id],
                "source_system_id": "system:source",
                "destination_system_id": "system:target",
                "business_object_ids": [],
                "business_summary": "Provides order data to a consumer.",
                "integration_solution_ids": [],
                "control_ids": [],
                "risk_ids": [],
                "unknown_ids": [],
                "artifact_ids": [artifact_id],
            }
        ],
        "delivery_paths": [],
        "controls": [],
        "risks": [],
        "unknowns": [],
        "artifact_annotations": [],
        "component_annotations": [],
        "lineage_annotations": [],
        "coverage": {
            "total_artifacts": 0,
            "cited_artifacts": 0,
            "total_evidence": 0,
            "cited_evidence": 0,
            "coverage_ratio": 0,
            "notes": [],
        },
    }
    topology, report = parse_and_validate_business_topology(
        json.dumps(payload),
        bundle=bundle,
        project_id="demo",
    )
    assert topology is not None
    assert report.validated is True
    assert topology.coverage.total_artifacts == 1
    assert topology.coverage.cited_artifacts == 1
    assert topology.coverage.technical_relationships == 1
    assert topology.coverage.grounded_technical_relationships == 1
    # Existing v1 artifacts remain safe to parse when the enriched additive
    # collections were not emitted by an earlier topology agent.
    assert topology.runtime_observations == []
    assert topology.conflicts == []

    payload["data_streams"][0]["source_system_id"] = "system:target"
    payload["data_streams"][0]["destination_system_id"] = "system:source"
    reversed_topology, reversed_report = parse_and_validate_business_topology(
        json.dumps(payload), bundle=bundle, project_id="demo"
    )
    assert reversed_topology is None
    assert any(
        "does not establish its declared source/destination" in error
        for error in reversed_report.errors
    )
    payload["data_streams"][0]["source_system_id"] = "system:source"
    payload["data_streams"][0]["destination_system_id"] = "system:target"

    fake_artifact_id = "artifact:unrelated"
    mismatched_bundle = replace(
        bundle,
        artifact_ids=frozenset({*bundle.artifact_ids, fake_artifact_id}),
        evidence_records={
            **bundle.evidence_records,
            fake_artifact_id: {
                "id": fake_artifact_id,
                "type": "scanner_artifact",
                "name": "UnrelatedJob",
                "job_id": "UnrelatedJob",
                "path": "Other/UnrelatedJob.item",
            },
        },
    )
    payload["data_streams"][0]["artifact_ids"] = [fake_artifact_id]
    _, mismatch_report = parse_and_validate_business_topology(
        json.dumps(payload), bundle=mismatched_bundle, project_id="demo"
    )
    assert mismatch_report.validated is False
    assert any(
        "directional evidence does not touch" in error
        for error in mismatch_report.errors
    )
    payload["data_streams"][0]["artifact_ids"] = [artifact_id]

    payload["data_streams"][0]["evidence_ids"] = [endpoint_id]
    _, direction_report = parse_and_validate_business_topology(
        json.dumps(payload),
        bundle=bundle,
        project_id="demo",
    )
    assert direction_report.validated is False
    assert any("no directional scanner evidence" in error for error in direction_report.errors)

    payload["data_streams"][0]["evidence_ids"] = [edge_id]
    payload["data_streams"][0]["delivery_path_id"] = "path:orders"
    payload["delivery_paths"] = [
        {
            "id": "path:orders",
            "name": "Order delivery path",
            "status": "inferred",
            "confidence": 0.7,
            "evidence_ids": [endpoint_id],
            "stream_id": "stream:orders",
            "segments": [
                {
                    "id": "segment:orders-rest",
                    "name": "REST request",
                    "status": "observed",
                    "confidence": 0.9,
                    "evidence_ids": [endpoint_id],
                    "sequence": 1,
                    "source_ref": "system:source",
                    "destination_ref": "system:target",
                    "mechanism": "REST",
                    "mode": "direct",
                }
            ],
        }
    ]
    _, segment_report = parse_and_validate_business_topology(
        json.dumps(payload),
        bundle=bundle,
        project_id="demo",
    )
    assert segment_report.validated is False
    assert any("segment:orders-rest has no directional scanner evidence" in error for error in segment_report.errors)


def test_confirmed_claim_without_business_declaration_is_rejected(tmp_path: Path) -> None:
    scan_root = tmp_path / "scan-output"
    staging = tmp_path / "staging"
    staging.mkdir()
    _write_scan(scan_root)
    bundle = build_business_topology_evidence(
        project_id="demo", scan_output_dir=scan_root, staging_dir=staging
    )
    artifact_id = next(iter(bundle.artifact_ids))
    payload = {
        "schema_version": "business-topology-v1",
        "scan_fingerprint": bundle.scan_fingerprint,
        "metadata": {"project_id": "demo", "scan_fingerprint": bundle.scan_fingerprint},
        "systems": [
            {
                "id": "system:one",
                "name": "System One",
                "status": "confirmed",
                "confidence": 1,
                "evidence_ids": [artifact_id],
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
    _, report = parse_and_validate_business_topology(
        json.dumps(payload), bundle=bundle, project_id="demo"
    )
    assert report.validated is False
    assert any("confirmed without approved business evidence" in error for error in report.errors)
    assert any("unsupported system promotion" in error for error in report.errors)


def test_runtime_and_conflicts_annotate_scanner_backed_topology_only(tmp_path: Path) -> None:
    scan_root = tmp_path / "scan-output"
    staging = tmp_path / "staging"
    staging.mkdir()
    _write_scan(scan_root)
    (staging / "_phase1").mkdir()
    (staging / "_phase1" / "supplemental.json").write_text(
        json.dumps(
            {
                "claims": [
                    {
                        "claim_id": "runtime-retries",
                        "claim_class": "observed_runtime",
                        "statement": "Retries were observed.",
                        "evidence_ids": ["runtime:item"],
                        "fact_ids": [],
                    },
                    {
                        "claim_id": "intended-push",
                        "claim_class": "intended_documented",
                        "statement": "The intended delivery mode is push.",
                        "evidence_ids": ["policy:item"],
                        "fact_ids": [],
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    (staging / "_analysis").mkdir()
    (staging / "_analysis" / "orders.md").write_text(
        "# Orders\n\nOwner: Fulfilment. Intended delivery is push; runtime showed retries.",
        encoding="utf-8",
    )
    bundle = build_business_topology_evidence(
        project_id="demo",
        scan_output_dir=scan_root,
        staging_dir=staging,
    )
    artifact_id = next(iter(bundle.artifact_ids))
    analysis_id = next(
        evidence_id
        for evidence_id, evidence_type in bundle.evidence_types.items()
        if evidence_type == "phase2_analysis"
    )
    typed_by_class = {
        record["claim_class"]: evidence_id
        for evidence_id, record in bundle.evidence_records.items()
        if bundle.evidence_types[evidence_id]
        in {"observed_runtime", "intended_documented"}
    }
    runtime_id = typed_by_class["observed_runtime"]
    intended_id = typed_by_class["intended_documented"]
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
                "purpose": "Provides order data",
                "owner": "Fulfilment",
                "status": "inferred",
                "confidence": 0.7,
                "evidence_ids": [artifact_id, identity_by_scanner_id["system:orders-source"]],
            },
            {
                "id": "system:target",
                "name": "REST consumer",
                "status": "inferred",
                "confidence": 0.7,
                "evidence_ids": [artifact_id, identity_by_scanner_id["system:orders-consumer"]],
            },
        ],
        "data_streams": [
            {
                "id": "stream:orders",
                "name": "Order delivery",
                "status": "inferred",
                "confidence": 0.8,
                "evidence_ids": [edge_id, analysis_id],
                "source_system_id": "system:source",
                "destination_system_id": "system:target",
                "business_summary": "Delivers orders to the REST consumer.",
                "runtime_observation_ids": ["runtime:retries"],
                "conflict_ids": ["conflict:delivery-mode"],
                "artifact_ids": [artifact_id],
            }
        ],
        "runtime_observations": [
            {
                "id": "runtime:retries",
                "name": "Retries observed",
                "status": "observed",
                "confidence": 0.6,
                "evidence_ids": [runtime_id],
                "stream_ids": ["stream:orders"],
                "observed_behavior": "The accepted analysis reports retry activity.",
            }
        ],
        "conflicts": [
            {
                "id": "conflict:delivery-mode",
                "name": "Intended and implemented delivery differ",
                "status": "inferred",
                "confidence": 0.8,
                "evidence_ids": [edge_id, intended_id],
                "conflict_type": "supporting_vs_implementation",
                "subject_refs": ["stream:orders"],
                "implementation_evidence_ids": [edge_id],
                "supporting_evidence_ids": [intended_id],
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

    topology, report = parse_and_validate_business_topology(
        json.dumps(payload), bundle=bundle, project_id="demo"
    )

    assert report.validated is True
    assert topology is not None
    assert topology.runtime_observations[0].stream_ids == ["stream:orders"]
    assert topology.conflicts[0].implementation_evidence_ids == [edge_id]

    payload["runtime_observations"][0]["evidence_ids"] = [analysis_id]
    generic_runtime, generic_runtime_report = parse_and_validate_business_topology(
        json.dumps(payload), bundle=bundle, project_id="demo"
    )
    assert generic_runtime is None
    assert any("no typed runtime evidence" in error for error in generic_runtime_report.errors)
    payload["runtime_observations"][0]["evidence_ids"] = [runtime_id]

    conflict = payload["conflicts"][0]
    conflict["conflict_type"] = "supporting_sources_disagree"
    conflict["implementation_evidence_ids"] = []
    conflict["supporting_evidence_ids"] = [runtime_id, intended_id]
    conflict["evidence_ids"] = [runtime_id, intended_id]
    supporting_topology, supporting_report = parse_and_validate_business_topology(
        json.dumps(payload), bundle=bundle, project_id="demo"
    )
    assert supporting_report.validated is True
    assert supporting_topology is not None
    conflict["supporting_evidence_ids"] = [intended_id]
    conflict["evidence_ids"] = [intended_id]
    one_source_topology, one_source_report = parse_and_validate_business_topology(
        json.dumps(payload), bundle=bundle, project_id="demo"
    )
    assert one_source_topology is None
    assert any("at least two typed supporting" in error for error in one_source_report.errors)
    conflict["conflict_type"] = "supporting_vs_implementation"
    conflict["implementation_evidence_ids"] = [edge_id]
    conflict["supporting_evidence_ids"] = [intended_id]
    conflict["evidence_ids"] = [edge_id, intended_id]

    payload["runtime_observations"][0]["source_system_id"] = "system:source"
    invalid_topology, invalid_report = parse_and_validate_business_topology(
        json.dumps(payload), bundle=bundle, project_id="demo"
    )
    assert invalid_topology is None
    assert invalid_report.validated is False
    assert "source_system_id" in invalid_report.errors[0]


def test_unresolved_or_supported_identity_is_required_for_system_promotion(
    tmp_path: Path,
) -> None:
    scan_root = tmp_path / "scan-output"
    staging = tmp_path / "staging"
    staging.mkdir()
    _write_scan(scan_root)
    bundle = build_business_topology_evidence(
        project_id="demo", scan_output_dir=scan_root, staging_dir=staging
    )
    artifact_id = next(iter(bundle.artifact_ids))
    endpoint_id = next(
        evidence_id
        for evidence_id, evidence_type in bundle.evidence_types.items()
        if evidence_type == "scanner_endpoint"
    )
    payload = {
        "schema_version": "business-topology-v1",
        "scan_fingerprint": bundle.scan_fingerprint,
        "metadata": {"project_id": "demo", "scan_fingerprint": bundle.scan_fingerprint},
        "systems": [
            {
                "id": "system:promoted-job",
                "name": "ReadOrders",
                "status": "inferred",
                "confidence": 0.7,
                "evidence_ids": [artifact_id],
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

    _, report = parse_and_validate_business_topology(
        json.dumps(payload), bundle=bundle, project_id="demo"
    )

    assert report.validated is False
    assert any("unsupported system promotion" in error for error in report.errors)

    payload["systems"][0]["evidence_ids"] = [endpoint_id]
    endpoint_topology, endpoint_report = parse_and_validate_business_topology(
        json.dumps(payload), bundle=bundle, project_id="demo"
    )
    assert endpoint_topology is None
    assert any("unsupported system promotion" in error for error in endpoint_report.errors)

    payload["systems"] = [
        {
            "id": "system:unresolved",
            "name": "Candidate system",
            "kind": "unresolved",
            "status": "not_established",
            "confidence": 0,
        }
    ]
    topology, required_report = parse_and_validate_business_topology(
        json.dumps(payload), bundle=bundle, project_id="demo"
    )
    assert topology is None
    assert required_report.validated is False
    assert "evidence_ids" in required_report.errors[0]

    payload["systems"][0]["evidence_ids"] = []
    payload["schema_version"] = "business-topology-v2-unsupported"
    unsupported, unsupported_report = parse_and_validate_business_topology(
        json.dumps(payload), bundle=bundle, project_id="demo"
    )
    assert unsupported is None
    assert unsupported_report.validated is False
    assert "schema_version" in unsupported_report.errors[0]


@pytest.mark.asyncio
async def test_business_topology_phase_failure_is_non_blocking(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    staging = tmp_path / "staging"
    staging.mkdir()
    monkeypatch.setattr(
        "mcp_server.services.business_topology.build_business_topology_evidence",
        MagicMock(side_effect=RuntimeError("synthetic topology failure")),
    )
    ctx = MagicMock(project_id="demo", staging_dir=staging, resume_state=None)
    facts = FactsManifest(
        project_id="demo",
        platform="generic",
        canonical_unit_label="unit",
        unit_count=0,
    )
    state = HarnessRunState(project_id="demo", facts_manifest=facts)
    result = await Phase37BusinessTopology(MagicMock()).run(ctx, state, AsyncMock())
    report = json.loads((staging / "_topology" / "validation_report.json").read_text(encoding="utf-8"))
    assert result.success is True
    assert report["validated"] is False
    assert "synthetic topology failure" in report["errors"][0]
    assert state.warnings


def test_evidence_unit_allowlist_scopes_artifacts_and_lineage(tmp_path: Path) -> None:
    """Composite sub-project scoping: only allowlisted units yield artifact and
    lineage evidence; landscape rows stay upload-wide (connective context)."""
    _write_scan(tmp_path)
    staging = tmp_path / "staging"
    staging.mkdir()

    # Unscoped baseline includes the ReadOrders artifact + its lineage entry.
    full = build_business_topology_evidence(
        project_id="project-id", scan_output_dir=tmp_path, staging_dir=staging,
    )
    assert len(full.artifact_ids) == 1
    assert len(full.lineage_ids) == 1

    # Allowlisting a different unit removes the artifact and lineage rows.
    staging_scoped = tmp_path / "staging_scoped"
    staging_scoped.mkdir()
    scoped = build_business_topology_evidence(
        project_id="project-id",
        scan_output_dir=tmp_path,
        staging_dir=staging_scoped,
        unit_allowlist={"SomeOtherUnit"},
    )
    assert len(scoped.artifact_ids) == 0
    assert len(scoped.lineage_ids) == 0
    # Landscape systems remain available as labeled cross-project context.
    assert any(t == "scanner_system" for t in scoped.evidence_types.values())

    # Allowlisting the actual unit keeps everything.
    staging_own = tmp_path / "staging_own"
    staging_own.mkdir()
    own = build_business_topology_evidence(
        project_id="project-id",
        scan_output_dir=tmp_path,
        staging_dir=staging_own,
        unit_allowlist={"ReadOrders"},
    )
    assert len(own.artifact_ids) == 1
    assert len(own.lineage_ids) == 1


def test_starter_pack_includes_cross_project_index_when_staged(tmp_path: Path) -> None:
    _write_scan(tmp_path)
    staging = tmp_path / "staging"
    (staging / "_topology").mkdir(parents=True)
    (staging / "_topology" / "cross_project_index.json").write_text(
        json.dumps({
            "subprojects": {"Alpha": {}, "Beta": {}},
            "call_edges": [{
                "from_subproject": "Alpha", "to_subproject": "Beta",
                "caller": "A_Main", "callee": "B_Worker",
            }],
        }),
        encoding="utf-8",
    )
    bundle = build_business_topology_evidence(
        project_id="project-id", scan_output_dir=tmp_path, staging_dir=staging,
    )
    starter = bundle.starter_pack_path.read_text(encoding="utf-8")
    assert "Cross-Project Context (composite upload)" in starter
    assert "B_Worker" in starter
    # Non-composite runs (no staged index) omit the section entirely.
    staging_plain = tmp_path / "staging_plain"
    staging_plain.mkdir()
    plain = build_business_topology_evidence(
        project_id="project-id", scan_output_dir=tmp_path, staging_dir=staging_plain,
    )
    assert "Cross-Project Context" not in plain.starter_pack_path.read_text(encoding="utf-8")
