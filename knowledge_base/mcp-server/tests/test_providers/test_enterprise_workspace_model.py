"""Sanitized workspace staging, exact tables, knowledge-model validation, question coverage."""
from __future__ import annotations

import json

import pytest

from mcp_server.providers.custom.enterprise import knowledge_model as km
from mcp_server.providers.custom.enterprise.knowledge_questions import question_coverage
from mcp_server.providers.custom.enterprise.layout import EXACT_MAPPING_TABLES, EXACT_UNITS, WORKSPACE_MANIFEST
from mcp_server.providers.custom.enterprise.workspace import (
    EnterpriseWorkspaceError,
    build_exact_tables,
    stage_sanitized_workspace,
)
from tests.test_providers import enterprise_fixture as fx


def _staged(tmp_path):
    scan = fx.write_scan_output(tmp_path / "scan-output")
    staging = tmp_path / "staging"
    staging.mkdir()
    receipt = stage_sanitized_workspace(scan, staging)
    exact = build_exact_tables(scan, staging)
    return scan, staging, receipt, exact


def test_workspace_is_complete_digest_verified_and_manifested(tmp_path):
    _, staging, receipt, _ = _staged(tmp_path)
    assert receipt.file_count == len(fx.SANITIZED_FILES)
    for relative, text in fx.SANITIZED_FILES.items():
        assert (staging / "_workspace" / relative).read_text(encoding="utf-8") == text
    manifest = json.loads((staging / WORKSPACE_MANIFEST).read_text())
    assert manifest["file_count"] == len(fx.SANITIZED_FILES)
    assert "[PASSWORD_REDACTED]" in manifest["note"]


@pytest.mark.parametrize("problem", ["missing", "tampered", "residual"])
def test_workspace_staging_fails_closed(tmp_path, problem):
    scan_dir = tmp_path / "scan-output"
    if problem == "missing":
        fx.write_scan_output(scan_dir)
        (scan_dir / "scan" / "sanitized" / "manifest.json").unlink()
        match = "no sanitized workspace package"
    elif problem == "tampered":
        fx.write_scan_output(scan_dir, tamper=True)
        match = "not covered by the manifest"
    else:
        fx.write_scan_output(scan_dir, residual_free=False)
        match = "residual redaction findings"
    staging = tmp_path / "staging"
    staging.mkdir()
    with pytest.raises(EnterpriseWorkspaceError, match=match):
        stage_sanitized_workspace(scan_dir, staging)
    assert not (staging / "_workspace" / "talend.project").exists()


def test_exact_tables_carry_dependencies_entry_points_errors_and_full_mapping_rows(tmp_path):
    _, staging, _, exact = _staged(tmp_path)
    units = json.loads((staging / EXACT_UNITS).read_text())["units"]
    assert units[fx.LOADER]["workspace_path"] == fx.ws(fx.LOADER)
    assert units[fx.LOADER]["disabled_component_count"] == 1
    assert set(exact.entry_point_units) == {fx.ROUTE}
    assert set(exact.error_handling_units) == {fx.ROUTE, fx.LOADER}
    assert exact.context_group_keys == ("Orders",)
    assert exact.inline_context_units == (fx.LOADER,)
    edges = {(e["source"], e["target"], e["relation_type"]) for e in exact.dependencies["edges"]}
    assert (fx.ROUTE, fx.LOADER, "route_invokes_job") in edges
    assert (fx.LOADER, fx.WRITER, "job_calls_job") in edges
    pinned = next(e for e in exact.dependencies["edges"] if e["relation_type"] == "job_calls_job")
    assert pinned["version"] == "0.3" and pinned["context_params"] == [{"name": "batch", "value": "context.batch_id"}]
    table = (staging / EXACT_MAPPING_TABLES / f"{fx.TABLE_ID}.md").read_text()
    for row in fx.mapping_rows():
        assert row["expression"].replace("|", "\\|") in table
    assert "row1.ignored_flag" in table
    assert "Offered input fields | 3" in table


