"""Tests for Slice 4 doc versioning + change history (US-9)."""

from __future__ import annotations

import json

import pytest

from mcp_server.services.doc_version_history import (
    ChangeHistoryEntry,
    CommitRange,
    DocVersionHistoryRepository,
    build_entry,
    record_push_outcome,
)

FIXED_TS = "2026-07-01T00:00:00+00:00"


# ---------------------------------------------------------------------------
# build_entry -- pure gating + formatting decision
# ---------------------------------------------------------------------------


def test_build_entry_returns_none_for_skip_outcome():
    entry = build_entry(
        project_id="proj",
        before_sha="a" * 40,
        after_sha="b" * 40,
        outcome="skip",
        touched_docs=["01_ARCHITECTURE_MAP.md"],  # even if present, skip never touches docs
        timestamp=FIXED_TS,
    )
    assert entry is None


@pytest.mark.parametrize("outcome", ["manual_review", "failed", "unknown", "", None])
def test_build_entry_returns_none_for_non_write_outcomes(outcome):
    entry = build_entry(
        project_id="proj",
        before_sha="a" * 40,
        after_sha="b" * 40,
        outcome=outcome,
        touched_docs=["01_ARCHITECTURE_MAP.md"],
        timestamp=FIXED_TS,
    )
    assert entry is None


@pytest.mark.parametrize("outcome", ["bounded_patch", "targeted_regen", "full_regen"])
def test_build_entry_returns_none_when_touched_docs_empty(outcome):
    assert build_entry(
        project_id="proj",
        before_sha="a" * 40,
        after_sha="b" * 40,
        outcome=outcome,
        touched_docs=[],
        timestamp=FIXED_TS,
    ) is None
    assert build_entry(
        project_id="proj",
        before_sha="a" * 40,
        after_sha="b" * 40,
        outcome=outcome,
        touched_docs=None,
        timestamp=FIXED_TS,
    ) is None


@pytest.mark.parametrize("outcome", ["bounded_patch", "targeted_regen", "full_regen"])
def test_build_entry_builds_for_write_outcomes_with_touched_docs(outcome):
    entry = build_entry(
        project_id="proj",
        before_sha="a" * 40,
        after_sha="b" * 40,
        outcome=outcome,
        touched_docs=["01_ARCHITECTURE_MAP.md", "", "02_DATA_FLOWS.md"],
        reason_code="true_patch_succeeded",
        timestamp=FIXED_TS,
    )
    assert entry is not None
    assert entry.project_id == "proj"
    assert entry.before_sha == "a" * 40
    assert entry.after_sha == "b" * 40
    assert entry.outcome == outcome
    # Blank doc names are dropped.
    assert entry.touched_docs == ("01_ARCHITECTURE_MAP.md", "02_DATA_FLOWS.md")
    assert entry.reason_code == "true_patch_succeeded"
    assert entry.timestamp == FIXED_TS


def test_build_entry_is_case_insensitive_on_outcome():
    entry = build_entry(
        project_id="proj",
        before_sha="a" * 40,
        after_sha="b" * 40,
        outcome="BOUNDED_PATCH",
        touched_docs=["01_ARCHITECTURE_MAP.md"],
        timestamp=FIXED_TS,
    )
    assert entry is not None
    assert entry.outcome == "bounded_patch"


def test_build_entry_defaults_missing_shas_to_empty_string():
    entry = build_entry(
        project_id="proj",
        before_sha=None,
        after_sha=None,
        outcome="full_regen",
        touched_docs=["01_ARCHITECTURE_MAP.md"],
        timestamp=FIXED_TS,
    )
    assert entry is not None
    assert entry.before_sha == ""
    assert entry.after_sha == ""


# ---------------------------------------------------------------------------
# ChangeHistoryEntry formatting -- ASCII-only output
# ---------------------------------------------------------------------------


