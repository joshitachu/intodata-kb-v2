"""Drift summary -- Slice 5 (Living-Document Reingestion, US-7/US-8).

Because whole-repository rebuild is manual-only (RD-02), operators need a
mechanical, always-visible signal for how much has accumulated since the
last full rebuild, so they know WHEN to trigger one. This module computes
that signal by folding S4's append-only change history
(``DocVersionHistoryRepository.read_all()``) -- it introduces no new
storage (per the Slice 5 brief: "reuse it, do NOT duplicate history
storage").

Two pure pieces, both free of I/O so they are exhaustively unit-testable:

- ``count_fact_check_failures`` -- counts how many groups/docs in ONE push's
  finalized ``git_update_summary`` were rejected by the Slice-2 G5
  validate-against-diff guard (``reason_code == "diff_fidelity_failed"``),
  as recorded in ``fallback_reasons`` (per scan-group) and
  ``synthesis_fallback_docs`` (per summary-doc) by
  ``services/true_patch_executor.py``.
- ``compute_drift_summary`` -- folds the ordered list of history entries
  (as returned by ``DocVersionHistoryRepository.read_all()``) into a
  ``DriftSummary``.

Reset semantics (the hardest part -- see
``.plans/slice-5-drift-manual-rebuild/critique.md`` Attack 2): the fold
resets its running counters ONLY when it encounters an entry whose
``reason_code`` equals ``config.manual_full_rebuild_reason_code``. It does
NOT reset on a bare ``outcome == "full_regen"``, because Slice 1's
*automatic, reasoned* rebaseline path (missing traceability manifest,
cache/support mismatch -- ``reason_code="full_rebaseline_required"``) also
produces a ``full_regen`` outcome, and that path is an accepted data-
integrity fallback, not an operator action -- treating it as a reset would
silently hide drift from the operator, defeating the entire point of this
slice (US-8).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from mcp_server.config import config

# Outcomes that represent one push's worth of "amendment" for drift-counting
# purposes. Mirrors doc_version_history.WRITE_OUTCOMES (the set of outcomes
# that ever produce a history entry at all) -- every entry already in the
# history is, by construction, one of these, but the constant is named
# locally so this module's intent reads standalone and does not silently
# drift if WRITE_OUTCOMES is ever narrowed for a different (recording)
# purpose than drift-counting.
_AMENDMENT_OUTCOMES = frozenset({"bounded_patch", "targeted_regen", "full_regen"})


@dataclass(frozen=True)
class DriftSummary:
    """Mechanical drift counters since the last fully-reconciled rebuild.

    ``last_reconciled_*`` are ``None`` when the history contains no manual
    full-rebuild entry yet (never fully reconciled since history began) --
    an honest "unknown" rather than a fabricated zero SHA.
    """

    amendments_since_rebuild: int = 0
    failed_fact_checks_since_rebuild: int = 0
    skipped_since_rebuild: int = 0
    last_reconciled_before_sha: str | None = None
    last_reconciled_after_sha: str | None = None
    last_reconciled_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "amendments_since_rebuild": self.amendments_since_rebuild,
            "failed_fact_checks_since_rebuild": self.failed_fact_checks_since_rebuild,
            "skipped_since_rebuild": self.skipped_since_rebuild,
            "last_reconciled_before_sha": self.last_reconciled_before_sha,
            "last_reconciled_after_sha": self.last_reconciled_after_sha,
            "last_reconciled_at": self.last_reconciled_at,
        }


def count_fact_check_failures(summary: dict[str, Any]) -> int:
    """Count S2 G5 diff-fidelity rejections recorded in one push's summary.

    Pure; no I/O. Looks at two independent maps a finalized
    ``git_update_summary`` may carry (both survive untouched into the
    persisted summary -- see plan.md "root-cause / seam verification"):

    - ``fallback_reasons``: ``{scan_group: reason_code}`` (Slice 2/4,
      ``true_patch_executor.py`` bounded-patch lane).
    - ``synthesis_fallback_docs``: ``{doc_name: reason_code}`` (Slice 3,
      summary-doc amendment lane).

    Each entry whose ``reason_code`` is ``"diff_fidelity_failed"`` counts
    once, regardless of which map it came from -- a single push may reject
    more than one group/doc.
    """
    total = 0
    for mapping_key in ("fallback_reasons", "synthesis_fallback_docs"):
        mapping = summary.get(mapping_key)
        if not isinstance(mapping, dict):
            continue
        total += sum(1 for reason in mapping.values() if reason == "diff_fidelity_failed")
    return total


def compute_drift_summary(entries: list[dict[str, Any]]) -> DriftSummary:
    """Fold append-order change-history entries into a ``DriftSummary``.

    ``entries`` is exactly the shape returned by
    ``DocVersionHistoryRepository.read_all()`` -- a list of the parsed JSONL
    dicts, oldest first. Pure; no filesystem access (the caller reads the
    repository; this function only aggregates already-loaded dicts).

    Tolerant of entries written before Slice 5 (missing
    ``failed_fact_checks`` / ``skipped_docs`` keys) -- both default to
    empty/zero rather than raising, so upgrading mid-history is safe.
    """
    amendments = 0
    failed_checks = 0
    skipped = 0
    last_before: str | None = None
    last_after: str | None = None
    last_at: str | None = None

    for entry in entries:
        if not isinstance(entry, dict):
            continue
        outcome = str(entry.get("outcome") or "").strip().lower()
        reason_code = entry.get("reason_code")

        if reason_code == config.manual_full_rebuild_reason_code:
            # Operator-triggered manual full rebuild: reset the running
            # counters and advance the reconciled baseline to THIS entry.
            # The reset entry itself is the new baseline, not an amendment
            # counted against it (critique.md Lens "double-counting").
            amendments = 0
            failed_checks = 0
            skipped = 0
            last_before = entry.get("before_sha") or None
            last_after = entry.get("after_sha") or None
            last_at = entry.get("timestamp") or None
            continue

        if outcome in _AMENDMENT_OUTCOMES:
            amendments += 1

        failed_checks += int(entry.get("failed_fact_checks") or 0)
        skipped_docs = entry.get("skipped_docs")
        if isinstance(skipped_docs, (list, tuple)):
            skipped += len(skipped_docs)

    return DriftSummary(
        amendments_since_rebuild=amendments,
        failed_fact_checks_since_rebuild=failed_checks,
        skipped_since_rebuild=skipped,
        last_reconciled_before_sha=last_before,
        last_reconciled_after_sha=last_after,
        last_reconciled_at=last_at,
    )
