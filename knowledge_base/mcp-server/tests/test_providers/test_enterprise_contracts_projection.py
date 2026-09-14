"""Typed submissions, assembly, repair targeting, projection, leakage and publication policy."""
from __future__ import annotations

import json

from mcp_server.providers.custom.enterprise import phase_contracts as pc
from mcp_server.providers.custom.enterprise.client_language import leakage_findings, validate_client_docs
from mcp_server.providers.custom.enterprise.integration_projection import (
    build_dossier_plan,
    render_catalogue,
    render_dossier,
    write_client_document,
    write_dossier_plan,
)
from mcp_server.providers.custom.enterprise.knowledge_model import persist_knowledge_model
from mcp_server.providers.custom.enterprise.knowledge_questions import write_question_coverage
from mcp_server.providers.custom.enterprise.layout import DOC_PROVENANCE_ROOT, RUN_RECEIPT
from mcp_server.providers.custom.enterprise.publication_policy import deterministic_review, evaluate_publication
from mcp_server.providers.custom.enterprise.workspace import build_exact_tables, stage_sanitized_workspace
from mcp_server.providers.custom.knowledge_contracts import write_json
from tests.test_providers import enterprise_fixture as fx


def _ectx(tmp_path) -> pc.EnterpriseContext:
    scan = fx.write_scan_output(tmp_path / "scan-output")
    staging = tmp_path / "staging"
    staging.mkdir()
    stage_sanitized_workspace(scan, staging)
    exact = build_exact_tables(scan, staging)
    return pc.EnterpriseContext(staging_dir=staging, project_id="orders", snapshot_id="snap", exact=exact, unit_ids=set(fx.UNITS))


def _submit(ectx, phase, payload):
    return pc.handle_submission(ectx, phase, json.dumps(payload))


def _complete_model_phases(ectx):
    for payload in fx.design_payloads():
        assert _submit(ectx, pc.PHASE_DEEP_ANALYZE, payload)["ok"]
    for payload in fx.journey_payloads():
        assert _submit(ectx, pc.PHASE_RECONSTRUCT, payload)["ok"]
    for payload in fx.model_payloads():
        assert _submit(ectx, pc.PHASE_MODEL, payload)["ok"]
    for payload in fx.reconcile_payloads():
        assert _submit(ectx, pc.PHASE_RECONCILE, payload)["ok"]
    model = pc.assemble_knowledge_model(ectx)
    report = pc.validate_assembled_model(ectx, model)
    assert report.ok, report.errors
    persist_knowledge_model(ectx.staging_dir, model, report)
    ectx.knowledge = model
    ectx.claim_ids = {e.claim_id for section in ("designs", "journeys", "mappings", "configuration", "error_paths", "business", "unknowns")
                      for e in getattr(model, section)}
    return model


def test_ledger_submissions_are_partial_durable_and_report_remaining(tmp_path):
    ectx = _ectx(tmp_path)
    payload = fx.design_payloads()[0]
    payload["designs"][1]["evidence"] = [{"path": fx.ws(fx.LOADER), "locator": "", "quote": "not present anywhere"}]
    payload["designs"].append({**payload["designs"][2], "unit_id": "Ghost_Job"})
    result = _submit(ectx, pc.PHASE_DEEP_ANALYZE, payload)
    assert result["ok"] is False
    assert result["accepted_count"] == 4  # route, writer, system, unknown
    assert {r.get("key") for r in result["rejected"]} == {fx.LOADER, "Ghost_Job"}
    assert result["remaining"] == [fx.LOADER]
    # A fresh session sees the same durable ledger.
    fresh = pc.EnterpriseContext(staging_dir=ectx.staging_dir, project_id="orders", snapshot_id="snap", exact=ectx.exact, unit_ids=ectx.unit_ids)
    assert pc.remaining_for(fresh, pc.PHASE_DEEP_ANALYZE) == [fx.LOADER]
    fixed = {"schema_version": "enterprise-estate-1", "designs": [fx.design_payloads()[0]["designs"][1]]}
    assert _submit(fresh, pc.PHASE_DEEP_ANALYZE, fixed)["remaining"] == []


def test_workspace_discovery_of_an_unlisted_artifact_is_accepted_evidence(tmp_path):
    ectx = _ectx(tmp_path)
    result = _submit(ectx, pc.PHASE_DEEP_ANALYZE, fx.design_payloads()[0])
    assert result["ok"], result["rejected"]
    stored = ectx.ledger(pc.PHASE_DEEP_ANALYZE).entries()[f"design:{fx.LOADER}"]
    assert any(ref["path"] == f"_workspace/{fx.STRUCTURE_PATH}" for ref in stored["evidence"])
    assert fx.STRUCTURE_PATH not in json.dumps(ectx.exact.units)  # the scanner never listed it


