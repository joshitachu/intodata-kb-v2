"""Tests for the blocking correctness gates (Slice 1)."""

from __future__ import annotations

from pathlib import Path

from mcp_server.providers.custom.models import FactsManifest, UnitFact
from mcp_server.services.group_doc_dependency import GroupDocDependency
from mcp_server.services.reingestion_gate import (
    THIN_FLOOR_BYTES,
    CorrectnessGate,
    load_facts_manifest,
)


def _dependency(
    group_to_docs: dict[str, set[str]],
    cross_cutting: set[str] | None = None,
) -> GroupDocDependency:
    doc_to_groups: dict[str, set[str]] = {}
    for group, docs in group_to_docs.items():
        for doc in docs:
            doc_to_groups.setdefault(doc, set()).add(group)
    return GroupDocDependency(
        {g: set(d) for g, d in group_to_docs.items()},
        doc_to_groups,
        set(cross_cutting or set()),
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


# --- Slice 3: mapped-doc tightening -------------------------------------------


def test_gate_mapped_doc_fails_when_unit_only_in_unrelated_doc(tmp_path: Path) -> None:
    """Tightened: a changed unit present only in a NON-mapped doc fails the gate."""
    staging = tmp_path / "docs_staging"
    facts = _facts([UnitFact(name="job_alpha", group="A")])
    # Group A maps to 01_MAPPED; the unit name only appears in 02_OTHER.
    _write_doc(staging, "01_MAPPED.md", "Mapped doc with no unit mention.")
    _write_doc(staging, "02_OTHER.md", "Unrelated doc that names job_alpha.")
    _write_analysis(staging, "A", _substantive("A"))
    dep = _dependency({"A": {"01_MAPPED"}})

    outcome = CorrectnessGate(changed_groups={"A"}, dependency=dep).evaluate(staging, facts)

    assert outcome.passed is False
    assert outcome.missing_units == ["job_alpha"]


def test_gate_mapped_doc_passes_when_unit_in_its_mapped_doc(tmp_path: Path) -> None:
    staging = tmp_path / "docs_staging"
    facts = _facts([UnitFact(name="job_alpha", group="A")])
    _write_doc(staging, "01_MAPPED.md", "Mapped doc documents job_alpha fully.")
    _write_doc(staging, "02_OTHER.md", "Unrelated doc.")
    _write_analysis(staging, "A", _substantive("A"))
    dep = _dependency({"A": {"01_MAPPED"}})

    outcome = CorrectnessGate(changed_groups={"A"}, dependency=dep).evaluate(staging, facts)

    assert outcome.passed is True
    assert outcome.missing_units == []


def test_gate_mapped_doc_credits_cross_cutting_doc(tmp_path: Path) -> None:
    """A cross-cutting doc is always part of a unit's mapped set."""
    staging = tmp_path / "docs_staging"
    facts = _facts([UnitFact(name="job_alpha", group="A")])
    _write_doc(staging, "01_MAPPED.md", "Mapped doc with no unit mention.")
    _write_doc(staging, "02_INVENTORY.md", "Inventory lists job_alpha.")
    _write_analysis(staging, "A", _substantive("A"))
    dep = _dependency({"A": {"01_MAPPED"}}, cross_cutting={"02_INVENTORY"})

    outcome = CorrectnessGate(changed_groups={"A"}, dependency=dep).evaluate(staging, facts)

    assert outcome.passed is True


def test_gate_loose_fallback_when_no_dependency(tmp_path: Path) -> None:
    """Without a provenance map the loose 'unit in some doc' check applies."""
    staging = tmp_path / "docs_staging"
    facts = _facts([UnitFact(name="job_alpha", group="A")])
    _write_doc(staging, "01_MAPPED.md", "No mention here.")
    _write_doc(staging, "02_OTHER.md", "But job_alpha appears in this doc.")
    _write_analysis(staging, "A", _substantive("A"))

    # dependency=None -> loose: presence in ANY doc is enough to pass.
    outcome = CorrectnessGate(changed_groups={"A"}, dependency=None).evaluate(staging, facts)

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
