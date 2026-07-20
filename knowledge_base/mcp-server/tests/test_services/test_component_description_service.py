"""Tests for the component-description parser (Level-2 graph tooltips).

Happy-path cases run against the real generated fixture doc at
``output/cdc_job_route/docs/02_COMPONENT_INVENTORY.md`` (config.project_root
resolves to the knowledge_base root in an unpatched service test). Defensive
column-detection and edge cases use synthetic docs written under a patched
project_root so column headers/order can be varied deterministically.
"""

from __future__ import annotations

import pytest

from mcp_server.config import config
from mcp_server.services.component_description_service import (
    parse_component_descriptions,
)

REAL_PROJECT = "cdc_job_route"
REAL_JOB = "sjItem_OffRamp_DB"


def test_real_fixture_returns_expected_descriptions():
    result = parse_component_descriptions(REAL_PROJECT, REAL_JOB)
    assert result, "expected a non-empty description map for the real fixture"

    # Component IDs from the doc's per-job table are the keys.
    assert "tDBOutput_1" in result
    assert "tExtractJSONFields_1" in result

    # The concise "Role" column is the description; backtick noise is stripped.
    assert "Inserts into table Items in MSSQL" in result["tDBOutput_1"]
    assert "`" not in result["tDBOutput_1"]
    assert "Extracts fields from JSON" in result["tExtractJSONFields_1"]

    # Descriptions are bounded.
    assert all(len(v) <= 300 for v in result.values())


def test_real_fixture_second_job_parses_independently():
    result = parse_component_descriptions(REAL_PROJECT, "sjItem_OnRamp")
    assert "tFileInputDelimited_1" in result
    assert "Reads CSV file" in result["tFileInputDelimited_1"]
    # A row from the OTHER job must not leak into this job's section.
    assert "tExtractJSONFields_1" not in result


def test_escaped_pipe_inside_description_cell_is_preserved():
    # tLogRow_1's Role cell contains an escaped pipe: "field separator `\\|`".
    result = parse_component_descriptions(REAL_PROJECT, REAL_JOB)
    assert "tLogRow_1" in result
    desc = result["tLogRow_1"]
    assert "Logs row data" in desc
    # The Active/Notes columns must not have bled into the description.
    assert "field separator" in desc


def test_unknown_job_returns_empty_map():
    assert parse_component_descriptions(REAL_PROJECT, "NoSuchJob") == {}


def test_missing_project_returns_empty_map():
    assert parse_component_descriptions("project_that_does_not_exist", REAL_JOB) == {}


# --- Synthetic docs for defensive parsing (patched project_root) ---------


@pytest.fixture
def doc_writer(tmp_path, monkeypatch):
    """Write a synthetic inventory doc under a patched project_root."""
    monkeypatch.setattr(config, "project_root", tmp_path)

    def _write(project_id: str, body: str, *, staging: bool = False) -> None:
        sub = "docs_staging" if staging else "docs"
        docs_dir = tmp_path / "output" / project_id / sub
        docs_dir.mkdir(parents=True, exist_ok=True)
        (docs_dir / "02_COMPONENT_INVENTORY.md").write_text(body, encoding="utf-8")

    return _write


def test_columns_detected_by_header_not_index(doc_writer):
    # Description column is first, id column is last: index-based parsing fails.
    doc_writer(
        "proj",
        """
### Job: JobX (2 components)

| Description | Type | Component ID |
|-------------|------|--------------|
| Reads the inbound file | tFileInput | tFileInput_1 |
| Writes to the target DB | tDBOutput | tDBOutput_1 |
""",
    )
    result = parse_component_descriptions("proj", "JobX")
    assert result == {
        "tFileInput_1": "Reads the inbound file",
        "tDBOutput_1": "Writes to the target DB",
    }


def test_component_in_scan_but_absent_from_doc_not_in_map(doc_writer):
    # Only tA_1 has a doc row; tB_1 (present in the scan) has none -> absent.
    doc_writer(
        "proj",
        """
### Job: JobX (2 components)

| Component ID | Role |
|--------------|------|
| tA_1 | Does the A thing |
""",
    )
    result = parse_component_descriptions("proj", "JobX")
    assert result == {"tA_1": "Does the A thing"}
    assert "tB_1" not in result


def test_no_description_column_returns_empty(doc_writer):
    doc_writer(
        "proj",
        """
### Job: JobX (1 components)

| Component ID | Type | Active |
|--------------|------|--------|
| tA_1 | tJava | Yes |
""",
    )
    assert parse_component_descriptions("proj", "JobX") == {}


def test_staging_fallback_when_docs_absent(doc_writer):
    doc_writer(
        "proj",
        """
### Job: JobX (1 components)

| Component ID | Role |
|--------------|------|
| tA_1 | From staging |
""",
        staging=True,
    )
    assert parse_component_descriptions("proj", "JobX") == {"tA_1": "From staging"}


def test_empty_description_cell_is_skipped(doc_writer):
    doc_writer(
        "proj",
        """
### Job: JobX (2 components)

| Component ID | Role |
|--------------|------|
| tA_1 | Real description |
| tB_1 |  |
""",
    )
    result = parse_component_descriptions("proj", "JobX")
    assert result == {"tA_1": "Real description"}


def test_description_length_is_bounded(doc_writer):
    long_text = "x" * 500
    doc_writer(
        "proj",
        f"""
### Job: JobX (1 components)

| Component ID | Role |
|--------------|------|
| tA_1 | {long_text} |
""",
    )
    result = parse_component_descriptions("proj", "JobX")
    assert len(result["tA_1"]) <= 300
    assert result["tA_1"].endswith("...")