def test_reconstruct_requires_entry_point_placement_and_defers_error_links(tmp_path):
    ectx = _ectx(tmp_path)
    payload = fx.journey_payloads()[0]
    payload["journeys"][0]["error_path_ids"] = ["early"]
    result = _submit(ectx, pc.PHASE_RECONSTRUCT, payload)
    assert result["ok"] is False and "linked deterministically later" in result["rejected"][0]["errors"][0]
    assert result["remaining"] == sorted(fx.UNITS)
    assert "unplaced entry points" in result["note"] and fx.ROUTE in result["note"]
    payload["journeys"][0]["error_path_ids"] = []
    assert _submit(ectx, pc.PHASE_RECONSTRUCT, payload)["remaining"] == []


def test_model_phase_keys_cover_tables_configuration_errors_and_business(tmp_path):
    ectx = _ectx(tmp_path)
    _submit(ectx, pc.PHASE_RECONSTRUCT, fx.journey_payloads()[0])
    assert pc.model_remaining(ectx) == sorted([f"mapping:{fx.TABLE_ID}", "config:context_group:Orders", f"config:unit:{fx.LOADER}",
                                               f"error:{fx.ROUTE}", f"error:{fx.LOADER}", "business:orders-intake"])
    result = _submit(ectx, pc.PHASE_MODEL, fx.model_payloads(retype_table=True)[0])
    assert result["ok"] is False and any("retype" in e for r in result["rejected"] for e in r["errors"])
    assert f"mapping:{fx.TABLE_ID}" in result["remaining"]
    assert _submit(ectx, pc.PHASE_MODEL, fx.model_payloads()[0])["remaining"] == []


def test_assembly_links_error_paths_computes_runtime_and_targets_repairs(tmp_path):
    ectx = _ectx(tmp_path)
    model = _complete_model_phases(ectx)
    journey = model.journeys[0]
    assert journey.error_path_ids == ["orders-load-die", "route-exception"]
    assert {r.unit_id: r.status for r in model.runtime} == {fx.ROUTE: "not_applicable", fx.LOADER: "unavailable", fx.WRITER: "unavailable"}
    assert all(entry.claim_id.startswith("ek-") for entry in model.designs)
    # Break one accepted entry and check the repair map points at its phase and key.
    ledger = ectx.ledger(pc.PHASE_MODEL)
    broken = ledger.entries()[f"mapping:{fx.TABLE_ID}"]
    broken["evidence"] = [{"path": fx.ws(fx.LOADER), "locator": "", "quote": "vanished text"}]
    ledger.accept(f"mapping:{fx.TABLE_ID}", broken)
    report = pc.validate_assembled_model(ectx, pc.assemble_knowledge_model(ectx))
    assert not report.ok
    assert pc.repair_targets_from_validation(report) == {pc.PHASE_MODEL: [f"mappings:{fx.TABLE_ID}"]}


def test_projection_embeds_full_exact_tables_and_client_docs_stay_clean(tmp_path):
    ectx = _ectx(tmp_path)
    model = _complete_model_phases(ectx)
    plan = build_dossier_plan(model, ectx.exact.units, ectx.exact.interfaces, min_designs=2)
    write_dossier_plan(ectx.staging_dir, plan)
    ectx.dossier_plan = plan
    assert plan.required_docs == ["INTEGRATION_CATALOGUE", "INTEGRATION__rest-orders-orders-database-warehouse-drop-folder"]
    assert plan.dossiers[0].material and "1 exact mapping tables" in plan.dossiers[0].materiality_reasons
    assert len(plan.interfaces) >= 4 and len({r.interface_id for r in plan.interfaces}) == len(plan.interfaces)
    claim_ids = sorted(ectx.claim_ids)
    for doc in plan.required_docs:
        result = _submit(ectx, pc.PHASE_WRITE, fx.doc_payload(doc, claim_ids))
        assert result["ok"], result["rejected"]
    sections = ectx.ledger(pc.PHASE_WRITE).entries()
    dossier_doc = plan.required_docs[1]
    markdown, provenance = render_dossier(plan.dossiers[0], sections[f"doc:{dossier_doc}"]["sections"], model, ectx.exact.units,
                                          plan.interfaces, ectx.staging_dir)
    write_client_document(ectx.staging_dir, dossier_doc, markdown, provenance)
    catalogue, cat_prov = render_catalogue(plan, sections["doc:INTEGRATION_CATALOGUE"]["sections"], model, ectx.exact.units, ectx.exact.capability)
    write_client_document(ectx.staging_dir, "INTEGRATION_CATALOGUE", catalogue, cat_prov)
    for row in fx.mapping_rows():
        assert row["output_field"] in markdown and row["expression"].replace("|", "\\|") in markdown
    assert f"#### Exact mapping table {fx.TABLE_ID}" in markdown
    assert "Order intake to warehouse" in catalogue and f"{dossier_doc}.md" in catalogue
    assert "one file destination is a context expression" in catalogue
    assert leakage_findings(markdown) == [] and leakage_findings(catalogue) == []
    assert validate_client_docs(ectx.staging_dir, plan.required_docs).ok
    sidecar = json.loads((ectx.staging_dir / DOC_PROVENANCE_ROOT / f"{dossier_doc}.json").read_text())
    assert sidecar["mapping_table_ids"] == [fx.TABLE_ID] and sidecar["sections"]["overview"]["claim_ids"] == claim_ids[:1]
    assert deterministic_review(ectx.staging_dir, plan, model) == []


