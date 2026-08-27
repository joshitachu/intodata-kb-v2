"""Stage A parsing + Stage B assembly for the doc-grounded topology lane.

The regression these pin is the verified werkorder loss: a model output with 14
systems / 8 streams / 8 delivery paths / 5 business objects was discarded whole
because claims carried the wrong ``status`` enum, streams did not cross-link
identity evidence, and a solution had an empty ``artifact_ids``. The published
fallback had 4 systems, 3 streams and no business layer.
"""

from __future__ import annotations

import json
from pathlib import Path

from mcp_server.services.business_topology.assembly import assemble_topology
from mcp_server.services.business_topology.doc_evidence import (
    build_design_artifact_index,
    collect_doc_evidence,
    doc_evidence_id,
    register_doc_evidence,
    resolve_designs,
    resolve_doc_citation,
)
from mcp_server.services.business_topology.evidence import (
    build_business_topology_evidence,
)
from mcp_server.services.business_topology.flow_extraction import (
    parse_flow_extraction,
)
from mcp_server.services.business_topology.pruning import prune_to_valid
from mcp_server.services.business_topology.validation import (
    validate_business_topology,
)


def _write_scan(root: Path) -> None:
    """A two-system Talend scan with one directional edge and four designs."""

    (root / "scan").mkdir(parents=True)
    jobs = [
        {
            "name": name,
            "job_id": name,
            "source_root": "IMPORT/PROJECT",
            "path": f"Orders/{name}.item",
            "artifact_kind": "job",
            "artifact_role": "execution",
            "components": [],
            "connections": [],
        }
        # GetLocation / GetLocations are deliberately both present: the resolver
        # must never fold one onto the other.
        for name in (
            "Maximo2AssetCheck_SendWorkOrder",
            "AssetCheck_GetWorkOrder",
            "AssetCheck_GetLocation",
            "AssetCheck_GetLocations",
        )
    ]
    (root / "scan_result.json").write_text(
        json.dumps({"platform": "talend", "jobs": jobs, "metadata_connections": []}),
        encoding="utf-8",
    )
    (root / "scan" / "manifest.json").write_text(
        json.dumps(
            {
                "source_roots": [{"id": "IMPORT/PROJECT", "project_id": "project-id"}],
                "artifact_relations": [],
                "artifact_resources": [],
                "artifact_endpoints": [],
                "artifact_channels": [],
            }
        ),
        encoding="utf-8",
    )
    (root / "scan" / "lineage.json").write_text(json.dumps({"entries": []}), encoding="utf-8")
    (root / "scan" / "landscape.json").write_text(
        json.dumps(
            {
                "systems": [
                    {
                        "id": "system:maximo",
                        "name": "Maximo",
                        "kind": "software_system",
                        "source_roots": ["IMPORT/PROJECT"],
                    },
                    {
                        "id": "system:assetcheck",
                        "name": "AssetCheck",
                        "kind": "software_system",
                        "source_roots": ["IMPORT/PROJECT"],
                    },
                ],
                "applications": [],
                "edges": [
                    {
                        "source": "system:maximo",
                        "target": "system:assetcheck",
                        "dependency_type": "data",
                        "mechanism": "rest",
                        "direction": "out",
                        "provenance": [
                            {"artifact_id": "Maximo2AssetCheck_SendWorkOrder"}
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


def _write_docs(staging: Path) -> None:
    (staging / "03_DATA_FLOWS_AND_LINEAGE.md").write_text(
        "# Data Flows\n\n"
        "## Maximo-to-AssetCheck Flows (REST/File Ingestion)\n\n"
        "Work order XML is received and served to AssetCheck.\n\n"
        "## AssetCheck Location Flows\n\n"
        "Locations are pulled on demand.\n",
        encoding="utf-8",
    )
    (staging / "01_ARCHITECTURE_MAP.md").write_text(
        "# Architecture\n\n## Systems\n\nMaximo and AssetCheck.\n", encoding="utf-8"
    )


def _bundle(tmp_path: Path):
    scan_root = tmp_path / "scan-output"
    staging = tmp_path / "staging"
    staging.mkdir()
    _write_scan(scan_root)
    _write_docs(staging)
    bundle = build_business_topology_evidence(
        project_id="demo", scan_output_dir=scan_root, staging_dir=staging
    )
    doc_bundle, doc_records = register_doc_evidence(bundle, staging)
    return bundle, doc_bundle, doc_records


_EXTRACTION = {
    "subproject": "Assetcheck",
    "systems": [
        {
            "name": "Maximo",
            "role": "Enterprise Asset Management system",
            "kind": "application",
            "aliases": ["MX"],
        },
        {"name": "AssetCheck", "role": "Field inspection app", "kind": "application"},
        {"name": "SharePoint", "role": "Document store", "kind": "external_service"},
    ],
    "flows": [
        {
            "name": "Maximo -> AssetCheck: Work Order ingestion",
            "source_system": "Maximo",
            "target_system": "AssetCheck",
            "business_object": "WorkOrder",
            "carries": "Work order XML received via REST and served to the app",
            "mechanism": "REST (ingest) + File staging + REST (serve)",
            "implementing_designs": [
                "Maximo2AssetCheck_SendWorkOrder",
                "AssetCheck_GetWorkOrder",
            ],
            "technical_path": [
                {
                    "name": "Receive work order",
                    "mechanism": "REST webhook",
                    "protocol": "REST",
                    "mode": "event_driven",
                    "component": "maximoRestService",
                    "resource": "WorkOrder XML",
                    "implementing_designs": ["Maximo2AssetCheck_SendWorkOrder"],
                },
                {
                    "name": "Serve to AssetCheck",
                    "mechanism": "REST API",
                    "protocol": "REST",
                    "component": "AssetCheckInterface",
                    "implementing_designs": ["AssetCheck_GetWorkOrder"],
                },
            ],
            "transformations": [
                {
                    "name": "WorkOrder status mapping",
                    "description": "Status translation",
                    "implementing_designs": ["AssetCheck_GetWorkOrder"],
                    "mappings": [
                        {
                            "source": "STATUS=VOORBEREID",
                            "target": "In Process",
                            "mapping_type": "value",
                        }
                    ],
                }
            ],
            "confidence": "high",
            "evidence": (
                "03_DATA_FLOWS_AND_LINEAGE.md - "
                "'Maximo-to-AssetCheck Flows (REST/File Ingestion)'"
            ),
        },
        {
            "name": "AssetCheck -> SharePoint: document sync",
            "source_system": "AssetCheck",
            "target_system": "SharePoint",
            "business_object": "Document",
            "carries": "Inspection documents pushed to SharePoint",
            "mechanism": "SOAP",
            "implementing_designs": ["AssetCheck_GetLocation"],
            "confidence": "medium",
            "evidence": "03_DATA_FLOWS_AND_LINEAGE.md - 'AssetCheck Location Flows'",
        },
    ],
    "excluded": [{"name": "MockApi", "reason": "test scaffolding"}],
}


def test_extraction_parses_and_drops_only_unusable_items() -> None:
    payload = json.loads(json.dumps(_EXTRACTION))
    payload["flows"].append(
        {
            "name": "unusable",
            "source_system": "unclear",
            "target_system": "AssetCheck",
            "confidence": "high",
        }
    )
    payload["flows"].append(
        {
            "name": "self loop",
            "source_system": "Maximo",
            "target_system": "maximo",
            "confidence": "high",
        }
    )
    extraction, dropped = parse_flow_extraction(
        json.dumps(payload), subproject="Assetcheck"
    )
    assert extraction is not None
    # The two real flows survive; only the unusable ones are reported.
    assert len(extraction.flows) == 2
    assert len(dropped) == 2
    assert any("unclear" in note for note in dropped)


def test_extraction_tolerates_prose_preamble_and_string_design_lists() -> None:
    """The verified Assetcheck/ETTM_part1 failure shape.

    Those runs emitted a sentence before the JSON and died with
    "Expecting value: line 1 column 1". A formatting choice must not cost a
    topology.
    """

    payload = json.loads(json.dumps(_EXTRACTION))
    payload["flows"][0]["implementing_designs"] = (
        "Maximo2AssetCheck_SendWorkOrder, AssetCheck_GetWorkOrder"
    )
    text = "Here is the extraction:\n\n```json\n" + json.dumps(payload) + "\n```"
    extraction, _ = parse_flow_extraction(text, subproject="Assetcheck")
    assert extraction is not None
    assert extraction.flows[0].implementing_designs == [
        "Maximo2AssetCheck_SendWorkOrder",
        "AssetCheck_GetWorkOrder",
    ]


def test_assembled_topology_validates_and_carries_the_business_layer(
    tmp_path: Path,
) -> None:
    _, doc_bundle, doc_records = _bundle(tmp_path)
    extraction, _ = parse_flow_extraction(
        json.dumps(_EXTRACTION), subproject="Assetcheck"
    )
    assert extraction is not None

    topology, notes = assemble_topology(
        extraction,
        bundle=doc_bundle,
        project_id="demo",
        doc_records=doc_records,
        subproject="Assetcheck",
        model_name="test-model",
    )
    report = validate_business_topology(
        topology, bundle=doc_bundle, project_id="demo"
    )
    assert report.validated, report.errors

    # The layer the deterministic overlay structurally cannot produce.
    assert topology.business_objects, "no business objects assembled"
    assert topology.delivery_paths, "no delivery paths assembled"
    assert topology.transformations, "no transformations assembled"
    assert topology.integration_solutions, "no integration solution assembled"

    scanner_stream = next(
        stream for stream in topology.data_streams if "workorder" in stream.id
    )
    # Scanner-edge backed: may reach `observed`, wired to real artifacts, and its
    # delivery path is anchored to the stream endpoints.
    assert scanner_stream.status == "observed"
    assert scanner_stream.artifact_ids
    assert scanner_stream.protocols == ["REST", "File"]
    path = next(
        item for item in topology.delivery_paths if item.id == scanner_stream.delivery_path_id
    )
    assert [segment.sequence for segment in path.segments] == [1, 2]
    assert path.segments[0].source_ref == scanner_stream.source_system_id
    assert path.segments[-1].destination_ref == scanner_stream.destination_system_id
    assert path.segments[0].mode == "event_driven"
    assert notes is not None


def test_documentation_only_flow_is_published_as_a_capped_tier(tmp_path: Path) -> None:
    """A flow the scanner never inferred as an edge is the interesting case.

    It is why the curated topology is richer than any scanner-only merge. It
    must publish, must stay below `observed`, and must still resolve real
    scanner artifacts so the drill-down lands on real jobs.
    """

    _, doc_bundle, doc_records = _bundle(tmp_path)
    extraction, _ = parse_flow_extraction(
        json.dumps(_EXTRACTION), subproject="Assetcheck"
    )
    assert extraction is not None
    topology, _ = assemble_topology(
        extraction,
        bundle=doc_bundle,
        project_id="demo",
        doc_records=doc_records,
        subproject="Assetcheck",
    )
    doc_stream = next(
        stream for stream in topology.data_streams if "sharepoint" in stream.id
    )
    assert doc_stream.status == "inferred"
    assert doc_stream.confidence <= 0.6
    assert doc_stream.artifact_ids, "documentation tier must still cite real artifacts"
    assert all(
        artifact_id in doc_bundle.artifact_ids for artifact_id in doc_stream.artifact_ids
    )
    # SharePoint has no scanner landscape identity, so it is honest about that.
    sharepoint = next(item for item in topology.systems if item.name == "SharePoint")
    assert sharepoint.kind == "unresolved"
    assert sharepoint.status == "proposed"

    report = validate_business_topology(topology, bundle=doc_bundle, project_id="demo")
    assert report.validated, report.errors
    assert any("asserted by generated documentation" in w for w in report.warnings)


def test_documentation_tier_cannot_claim_observed(tmp_path: Path) -> None:
    _, doc_bundle, doc_records = _bundle(tmp_path)
    extraction, _ = parse_flow_extraction(
        json.dumps(_EXTRACTION), subproject="Assetcheck"
    )
    assert extraction is not None
    topology, _ = assemble_topology(
        extraction,
        bundle=doc_bundle,
        project_id="demo",
        doc_records=doc_records,
        subproject="Assetcheck",
    )
    doc_stream = next(
        stream for stream in topology.data_streams if "sharepoint" in stream.id
    )
    doc_stream.status = "observed"
    report = validate_business_topology(topology, bundle=doc_bundle, project_id="demo")
    assert not report.validated
    assert any("documentation-derived" in error for error in report.errors)


def test_design_resolution_is_exact_never_fuzzy(tmp_path: Path) -> None:
    """`AssetCheck_GetLocation` and `AssetCheck_GetLocations` are two jobs."""

    _, doc_bundle, _ = _bundle(tmp_path)
    index = build_design_artifact_index(doc_bundle)
    singular, _ = resolve_designs(["AssetCheck_GetLocation"], index)
    plural, _ = resolve_designs(["AssetCheck_GetLocations"], index)
    assert singular and plural
    assert singular != plural

    resolved, unresolved = resolve_designs(["AssetCheck_GetLoc"], index)
    assert not resolved
    assert unresolved == ["AssetCheck_GetLoc"]


def test_doc_citation_resolves_to_the_named_section(tmp_path: Path) -> None:
    _, _, doc_records = _bundle(tmp_path)
    resolved = resolve_doc_citation(
        "03_DATA_FLOWS_AND_LINEAGE.md - 'Maximo-to-AssetCheck Flows (REST/File Ingestion)'",
        doc_records,
    )
    assert resolved == [
        doc_evidence_id(
            "03_DATA_FLOWS_AND_LINEAGE.md",
            "Maximo-to-AssetCheck Flows (REST/File Ingestion)",
        )
    ]
    # A paraphrased heading still resolves to the document rather than nothing.
    assert resolve_doc_citation("03_DATA_FLOWS_AND_LINEAGE.md", doc_records) == [
        doc_evidence_id("03_DATA_FLOWS_AND_LINEAGE.md", "")
    ]


def test_doc_tier_cannot_promote_a_system_or_create_a_scanner_edge(
    tmp_path: Path,
) -> None:
    """The tier makes citations resolvable, not authoritative."""

    from mcp_server.services.business_topology.validation import (
        _DIRECTIONAL_EVIDENCE_TYPES,
        _SCANNER_EVIDENCE_TYPES,
        _SYSTEM_IDENTITY_EVIDENCE_TYPES,
    )

    assert "documentation_derived" not in _SCANNER_EVIDENCE_TYPES
    assert "documentation_derived" not in _DIRECTIONAL_EVIDENCE_TYPES
    assert "documentation_derived" not in _SYSTEM_IDENTITY_EVIDENCE_TYPES


def test_pruning_keeps_the_architecture_when_one_claim_is_invalid(
    tmp_path: Path,
) -> None:
    """The exact ETTM_part2 loss, inverted into a regression test."""

    _, doc_bundle, doc_records = _bundle(tmp_path)
    extraction, _ = parse_flow_extraction(
        json.dumps(_EXTRACTION), subproject="Assetcheck"
    )
    assert extraction is not None
    topology, _ = assemble_topology(
        extraction,
        bundle=doc_bundle,
        project_id="demo",
        doc_records=doc_records,
        subproject="Assetcheck",
    )
    before = len(topology.data_streams)
    assert before >= 2

    # Break exactly one stream the way the model broke all of them.
    doc_stream = next(
        stream for stream in topology.data_streams if "sharepoint" in stream.id
    )
    doc_stream.artifact_ids = ["artifact:deadbeefdeadbeefdead"]

    assert not validate_business_topology(
        topology, bundle=doc_bundle, project_id="demo"
    ).validated

    topology, report, dropped = prune_to_valid(
        topology, bundle=doc_bundle, project_id="demo"
    )
    assert report.validated, report.errors
    assert doc_stream.id in dropped
    assert len(topology.data_streams) == before - 1
    # The surviving architecture is intact, not emptied.
    assert topology.delivery_paths
    assert topology.business_objects
    assert any("dropped" in note for note in topology.coverage.notes)


def test_pruning_refuses_to_hide_a_global_defect(tmp_path: Path) -> None:
    _, doc_bundle, doc_records = _bundle(tmp_path)
    extraction, _ = parse_flow_extraction(
        json.dumps(_EXTRACTION), subproject="Assetcheck"
    )
    assert extraction is not None
    topology, _ = assemble_topology(
        extraction,
        bundle=doc_bundle,
        project_id="demo",
        doc_records=doc_records,
        subproject="Assetcheck",
    )
    topology.scan_fingerprint = "stale-fingerprint"
    _, report, dropped = prune_to_valid(
        topology, bundle=doc_bundle, project_id="demo"
    )
    assert not report.validated
    assert not dropped


def test_scanner_floor_merges_without_duplicating_systems(tmp_path: Path) -> None:
    """Extraction recall must not cost scanner edge coverage.

    The two builders mint different system ids (`sys-maximo` vs
    `sys-maximo-a1b2c3`), so the merge has to fold on scanner identity evidence
    or it publishes the same system twice.
    """

    from mcp_server.services.business_topology.assembly import merge_scanner_floor
    from mcp_server.services.business_topology.deterministic import (
        build_deterministic_topology,
    )

    scanner_bundle, doc_bundle, doc_records = _bundle(tmp_path)
    deterministic = build_deterministic_topology(scanner_bundle, project_id="demo")
    assert deterministic.data_streams, "fixture must give the floor something to add"

    # An extraction that describes ONLY the SharePoint flow, missing the
    # scanner-proven Maximo -> AssetCheck edge entirely.
    partial = json.loads(json.dumps(_EXTRACTION))
    partial["flows"] = [partial["flows"][1]]
    extraction, _ = parse_flow_extraction(json.dumps(partial), subproject="Assetcheck")
    assert extraction is not None
    topology, _ = assemble_topology(
        extraction,
        bundle=doc_bundle,
        project_id="demo",
        doc_records=doc_records,
        subproject="Assetcheck",
    )
    before = len(topology.data_streams)

    notes = merge_scanner_floor(topology, deterministic)
    assert notes, "the missing scanner edge should have been added"
    assert len(topology.data_streams) > before

    # No duplicate systems: Maximo and AssetCheck appear once each.
    names = [system.name.casefold() for system in topology.systems]
    assert len(names) == len(set(names)), names
    ids = [system.id for system in topology.systems]
    assert len(ids) == len(set(ids))

    report = validate_business_topology(topology, bundle=doc_bundle, project_id="demo")
    assert report.validated, report.errors


def test_scanner_floor_does_not_overwrite_a_richer_extracted_flow(
    tmp_path: Path,
) -> None:
    from mcp_server.services.business_topology.assembly import merge_scanner_floor
    from mcp_server.services.business_topology.deterministic import (
        build_deterministic_topology,
    )

    scanner_bundle, doc_bundle, doc_records = _bundle(tmp_path)
    deterministic = build_deterministic_topology(scanner_bundle, project_id="demo")
    extraction, _ = parse_flow_extraction(
        json.dumps(_EXTRACTION), subproject="Assetcheck"
    )
    assert extraction is not None
    topology, _ = assemble_topology(
        extraction,
        bundle=doc_bundle,
        project_id="demo",
        doc_records=doc_records,
        subproject="Assetcheck",
    )
    merge_scanner_floor(topology, deterministic)

    # The extracted Maximo -> AssetCheck flow keeps its business object and
    # delivery path; the bare scanner edge for the same pair is not added again.
    maximo_streams = [
        stream
        for stream in topology.data_streams
        if stream.source_system_id == "sys-maximo"
        and stream.destination_system_id == "sys-assetcheck"
    ]
    assert len(maximo_streams) == 1
    assert maximo_streams[0].business_object_ids
    assert maximo_streams[0].delivery_path_id


def test_doc_citation_does_not_borrow_sections_from_other_documents(
    tmp_path: Path,
) -> None:
    """A short heading in another document must not attach its evidence.

    `01_ARCHITECTURE_MAP.md` has a `## Systems` heading. A citation naming only
    the data-flows document must never resolve to it.
    """

    _, _, doc_records = _bundle(tmp_path)
    resolved = resolve_doc_citation(
        "03_DATA_FLOWS_AND_LINEAGE.md - 'Systems that exchange work orders'",
        doc_records,
    )
    architecture_sections = {
        evidence_id
        for evidence_id, record in doc_records.items()
        if record.get("document") == "01_ARCHITECTURE_MAP.md" and record.get("section")
    }
    assert not (set(resolved) & architecture_sections)
    assert resolved == [doc_evidence_id("03_DATA_FLOWS_AND_LINEAGE.md", "")]


def test_secretlike_prose_is_redacted_instead_of_failing_the_overlay(
    tmp_path: Path,
) -> None:
    """A leaked connection string must cost that phrase, not the topology.

    Secret findings are correctly non-prunable, so an unredacted string would
    fail the whole overlay and silently downgrade the run to the deterministic
    skeleton.
    """

    _, doc_bundle, doc_records = _bundle(tmp_path)
    payload = json.loads(json.dumps(_EXTRACTION))
    payload["flows"][0]["carries"] = (
        "Pulled from mssql://svc_user:Sup3rSecret@db.internal/maximo then served"
    )
    extraction, _ = parse_flow_extraction(json.dumps(payload), subproject="Assetcheck")
    assert extraction is not None
    topology, _ = assemble_topology(
        extraction,
        bundle=doc_bundle,
        project_id="demo",
        doc_records=doc_records,
        subproject="Assetcheck",
    )
    stream = next(s for s in topology.data_streams if "workorder" in s.id)
    assert "Sup3rSecret" not in stream.description
    assert "Sup3rSecret" not in stream.business_summary
    assert "[REDACTED]" in stream.description

    report = validate_business_topology(topology, bundle=doc_bundle, project_id="demo")
    assert report.validated, report.errors


def test_endpoint_corroborated_flow_is_not_capped_like_a_bare_assertion(
    tmp_path: Path,
) -> None:
    """The real werkorder shape: no system-to-system edge, but real endpoints.

    The scanner landscape models `App -> System` edges while documentation
    describes `System -> System` business flows, so an exact direction lookup
    finds nothing. When the flow's OWN implementing designs are scanner-proven
    at both endpoints, that is corroboration, and flattening it to the
    documentation cap renders an established flow as guesswork.
    """

    _, doc_bundle, doc_records = _bundle(tmp_path)
    payload = json.loads(json.dumps(_EXTRACTION))
    # Reverse direction: no scanner edge exists for AssetCheck -> Maximo, but
    # the named design sits on an edge touching both systems.
    payload["flows"] = [
        {
            "name": "AssetCheck -> Maximo: status callback",
            "source_system": "AssetCheck",
            "target_system": "Maximo",
            "business_object": "WorkOrderStatus",
            "carries": "Completion status returned to Maximo",
            "mechanism": "REST",
            "implementing_designs": ["Maximo2AssetCheck_SendWorkOrder"],
            "confidence": "high",
            "evidence": (
                "03_DATA_FLOWS_AND_LINEAGE.md - "
                "'Maximo-to-AssetCheck Flows (REST/File Ingestion)'"
            ),
        }
    ]
    extraction, _ = parse_flow_extraction(json.dumps(payload), subproject="Assetcheck")
    assert extraction is not None
    topology, notes = assemble_topology(
        extraction,
        bundle=doc_bundle,
        project_id="demo",
        doc_records=doc_records,
        subproject="Assetcheck",
    )
    stream = topology.data_streams[0]
    assert stream.status == "inferred", "direction stays documentation-asserted"
    assert stream.confidence > 0.6, "corroborated flow must not be capped"
    assert any("scanner-proven at both endpoints" in note for note in notes)
    assert topology.coverage.grounded_technical_relationships == 1

    report = validate_business_topology(topology, bundle=doc_bundle, project_id="demo")
    assert report.validated, report.errors


def test_documents_are_discovered_not_assumed(tmp_path: Path) -> None:
    """A pack renames the whole document set; the lane must still work.

    A fixed catalog filename list would silently disable doc-grounded topology
    for every pack that renames its documents.
    """

    from mcp_server.services.business_topology.doc_evidence import (
        discover_doc_filenames,
        primary_document_id,
    )

    docs = tmp_path / "packdocs"
    docs.mkdir()
    (docs / "SECURITY_POSTURE.md").write_text("# S\n\n## Controls\n\nx\n", encoding="utf-8")
    (docs / "PII_DATA_FLOW_REPORT.md").write_text(
        "# P\n\n## Vendor exchanges\n\nx\n", encoding="utf-8"
    )
    (docs / "_internal_notes.md").write_text("# skip me\n", encoding="utf-8")

    names = discover_doc_filenames(docs)
    # Ranked: the flow-bearing document leads; internal artifacts excluded.
    assert names[0] == "PII_DATA_FLOW_REPORT.md"
    assert "SECURITY_POSTURE.md" in names
    assert not any(name.startswith("_") for name in names)

    records = collect_doc_evidence(docs)
    assert records
    assert primary_document_id(records) == doc_evidence_id(
        "PII_DATA_FLOW_REPORT.md", ""
    )
    # A citation into the renamed document resolves to its own section.
    assert resolve_doc_citation(
        "PII_DATA_FLOW_REPORT.md - 'Vendor exchanges'", records
    ) == [doc_evidence_id("PII_DATA_FLOW_REPORT.md", "Vendor exchanges")]


def test_prompt_lists_the_documents_that_actually_exist() -> None:
    from mcp_server.services.business_topology.prompt import flow_extraction_prompt

    prompt = flow_extraction_prompt(
        subproject="PackRun", documents=["PII_DATA_FLOW_REPORT.md", "SECURITY_POSTURE.md"]
    )
    assert "PII_DATA_FLOW_REPORT.md" in prompt
    assert "${" not in prompt
    # No standard-catalog filename is asserted anywhere in the rendered prompt.
    assert "03_DATA_FLOWS_AND_LINEAGE.md" not in prompt

    empty = flow_extraction_prompt(subproject="PackRun", documents=[])
    assert "none were published" in empty