def _valid_model(staging, exact) -> km.KnowledgeModel:
    def ev(unit: str, quote: str) -> km.EvidenceRef:
        return km.EvidenceRef(path=fx.ws(unit), quote=quote)

    views = km.AudienceViews(developer="d", operator="o", architect="a", business_owner="unknown: not stated")
    return km.assign_claim_ids(km.KnowledgeModel(
        snapshot_id="snap", project_id="orders",
        designs=[km.DesignEntry(unit_id=u, kind="job", summary="s", views=views, evidence=[ev(u, "componentName")]) for u in fx.UNITS],
        journeys=[km.Journey(journey_id="orders-intake", name="Order intake", trigger=km.JourneyTrigger(kind="rest", unit_id=fx.ROUTE),
                             steps=[km.JourneyStep(order=1, unit_id=fx.ROUTE, description="receive"),
                                    km.JourneyStep(order=2, unit_id=fx.LOADER, description="load"),
                                    km.JourneyStep(order=3, unit_id=fx.WRITER, description="write")],
                             destinations=[km.JourneyDestination(system="ORDERS database", mechanism="db")],
                             implementing_units=list(fx.UNITS), mapping_table_ids=[fx.TABLE_ID], error_path_ids=["p1", "p2"],
                             evidence=[ev(fx.ROUTE, "cTalendJob")])],
        mappings=[km.MappingInterpretation(table_id=fx.TABLE_ID, unit_id=fx.LOADER, summary="maps orders", evidence=[ev(fx.LOADER, "row1.order_id")])],
        configuration=[km.ConfigurationEntry(scope="context_group", key="Orders", summary="hosts", evidence=[km.EvidenceRef(path="_workspace/context/Orders_0.1.item", quote="/prod/out")]),
                       km.ConfigurationEntry(scope="unit", key=fx.LOADER, summary="inline", evidence=[ev(fx.LOADER, "repositoryContextId")])],
        error_paths=[km.ErrorPath(error_path_id="p1", unit_id=fx.LOADER, trigger="t", handling="h", evidence=[ev(fx.LOADER, "tDie")]),
                     km.ErrorPath(error_path_id="p2", unit_id=fx.ROUTE, trigger="t", handling="h", evidence=[ev(fx.ROUTE, "cOnException")])],
        runtime=[km.RuntimeObservation(unit_id=u, status="unavailable", summary="no runtime source") for u in fx.UNITS],
        business=[km.BusinessMeaning(journey_id="orders-intake", statement="orders flow", status="inferred", source_authority="implemented_design")],
        unknowns=[km.Unknown(unknown_id="owner", subject=fx.LOADER, question="Who owns it?", reason="not in export", audience=["business_owner"])],
    ))


def _validate(staging, exact, model):
    return km.validate_knowledge_model(model, staging_dir=staging, unit_ids=set(fx.UNITS), mapping_table_ids=set(exact.mapping_table_ids),
                                       entry_point_units=set(exact.entry_point_units), error_handling_units=set(exact.error_handling_units),
                                       context_group_keys=set(exact.context_group_keys), inline_context_units=set(exact.inline_context_units))


def test_valid_model_passes_and_coverage_counts_are_complete(tmp_path):
    _, staging, _, exact = _staged(tmp_path)
    report = _validate(staging, exact, _valid_model(staging, exact))
    assert report.ok, report.errors
    assert report.coverage["designs"] == 3 and report.coverage["mapping_tables_interpreted"] == 1


def test_validation_fails_closed_on_gaps_and_ungrounded_claims(tmp_path):
    _, staging, _, exact = _staged(tmp_path)
    model = _valid_model(staging, exact)
    model.designs = model.designs[1:]  # drop the route analysis
    model.mappings[0].evidence = [km.EvidenceRef(path=fx.ws(fx.LOADER), quote="text that is not in the file")]
    model.business[0].evidence = [km.EvidenceRef(path="_method/talend/profile.md")]
    model.error_paths = model.error_paths[:1]
    report = _validate(staging, exact, model)
    assert not report.ok
    assert any("unit not analysed" in e and fx.ROUTE in e for e in report.errors)
    assert any("quoted evidence not found" in e for e in report.errors)
    assert any("outside the evidence roots" in e for e in report.errors)
    assert any("has no error path" in e and fx.ROUTE in e for e in report.errors)
    assert fx.ROUTE in report.failed_entries["designs"]
    assert fx.TABLE_ID in report.failed_entries["mappings"]


