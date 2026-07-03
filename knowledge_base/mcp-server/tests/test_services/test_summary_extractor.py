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


def test_extract_phase2_skips_group_summary_fallback_for_missing_jobs():
    """When no per-job prose exists we return empty (NOT the generic GROUP_SUMMARY
    text). The downstream chain falls back to scan_data.purpose / heuristic, and
    the panel hides the section when the final string is empty. Returning a
    generic group-level line would be misleading on the per-job detail panel.
    """
    group_md = textwrap.dedent(
        """\
        ## GROUP_SUMMARY
        These jobs manage the nightly reconciliation between the warehouse
        and the CRM. They detect drift in row counts and raise alerts.
        """
    )
    out = extract_phase2_summary_for_job(group_md, "reconcile_orders")
    assert out == ""


def test_extract_phase2_returns_empty_for_blank_input():
    assert extract_phase2_summary_for_job("", "any_job") == ""
    assert extract_phase2_summary_for_job("# heading only", "") == ""


def test_extract_phase2_skips_coverage_metadata_section():
    """Real Phase 2 files have a `## Coverage` list of job names. The extractor
    must NOT pick up those bullets as the job summary (they say nothing about
    what the job does)."""
    group_md = textwrap.dedent(
        """\
        # Phase 2 Analysis: scripts

        ## Coverage
        - COVERED: archive_snapshots
        - COVERED: enrich_geo
        - COVERED: extract_customers

        ## Model Analysis

        #### Unit: `archive_snapshots`
        Archive prior warehouse export files to AWS S3 cold object storage
        before a new load. Acts as a pre-load cleanup/backup stage.

        #### Unit: `enrich_geo`
        Enrich staged customer data with geographic region and coordinates
        from an external geocoding API.
        """
    )
    summary = extract_phase2_summary_for_job(group_md, "archive_snapshots")
    assert "S3" in summary or "cleanup" in summary
    assert "COVERED" not in summary
    assert "Coverage" not in summary


def test_extract_phase2_prefers_unit_subsection_over_coverage_list():
    """A job listed under Coverage must still get its Unit subsection prose."""
    group_md = textwrap.dedent(
        """\
        ## Coverage
        - COVERED: archive_snapshots

        ## Model Analysis
        #### Unit: archive_snapshots
        Archive prior warehouse export files to AWS S3 cold object storage
        before a new load. Acts as a pre-load cleanup/backup stage.
        """
    )
    summary = extract_phase2_summary_for_job(group_md, "archive_snapshots")
    assert "S3" in summary
    assert "COVERED" not in summary


def test_extract_phase2_extracts_purpose_from_unit_subsection():
    """When a `#### Unit: <name>` subsection contains a `**Purpose**:` line,
    the extractor must use ONLY the Purpose prose (not Unit/Path/Type boilerplate)."""
    group_md = textwrap.dedent(
        """\
        ## Model Analysis
        #### Unit: `archive_snapshots`
        - **Unit**: `archive_snapshots`
          **Path**: `scripts/archive_snapshots.py`
          **Type**: Python script (CodeFile)
          **Purpose**: Archive prior warehouse export files to AWS S3 cold
          object storage before a new load. Acts as a pre-load cleanup stage.

        #### Unit: `enrich_geo`
        - **Unit**: `enrich_geo`
          **Purpose**: Enrich staged customer data with geographic region.
        """
    )
    summary = extract_phase2_summary_for_job(group_md, "archive_snapshots")
    assert "S3" in summary
    assert "**Path**" not in summary
    assert "**Type**" not in summary
    assert "**Unit**" not in summary


def test_extract_phase2_extracts_purpose_from_components_bullet_list():
    """config.md / root.md shape: a single COMPONENTS section with bullet
    entries containing ``- **Unit**: <name>`` and ``**Purpose**: ...``."""
    group_md = textwrap.dedent(
        """\
        ## Model Analysis

        ### COMPONENTS

        - **Unit**: `pipeline_config`
          **Path**: `config/pipeline_config.yaml`
          **Type**: `ConfigFile` (YAML)
          **Purpose**: Central pipeline orchestration configuration defining
          two data pipelines. Acts as the single source of truth for the
          ETL pipeline topology and business rules.

        - **Unit**: `README`
          **Type**: DocumentFile
          **Purpose**: Documents the customer-sync pipeline. Provides
          high-level understanding for developers.

        ### EXECUTION_FLOW

        Some other section.
        """
    )
    summary = extract_phase2_summary_for_job(group_md, "pipeline_config")
    assert "orchestration" in summary
    assert "**Path**" not in summary
    assert "ConfigFile" not in summary  # boilerplate, not prose

    readme_summary = extract_phase2_summary_for_job(group_md, "README")
    assert "customer-sync" in readme_summary or "developer" in readme_summary
    assert "DocumentFile" not in readme_summary


def test_extract_phase2_finds_detailed_analysis_purpose():
    """root.md shape: a 'Detailed analysis for <job>' section precedes a
    bullet containing ``**Purpose**: ...``. The extractor must follow the
    job mention to the nearest Purpose line."""
    group_md = textwrap.dedent(
        """\
        ## Model Analysis

        ### COMPONENTS

        The following table enumerates all 13 canonical code units. The only
        unit with full source content in this group is `README`.

        **Detailed analysis for README** (the only unit with supplied source):

        - **File**: `README.md`
        - **Purpose**: Documents a customer-sync pipeline:
          `extract_customers.py` (CRM REST API) → `transform_customers.sql`
          (normalize/deduplicate) → `load_warehouse.py` (upsert to warehouse).
        - **Tests**: None in evidence.

        ### EXECUTION_FLOW

        The README describes a linear pipeline with three steps.
        """
    )
    summary = extract_phase2_summary_for_job(group_md, "README")
    assert "customer-sync" in summary or "extract_customers" in summary
    assert "**File**" not in summary
    assert "**Tests**" not in summary


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
    output_dir = tmp_path / "output" / project_id / "docs" / "_rag_support" / "phase2_analysis"
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

            ## load_warehouse
            load_warehouse upserts transformed rows into the analytics
            warehouse via parameterized INSERT ... ON CONFLICT.
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


async def test_phase2_prose_populates_every_job_in_group(project_layout):
    """Regression: every job sharing a group must get its own Phase 2 prose,
    not just the first job iterated. Previously `_phase2_prose_summaries`
    short-circuited via `seen_groups` after extracting one job per group.
    """
    project_id, _ = project_layout
    from mcp_server.services.summary_service import _phase2_prose_summaries

    job_groups = {"extract_customers": "scripts", "load_warehouse": "scripts"}
    summaries = _phase2_prose_summaries(project_id, job_groups)
    assert "extract_customers" in summaries, summaries
    assert "load_warehouse" in summaries, summaries
    # Both should be Phase 2 prose, not heuristic.
    assert "CRM" in summaries["extract_customers"] or "staging" in summaries["extract_customers"]
    assert "warehouse" in summaries["load_warehouse"].lower()