def test_to_json_line_is_ascii_only_and_valid_json():
    entry = ChangeHistoryEntry(
        project_id="proj",
        before_sha="a" * 40,
        after_sha="b" * 40,
        outcome="bounded_patch",
        touched_docs=("café_notes.md",),  # non-ASCII input must be escaped, not dropped
        timestamp=FIXED_TS,
        reason_code="true_patch_succeeded",
        sections={"café_notes.md": ("Overview",)},
    )
    line = entry.to_json_line()
    assert line.isascii()
    parsed = json.loads(line)
    assert parsed["touched_docs"] == ["café_notes.md"]
    assert parsed["sections"] == {"café_notes.md": ["Overview"]}
    assert parsed["outcome"] == "bounded_patch"
    assert parsed["reason_code"] == "true_patch_succeeded"
    assert parsed["timestamp"] == FIXED_TS


# ---------------------------------------------------------------------------
# DocVersionHistoryRepository -- I/O boundary
# ---------------------------------------------------------------------------


def _entry(project_id="proj", before="a" * 40, after="b" * 40, docs=("01_ARCHITECTURE_MAP.md",), ts=FIXED_TS, outcome="bounded_patch"):
    return ChangeHistoryEntry(
        project_id=project_id,
        before_sha=before,
        after_sha=after,
        outcome=outcome,
        touched_docs=tuple(docs),
        timestamp=ts,
    )


def test_append_creates_history_file_with_one_ascii_line(tmp_path):
    repo = DocVersionHistoryRepository("proj", tmp_path)
    repo.append(_entry())

    assert repo.history_path.is_file()
    raw = repo.history_path.read_bytes()
    assert raw.decode("ascii")  # raises if any non-ASCII byte snuck in
    lines = raw.decode("ascii").splitlines()
    assert len(lines) == 1
    parsed = json.loads(lines[0])
    assert parsed["touched_docs"] == ["01_ARCHITECTURE_MAP.md"]


def test_append_is_append_only_never_rewrites_prior_entries(tmp_path):
    repo = DocVersionHistoryRepository("proj", tmp_path)
    repo.append(_entry(before="a" * 40, after="b" * 40, docs=["01_ARCHITECTURE_MAP.md"], ts="2026-07-01T00:00:00+00:00"))
    first_bytes = repo.history_path.read_bytes()

    repo.append(_entry(before="b" * 40, after="c" * 40, docs=["02_DATA_FLOWS.md"], ts="2026-07-02T00:00:00+00:00"))
    second_bytes = repo.history_path.read_bytes()

    # The first entry's bytes are an unmodified prefix of the file after the
    # second append -- prior entries are never rewritten or deleted (AC4).
    assert second_bytes.startswith(first_bytes)
    lines = second_bytes.decode("ascii").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["after_sha"] == "b" * 40
    assert json.loads(lines[1])["after_sha"] == "c" * 40


def test_append_updates_sidecar_for_touched_docs_and_project(tmp_path):
    repo = DocVersionHistoryRepository("proj", tmp_path)
    repo.append(_entry(before="a" * 40, after="b" * 40, docs=["01_ARCHITECTURE_MAP.md", "02_DATA_FLOWS.md"]))

    doc_range = repo.resolve("01_ARCHITECTURE_MAP.md")
    assert doc_range == CommitRange(before_sha="a" * 40, after_sha="b" * 40)

    other_doc_range = repo.resolve("02_DATA_FLOWS.md")
    assert other_doc_range == CommitRange(before_sha="a" * 40, after_sha="b" * 40)

    project_range = repo.resolve(None)
    assert project_range == CommitRange(before_sha="a" * 40, after_sha="b" * 40)

    assert repo.resolve("99_NEVER_TOUCHED.md") is None


def test_resolve_returns_latest_update_per_document(tmp_path):
    repo = DocVersionHistoryRepository("proj", tmp_path)
    repo.append(_entry(before="a" * 40, after="b" * 40, docs=["01_ARCHITECTURE_MAP.md"], ts="2026-07-01T00:00:00+00:00"))
    repo.append(_entry(before="b" * 40, after="c" * 40, docs=["02_DATA_FLOWS.md"], ts="2026-07-02T00:00:00+00:00"))

    # 01_ARCHITECTURE_MAP was not touched by the second push -- its recorded
    # range must still reflect its own last update, not the whole project's.
    assert repo.resolve("01_ARCHITECTURE_MAP.md") == CommitRange(before_sha="a" * 40, after_sha="b" * 40)
    assert repo.resolve("02_DATA_FLOWS.md") == CommitRange(before_sha="b" * 40, after_sha="c" * 40)
    # Project-level range reflects the most recent push overall.
    assert repo.resolve(None) == CommitRange(before_sha="b" * 40, after_sha="c" * 40)


