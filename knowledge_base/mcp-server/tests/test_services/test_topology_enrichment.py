"""Deterministic topology enrichment (services/business_topology/enrichment.py).

All fixtures are synthetic: a five-design Talend-shaped scan with one DI job
per concern, one ESB route, one deactivated job and one tRunJob child.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mcp_server.services.business_topology import (
    build_business_topology_evidence,
    build_deterministic_topology,
    load_business_topology_evidence,
    validate_business_topology,
)
from mcp_server.services.business_topology.enrichment import (
    FILLED_FIELDS,
    EnrichmentSources,
    enrich_topology,
    persist_runtime_evidence,
    register_runtime_evidence,
)
from mcp_server.services.business_topology.evidence import artifact_evidence_id
from mcp_server.services.business_topology.models import BusinessTopology

PROJECT = "synthetic-project"
ROOT = "SYNTH/PROJECT"


def _param(name: str, value: str) -> dict:
    return {"name": name, "value": value}


def _component(name: str, ctype: str, *, active: bool = True, schema: list[str] | None = None) -> dict:
    item: dict = {"unique_name": name, "component_type": ctype, "params": []}
    if not active:
        item["params"].append(_param("ACTIVATE", "false"))
    if schema is not None:
        item["schemas"] = [
            {"connector": "FLOW", "columns": [{"name": column} for column in schema]}
        ]
    return item


def _flow(name: str, source: str, target: str, connector: str = "FLOW", params: list | None = None) -> dict:
    return {
        "name": name,
        "source": source,
        "target": target,
        "connector_name": connector,
        "params": params or [],
    }


def _job(name: str, components: list[dict], connections: list[dict], **extra) -> dict:
    return {
        "name": name,
        "job_id": name,
        "source_root": ROOT,
        "path": f"process/{name}_0.1.item",
        "version": "0.1",
        "artifact_kind": "job",
        "artifact_role": "execution",
        "active": True,
        "is_esb_route": False,
        "components": components,
        "connections": connections,
        **extra,
    }


JOBS = [
    _job(
        "Orders_GetLocation",
        [
            _component("tDBInput_1", "tDBInput", schema=["ID", "NAME", "CITY", "ZIP", "COUNTRY"]),
            _component("tMap_1", "tMap"),
            _component("tRESTClient_1", "tRESTClient"),
            _component("tFileOutputDelimited_1", "tFileOutputDelimited", active=False),
            _component("tLogRow_1", "tLogRow", active=False),
            _component("tJava_1", "tJava"),
            _component("tRunJob_1", "tRunJob"),
        ],
        [
            _flow("row1", "tDBInput_1", "tMap_1"),
            _flow("out1", "tMap_1", "tRESTClient_1"),
            _flow("out2", "tMap_1", "tFileOutputDelimited_1"),
            _flow(
                "If1",
                "tJava_1",
                "tRunJob_1",
                connector="RUN_IF",
                params=[_param("CONDITION", '((Integer)globalMap.get("tDBInput_1_NB_LINE")) > 0')],
            ),
        ],
    ),
    _job(
        "Orders_GetLocations",
        [
            _component("tDBInput_1", "tDBInput", schema=["ID", "NAME"]),
            _component("tDBOutput_1", "tDBOutput"),
        ],
        [_flow("row1", "tDBInput_1", "tDBOutput_1")],
    ),
    _job(
        "Orders_Route",
        [
            _component("cJMS_1", "cJMS"),
            _component("cProcessor_1", "cProcessor"),
            _component("cHttp_1", "cHttp"),
            _component("cFile_1", "cFile"),
        ],
        [
            _flow("route1", "cJMS_1", "cProcessor_1", connector="ROUTE"),
            _flow(
                "when1",
                "cProcessor_1",
                "cHttp_1",
                connector="ROUTE_WHEN",
                params=[
                    _param("CONDITION", "${header.orderType} == 'EXPRESS'"),
                    _param("ROUTETYPE", "simple"),
                ],
            ),
            _flow(
                "when2",
                "cProcessor_1",
                "cFile_1",
                connector="ROUTE_WHEN",
                params=[
                    _param("CONDITION", "${header.target} == 'https://svc:hunter2@example.invalid/x'"),
                    _param("ROUTETYPE", "simple"),
                ],
            ),
        ],
        artifact_kind="route",
        is_esb_route=True,
    ),
    _job(
        "Legacy_Export",
        [
            _component("tDBInput_1", "tDBInput"),
            _component("tFileOutputDelimited_1", "tFileOutputDelimited"),
        ],
        [_flow("row1", "tDBInput_1", "tFileOutputDelimited_1")],
        active=False,
    ),
    _job(
        "Orders_Child",
        [
            _component("tDBInput_1", "tDBInput"),
            _component("tDBOutput_1", "tDBOutput"),
        ],
        [_flow("row1", "tDBInput_1", "tDBOutput_1")],
    ),
]


def _lineage(job: str, source: str, target: str, steps: list[tuple[str, str]], *, resolved: bool, target_component: str) -> dict:
    return {
        "job_name": job,
        "source_table": "row1",
        "source_column": source,
        "target_table": "out1",
        "target_column": target,
        "transformations": [
            {"transformation_type": kind, "expression": expression} for kind, expression in steps
        ],
        "confidence": "certain",
        "resolved": resolved,
        "source_component": "tDBInput_1",
        "target_component": target_component,
    }


LINEAGE = [
    _lineage("Orders_GetLocation", "ID", "locationId", [("direct_map", "row1.ID")], resolved=True, target_component="tRESTClient_1"),
    _lineage(
        "Orders_GetLocation",
        "NAME",
        "locationName",
        [("expression", "StringHandling.UPCASE(row1.NAME)"), ("direct_map", "Var.name")],
        resolved=True,
        target_component="tRESTClient_1",
    ),
    _lineage("Orders_GetLocation", "CITY", "city", [("direct_map", "row1.CITY")], resolved=True, target_component="tFileOutputDelimited_1"),
    _lineage("Orders_GetLocation", "ZIP", "zip", [("direct_map", "row1.ZIP")], resolved=False, target_component="tMap_1"),
    _lineage("Orders_GetLocations", "ID", "ID", [("direct_map", "row1.ID")], resolved=True, target_component="tDBOutput_1"),
]

SYSTEMS = {
    "crm": "Customer Registry",
    "warehouse": "Warehouse Store",
    "broker": "Message Broker",
    "archive": "Archive Share",
    "ledger": "Ledger Store",
}
EDGES = [
    ("crm", "warehouse", "database", "Orders_GetLocation"),
    ("crm", "ledger", "database", "Orders_GetLocations"),
    ("broker", "warehouse", "jms", "Orders_Route"),
    ("crm", "archive", "file", "Legacy_Export"),
    ("ledger", "archive", "database", "Orders_Child"),
]


def _write_scan(root: Path) -> Path:
    (root / "scan").mkdir(parents=True)
    (root / "scan_result.json").write_text(
        json.dumps({"platform": "talend", "jobs": JOBS, "metadata_connections": []}),
        encoding="utf-8",
    )
    (root / "scan" / "manifest.json").write_text(
        json.dumps(
            {
                "source_roots": [{"id": ROOT, "project_id": PROJECT}],
                "job_called_by": {"Orders_Child": ["Orders_GetLocation"]},
                "artifact_relations": [],
                "artifact_resources": [],
                "artifact_endpoints": [],
                "artifact_channels": [],
            }
        ),
        encoding="utf-8",
    )
    (root / "scan" / "lineage.json").write_text(json.dumps({"entries": LINEAGE}), encoding="utf-8")
    (root / "scan" / "landscape.json").write_text(
        json.dumps(
            {
                "systems": [
                    {"id": f"system:{key}", "name": name, "kind": "database", "source_roots": [ROOT]}
                    for key, name in SYSTEMS.items()
                ],
                "applications": [],
                "edges": [
                    {
                        "source": f"system:{source}",
                        "target": f"system:{target}",
                        "dependency_type": "data",
                        "mechanism": mechanism,
                        "direction": "out",
                        "provenance": [{"artifact_id": artifact}],
                    }
                    for source, target, mechanism, artifact in EDGES
                ],
            }
        ),
        encoding="utf-8",
    )
    return root


def _runtime_payload() -> dict:
    return {
        "schema_version": 1,
        "window_days": 90,
        "mode": "data",
        "partial": False,
        "scope_mismatch": False,
        "requested_units": 5,
        "source_key": "tmc:synthetic",
        "read_environments": ["PROD"],
        "by_unit": {
            "Orders_GetLocation": {
                "unit_name": "Orders_GetLocation",
                "job_name": "Orders_GetLocation",
                "environments": ["PROD"],
                "total_runs": 120,
                "success_runs": 117,
                "failed_runs": 3,
                "failure_rate": 0.025,
                "first_run_at": "2026-06-01T00:00:00Z",
                "last_run_at": "2026-08-30T00:00:00Z",
                "gap_p25_seconds": 3550,
                "gap_p50_seconds": 3600,
                "gap_p75_seconds": 3650,
                "gap_sample_size": 119,
                "artifact_version": "0.2",
            },
            "Orders_GetLocations": {
                "unit_name": "Orders_GetLocations",
                "job_name": "Orders_GetLocations",
                "environments": ["PROD"],
                "total_runs": 0,
                "failed_runs": 0,
            },
        },
        "unmatched_units": ["Orders_Route", "Orders_Child", "Legacy_Export"],
        "unretrieved_units": [],
    }


@pytest.fixture()
def scan(tmp_path: Path):
    scan_root = _write_scan(tmp_path / "scan_cache")
    staging = tmp_path / "staging"
    staging.mkdir()
    bundle = build_business_topology_evidence(
        project_id=PROJECT, scan_output_dir=scan_root, staging_dir=staging
    )
    topology = build_deterministic_topology(bundle, project_id=PROJECT)
    baseline = validate_business_topology(topology, bundle=bundle, project_id=PROJECT)
    assert baseline.validated, baseline.errors
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "01_OVERVIEW.md").write_text("The Orders_GetLocations job copies ids.\n", encoding="utf-8")
    (docs / "02_FLOWS.md").write_text(
        "## Flows\nOrders_GetLocation. calls the service; Orders_Route listens.\n",
        encoding="utf-8",
    )
    (docs / "_internal.md").write_text("Orders_GetLocation Orders_Child\n", encoding="utf-8")
    return scan_root, bundle, topology, docs


def _stream_for(topology: BusinessTopology, job_name: str):
    job = next(item for item in JOBS if item["name"] == job_name)
    artifact_id = artifact_evidence_id(job)
    return next(stream for stream in topology.data_streams if artifact_id in stream.artifact_ids)


def _full_sources(scan_root: Path, docs: Path, **overrides) -> EnrichmentSources:
    values = {"scan_dir": scan_root, "runtime_payload": _runtime_payload(), "docs_dir": docs}
    values.update(overrides)
    return EnrichmentSources(**values)


def _enriched(scan):
    scan_root, bundle, topology, docs = scan
    return enrich_topology(topology, _full_sources(scan_root, docs))


# --- per-field ---------------------------------------------------------------


def test_contract_width_is_a_narrowing_funnel_from_scanner_counts(scan) -> None:
    enriched, report = _enriched(scan)
    width = _stream_for(enriched, "Orders_GetLocation").contract_width
    assert width is not None
    # 5 schema columns offered, 4 read by lineage, 2 reach an active destination
    # component (CITY lands on a deactivated output, ZIP is unresolved).
    assert (width.offered, width.mapped, width.written) == (5, 4, 2)
    assert "lineage over 1 design(s)" in width.note
    assert "2 distinct destination fields" in width.note
    assert report.filled["contract_width"] >= 2
    # No lineage and no schema -> nothing is fabricated.
    assert _stream_for(enriched, "Orders_Route").contract_width is None
    assert "contract_width" in report.skipped


def test_offered_is_not_published_when_schemas_are_not_recorded(scan) -> None:
    enriched, _ = _enriched(scan)
    # Orders_GetLocations' schema lists ID and NAME; lineage reads ID.
    width = _stream_for(enriched, "Orders_GetLocations").contract_width
    assert (width.offered, width.mapped, width.written) == (2, 1, 1)


def test_conditions_come_from_edges_deactivation_and_runtime_cadence(scan) -> None:
    enriched, report = _enriched(scan)
    route = _stream_for(enriched, "Orders_Route")
    filters = [item for item in route.conditions if item.kind == "filter"]
    assert len(filters) == 2
    assert any("${header.orderType} == 'EXPRESS'" in item.statement for item in filters)
    assert all("hunter2" not in item.statement for item in filters)
    assert any("[REDACTED]" in item.statement for item in filters)
    assert all("Orders_Route connection" in item.evidence_note for item in filters)

    job = _stream_for(enriched, "Orders_GetLocation")
    kinds = {item.kind for item in job.conditions}
    assert {"trigger", "disabled", "schedule"} <= kinds
    trigger = next(item for item in job.conditions if item.kind == "trigger")
    assert "tDBInput_1_NB_LINE" in trigger.statement
    disabled = next(item for item in job.conditions if item.kind == "disabled")
    # The deactivated tLogRow is logging and not part of the delivery.
    assert "tFileOutputDelimited_1" in disabled.statement
    assert "tLogRow_1" not in disabled.statement
    schedule = next(item for item in job.conditions if item.kind == "schedule")
    assert "every 1 h (median gap 3600 s" in schedule.statement
    assert "dominant environment PROD" in schedule.evidence_note

    legacy = _stream_for(enriched, "Legacy_Export")
    assert any(
        item.kind == "disabled" and "is deactivated" in item.statement for item in legacy.conditions
    )
    assert report.filled["conditions"] >= 5


def test_conditions_union_with_upstream_and_dedupe(scan) -> None:
    scan_root, _, topology, docs = scan
    first, _ = enrich_topology(topology, _full_sources(scan_root, docs))
    stream = _stream_for(first, "Orders_Route")
    before = [item.model_dump() for item in stream.conditions]
    second, report = enrich_topology(first, _full_sources(scan_root, docs))
    assert [item.model_dump() for item in _stream_for(second, "Orders_Route").conditions] == before
    assert report.filled["conditions"] == 0


def test_mechanism_direction_and_lifecycle(scan) -> None:
    enriched, _ = _enriched(scan)
    job = _stream_for(enriched, "Orders_GetLocation")
    assert job.mechanism.startswith("Talend DI job: tDBInput -> tMap -> tRESTClient")
    assert job.direction_pattern == "pull"
    assert job.lifecycle == "active"

    route = _stream_for(enriched, "Orders_Route")
    assert route.mechanism.startswith("ESB route: cJMS -> cProcessor")
    assert route.direction_pattern == "push"

    assert _stream_for(enriched, "Legacy_Export").lifecycle == "stopped"


def test_absence_is_not_dormancy(scan) -> None:
    enriched, report = _enriched(scan)
    # Covered by the mirror with zero runs in the window -> dormant.
    assert _stream_for(enriched, "Orders_GetLocations").lifecycle == "dormant"
    # Not known to the mirror at all -> no lifecycle verdict, an absence instead.
    child = _stream_for(enriched, "Orders_Child")
    route = _stream_for(enriched, "Orders_Route")
    assert child.lifecycle == "unknown"
    assert route.lifecycle == "unknown"
    by_id = {item.id: item for item in enriched.runtime_observations}
    child_obs = by_id[f"rt-{child.id}"]
    assert child_obs.kind == "absence"
    assert "tRunJob by Orders_GetLocation" in child_obs.observed_behavior
    route_obs = by_id[f"rt-{route.id}"]
    assert route_obs.kind == "absence"
    assert "not TMC-scheduled" in route_obs.observed_behavior


def test_upstream_values_are_not_overwritten(scan) -> None:
    scan_root, _, topology, docs = scan
    seeded = topology.model_copy(deep=True)
    stream = _stream_for(seeded, "Orders_GetLocation")
    stream.mechanism = "Curated mechanism"
    stream.direction_pattern = "push"
    stream.lifecycle = "duplicate"
    enriched, _ = enrich_topology(seeded, _full_sources(scan_root, docs))
    result = _stream_for(enriched, "Orders_GetLocation")
    assert (result.mechanism, result.direction_pattern, result.lifecycle) == (
        "Curated mechanism",
        "push",
        "duplicate",
    )
    # The scanner is authoritative for contract width.
    assert result.contract_width is not None and result.contract_width.mapped == 4


def test_deployment_agreement_from_runtime_version(scan) -> None:
    enriched, report = _enriched(scan)
    deployment = _stream_for(enriched, "Orders_GetLocation").deployment
    assert deployment is not None
    assert (deployment.supplied_version, deployment.deployed_version) == ("0.1", "0.2")
    assert deployment.agreement == "supplied_behind"
    assert _stream_for(enriched, "Orders_GetLocations").deployment is None
    assert "deployed version" in report.skipped["deployment"]


def test_document_refs_are_boundary_aware(scan) -> None:
    enriched, _ = _enriched(scan)
    assert _stream_for(enriched, "Orders_GetLocation").document_refs == ["02_FLOWS.md"]
    assert _stream_for(enriched, "Orders_GetLocations").document_refs == ["01_OVERVIEW.md"]
    assert _stream_for(enriched, "Orders_Route").document_refs == ["02_FLOWS.md"]
    # `_`-prefixed internals are never references.
    assert _stream_for(enriched, "Orders_Child").document_refs == []


def test_transformation_mappings_gain_verbatim_expressions(scan) -> None:
    enriched, report = _enriched(scan)
    stream = _stream_for(enriched, "Orders_GetLocation")
    transformation = next(item for item in enriched.transformations if stream.id in item.stream_ids)
    by_source = {mapping.source: mapping for mapping in transformation.mappings}
    assert by_source["NAME"].expression == "StringHandling.UPCASE(row1.NAME) -> Var.name"
    assert by_source["NAME"].category == "expression+direct_map"
    assert by_source["NAME"].reaches_target == "yes"
    assert by_source["CITY"].reaches_target == "disabled"
    assert by_source["ZIP"].reaches_target == "unknown"
    assert transformation.kind == "field_mapping"
    assert report.filled["transformations.expression"] >= 4


def test_runtime_observations_carry_window_source_and_metrics(scan) -> None:
    enriched, report = _enriched(scan)
    stream = _stream_for(enriched, "Orders_GetLocation")
    observation = next(item for item in enriched.runtime_observations if item.id == f"rt-{stream.id}")
    assert observation.id in stream.runtime_observation_ids
    assert observation.kind == "reliability"
    assert observation.status == "observed"
    metrics = {metric.label: metric for metric in observation.metrics}
    assert metrics["runs"].value == "120"
    assert metrics["failures"].value == "3"
    assert metrics["failure rate"].value == "0.0250"
    assert metrics["median gap between runs"].value == "3600"
    assert all(metric.window.startswith("last 90 days") for metric in observation.metrics)
    assert all(
        metric.source == "TMC runtime mirror, env PROD, read scope environments PROD"
        for metric in observation.metrics
    )
    assert set(observation.evidence_ids) <= set(report.runtime_evidence)
    assert all(
        record["type"] == "observed_runtime" for record in report.runtime_evidence.values()
    )
    assert report.filled["runtime_observations"] == len(enriched.data_streams)
    assert report.filled["runtime_observations.absence"] == 3


# --- validation and evidence registration ------------------------------------


def test_enriched_topology_validates_once_runtime_evidence_is_registered(scan) -> None:
    _, bundle, _, _ = scan
    enriched, report = _enriched(scan)
    unregistered = validate_business_topology(enriched, bundle=bundle, project_id=PROJECT)
    assert any("no typed runtime evidence" in error for error in unregistered.errors)

    extended = register_runtime_evidence(bundle, report.runtime_evidence)
    validated = validate_business_topology(enriched, bundle=extended, project_id=PROJECT)
    assert validated.validated, validated.errors
    assert not any("contract width" in warning for warning in validated.warnings)


def test_persisted_runtime_evidence_survives_a_publication_reload(scan) -> None:
    _, bundle, _, _ = scan
    enriched, report = _enriched(scan)
    persist_runtime_evidence(bundle.root / "evidence", report.runtime_evidence)
    reloaded = load_business_topology_evidence(bundle.root)
    assert set(report.runtime_evidence) <= reloaded.evidence_ids
    result = validate_business_topology(enriched, bundle=reloaded, project_id=PROJECT)
    assert result.validated, result.errors

    # A later attempt without runtime data must not leave a stale ledger behind.
    persist_runtime_evidence(bundle.root / "evidence", {})
    assert not (bundle.root / "evidence" / "runtime.jsonl").exists()
    cleared = load_business_topology_evidence(bundle.root)
    assert not set(report.runtime_evidence) & cleared.evidence_ids


def test_without_runtime_payload_the_output_validates_against_the_plain_bundle(scan) -> None:
    scan_root, bundle, topology, docs = scan
    enriched, report = enrich_topology(
        topology, EnrichmentSources(scan_dir=scan_root, docs_dir=docs)
    )
    assert not enriched.runtime_observations
    assert report.runtime_evidence == {}
    assert report.skipped["runtime_observations"].startswith("no runtime payload supplied")
    result = validate_business_topology(enriched, bundle=bundle, project_id=PROJECT)
    assert result.validated, result.errors


# --- robustness, scoping, stability -------------------------------------------


def test_no_inputs_changes_nothing_and_does_not_raise(scan, tmp_path: Path) -> None:
    _, _, topology, _ = scan
    before = topology.model_dump_json()
    enriched, report = enrich_topology(
        topology, EnrichmentSources(scan_dir=tmp_path / "missing")
    )
    assert enriched.model_dump_json() == before
    assert topology.model_dump_json() == before
    assert "scan_result.json not found" in report.skipped["scan"]
    assert report.skipped["runtime_observations"].startswith("no runtime payload supplied")
    assert report.skipped["document_refs"].startswith("no docs_dir supplied")
    assert set(report.filled) == set(FILLED_FIELDS)
    assert not any(report.filled.values())


@pytest.mark.parametrize(
    "payload",
    [
        "not an object",
        {"mode": "data", "window_days": "ninety", "by_unit": {"x": {"total_runs": "many"}}},
        {"mode": "unavailable", "by_unit": {}},
        {"mode": "data", "by_unit": ["wrong", "shape"]},
    ],
)
def test_malformed_runtime_payload_degrades(scan, payload) -> None:
    scan_root, bundle, topology, docs = scan
    enriched, report = enrich_topology(
        topology, _full_sources(scan_root, docs, runtime_payload=payload)
    )
    extended = register_runtime_evidence(bundle, report.runtime_evidence)
    assert validate_business_topology(enriched, bundle=extended, project_id=PROJECT).validated
    json.dumps(report.as_dict())


def test_malformed_scan_file_degrades(scan) -> None:
    scan_root, _, topology, docs = scan
    (scan_root / "scan" / "lineage.json").write_text("{not json", encoding="utf-8")
    enriched, report = enrich_topology(topology, _full_sources(scan_root, docs))
    # Designs still resolve; lineage-derived counts do not.
    width = _stream_for(enriched, "Orders_GetLocation").contract_width
    assert width is not None and width.mapped is None and width.offered == 5
    assert _stream_for(enriched, "Orders_GetLocation").mechanism


def test_unit_allowlist_scopes_enrichment_to_the_partition(scan) -> None:
    scan_root, _, topology, docs = scan
    enriched, _ = enrich_topology(
        topology,
        _full_sources(scan_root, docs, unit_allowlist=frozenset({"Orders_Route"})),
    )
    assert _stream_for(enriched, "Orders_Route").mechanism.startswith("ESB route")
    outside = _stream_for(enriched, "Orders_GetLocation")
    assert outside.mechanism == ""
    assert outside.contract_width is None
    assert outside.document_refs == []
    assert not any(obs.id == f"rt-{outside.id}" for obs in enriched.runtime_observations)


def test_output_is_byte_stable_pure_and_idempotent(scan) -> None:
    scan_root, _, topology, docs = scan
    before = topology.model_dump_json()
    first, first_report = enrich_topology(topology, _full_sources(scan_root, docs))
    second, second_report = enrich_topology(topology, _full_sources(scan_root, docs))
    assert topology.model_dump_json() == before
    assert first.model_dump_json() == second.model_dump_json()
    assert json.dumps(first_report.as_dict()) == json.dumps(second_report.as_dict())
    again, _ = enrich_topology(first, _full_sources(scan_root, docs))
    assert again.model_dump_json() == first.model_dump_json()
    assert len({item.id for item in again.runtime_observations}) == len(again.runtime_observations)
