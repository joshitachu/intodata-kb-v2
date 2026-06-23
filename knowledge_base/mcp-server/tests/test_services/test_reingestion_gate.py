"""Tests for the blocking correctness gates (Slice 1)."""

from __future__ import annotations

from pathlib import Path

from mcp_server.providers.custom.models import FactsManifest, UnitFact
from mcp_server.services.reingestion_gate import (
    THIN_FLOOR_BYTES,
    CorrectnessGate,
    load_facts_manifest,
)


def _facts(units: list[UnitFact]) -> FactsManifest:
    return FactsManifest(
        project_id="proj",
        platform="generic",
        canonical_unit_label="units",
        unit_count=len(units),
        groups={u.group: 1 for u in units},
        units=units,
    )


def _write_doc(staging: Path, name: str, body: str) -> None:
    staging.mkdir(parents=True, exist_ok=True)
    (staging / name).write_text(body, encoding="utf-8")


def _write_analysis(staging: Path, group: str, body: str) -> None:
    # Phase 4 publishes analyses under _rag_support/phase2_analysis/.
    target = staging / "_rag_support" / "phase2_analysis"
    target.mkdir(parents=True, exist_ok=True)
    (target / f"{group}.md").write_text(body, encoding="utf-8")


def _substantive(group: str) -> str:
    # Above the THIN floor and contains the required GROUP_SUMMARY section.
    return "### GROUP_SUMMARY\n" + (f"Substantive analysis for {group}. " * 40)


def test_gate_passes_when_changed_unit_present_and_analysis_substantive(tmp_path: Path) -> None:
    staging = tmp_path / "docs_staging"
    facts = _facts([UnitFact(name="job_alpha", group="A")])
    _write_doc(staging, "01_X.md", "Documents job_alpha in detail.")
    _write_analysis(staging, "A", _substantive("A"))

    outcome = CorrectnessGate(changed_groups={"A"}).evaluate(staging, facts)

    assert outcome.passed is True
    assert outcome.evaluated is True
    assert outcome.missing_units == []
    assert outcome.empty_analysis_groups == []


def test_gate_fails_when_changed_unit_missing_from_docs(tmp_path: Path) -> None:
    staging = tmp_path / "docs_staging"
    facts = _facts([UnitFact(name="job_added", group="A")])
    _write_doc(staging, "01_X.md", "This doc never names the new unit.")
    _write_analysis(staging, "A", _substantive("A"))

    outcome = CorrectnessGate(changed_groups={"A"}).evaluate(staging, facts)

    assert outcome.passed is False
    assert outcome.missing_units == ["job_added"]
    assert "A" in outcome.failing_groups


def test_gate_ignores_units_in_unchanged_groups(tmp_path: Path) -> None:
    staging = tmp_path / "docs_staging"
    facts = _facts(
        [UnitFact(name="job_alpha", group="A"), UnitFact(name="job_beta", group="B")]
    )
    # job_beta (unchanged group B) is absent from docs but must not trip the gate.
    _write_doc(staging, "01_X.md", "Documents job_alpha only.")
    _write_analysis(staging, "A", _substantive("A"))

    outcome = CorrectnessGate(changed_groups={"A"}).evaluate(staging, facts)

    assert outcome.passed is True
    assert outcome.missing_units == []


def test_gate_fails_on_thin_analysis(tmp_path: Path) -> None:
    staging = tmp_path / "docs_staging"
    facts = _facts([UnitFact(name="job_alpha", group="A")])
    _write_doc(staging, "01_X.md", "Documents job_alpha.")
    assert THIN_FLOOR_BYTES == 500
    _write_analysis(staging, "A", "### GROUP_SUMMARY\nstub")  # below floor

    outcome = CorrectnessGate(changed_groups={"A"}).evaluate(staging, facts)

    assert outcome.passed is False
    assert outcome.empty_analysis_groups == ["A"]
    assert "A" in outcome.failing_groups


def test_gate_fails_on_missing_analysis(tmp_path: Path) -> None:
    staging = tmp_path / "docs_staging"
    facts = _facts([UnitFact(name="job_alpha", group="A")])
    _write_doc(staging, "01_X.md", "Documents job_alpha.")
    # No analysis written for group A.

    outcome = CorrectnessGate(changed_groups={"A"}).evaluate(staging, facts)

    assert outcome.passed is False
    assert outcome.empty_analysis_groups == ["A"]


def test_gate_fails_when_required_section_absent(tmp_path: Path) -> None:
    staging = tmp_path / "docs_staging"
    facts = _facts([UnitFact(name="job_alpha", group="A")])
    _write_doc(staging, "01_X.md", "Documents job_alpha.")
    # Long enough but missing the GROUP_SUMMARY section.
    _write_analysis(staging, "A", "### OTHER\n" + ("filler " * 100))

    outcome = CorrectnessGate(changed_groups={"A"}, require_sections=True).evaluate(staging, facts)

    assert outcome.passed is False
    assert outcome.empty_analysis_groups == ["A"]


def test_gate_no_op_when_no_changed_groups(tmp_path: Path) -> None:
    staging = tmp_path / "docs_staging"
    facts = _facts([UnitFact(name="job_alpha", group="A")])

    outcome = CorrectnessGate(changed_groups=set()).evaluate(staging, facts)

    assert outcome.passed is True
    assert outcome.evaluated is False


def test_gate_reads_legacy_analysis_dir_fallback(tmp_path: Path) -> None:
    staging = tmp_path / "docs_staging"
    facts = _facts([UnitFact(name="job_alpha", group="A")])
    _write_doc(staging, "01_X.md", "Documents job_alpha.")
    legacy = staging / "_analysis"
    legacy.mkdir(parents=True)
    (legacy / "A.md").write_text(_substantive("A"), encoding="utf-8")

    outcome = CorrectnessGate(changed_groups={"A"}).evaluate(staging, facts)

    assert outcome.passed is True


def test_load_facts_manifest_roundtrip_and_missing(tmp_path: Path) -> None:
    staging = tmp_path / "docs_staging"
    staging.mkdir(parents=True)
    assert load_facts_manifest(staging) is None

    facts = _facts([UnitFact(name="job_alpha", group="A")])
    facts.write(staging / "facts_manifest.json")
    loaded = load_facts_manifest(staging)
    assert loaded is not None
    assert loaded.unit_names() == {"job_alpha"}