def test_resolve_with_no_history_returns_none(tmp_path):
    repo = DocVersionHistoryRepository("proj", tmp_path)
    assert repo.resolve(None) is None
    assert repo.resolve("01_ARCHITECTURE_MAP.md") is None


def test_read_all_returns_entries_in_append_order(tmp_path):
    repo = DocVersionHistoryRepository("proj", tmp_path)
    repo.append(_entry(after="b" * 40, ts="2026-07-01T00:00:00+00:00"))
    repo.append(_entry(before="b" * 40, after="c" * 40, ts="2026-07-02T00:00:00+00:00"))

    entries = repo.read_all()
    assert [e["after_sha"] for e in entries] == ["b" * 40, "c" * 40]


def test_history_and_sidecar_paths_are_under_project_output_dir(tmp_path):
    repo = DocVersionHistoryRepository("proj", tmp_path)
    assert repo.history_path.parent == repo.sidecar_path.parent
    assert repo.history_path.parent.parent == tmp_path
    assert repo.history_path.name == "change_history.jsonl"
    assert repo.sidecar_path.name == "doc_commit_ranges.json"


# ---------------------------------------------------------------------------
# record_push_outcome -- the choke-point helper
# ---------------------------------------------------------------------------


def test_record_push_outcome_skips_skip_summary(tmp_path):
    summary = {
        "before": "a" * 40,
        "after": "b" * 40,
        "outcome": "skip",
        "touched_docs": [],
        "reason_code": "fingerprint_no_change",
    }
    result = record_push_outcome("proj", summary, output_dir=tmp_path)
    assert result is None
    repo = DocVersionHistoryRepository("proj", tmp_path)
    assert not repo.history_path.exists()
    assert not repo.sidecar_path.exists()


def test_record_push_outcome_skips_manual_review_summary(tmp_path):
    # manual_review summaries may carry candidate `touched_docs` (the router's
    # affected-docs list) even though nothing was actually written -- must
    # not be recorded (see plan.md design decision).
    summary = {
        "before": "a" * 40,
        "after": "b" * 40,
        "outcome": "manual_review",
        "touched_docs": ["01_ARCHITECTURE_MAP.md"],
        "reason_code": "full_rebaseline_required",
    }
    result = record_push_outcome("proj", summary, output_dir=tmp_path)
    assert result is None
    repo = DocVersionHistoryRepository("proj", tmp_path)
    assert not repo.history_path.exists()


def test_record_push_outcome_appends_entry_for_bounded_patch(tmp_path):
    summary = {
        "before": "a" * 40,
        "after": "b" * 40,
        "outcome": "bounded_patch",
        "action": "true_patch",
        "touched_docs": ["04_BUSINESS_RULES_AND_TRANSFORMATIONS.md"],
        "reason_code": "true_patch_succeeded",
    }
    result = record_push_outcome("proj", summary, output_dir=tmp_path)
    assert result is not None
    assert result.outcome == "bounded_patch"
    assert result.touched_docs == ("04_BUSINESS_RULES_AND_TRANSFORMATIONS.md",)

    repo = DocVersionHistoryRepository("proj", tmp_path)
    entries = repo.read_all()
    assert len(entries) == 1
    assert entries[0]["outcome"] == "bounded_patch"
    assert repo.resolve("04_BUSINESS_RULES_AND_TRANSFORMATIONS.md") == CommitRange(
        before_sha="a" * 40, after_sha="b" * 40
    )


def test_record_push_outcome_is_exactly_one_entry_per_push(tmp_path):
    summary = {
        "before": "a" * 40,
        "after": "b" * 40,
        "outcome": "targeted_regen",
        "touched_docs": ["01_ARCHITECTURE_MAP.md", "02_DATA_FLOWS.md"],
        "reason_code": "targeted_regen_succeeded",
    }
    record_push_outcome("proj", summary, output_dir=tmp_path)

    repo = DocVersionHistoryRepository("proj", tmp_path)
    entries = repo.read_all()
    # One push naming two touched docs -> exactly one change-history entry.
    assert len(entries) == 1
    assert entries[0]["touched_docs"] == ["01_ARCHITECTURE_MAP.md", "02_DATA_FLOWS.md"]


