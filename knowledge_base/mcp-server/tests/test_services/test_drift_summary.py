"""Tests for the pure Slice 5 drift-summary fold (US-7/US-8)."""

from __future__ import annotations

from mcp_server.config import config
from mcp_server.services.drift_summary import (
    DriftSummary,
    compute_drift_summary,
    count_fact_check_failures,
)


# ---------------------------------------------------------------------------
# count_fact_check_failures
# ---------------------------------------------------------------------------


def test_count_fact_check_failures_returns_zero_for_empty_summary():
    assert count_fact_check_failures({}) == 0


def test_count_fact_check_failures_counts_group_fallback_reasons():
    summary = {"fallback_reasons": {"group_a": "diff_fidelity_failed", "group_b": "phase2_patch_error"}}
    assert count_fact_check_failures(summary) == 1


def test_count_fact_check_failures_counts_synthesis_fallback_docs():
    summary = {"synthesis_fallback_docs": {"09_EXECUTIVE_SUMMARY": "diff_fidelity_failed"}}
    assert count_fact_check_failures(summary) == 1


def test_count_fact_check_failures_sums_across_both_maps():
    summary = {
        "fallback_reasons": {"group_a": "diff_fidelity_failed", "group_b": "diff_fidelity_failed"},
        "synthesis_fallback_docs": {"01_ARCHITECTURE_MAP": "diff_fidelity_failed"},
    }
    assert count_fact_check_failures(summary) == 3


def test_count_fact_check_failures_ignores_non_diff_fidelity_reasons():
    summary = {
        "fallback_reasons": {"group_a": "g3_validation_failed"},
        "synthesis_fallback_docs": {"doc": "removal_incomplete"},
    }
    assert count_fact_check_failures(summary) == 0


def test_count_fact_check_failures_tolerates_wrong_types():
    assert count_fact_check_failures({"fallback_reasons": None, "synthesis_fallback_docs": "x"}) == 0
    assert count_fact_check_failures({"fallback_reasons": ["not", "a", "dict"]}) == 0


# ---------------------------------------------------------------------------
# compute_drift_summary
# ---------------------------------------------------------------------------


def _entry(
    *,
    outcome: str,
    reason_code: str | None = None,
    before_sha: str = "a" * 40,
    after_sha: str = "b" * 40,
    timestamp: str = "2026-07-01T00:00:00+00:00",
    failed_fact_checks: int = 0,
    skipped_docs: list[str] | None = None,
) -> dict:
    return {
        "outcome": outcome,
        "reason_code": reason_code,
        "before_sha": before_sha,
        "after_sha": after_sha,
        "timestamp": timestamp,
        "failed_fact_checks": failed_fact_checks,
        "skipped_docs": skipped_docs if skipped_docs is not None else [],
    }


def test_compute_drift_summary_empty_history_returns_zeroed_unreconciled_summary():
    result = compute_drift_summary([])
    assert result == DriftSummary()
    assert result.last_reconciled_after_sha is None


def test_compute_drift_summary_ac1_counts_n_amendments_since_last_rebuild():
    entries = [_entry(outcome="bounded_patch") for _ in range(4)]
    result = compute_drift_summary(entries)
    assert result.amendments_since_rebuild == 4


def test_compute_drift_summary_counts_targeted_and_full_regen_as_amendments_too():
    entries = [
        _entry(outcome="bounded_patch"),
        _entry(outcome="targeted_regen"),
        _entry(outcome="full_regen", reason_code="full_rebaseline_required"),
    ]
    result = compute_drift_summary(entries)
    assert result.amendments_since_rebuild == 3


def test_compute_drift_summary_ac2_sums_failed_fact_checks_across_pushes():
    entries = [
        _entry(outcome="bounded_patch", failed_fact_checks=1),
        _entry(outcome="bounded_patch", failed_fact_checks=2),
        _entry(outcome="bounded_patch", failed_fact_checks=0),
    ]
    result = compute_drift_summary(entries)
    assert result.failed_fact_checks_since_rebuild == 3
    # The push that failed a fact-check still counts as an amendment (the
    # patch executor fell back to scoped-local re-derive and something was
    # still written) -- AC1 and AC2 are independent counters, not exclusive.
    assert result.amendments_since_rebuild == 3


def test_compute_drift_summary_sums_skipped_docs():
    entries = [
        _entry(outcome="bounded_patch", skipped_docs=["01_ARCHITECTURE_MAP"]),
        _entry(outcome="bounded_patch", skipped_docs=["09_EXECUTIVE_SUMMARY", "02_ORDERS"]),
    ]
    result = compute_drift_summary(entries)
    assert result.skipped_since_rebuild == 3


