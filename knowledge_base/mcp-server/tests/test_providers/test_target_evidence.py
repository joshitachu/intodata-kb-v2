"""Tests for the per-target deterministic evidence bundles (Phase 3 evidence lane).

All fixture values are synthetic. Shapes mirror the real Talend scan cache
(jobs/*.json + contexts.json) without any client data.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from mcp_server.providers.base import DocGenContext
from mcp_server.providers.custom.harness import CustomHarnessProvider
from mcp_server.providers.custom.target_evidence import (
    TargetEvidenceBuilder,
    extract_component_refs,
    filter_fact_table,
)

DOC_NAME = "01_TEMPLATE__GroupA__Job_GetThing"


def _job_getthing() -> dict:
    return {
        "name": "Job_GetThing",
        "path": "GroupA/Job_GetThing_1.0.item",
        "group": "GroupA",
        "version": "1.0",
        "components": [
            {
                "unique_name": "tJavaRow_1",
                "component_type": "tJavaRow",
                "params": [
                    {"name": "CODE", "value": 'globalMap.put("FileName", input_row.FileName);'},
                    {"name": "IMPORT", "value": "//import java.util.List;"},
                ],
                "incoming_connections": [],
                "outgoing_connections": [],
                "metadata_connection_ref": "",
            },
            {
                "unique_name": "tDBInput_1",
                "component_type": "tDBInput",
                "params": [
                    {"name": "QUERY", "value": "SELECT Id FROM Things WHERE Code='fake'"},
                    {"name": "TABLE", "value": '"Things"'},
                    {"name": "DIE_ON_ERROR", "value": "false"},
                    {"name": "SCREEN_OFFSET_X", "value": "64"},
                ],
                "incoming_connections": [],
                "outgoing_connections": [],
                "metadata_connection_ref": "",
            },
            {
                "unique_name": "tFileCopy_1",
                "component_type": "tFileCopy",
                "params": [
                    {"name": "DESTINATION", "value": "context.Processed"},
                    {"name": "REMOVE_FILE", "value": "true"},
                ],
                "incoming_connections": [],
                "outgoing_connections": [],
                "metadata_connection_ref": "",
            },
        ],
        "connections": [
            {"name": "row1", "source": "tDBInput_1", "target": "tJavaRow_1", "connector_name": "FLOW", "line_style": 0},
        ],
        "context_groups": [
            {
                "name": "inline",
                "variables": [
                    {"name": "SslMode", "type": "id_String", "values": {"DEV": "ssl=require", "TST": ""}},
                    {"name": "Processed", "type": "id_String", "values": {"DEV": "C:\\fake\\dev\\", "PROD": "D:\\fake\\prod\\"}},
                ],
            }
        ],
        "tmap_data": [
            {
                "input_tables": [],
                "output_tables": [],
                "entries": [
                    {
                        "table_name": "out1",
                        "column_name": "Message",
                        "expression": '"ok".equals(row1.status)? "GOOD" : "BAD"',
                    }
                ],
                "var_entries": [],
            }
        ],
        "trunjob_refs": [],
        "raw_routines": {},
        "subgroup": "",
        "active": True,
        "is_esb_route": False,
        "esb_component_types": [],
    }


def _job_main() -> dict:
    return {
        "name": "Job_Main",
        "path": "GroupA/Job_Main_1.0.item",
        "group": "GroupA",
        "version": "1.0",
        "components": [
            {
                "unique_name": "tRunJob_4",
                "component_type": "tRunJob",
                "params": [],
                "incoming_connections": [],
                "outgoing_connections": [],
                "metadata_connection_ref": "",
            }
        ],
        "connections": [],
        "context_groups": [],
        "tmap_data": [],
        "trunjob_refs": [
            {"child_job": "Job_GetThing", "child_job_xmi_id": "_fake", "context_params": []}
        ],
        "raw_routines": {},
        "subgroup": "",
        "active": True,
        "is_esb_route": False,
        "esb_component_types": [],
    }


def _shared_contexts() -> list[dict]:
    return [
        {
            "name": "SharedCfg",
            "variables": [
                {
                    "name": "SslMode",
                    "type": "id_String",
                    "values": {"DEV": "ssl=require", "TST": "ssl=require", "Default": "ssl=require"},
                },
                {
                    "name": "OnlyShared",
                    "type": "id_String",
                    "values": {"Default": "fake-shared-value"},
                },
            ],
        }
    ]


@pytest.fixture
def scan_output(tmp_path: Path) -> Path:
    scan_output = tmp_path / "intodoc-output" / "proj"
    jobs_dir = scan_output / "scan" / "jobs" / "GroupA"
    jobs_dir.mkdir(parents=True)
    (jobs_dir / "Job_GetThing.json").write_text(json.dumps(_job_getthing()), encoding="utf-8")
    (jobs_dir / "Job_Main.json").write_text(json.dumps(_job_main()), encoding="utf-8")
    (scan_output / "scan" / "contexts.json").write_text(
        json.dumps(_shared_contexts()), encoding="utf-8"
    )
    (scan_output / "scan" / "manifest.json").write_text(json.dumps({
        "job_routine_links": {"Job_GetThing": [{
            "routine_id": "root::Rules", "name": "Rules", "source_root": "root",
            "resolution": "same_root",
        }]},
    }), encoding="utf-8")
    (scan_output / "scan_result.json").write_text(json.dumps({
        "jobs": [_job_getthing(), _job_main()],
        "routines": [{"routine_id": "root::Rules", "name": "Rules",
                      "source_root": "root", "path": "code/routines/Rules.item",
                      "source_complete": True,
                      "source_code": "public static void apply() { decisive_tail_rule(); }"}],
    }), encoding="utf-8")
    return scan_output


@pytest.fixture
def staging(tmp_path: Path) -> Path:
    staging = tmp_path / "staging"
    staging.mkdir()
    return staging


def _build(scan_output: Path, staging: Path, max_bytes: int = 120_000):
    builder = TargetEvidenceBuilder(scan_output, staging, max_bytes)
    return builder.build(DOC_NAME, "Job_GetThing")


def test_bundle_contains_verbatim_evidence(scan_output, staging):
    evidence = _build(scan_output, staging)
    assert evidence is not None
    text = evidence.path.read_text(encoding="utf-8")
    assert 'globalMap.put("FileName", input_row.FileName);' in text
    assert "SELECT Id FROM Things WHERE Code='fake'" in text
    assert '"ok".equals(row1.status)? "GOOD" : "BAD"' in text
    assert evidence.categories == {"code", "tmap", "sql", "context"}
    # Commented-out import placeholders must not masquerade as code.
    assert "//import java.util.List;" not in text
    assert "## REFERENCED ROUTINE IMPLEMENTATIONS" in text
    assert "decisive_tail_rule" in text


def test_bundle_prefers_top_level_code_field(scan_output, staging):
    job = _job_getthing()
    job["components"][0]["code"] = "TOP_LEVEL_CODE();"
    jobs_dir = scan_output / "scan" / "jobs" / "GroupA"
    (jobs_dir / "Job_GetThing.json").write_text(json.dumps(job), encoding="utf-8")
    evidence = _build(scan_output, staging)
    text = evidence.path.read_text(encoding="utf-8")
    assert "TOP_LEVEL_CODE();" in text


def test_context_rows_carry_provenance_and_divergence(scan_output, staging):
    evidence = _build(scan_output, staging)
    text = evidence.path.read_text(encoding="utf-8")
    assert "job-embedded" in text
    # Job says TST="", shared group says TST=ssl=require -> divergence flagged.
    assert "DIVERGES from shared group 'SharedCfg' on TST" in text
    # Shared-only variable listed with shared provenance.
    assert "OnlyShared" in text
    assert "shared group 'SharedCfg' (not embedded in this job)" in text
    # Default environment value from the shared group is visible.
    assert "Default=fake-shared-value" in text


def test_callers_and_callees_listed(scan_output, staging):
    evidence = _build(scan_output, staging)
    text = evidence.path.read_text(encoding="utf-8")
    assert "Job_Main" in text
    assert "tRunJob_4" in text
    # Caller components are legal references for the validators.
    assert "tRunJob_4" in evidence.component_refs
    assert {"tJavaRow_1", "tDBInput_1", "tFileCopy_1"} <= set(evidence.component_refs)


def test_trim_drops_derived_sections_before_verbatim(scan_output, staging):
    evidence = _build(scan_output, staging, max_bytes=600)
    text = evidence.path.read_text(encoding="utf-8")
    # Derived niceties gone.
    assert "COMPONENT CONNECTIONS" not in text
    assert "DESTINATION = context.Processed" not in text
    # Verbatim evidence intact, with an explicit over-budget note.
    assert 'globalMap.put("FileName", input_row.FileName);' in text
    assert "SELECT Id FROM Things" in text
    assert evidence.truncated is True
    assert "exceeds the configured evidence byte budget" in text


def test_unknown_unit_returns_none(scan_output, staging):
    builder = TargetEvidenceBuilder(scan_output, staging, 120_000)
    assert builder.build(DOC_NAME, "No_Such_Job") is None


def test_filter_fact_table_is_unit_boundary_aware(tmp_path):
    source = tmp_path / "dependency_map.md"
    source.write_text(
        "# Dependency Map\n\n"
        "|source|target|evidence|\n"
        "|---|---|---|\n"
        "|Job_Main|Job_GetThing|GroupA/Job_GetThing_1.0.item|\n"
        "|Job_Main|Job_GetThings|GroupA/Job_GetThings_1.0.item|\n"
        "|tX_1|tY_2|GroupA/Job_Other_1.0.item|\n",
        encoding="utf-8",
    )
    dest_dir = tmp_path / "_analysis"
    filtered = filter_fact_table(source, "Job_GetThing", dest_dir, 24_000)
    assert filtered is not None and filtered != source
    text = filtered.read_text(encoding="utf-8")
    assert "Job_GetThing_1.0.item" in text
    # The plural sibling must NOT leak into the singular unit's map.
    assert "Job_GetThings_1.0.item" not in text
    assert "Job_Other_1.0.item" not in text
    assert "|source|target|evidence|" in text


def test_filter_fact_table_passes_through_non_tables(tmp_path):
    source = tmp_path / "notes.md"
    source.write_text("# Notes\n\nprose only\n", encoding="utf-8")
    assert filter_fact_table(source, "Job_GetThing", tmp_path / "_analysis", 1000) == source


def test_phase3_context_files_prepends_bundle_and_filters_maps(
    scan_output, staging, tmp_path, monkeypatch
):
    from mcp_server.config import config as mcp_config

    monkeypatch.setattr(mcp_config, "intodoc_output_dir", tmp_path / "intodoc-output")
    (staging / "dependency_map.md").write_text(
        "# Dependency Map\n\n|source|target|evidence|\n|---|---|---|\n"
        "|Job_Main|Job_GetThing|GroupA/Job_GetThing_1.0.item|\n"
        "|Job_Main|Job_GetThings|GroupA/Job_GetThings_1.0.item|\n",
        encoding="utf-8",
    )
    ctx = DocGenContext(
        project_id="proj",
        staging_dir=staging,
        staging_abs=str(staging),
        logs_abs=str(tmp_path / "logs"),
        preprocess_groups=["GroupA"],
        total_jobs=2,
        total_groups=1,
        effective_model="fake/model",
        write_model="fake/model",
        platform="talend",
        analysis_guide_file="_analysis_guide.md",
    )
    provider = CustomHarnessProvider(MagicMock())
    provider._per_job_doc_units = {DOC_NAME: "Job_GetThing"}
    provider._doc_bundle_map = {DOC_NAME: "core"}

    files = provider._phase3_context_files(ctx, DOC_NAME)

    assert files, "expected context files"
    assert files[0].name == f"_evidence_{DOC_NAME}.md"
    filtered = [f for f in files if f.name.endswith("dependency_map.md")]
    assert filtered and filtered[0].name.startswith("_filtered_")
    filtered_text = filtered[0].read_text(encoding="utf-8")
    assert "Job_GetThings_1.0.item" not in filtered_text
    # Bundle is cached for the validators.
    assert DOC_NAME in provider._target_evidence


def test_extract_component_refs():
    refs = extract_component_refs("uses tJavaRow_1 and tMap_2; tMap without number ignored")
    assert refs == {"tJavaRow_1", "tMap_2"}


# ── GAP 1: resolve a unit by job_id, not display name ────────────────


def _dup_job(job_id: str, marker_code: str) -> dict:
    """Two jobs share the display name 'MessageMonitoring'; only job_id differs.

    Mirrors real multi-export-root scans where the same display name recurs and
    build_facts_manifest hands the builder the unique job_id, not the name.
    """
    return {
        "name": "MessageMonitoring",
        "job_id": job_id,
        "path": f"{job_id}/MessageMonitoring_1.0.item",
        "group": "GroupA",
        "version": "1.0",
        "components": [
            {
                "unique_name": "tJava_1",
                "component_type": "tJava",
                "params": [{"name": "CODE", "value": marker_code}],
                "metadata_connection_ref": "",
            }
        ],
        "connections": [],
        "context_groups": [],
        "tmap_data": [],
        "trunjob_refs": [],
        "raw_routines": {},
        "subgroup": "",
        "active": True,
        "is_esb_route": False,
        "esb_component_types": [],
    }


def test_resolves_duplicated_display_name_by_job_id(tmp_path):
    scan_output = tmp_path / "intodoc-output" / "proj"
    jobs_dir = scan_output / "scan" / "jobs" / "GroupA"
    jobs_dir.mkdir(parents=True)
    (jobs_dir / "dup-aaa.json").write_text(
        json.dumps(_dup_job("dup-aaaaaa111111", "MARKER_COPY_A();")), encoding="utf-8"
    )
    (jobs_dir / "dup-bbb.json").write_text(
        json.dumps(_dup_job("dup-bbbbbb222222", "MARKER_COPY_B();")), encoding="utf-8"
    )
    staging = tmp_path / "staging"
    staging.mkdir()
    builder = TargetEvidenceBuilder(scan_output, staging, 120_000)

    ev_a = builder.build("doc_a", "dup-aaaaaa111111")
    ev_b = builder.build("doc_b", "dup-bbbbbb222222")

    assert ev_a is not None and ev_b is not None
    text_a = ev_a.path.read_text(encoding="utf-8")
    text_b = ev_b.path.read_text(encoding="utf-8")
    # Each hashed unit resolves to its own scan job, not the other copy.
    assert "MARKER_COPY_A();" in text_a and "MARKER_COPY_B();" not in text_a
    assert "MARKER_COPY_B();" in text_b and "MARKER_COPY_A();" not in text_b


def test_resolves_by_display_name_when_job_id_absent(tmp_path):
    """Older scans set no job_id; the harness then passes the display name."""
    scan_output = tmp_path / "intodoc-output" / "proj"
    jobs_dir = scan_output / "scan" / "jobs" / "GroupA"
    jobs_dir.mkdir(parents=True)
    job = _job_getthing()  # no job_id field
    (jobs_dir / "Job_GetThing.json").write_text(json.dumps(job), encoding="utf-8")
    staging = tmp_path / "staging"
    staging.mkdir()
    builder = TargetEvidenceBuilder(scan_output, staging, 120_000)
    assert builder.build(DOC_NAME, "Job_GetThing") is not None


# ── GAP 2: scope shared context to the groups the unit references ─────


def test_context_scoped_to_referenced_shared_groups(tmp_path):
    """Only shared groups the unit references (by repository_context_id) are rendered."""
    scan_output = tmp_path / "intodoc-output" / "proj"
    jobs_dir = scan_output / "scan" / "jobs" / "GroupA"
    jobs_dir.mkdir(parents=True)
    job = {
        "name": "Scoped_Job",
        "job_id": "scoped-1",
        "path": "GroupA/Scoped_Job_1.0.item",
        "group": "GroupA",
        "version": "1.0",
        "components": [
            {
                "unique_name": "tJava_1",
                "component_type": "tJava",
                "params": [{"name": "CODE", "value": "keep();"}],
                "metadata_connection_ref": "",
            }
        ],
        "connections": [],
        "context_groups": [
            {
                "name": "inline",
                "variables": [
                    {
                        "name": "Endpoint",
                        "type": "id_String",
                        "values": {"DEV": "http://dev", "PROD": "http://prod"},
                        "repository_context_id": "ctx-used",
                    }
                ],
            }
        ],
        "tmap_data": [],
        "trunjob_refs": [],
        "raw_routines": {},
        "subgroup": "",
        "active": True,
        "is_esb_route": False,
        "esb_component_types": [],
    }
    (jobs_dir / "Scoped_Job.json").write_text(json.dumps(job), encoding="utf-8")
    shared = [
        {
            "name": "UsedGroup",
            "context_id": "ctx-used",
            "variables": [
                {"name": "Endpoint", "type": "id_String",
                 "values": {"DEV": "http://dev", "PROD": "http://prod-DIVERGED"}},
                {"name": "UsedOnlyShared", "type": "id_String", "values": {"Default": "used-shared-val"}},
            ],
        },
        {
            "name": "UnusedGroup",
            "context_id": "ctx-unused",
            "variables": [
                {"name": "IrrelevantVar", "type": "id_String", "values": {"Default": "noise-value"}},
            ],
        },
    ]
    (scan_output / "scan" / "contexts.json").write_text(json.dumps(shared), encoding="utf-8")
    staging = tmp_path / "staging"
    staging.mkdir()
    builder = TargetEvidenceBuilder(scan_output, staging, 120_000)

    evidence = builder.build("doc_scoped", "scoped-1")
    assert evidence is not None
    text = evidence.path.read_text(encoding="utf-8")
    # Referenced group's shared-only var and divergence are present.
    assert "UsedOnlyShared" in text
    assert "DIVERGES from shared group 'UsedGroup' on PROD" in text
    # The unreferenced group is not dumped into this unit's bundle.
    assert "IrrelevantVar" not in text
    assert "noise-value" not in text
    assert "UnusedGroup" not in text