# ---------------------------------------------------------------------------
# Slice 5 (US-8) additive fields -- failed_fact_checks / skipped_docs.
# Additive only: every assertion above this line is unmodified S4 behavior.
# ---------------------------------------------------------------------------


def test_build_entry_defaults_failed_fact_checks_and_skipped_docs_to_empty():
    entry = build_entry(
        project_id="proj",
        before_sha="a" * 40,
        after_sha="b" * 40,
        outcome="bounded_patch",
        touched_docs=["doc.md"],
        timestamp=FIXED_TS,
    )
    assert entry is not None
    assert entry.failed_fact_checks == 0
    assert entry.skipped_docs == ()
    assert entry.to_json_dict()["failed_fact_checks"] == 0
    assert entry.to_json_dict()["skipped_docs"] == []


def test_build_entry_carries_explicit_failed_fact_checks_and_skipped_docs():
    entry = build_entry(
        project_id="proj",
        before_sha="a" * 40,
        after_sha="b" * 40,
        outcome="bounded_patch",
        touched_docs=["doc.md"],
        timestamp=FIXED_TS,
        failed_fact_checks=2,
        skipped_docs=["09_EXECUTIVE_SUMMARY", "group_a"],
    )
    assert entry is not None
    assert entry.failed_fact_checks == 2
    assert entry.skipped_docs == ("09_EXECUTIVE_SUMMARY", "group_a")
    assert entry.to_json_dict()["skipped_docs"] == ["09_EXECUTIVE_SUMMARY", "group_a"]


def test_record_push_outcome_derives_failed_fact_checks_from_fallback_reasons(tmp_path):
    summary = {
        "before": "a" * 40,
        "after": "b" * 40,
        "outcome": "bounded_patch",
        "touched_docs": ["02_ORDERS.md"],
        "reason_code": "true_patch_succeeded",
        "fallback_reasons": {"group_x": "diff_fidelity_failed", "group_y": "g3_validation_failed"},
    }
    record_push_outcome("proj", summary, output_dir=tmp_path)

    repo = DocVersionHistoryRepository("proj", tmp_path)
    entries = repo.read_all()
    assert len(entries) == 1
    assert entries[0]["failed_fact_checks"] == 1
    # fallback_reasons names REROUTED scan groups (still regenerated via the
    # scoped-local path), not skipped docs -- only synthesis_fallback_docs
    # (left byte-unchanged) count as "skipped" (see next test).
    assert entries[0]["skipped_docs"] == []


def test_record_push_outcome_derives_skipped_docs_from_synthesis_fallback_docs(tmp_path):
    summary = {
        "before": "a" * 40,
        "after": "b" * 40,
        "outcome": "bounded_patch",
        "touched_docs": ["02_ORDERS.md"],
        "reason_code": "true_patch_succeeded",
        "synthesis_fallback_docs": {"09_EXECUTIVE_SUMMARY": "diff_fidelity_failed"},
    }
    record_push_outcome("proj", summary, output_dir=tmp_path)

    repo = DocVersionHistoryRepository("proj", tmp_path)
    entries = repo.read_all()
    assert entries[0]["failed_fact_checks"] == 1
    assert entries[0]["skipped_docs"] == ["09_EXECUTIVE_SUMMARY"]


def test_record_push_outcome_with_no_fallbacks_reports_zero_and_empty(tmp_path):
    summary = {
        "before": "a" * 40,
        "after": "b" * 40,
        "outcome": "bounded_patch",
        "touched_docs": ["02_ORDERS.md"],
        "reason_code": "true_patch_succeeded",
    }
    record_push_outcome("proj", summary, output_dir=tmp_path)

    repo = DocVersionHistoryRepository("proj", tmp_path)
    entries = repo.read_all()
    assert entries[0]["failed_fact_checks"] == 0
    assert entries[0]["skipped_docs"] == []