def test_retyped_mapping_table_and_orphan_entry_point_are_rejected(tmp_path):
    _, staging, _, exact = _staged(tmp_path)
    model = _valid_model(staging, exact)
    model.mappings[0].summary = "| a | b |\n|---|---|\n| 1 | 2 |\n| 3 | 4 |"
    model.journeys[0].trigger = km.JourneyTrigger(kind="rest", unit_id=fx.LOADER)
    model.journeys[0].steps = model.journeys[0].steps[1:]
    model.journeys[0].implementing_units = [fx.LOADER, fx.WRITER]
    report = _validate(staging, exact, model)
    assert any("reproduces a field table" in e for e in report.errors)
    assert any("entry point not placed" in e and fx.ROUTE in e for e in report.errors)


def test_questions_answer_from_model_only_and_stay_unknown_when_unsupported(tmp_path):
    _, staging, _, exact = _staged(tmp_path)
    model = _valid_model(staging, exact)
    report = question_coverage(model, exact.units)
    answers = {a["question_id"]: a for a in report["answers"]}
    assert answers[f"dev.depends.{fx.LOADER}"]["status"] == "answered"
    assert fx.WRITER in answers[f"dev.depends.{fx.LOADER}"]["answer"]
    assert answers[f"ops.runtime.{fx.LOADER}"]["status"] == "unknown"
    assert "unavailable" in answers[f"ops.runtime.{fx.LOADER}"]["reason"]
    assert answers[f"biz.owner.{fx.LOADER}"]["status"] == "unknown"
    assert answers[f"biz.owner.{fx.LOADER}"]["reason"] == "not in export"
    assert answers["biz.purpose.orders-intake"]["status"] == "answered"
    assert answers["biz.purpose.orders-intake"]["answer"].startswith("Inferred from the implemented design")
    assert answers[f"ops.trigger_failure.{fx.WRITER}"]["status"] == "answered"
    assert "No error-handling construct" in answers[f"ops.trigger_failure.{fx.WRITER}"]["answer"]
    assert set(report["by_audience"]) == {"developer", "operator", "architect", "business_owner"}


def test_manifest_direction_vocabulary_is_normalized_and_endpoint_triggers_count(tmp_path):
    """The scanner says input/output; a called route with a REST front door is still an entry point."""
    import json as _json

    scan = fx.write_scan_output(tmp_path / "scan-output")
    result = _json.loads((scan / "scan_result.json").read_text())
    # Make the route a callee too (another design calls it) so root-ness cannot rescue it.
    result["jobs"][2]["artifact_refs"] = [{"target_name": fx.ROUTE, "relation_type": "route_invokes_job",
                                           "component_name": "cTalendJob_9", "resolved_artifact_id": fx.ROUTE,
                                           "resolution": "same_root", "reference_source": "identity"}]
    result["manifest"]["artifact_endpoints"][0]["direction"] = "input"
    (scan / "scan_result.json").write_text(_json.dumps(result), encoding="utf-8")
    (scan / "scan" / "manifest.json").write_text(_json.dumps(result["manifest"]), encoding="utf-8")
    staging = tmp_path / "staging"
    staging.mkdir()
    stage_sanitized_workspace(scan, staging)
    exact = build_exact_tables(scan, staging)
    route_rows = exact.interfaces["by_unit"][fx.ROUTE]
    assert {row["direction"] for row in route_rows if row["kind"] == "endpoint"} == {"inbound"}
    assert exact.units[fx.ROUTE]["callers"] == [fx.WRITER]
    assert fx.ROUTE in exact.entry_point_units