def test_write_contract_rejects_leakage_thin_sections_and_unknown_claims(tmp_path):
    ectx = _ectx(tmp_path)
    model = _complete_model_phases(ectx)
    plan = build_dossier_plan(model, ectx.exact.units, ectx.exact.interfaces, min_designs=2)
    ectx.dossier_plan = plan
    dossier_doc = plan.required_docs[1]
    payload = fx.doc_payload(dossier_doc, sorted(ectx.claim_ids))
    payload["sections"]["overview"]["text"] += " See _evidence_registry/enterprise for details ev-0123456789abcdef01234567."
    payload["sections"]["configuration"]["text"] = "short"
    payload["sections"]["operations"]["claim_ids"] = ["ek-doesnotexist"]
    result = _submit(ectx, pc.PHASE_WRITE, payload)
    errors = result["rejected"][0]["errors"]
    assert any("leaks internal provenance" in e for e in errors)
    assert any("too thin" in e for e in errors)
    assert any("unknown claim ids" in e for e in errors)
    assert pc.write_remaining(ectx) == plan.required_docs


def test_publication_policy_blocks_leakage_missing_tables_and_missing_docs(tmp_path):
    ectx = _ectx(tmp_path)
    model = _complete_model_phases(ectx)
    plan = build_dossier_plan(model, ectx.exact.units, ectx.exact.interfaces, min_designs=2)
    write_dossier_plan(ectx.staging_dir, plan)
    ectx.dossier_plan = plan
    write_question_coverage(ectx.staging_dir, model, ectx.exact.units)
    write_json(ectx.staging_dir / RUN_RECEIPT, {"status": "complete"})
    claim_ids = sorted(ectx.claim_ids)
    for doc in plan.required_docs:
        _submit(ectx, pc.PHASE_WRITE, fx.doc_payload(doc, claim_ids))
    sections = ectx.ledger(pc.PHASE_WRITE).entries()
    dossier_doc = plan.required_docs[1]
    markdown, prov = render_dossier(plan.dossiers[0], sections[f"doc:{dossier_doc}"]["sections"], model, ectx.exact.units, plan.interfaces, ectx.staging_dir)
    write_client_document(ectx.staging_dir, dossier_doc, markdown, prov)
    catalogue, cat_prov = render_catalogue(plan, sections["doc:INTEGRATION_CATALOGUE"]["sections"], model, ectx.exact.units, ectx.exact.capability)
    write_client_document(ectx.staging_dir, "INTEGRATION_CATALOGUE", catalogue, cat_prov)
    assert evaluate_publication(ectx.staging_dir).ok
    # Injected leakage
    path = ectx.staging_dir / f"{dossier_doc}.md"
    clean = path.read_text()
    path.write_text(clean + "\nSee _analysis/_exact/mapping_tables for the source.\n")
    verdict = evaluate_publication(ectx.staging_dir)
    assert not verdict.ok and verdict.checks["no_internal_leakage"] is False
    # Dropped mapping rows (shallow dossier)
    path.write_text(clean.replace("| out1 | status |", "| removed | removed |", 1).replace(f"#### Exact mapping table {fx.TABLE_ID}", "#### Exact mapping table deadbeef"))
    verdict = evaluate_publication(ectx.staging_dir)
    assert not verdict.ok and verdict.checks["mapping_depth"] is False
    # Missing document
    path.unlink()
    verdict = evaluate_publication(ectx.staging_dir)
    assert not verdict.ok and verdict.checks["required_documents"] is False
    assert verdict.draft_label.startswith("draft")