def test_compute_drift_summary_reasoned_automatic_full_regen_does_not_reset(monkeypatch):
    """Critique Attack 2's sharpest falsifier: an AUTOMATIC, reasoned full_regen
    (full_rebaseline_required) must NOT be mistaken for the manual-rebuild reset --
    it still counts as an amendment and leaves last_reconciled_* untouched."""
    entries = [
        _entry(outcome="bounded_patch"),
        _entry(outcome="full_regen", reason_code="full_rebaseline_required", after_sha="c" * 40),
        _entry(outcome="bounded_patch"),
    ]
    result = compute_drift_summary(entries)
    assert result.amendments_since_rebuild == 3, (
        "reasoned automatic full_regen must count as an amendment, not reset the counters"
    )
    assert result.last_reconciled_after_sha is None, (
        "no manual rebuild ever happened in this history; last_reconciled must stay unknown"
    )


def test_compute_drift_summary_manual_rebuild_reason_code_resets_counters():
    entries = [
        _entry(outcome="bounded_patch"),
        _entry(outcome="bounded_patch"),
        _entry(
            outcome="full_regen",
            reason_code=config.manual_full_rebuild_reason_code,
            before_sha="d" * 40,
            after_sha="e" * 40,
            timestamp="2026-07-01T12:00:00+00:00",
        ),
    ]
    result = compute_drift_summary(entries)
    assert result.amendments_since_rebuild == 0, "the reset entry itself must not count as an amendment"
    assert result.failed_fact_checks_since_rebuild == 0
    assert result.skipped_since_rebuild == 0
    assert result.last_reconciled_before_sha == "d" * 40
    assert result.last_reconciled_after_sha == "e" * 40
    assert result.last_reconciled_at == "2026-07-01T12:00:00+00:00"


def test_compute_drift_summary_ac4_counters_reset_and_amendments_resume_after_manual_rebuild():
    entries = [
        _entry(outcome="bounded_patch"),
        _entry(outcome="bounded_patch", failed_fact_checks=1),
        _entry(outcome="full_regen", reason_code=config.manual_full_rebuild_reason_code, after_sha="f" * 40),
        _entry(outcome="bounded_patch"),
    ]
    result = compute_drift_summary(entries)
    assert result.amendments_since_rebuild == 1, "only pushes AFTER the reset should count"
    assert result.failed_fact_checks_since_rebuild == 0
    assert result.last_reconciled_after_sha == "f" * 40


def test_compute_drift_summary_multiple_manual_rebuilds_only_the_latest_wins():
    entries = [
        _entry(outcome="bounded_patch"),
        _entry(outcome="full_regen", reason_code=config.manual_full_rebuild_reason_code, after_sha="a1" * 20),
        _entry(outcome="bounded_patch"),
        _entry(outcome="full_regen", reason_code=config.manual_full_rebuild_reason_code, after_sha="b2" * 20),
        _entry(outcome="bounded_patch"),
    ]
    result = compute_drift_summary(entries)
    assert result.amendments_since_rebuild == 1
    assert result.last_reconciled_after_sha == "b2" * 20


def test_compute_drift_summary_tolerates_pre_slice5_entries_missing_new_fields():
    """Entries written by S4-only code (before this slice) lack
    `failed_fact_checks` / `skipped_docs` entirely -- must not raise, must
    treat as zero/empty."""
    legacy_entry = {
        "outcome": "bounded_patch",
        "reason_code": "true_patch_succeeded",
        "before_sha": "a" * 40,
        "after_sha": "b" * 40,
        "timestamp": "2026-06-01T00:00:00+00:00",
        "touched_docs": ["02_ORDERS"],
        # no failed_fact_checks, no skipped_docs keys at all
    }
    result = compute_drift_summary([legacy_entry])
    assert result.amendments_since_rebuild == 1
    assert result.failed_fact_checks_since_rebuild == 0
    assert result.skipped_since_rebuild == 0


def test_compute_drift_summary_ignores_non_dict_entries_defensively():
    result = compute_drift_summary([None, "garbage", 42, _entry(outcome="bounded_patch")])  # type: ignore[list-item]
    assert result.amendments_since_rebuild == 1


def test_compute_drift_summary_skip_outcome_never_reaches_history_but_is_ignored_if_present():
    # Defensive: even if a "skip" entry somehow appeared (should not per S4's
    # build_entry gating), it must not be counted as an amendment.
    entries = [_entry(outcome="skip"), _entry(outcome="bounded_patch")]
    result = compute_drift_summary(entries)
    assert result.amendments_since_rebuild == 1
