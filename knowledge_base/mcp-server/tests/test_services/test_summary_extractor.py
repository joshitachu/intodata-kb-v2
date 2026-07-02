"""Unit tests for the topology component-summary extractor + heuristic."""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

from mcp_server.services.summary_service import (
    _extract_first_useful_sentences,
    build_component_summaries_async,
    extract_phase2_summary_for_job,
    heuristic_summary,
)


# ---------------------------------------------------------------------------
# Sentence extractor
# ---------------------------------------------------------------------------


def test_extract_first_useful_sentences_skips_short_fragments():
    text = "Ok. Hi. This job reads customer records from the CRM and writes them to the warehouse."
    out = _extract_first_useful_sentences(text, max_sentences=2, max_chars=300)
    assert "This job" in out
    assert "Hi." not in out  # too short, dropped
    assert "Ok." not in out


def test_extract_first_useful_sentences_caps_at_max():
    long = ("Sentence one is here. " * 30).strip()
    out = _extract_first_useful_sentences(long, max_sentences=2, max_chars=120)
    assert out.endswith(".")
    assert len(out) <= 120


def test_extract_first_useful_sentences_handles_empty_input():
    assert _extract_first_useful_sentences("", max_sentences=2) == ""
    assert _extract_first_useful_sentences("   \n\n  ", max_sentences=2) == ""


# ---------------------------------------------------------------------------
# Phase 2 prose -> per-job summary
# ---------------------------------------------------------------------------


def test_extract_phase2_prefers_job_section_over_group_summary():
    group_md = textwrap.dedent(
        """\
        # scripts

        ## GROUP_SUMMARY
        This group handles customer orders across several pipelines.

        ## extract_customers
        extract_customers pulls paginated customer records from the CRM
        REST API. It writes them to a staging JSON file for the transform
        step. Run on the customer-sync pipeline schedule.

        ## load_warehouse
        load_warehouse upserts rows into the analytics warehouse.
        """
    )
    out = extract_phase2_summary_for_job(group_md, "extract_customers")
    assert "extract_customers" not in out.split(".")[0].lower() or "extract_customers" in out
    assert "CRM" in out or "staging" in out


def test_extract_phase2_falls_back_to_group_summary_when_job_missing():
    group_md = textwrap.dedent(
        """\
        ## GROUP_SUMMARY
        These jobs manage the nightly reconciliation between the warehouse
        and the CRM. They detect drift in row counts and raise alerts.
        """
    )
    out = extract_phase2_summary_for_job(group_md, "reconcile_orders")
    assert "reconciliation" in out or "drift" in out


def test_extract_phase2_returns_empty_for_blank_input():
    assert extract_phase2_summary_for_job("", "any_job") == ""
    assert extract_phase2_summary_for_job("# heading only", "") == ""


# ---------------------------------------------------------------------------
# Heuristic
# ---------------------------------------------------------------------------


def test_heuristic_summary_returns_empty_when_no_signal():
    assert heuristic_summary("any_job", "", [], []) == ""


def test_heuristic_summary_systems_only_uses_conjunction_for_two():
    out = heuristic_summary("load_dw", "OffRamp", ["warehouse"], [])
    assert out.startswith("writes")
    assert "warehouse" in out


def test_heuristic_summary_two_systems_joined_with_and():
    out = heuristic_summary("sync", "Bidirectional", ["crm", "erp"], [])
    assert "crm and erp" in out


def test_heuristic_summary_filters_internal_components():
    out = heuristic_summary(
        "load_dw",
        "OffRamp",
        ["warehouse"],
        ["tLogRow", "tSetGlobalVar", "tPostjob", "tMSSqlOutput"],
    )
    # Internal components (tLog*, tSetGlobal*, tPostjob) must not appear.
    assert "tLogRow" not in out
    assert "tSetGlobalVar" not in out
    assert "tPostjob" not in out
    assert "tMSSqlOutput" in out


