"""Tests for the deterministic fabrication checks (component echo + false unavailability)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from mcp_server.providers.custom.fabrication_validators import (
    availability_claim_errors,
    component_reference_errors,
    job_scoped_component_reference_errors,
)
from mcp_server.providers.custom.harness import CustomHarnessProvider
from mcp_server.providers.custom.target_evidence import TargetEvidence

ALLOWED = frozenset({"tSetGlobalVar_1", "tSetGlobalVar_4", "tSetGlobalVar_5", "tJavaRow_1", "tRunJob_4"})


def test_invented_component_is_flagged():
    # The verified production failure: tSetGlobalVar_3 invented for a job whose
    # real components are tSetGlobalVar_1/_4/_5.
    text = "- tSetGlobalVar_1 sets ProcessId\n- tSetGlobalVar_3 stores ProcessID\n"
    errors = component_reference_errors(text, ALLOWED)
    assert len(errors) == 1
    assert "tSetGlobalVar_3" in errors[0]
    assert "tSetGlobalVar_1" not in errors[0].split(":")[1].split(".")[0]


def test_real_components_pass():
    text = "- tSetGlobalVar_1 and tJavaRow_1; invoked by parent via tRunJob_4\n"
    assert component_reference_errors(text, ALLOWED) == []


def test_empty_allowed_set_disables_check():
    assert component_reference_errors("mentions tGhost_9", frozenset()) == []


def test_code_unavailable_claim_flagged_when_bundle_has_code():
    text = "- tJava_1, tJava_2 - custom Java logic (code unavailable)\n"
    errors = availability_claim_errors(text, frozenset({"code"}))
    assert len(errors) == 1
    assert "CUSTOM CODE (VERBATIM)" in errors[0]


def test_tmap_truncation_claim_flagged():
    # Verbatim production line from run 50's merged analysis.
    text = "| tMap | tMap_1 | GeneralLogMap - maps stats to log (full expression NOT available in chunk). |"
    errors = availability_claim_errors(text, frozenset({"tmap"}))
    assert len(errors) == 1
    assert "TMAP EXPRESSIONS (VERBATIM)" in errors[0]


def test_claim_silent_when_category_genuinely_absent():
    text = "custom Java logic (code unavailable)"
    assert availability_claim_errors(text, frozenset({"sql"})) == []


def test_runtime_facts_are_legitimately_unavailable():
    text = (
        "- Throughput metrics are not available in a static scan.\n"
        "- No sample location files were included in the export.\n"
        "- Historical error rates not captured; ask operations.\n"
    )
    assert availability_claim_errors(text, frozenset({"code", "tmap", "sql", "context"})) == []


def test_claims_capped():
    text = "\n".join("custom code unavailable here" for _ in range(10))
    errors = availability_claim_errors(text, frozenset({"code"}))
    assert len(errors) == 5


# --- job-scoped component-reference checks (bin-packed multi-job tasks) ------
#
# Verified production failure (reversEngineer_ALI run 51, group AssetCheck_Interface,
# task_1 bin-packing AssetCheck_GetLocation/_Locations/_GetWorkOrder/_GetWorkOrders
# into one call): 'tMSSqlOutput_1' is real for GetLocation only; the model wrote it
# under GetWorkOrder's own attributed span, which actually uses tDBOutput_1.
# Checking against the whole task's combined evidence let it pass (the name IS
# real somewhere in the batch); per-job scoping must catch it.

_JOB_A_REFS = frozenset({"tMSSqlOutput_1", "tMSSqlConnection_1", "tFileExist_1"})
_JOB_B_REFS = frozenset({"tDBOutput_1", "tDBConnection_1", "tFileList_1"})


def test_job_scoped_check_catches_sibling_misattribution():
    # Shaped exactly like the real bug: JobB's own attributed span cites JobA's
    # real component.
    text = (
        "#### JobA\n"
        "Logs via tMSSqlOutput_1.\n"
        "#### JobB\n"
        "Logs via tMSSqlOutput_1 (insert into LogInfo).\n"
    )
    errors = job_scoped_component_reference_errors(
        text,
        {"JobA": _JOB_A_REFS, "JobB": _JOB_B_REFS},
        _JOB_A_REFS | _JOB_B_REFS,
    )
    assert len(errors) == 1
    assert "tMSSqlOutput_1" in errors[0]
    assert "JobB" in errors[0]
    # Must not also flag JobA -- tMSSqlOutput_1 is genuinely JobA's own component.
    assert "JobA" not in errors[0].split(":")[0]


def test_job_scoped_check_passes_correct_attribution():
    text = (
        "#### JobA\n"
        "Logs via tMSSqlOutput_1.\n"
        "#### JobB\n"
        "Logs via tDBOutput_1.\n"
    )
    errors = job_scoped_component_reference_errors(
        text,
        {"JobA": _JOB_A_REFS, "JobB": _JOB_B_REFS},
        _JOB_A_REFS | _JOB_B_REFS,
    )
    assert errors == []


def test_job_scoped_check_does_not_flag_genuine_cross_job_synthesis():
    # A heading that names no known unit (a cross-job summary, business-rule
    # heading, table-ownership discussion, ...) is unattributed and must fall
    # back to the combined task evidence -- not be rejected just because it
    # spans multiple jobs, and not silently exempted from checking either.
    text = (
        "#### JobA\n"
        "Logs via tMSSqlOutput_1.\n"
        "#### JobB\n"
        "Logs via tDBOutput_1.\n"
        "#### Table: LogInfo\n"
        "Jobs that write here: JobA via tMSSqlOutput_1, JobB via tDBOutput_1.\n"
    )
    errors = job_scoped_component_reference_errors(
        text,
        {"JobA": _JOB_A_REFS, "JobB": _JOB_B_REFS},
        _JOB_A_REFS | _JOB_B_REFS,
    )
    assert errors == []

    # But a genuinely unknown component in that same unattributed section is
    # still caught against the combined fallback evidence.
    bad_cross_job = text.replace("tMSSqlOutput_1, JobB", "tGhost_9, JobB")
    errors = job_scoped_component_reference_errors(
        bad_cross_job,
        {"JobA": _JOB_A_REFS, "JobB": _JOB_B_REFS},
        _JOB_A_REFS | _JOB_B_REFS,
    )
    assert len(errors) == 1
    assert "tGhost_9" in errors[0]


def test_job_scoped_check_no_headings_falls_back_to_task_wide():
    # Output with no '#### ' headings at all (degenerate/older shape): behave
    # exactly like the pre-existing whole-task check, no regression.
    text = "Logs via tMSSqlOutput_1 and tGhost_9.\n"
    errors = job_scoped_component_reference_errors(
        text,
        {"JobA": _JOB_A_REFS, "JobB": _JOB_B_REFS},
        _JOB_A_REFS | _JOB_B_REFS,
    )
    assert len(errors) == 1
    assert "tGhost_9" in errors[0]
    assert "tMSSqlOutput_1" not in errors[0].split(":")[1].split(".")[0]


def test_job_scoped_check_missing_unit_with_no_evidence_is_skipped():
    # A task's missing_units have no evidence anywhere -- an empty per-job
    # refs set disables the per-job check for that job (nothing to verify
    # against), matching component_reference_errors' existing semantics.
    text = "#### JobC\nClaims to use tGhost_9.\n"
    errors = job_scoped_component_reference_errors(
        text,
        {"JobC": frozenset()},
        frozenset(),
    )
    assert errors == []


def test_job_scoped_check_word_boundary_no_prefix_bleed():
    # 'AssetCheck_GetWorkOrder' must not swallow 'AssetCheck_GetWorkOrders'.
    refs_short = frozenset({"tDBOutput_1"})
    refs_long = frozenset({"tMSSqlOutput_9"})
    text = (
        "#### AssetCheck_GetWorkOrder\n"
        "Uses tDBOutput_1.\n"
        "#### AssetCheck_GetWorkOrders\n"
        "Uses tMSSqlOutput_9.\n"
    )
    errors = job_scoped_component_reference_errors(
        text,
        {
            "AssetCheck_GetWorkOrder": refs_short,
            "AssetCheck_GetWorkOrders": refs_long,
        },
        refs_short | refs_long,
    )
    assert errors == []


def _doc_text(extra: str) -> str:
    return "# Doc Title\n\n" + ("evidence-based prose. " * 20) + "\n" + extra


def test_validate_phase3_doc_merges_fabrication_errors(tmp_path: Path):
    provider = CustomHarnessProvider(MagicMock())
    doc = "01_TEMPLATE__G__J"
    provider._target_evidence[doc] = TargetEvidence(
        path=tmp_path / "_evidence.md",
        unit_name="J",
        component_refs=frozenset({"tJavaRow_1"}),
        categories=frozenset({"code"}),
        truncated=False,
    )
    bad = _doc_text("tSetGlobalVar_3 does things; also the code unavailable in scan.\n")
    report = provider._validate_phase3_doc(bad, doc)
    assert report["ok"] is False
    assert any("tSetGlobalVar_3" in e for e in report["errors"])
    assert any("CUSTOM CODE" in e for e in report["errors"])

    good = _doc_text("tJavaRow_1 stores the file name (see verbatim code).\n")
    assert provider._validate_phase3_doc(good, doc)["ok"] is True


def test_validate_phase3_doc_without_bundle_keeps_plain_validation():
    provider = CustomHarnessProvider(MagicMock())
    report = provider._validate_phase3_doc(_doc_text("mentions tGhost_9 freely"), "NO_BUNDLE_DOC")
    assert report["ok"] is True


def test_allowed_component_refs_and_merge(tmp_path: Path):
    evidence = tmp_path / "task.md"
    evidence.write_text("## Job: X\n- tJavaRow_1 reads\n- tFileCopy_1 moves\n", encoding="utf-8")
    refs = CustomHarnessProvider._allowed_component_refs([evidence, tmp_path / "missing.md"])
    assert refs == frozenset({"tJavaRow_1", "tFileCopy_1"})

    base = {"ok": True, "errors": [], "missing_units": []}
    merged = CustomHarnessProvider._merge_component_ref_errors(
        base, "output cites tGhost_9", refs, "this task's evidence file"
    )
    assert merged["ok"] is False
    assert any("tGhost_9" in e for e in merged["errors"])
    untouched = CustomHarnessProvider._merge_component_ref_errors(
        base, "output cites tJavaRow_1", refs, "this task's evidence file"
    )
    assert untouched["ok"] is True


def test_merge_component_ref_errors_job_scoped_catches_sibling_misattribution():
    # Same shape as the real production bug, exercised through the harness
    # entry point: allowed_refs (task-wide) accepts tMSSqlOutput_1 because it
    # is real for JobA, but job_refs must still catch it when the output
    # attributes it to JobB.
    base = {"ok": True, "errors": [], "missing_units": []}
    allowed_refs = _JOB_A_REFS | _JOB_B_REFS
    job_refs = {"JobA": _JOB_A_REFS, "JobB": _JOB_B_REFS}
    text = "#### JobA\nUses tMSSqlOutput_1.\n#### JobB\nUses tMSSqlOutput_1.\n"

    without_job_scoping = CustomHarnessProvider._merge_component_ref_errors(
        base, text, allowed_refs, "this task's evidence file"
    )
    assert without_job_scoping["ok"] is True  # the bug: passes task-wide

    with_job_scoping = CustomHarnessProvider._merge_component_ref_errors(
        base, text, allowed_refs, "this task's evidence file", job_refs=job_refs
    )
    assert with_job_scoping["ok"] is False
    assert any("JobB" in e and "tMSSqlOutput_1" in e for e in with_job_scoping["errors"])
