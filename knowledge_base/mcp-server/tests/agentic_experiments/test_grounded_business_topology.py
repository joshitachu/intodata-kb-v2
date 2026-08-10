"""Grounded topology preparation uses only immutable experiment sources."""

from __future__ import annotations

import json
from pathlib import Path
import shutil

import pytest

from mcp_server.experiments.application.grounded_business_topology import (
    GroundedBusinessTopologyError,
    prepare_grounded_business_topology,
    validate_grounded_business_topology,
)


def _write_scan(root: Path, *, semantic_name: str = "scanner guess") -> None:
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
                                "params": [{"name": "METHOD", "value": "GET"}],
                            }
                        ],
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
                "semantic_topology": {"systems": [{"name": semantic_name}]},
            }
        ),
        encoding="utf-8",
    )


def _write_multi_source_scan(root: Path) -> None:
    _write_scan(root)
    scan_path = root / "scan_result.json"
    scan = json.loads(scan_path.read_text(encoding="utf-8"))
    scan["jobs"].append(
        {
            "name": "PublishInvoices",
            "job_id": "PublishInvoices",
            "source_root": "IMPORT/FINANCE",
            "path": "Invoices/PublishInvoices.item",
            "artifact_kind": "job",
            "artifact_role": "execution",
            "components": [
                {
                    "unique_name": "tJMSOutput_1",
                    "component_type": "tJMSOutput",
                    "params": [],
                }
            ],
        }
    )
    scan_path.write_text(json.dumps(scan), encoding="utf-8")

    manifest_path = root / "scan" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["source_roots"].append(
        {"id": "IMPORT/FINANCE", "project_id": "finance-project"}
    )
    manifest["artifact_relations"] = [
        {
            "source": "ReadOrders",
            "target": "PublishInvoices",
            "direction": "outbound",
            "mechanism": "JMS",
        }
    ]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")


def _source_items(root: Path, *, include_unknown: bool = False) -> tuple[tuple[str, Path], ...]:
    items = [
        ("scan:scan_result.json", root / "scan_result.json"),
        ("scan:scan/manifest.json", root / "scan" / "manifest.json"),
        ("scan:scan/lineage.json", root / "scan" / "lineage.json"),
        ("scan:scan/landscape.json", root / "scan" / "landscape.json"),
    ]
    if include_unknown:
        ignored = root / "ignored.json"
        ignored.write_text("{}", encoding="utf-8")
        items.append(("scan:unrelated/ignored.json", ignored))
    return tuple(items)