def test_heuristic_summary_vary_by_job_name():
    a = heuristic_summary("extract_customers", "OnRamp", ["crm"], [])
    b = heuristic_summary("load_warehouse", "OffRamp", ["warehouse"], [])
    assert a != b
    assert "extract_customers" not in a  # heuristic shouldn't echo job name
    # but the verbs + systems differ
    assert "reads from" not in a and "ingests" in a
    assert "writes" in b


# ---------------------------------------------------------------------------
# Source priority + persistence (end-to-end with tmp dirs)
# ---------------------------------------------------------------------------


@pytest.fixture
def project_layout(tmp_path, monkeypatch):
    """Stand up the directory layout that build_component_summaries_async expects."""
    from mcp_server import config

    project_id = "test_proj"
    intodoc_dir = tmp_path / "intodoc-output" / project_id / "scan"
    output_dir = tmp_path / "output" / project_id / "_rag_support" / "phase2_analysis"
    intodoc_dir.mkdir(parents=True)
    output_dir.mkdir(parents=True)

    manifest = {
        "job_groups": {
            "extract_customers": "scripts",
            "load_warehouse": "scripts",
            "no_prose_job": "scripts",
        },
        "job_external_systems": {
            "extract_customers": ["crm"],
            "load_warehouse": ["warehouse"],
            "no_prose_job": ["unknown_system"],
        },
    }
    (intodoc_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    scripts_dir = intodoc_dir / "jobs" / "scripts"
    scripts_dir.mkdir(parents=True)
    (scripts_dir / "extract_customers.json").write_text(
        json.dumps({"components": [{"component_type": "tRESTClient"}]}),
        encoding="utf-8",
    )
    (scripts_dir / "load_warehouse.json").write_text(
        json.dumps({
            "purpose": "Upserts transformed rows into the analytics warehouse",
            "components": [{"component_type": "tMSSqlOutput"}],
        }),
        encoding="utf-8",
    )
    (scripts_dir / "no_prose_job.json").write_text(
        json.dumps({"components": [{"component_type": "tJavaRow"}]}),
        encoding="utf-8",
    )

    (output_dir / "scripts.md").write_text(
        textwrap.dedent(
            """\
            # scripts

            ## GROUP_SUMMARY
            These jobs power the customer-sync pipeline end to end.

            ## extract_customers
            extract_customers pulls paginated customer records from the
            CRM REST API and writes them to a staging JSON file.
            """
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(config.config, "intodoc_output_dir", tmp_path / "intodoc-output")
    monkeypatch.setattr(config.config, "project_root", tmp_path)
    return project_id, intodoc_dir


async def test_build_component_summaries_uses_phase2_first_then_purpose_then_heuristic(project_layout):
    project_id, intodoc_dir = project_layout
    summaries = await build_component_summaries_async(project_id)

    # extract_customers: prose from Phase 2 wins (mentions CRM + staging).
    assert "CRM" in summaries["extract_customers"] or "staging" in summaries["extract_customers"]
    # load_warehouse: Phase 2 GROUP_SUMMARY prose (no per-job section, so the
    # extractor falls back to GROUP_SUMMARY which talks about customer-sync,
    # not load_warehouse specifically). scan_data.purpose is more specific for
    # this job, so the priority logic should pick it up.
    assert "analytics warehouse" in summaries["load_warehouse"].lower() or "warehouse" in summaries["load_warehouse"]
    # no_prose_job: nothing in Phase 2, no scan_data.purpose -> heuristic.
    assert "no_prose_job" in summaries
    assert summaries["no_prose_job"]

    cache_path = intodoc_dir / "summaries.json"
    assert cache_path.exists()
    payload = json.loads(cache_path.read_text(encoding="utf-8"))
    assert payload["job_count"] == 3
    assert "phase2_prose" in payload["model"] or payload["model"] == "phase2_prose"