def _accepted(root: Path) -> tuple[dict[str, Path], dict[str, Path]]:
    phase1 = root / "candidate-phase1"
    phase1.mkdir()
    structure = phase1 / "structure.json"
    structure.write_text(
        json.dumps(
            {
                "claims": [
                    {
                        "name": "Order source",
                        "purpose": "Provides orders for downstream processing",
                    },
                    {
                        "claim_id": "runtime-retries",
                        "claim_class": "observed_runtime",
                        "statement": "Retries were observed.",
                        "evidence_ids": ["runtime:item"],
                        "fact_ids": [],
                    },
                    {
                        "claim_id": "intended-direct",
                        "claim_class": "intended_documented",
                        "statement": "Direct delivery is intended.",
                        "evidence_ids": ["policy:item"],
                        "fact_ids": [],
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    analyses = root / "candidate-analyses"
    analyses.mkdir()
    operations = analyses / "orders.md"
    operations.write_text("# Orders\n\nREST GET is observed.", encoding="utf-8")
    return (
        {"candidate/orchestration/evidence_packets/structure.json": structure},
        {"candidate/intermediates/phase2/orders.md": operations},
    )


def _prepare(tmp_path: Path, *, source_root: Path, source_items=None, max_packet_bytes=4 * 1024 * 1024):
    tmp_path.mkdir(parents=True, exist_ok=True)
    phase1, phase2 = _accepted(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return prepare_grounded_business_topology(
        project_id="demo",
        source_items=source_items or _source_items(source_root),
        accepted_phase1=phase1,
        accepted_phase2=phase2,
        work_root=workspace,
        packet_root=Path("role-input"),
        max_packet_bytes=max_packet_bytes,
    )


def test_preparation_uses_frozen_snapshot_not_mutated_live_scan(tmp_path: Path) -> None:
    live_scan = tmp_path / "live-scan"
    _write_scan(live_scan, semantic_name="live scanner proposal")
    frozen_scan = tmp_path / "frozen-source-snapshot"
    shutil.copytree(live_scan, frozen_scan)

    # The current project scan changes after source snapshot creation.  The
    # helper receives only the immutable snapshot copy and cannot see this.
    (live_scan / "scan_result.json").write_text(
        json.dumps({"platform": "talend", "jobs": [{"name": "ChangedLiveJob"}]}),
        encoding="utf-8",
    )

    preparation = _prepare(tmp_path, source_root=frozen_scan)
    frozen_payload = json.loads((preparation.scan_root / "scan_result.json").read_text())
    assert frozen_payload["jobs"][0]["name"] == "ReadOrders"
    assert "ChangedLiveJob" not in preparation.bundle.overview_path.read_text(encoding="utf-8")


def test_preparation_excludes_legacy_semantic_overlay_and_stages_nested_analysis(tmp_path: Path) -> None:
    scan = tmp_path / "frozen"
    _write_scan(scan, semantic_name="do not expose this proposal")
    preparation = _prepare(
        tmp_path,
        source_root=scan,
        source_items=_source_items(scan, include_unknown=True),
    )

    overview = preparation.bundle.overview_path.read_text(encoding="utf-8")
    packet = (preparation.packet_root / "topology" / "packet_manifest.json").read_text(
        encoding="utf-8"
    )
    assert "do not expose this proposal" not in overview
    assert json.loads(overview)["semantic_derivations"] == []
    assert json.loads(packet)["semantic_derivations"] == []
    assert json.loads(packet)["input_contract"] == (
        "accepted_phase1_phase2_plus_scanner_evidence"
    )
    assert (preparation.staging_dir / "_phase1" / "structure.json").is_file()
    assert (preparation.staging_dir / "_analysis" / "phase2__orders.md").is_file()
    assert preparation.prompt_path.is_file()
    assert "candidate/topology/business_topology.json" in preparation.prompt_path.read_text(
        encoding="utf-8"
    )
    assert (preparation.packet_root / "topology" / "analyses" / "phase1" / "structure.json").is_file()
    assert (preparation.packet_root / "topology" / "analyses" / "phase2" / "phase2__orders.md").read_text(
        encoding="utf-8"
    ) == "# Orders\n\nREST GET is observed."
    supporting_rows = [
        json.loads(line)
        for line in (
            preparation.packet_root
            / "topology"
            / "evidence"
            / "supporting_artifacts.jsonl"
        )
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert supporting_rows
    assert all(
        (preparation.packet_root / row["path"]).is_file()
        for row in supporting_rows
    )
    packet_overview = json.loads(
        (preparation.packet_root / "topology" / "factual_evidence_index.json").read_text(
            encoding="utf-8"
        )
    )
    assert all(
        (preparation.packet_root / path).is_file()
        for path in packet_overview["evidence_files"].values()
    )
    assert "_analysis/" not in (
        preparation.packet_root / "topology" / "_doc_pack_business_topology.md"
    ).read_text(encoding="utf-8")
    prompt = preparation.prompt_path.read_text(encoding="utf-8")
    assert "topology/analyses/phase1/" in prompt
    assert "topology/analyses/phase2/" in prompt
    assert "Runtime observations may only annotate" in prompt
    assert "_topology/" not in prompt


def test_topology_packet_never_contains_phase3_writer_documents(tmp_path: Path) -> None:
    """Topology receives accepted Phase 1/2 context, never final writer output."""

    scan = tmp_path / "frozen"
    _write_scan(scan)
    # A writer artifact may already exist in the experiment workspace when the
    # optional topology leaf starts.  It must not be copied into the topology
    # packet or become available through its contained evidence files.
    # The helper creates the workspace itself; create the writer output after
    # packet preparation below to model a completed upstream writer role.
    preparation = _prepare(tmp_path / "prepared", source_root=scan)
    writer_root = preparation.packet_root.parents[1] / "candidate" / "docs"
    writer_root.mkdir(parents=True)
    (writer_root / "09_EXECUTIVE_SUMMARY.md").write_text(
        "A writer-only claim that must not reach topology.", encoding="utf-8"
    )
    packet_files = {
        path.relative_to(preparation.packet_root).as_posix()
        for path in preparation.packet_root.rglob("*")
        if path.is_file()
    }
    packet_text = "\n".join(
        path.read_text(encoding="utf-8", errors="replace")
        for path in preparation.packet_root.rglob("*")
        if path.is_file()
    )
    assert not any(path.startswith("candidate/docs/") for path in packet_files)
    assert "writer-only claim" not in packet_text


def test_runtime_observation_cannot_use_phase2_prose_as_runtime_provenance(
    tmp_path: Path,
) -> None:
    """Runtime annotations require an observed-runtime evidence class."""

    scan = tmp_path / "frozen"
    _write_scan(scan)
    preparation = _prepare(tmp_path / "prepared", source_root=scan)
    phase2_id = next(
        evidence_id
        for evidence_id, evidence_type in preparation.bundle.evidence_types.items()
        if evidence_type == "phase2_analysis"
    )
    payload = {
        "schema_version": "business-topology-v1",
        "scan_fingerprint": preparation.bundle.scan_fingerprint,
        "metadata": {
            "project_id": "demo",
            "scan_fingerprint": preparation.bundle.scan_fingerprint,
        },
        "runtime_observations": [
            {
                "id": "runtime:unsupported",
                "name": "Unsupported runtime claim",
                "status": "observed",
                "confidence": 0.8,
                "evidence_ids": [phase2_id],
                "stream_ids": ["stream:missing"],
                "observed_behavior": "Phase 2 prose says the stream retried.",
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
    result = validate_grounded_business_topology(
        json.dumps(payload), project_id="demo", preparation=preparation
    )
    assert result.topology is None
    assert any("no typed runtime evidence" in error for error in result.report.errors)


@pytest.mark.parametrize(
    "logical_identity",
    ("scan:../scan_result.json", "scan:/scan_result.json", "scan:scan\\manifest.json"),
)
def test_preparation_rejects_unsafe_frozen_logical_paths(tmp_path: Path, logical_identity: str) -> None:
    scan = tmp_path / "frozen"
    _write_scan(scan)
    items = list(_source_items(scan))
    items[0] = (logical_identity, scan / "scan_result.json")
    with pytest.raises(GroundedBusinessTopologyError, match="unsafe"):
        _prepare(tmp_path, source_root=scan, source_items=tuple(items))


def test_preparation_requires_all_factual_scan_artifacts(tmp_path: Path) -> None:
    scan = tmp_path / "frozen"
    _write_scan(scan)
    items = tuple(item for item in _source_items(scan) if not item[0].endswith("lineage.json"))
    with pytest.raises(GroundedBusinessTopologyError, match="missing required scanner artifacts"):
        _prepare(tmp_path, source_root=scan, source_items=items)


def test_preparation_requires_accepted_phase1_and_phase2_inputs(tmp_path: Path) -> None:
    scan = tmp_path / "frozen"
    _write_scan(scan)
    _phase1, phase2 = _accepted(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    with pytest.raises(GroundedBusinessTopologyError, match="both required"):
        prepare_grounded_business_topology(
            project_id="demo",
            source_items=_source_items(scan),
            accepted_phase1={},
            accepted_phase2=phase2,
            work_root=workspace,
            packet_root=Path("role-input"),
            max_packet_bytes=4 * 1024 * 1024,
        )


def test_final_packet_byte_cap_includes_phase1_phase2_prompt_and_manifest(
    tmp_path: Path,
) -> None:
    scan = tmp_path / "frozen"
    _write_scan(scan)
    baseline = _prepare(tmp_path / "baseline", source_root=scan)
    evidence_only_bytes = sum(
        path.stat().st_size
        for path in baseline.bundle.root.rglob("*")
        if path.is_file() and not path.is_symlink()
    )
    final_packet_bytes = sum(
        path.stat().st_size
        for path in baseline.packet_root.rglob("*")
        if path.is_file() and not path.is_symlink()
    )
    assert final_packet_bytes > evidence_only_bytes

    with pytest.raises(GroundedBusinessTopologyError, match="exceeds byte budget"):
        _prepare(
            tmp_path / "capped",
            source_root=scan,
            max_packet_bytes=evidence_only_bytes,
        )


def test_talend_only_validation_normalizes_and_rejects_unknown_evidence(
    tmp_path: Path,
) -> None:
    scan = tmp_path / "frozen"
    _write_scan(scan)
    preparation = _prepare(tmp_path, source_root=scan)
    artifact_id = next(iter(preparation.bundle.artifact_ids))
    phase2_id = next(
        evidence_id
        for evidence_id, evidence_type in preparation.bundle.evidence_types.items()
        if evidence_type == "phase2_analysis"
    )
    identity_by_scanner_id = {
        record["scanner_id"]: evidence_id
        for evidence_id, record in preparation.bundle.evidence_records.items()
        if preparation.bundle.evidence_types[evidence_id] == "scanner_system"
    }
    edge_id = next(
        evidence_id
        for evidence_id, evidence_type in preparation.bundle.evidence_types.items()
        if evidence_type == "scanner_edge"
    )
    payload = {
        "schema_version": "business-topology-v1",
        "scan_fingerprint": preparation.bundle.scan_fingerprint,
        "metadata": {
            "project_id": "demo",
            "scan_fingerprint": preparation.bundle.scan_fingerprint,
        },
        "systems": [
            {
                "id": "system:source",
                "name": "Order source",
                "status": "proposed",
                "confidence": 0.7,
                "evidence_ids": [
                    artifact_id,
                    phase2_id,
                    identity_by_scanner_id["system:orders-source"],
                ],
            },
            {
                "id": "system:target",
                "name": "Order consumer",
                "status": "proposed",
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
                "name": "Order transfer",
                "status": "inferred",
                "confidence": 0.7,
                "evidence_ids": [edge_id],
                "source_system_id": "system:source",
                "destination_system_id": "system:target",
                "business_summary": "Provides order data to a consumer.",
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
    valid = validate_grounded_business_topology(
        json.dumps(payload), project_id="demo", preparation=preparation
    )
    assert valid.report.validated is True
    assert valid.topology is not None
    assert valid.normalized_json is not None
    assert valid.state_payload["available"] is True
    assert json.loads(valid.normalized_json)["coverage"]["cited_artifacts"] == 1
    assert json.loads(preparation.bundle.overview_path.read_text())["platform"] == "talend"

    payload["data_streams"][0]["evidence_ids"] = ["endpoint:not-real"]
    invalid = validate_grounded_business_topology(
        json.dumps(payload), project_id="demo", preparation=preparation
    )
    assert invalid.topology is None
    assert invalid.normalized_json is None
    assert invalid.state_payload["available"] is False
    assert any("unknown evidence" in error for error in invalid.report.errors)


def test_multi_source_topology_covers_l1_l2_runtime_conflicts_and_unknowns(
    tmp_path: Path,
) -> None:
    scan = tmp_path / "multi-source"
    _write_multi_source_scan(scan)
    preparation = _prepare(tmp_path, source_root=scan)
    artifact_by_name = {
        str(record.get("name")): evidence_id
        for evidence_id, record in preparation.bundle.evidence_records.items()
        if preparation.bundle.evidence_types[evidence_id] == "scanner_artifact"
    }
    relation_id = next(
        evidence_id
        for evidence_id, evidence_type in preparation.bundle.evidence_types.items()
        if evidence_type == "scanner_relation"
    )
    lineage_id = next(iter(preparation.bundle.lineage_ids))
    phase1_id = next(
        evidence_id
        for evidence_id, evidence_type in preparation.bundle.evidence_types.items()
        if evidence_type == "phase1_analysis"
    )
    phase2_id = next(
        evidence_id
        for evidence_id, evidence_type in preparation.bundle.evidence_types.items()
        if evidence_type == "phase2_analysis"
    )
    typed_by_class = {
        record["claim_class"]: evidence_id
        for evidence_id, record in preparation.bundle.evidence_records.items()
        if preparation.bundle.evidence_types[evidence_id]
        in {"observed_runtime", "intended_documented"}
    }
    identity_ids = [
        evidence_id
        for evidence_id, evidence_type in preparation.bundle.evidence_types.items()
        if evidence_type == "scanner_system"
    ]
    read_orders_id = artifact_by_name["ReadOrders"]
    publish_invoices_id = artifact_by_name["PublishInvoices"]
    payload = {
        "schema_version": "business-topology-v1",
        "scan_fingerprint": preparation.bundle.scan_fingerprint,
        "metadata": {
            "project_id": "demo",
            "scan_fingerprint": preparation.bundle.scan_fingerprint,
        },
        "systems": [
            {
                "id": "system:orders",
                "name": "Order management",
                "purpose": "Supplies orders",
                "owner": "Operations",
                "status": "inferred",
                "confidence": 0.8,
                "evidence_ids": [read_orders_id, phase1_id, identity_ids[0]],
            },
            {
                "id": "system:finance",
                "name": "Finance",
                "purpose": "Consumes invoice-ready orders",
                "status": "inferred",
                "confidence": 0.8,
                "evidence_ids": [publish_invoices_id, phase2_id, identity_ids[1]],
            },
        ],
        "integration_solutions": [
            {
                "id": "solution:orders",
                "name": "Order integration",
                "status": "observed",
                "confidence": 1,
                "evidence_ids": [read_orders_id],
                "platform": "talend",
                "role": "source integration",
                "artifact_ids": [read_orders_id],
            },
            {
                "id": "solution:finance",
                "name": "Finance integration",
                "status": "observed",
                "confidence": 1,
                "evidence_ids": [publish_invoices_id],
                "platform": "talend",
                "role": "destination integration",
                "artifact_ids": [publish_invoices_id],
            },
        ],
        "business_objects": [
            {
                "id": "object:order",
                "name": "Order",
                "status": "inferred",
                "confidence": 0.8,
                "evidence_ids": [lineage_id, phase2_id],
            }
        ],
        "data_streams": [
            {
                "id": "stream:orders-finance",
                "name": "Orders to finance",
                "status": "inferred",
                "confidence": 0.9,
                "evidence_ids": [relation_id, phase2_id],
                "source_system_id": "system:orders",
                "destination_system_id": "system:finance",
                "business_object_ids": ["object:order"],
                "business_summary": "Moves orders to finance processing.",
                "integration_solution_ids": ["solution:orders", "solution:finance"],
                "delivery_path_id": "path:orders-finance",
                "control_ids": ["control:jms"],
                "risk_ids": ["risk:retry"],
                "unknown_ids": ["unknown:sla"],
                "runtime_observation_ids": ["runtime:retries"],
                "conflict_ids": ["conflict:delivery"],
                "artifact_ids": [read_orders_id, publish_invoices_id],
            }
        ],
        "delivery_paths": [
            {
                "id": "path:orders-finance",
                "name": "Orders delivery path",
                "status": "observed",
                "confidence": 0.9,
                "evidence_ids": [relation_id],
                "stream_id": "stream:orders-finance",
                "segments": [
                    {
                        "id": "segment:jms",
                        "name": "JMS delivery",
                        "status": "observed",
                        "confidence": 0.9,
                        "evidence_ids": [relation_id],
                        "sequence": 1,
                        "source_ref": "solution:orders",
                        "destination_ref": "solution:finance",
                        "mechanism": "JMS",
                        "mode": "buffered",
                    }
                ],
            }
        ],
        "controls": [
            {
                "id": "control:jms",
                "name": "Buffered delivery",
                "status": "observed",
                "confidence": 0.9,
                "evidence_ids": [relation_id],
                "stream_ids": ["stream:orders-finance"],
                "control_type": "delivery",
            }
        ],
        "runtime_observations": [
            {
                "id": "runtime:retries",
                "name": "Retry activity",
                "status": "observed",
                "confidence": 0.6,
                "evidence_ids": [typed_by_class["observed_runtime"]],
                "stream_ids": ["stream:orders-finance"],
                "observed_behavior": "The accepted Phase 1 analysis reports retries.",
            }
        ],
        "conflicts": [
            {
                "id": "conflict:delivery",
                "name": "Intended direct delivery differs from JMS",
                "status": "inferred",
                "confidence": 0.8,
                "evidence_ids": [relation_id, typed_by_class["intended_documented"]],
                "conflict_type": "supporting_vs_implementation",
                "subject_refs": ["stream:orders-finance"],
                "implementation_evidence_ids": [relation_id],
                "supporting_evidence_ids": [typed_by_class["intended_documented"]],
            }
        ],
        "risks": [
            {
                "id": "risk:retry",
                "name": "Retry behavior is not fully established",
                "status": "inferred",
                "confidence": 0.6,
                "evidence_ids": [phase1_id],
                "stream_ids": ["stream:orders-finance"],
            }
        ],
        "unknowns": [
            {
                "id": "unknown:sla",
                "name": "Delivery SLA",
                "status": "not_established",
                "confidence": 0,
                "evidence_ids": [],
                "stream_ids": ["stream:orders-finance"],
                "question": "What delivery SLA applies?",
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

    result = validate_grounded_business_topology(
        json.dumps(payload), project_id="demo", preparation=preparation
    )

    assert result.report.validated is True
    assert result.topology is not None
    assert result.topology.coverage.total_artifacts == 2
    assert result.topology.coverage.technical_relationships == 2
    assert result.topology.coverage.grounded_technical_relationships == 2
    assert result.topology.runtime_observations[0].stream_ids == ["stream:orders-finance"]
    assert result.topology.conflicts[0].implementation_evidence_ids == [relation_id]
