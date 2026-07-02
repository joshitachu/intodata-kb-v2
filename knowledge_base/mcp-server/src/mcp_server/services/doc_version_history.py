"""Doc version history -- Slice 4 (Living-Document Reingestion, US-9).

Tags every document written by an amendment or re-derive with the source
commit range that produced it, and maintains a per-project, append-only
change-history log of every push that actually changed documents.

Storage choice: project-scoped files under
``output/{project_id}/{config.doc_version_history_dirname}/`` --

- ``change_history.jsonl`` -- append-only log, one JSON object per line
  (ASCII-only). Never rewritten; new entries are opened with ``"a"`` and only
  ever add bytes at the end of the file.
- ``doc_commit_ranges.json`` -- a small derived index (NOT the source of
  truth -- the JSONL log is) mapping each document name, plus a special
  project-wide key, to the commit range of its most recent update. This is
  the rollback lookup (AC5). It is safe to rebuild from the JSONL log at any
  time; it is rewritten atomically (tmp file + ``os.replace``) on every
  ``append()`` call, matching the pattern already used by
  ``git_reingestion.persist_group_prev_scans`` for the ``prev_scan/`` sidecar.

See ``.plans/slice-4-versioning-history/plan.md`` for the full justification
(file-based vs. a DB table).

Design: the two pure pieces (``ChangeHistoryEntry`` formatting and the
``build_entry`` gating decision) have no I/O and are exhaustively unit
tested. ``DocVersionHistoryRepository`` is the thin I/O boundary (Repository
pattern) -- it never decides *whether* to write, only *how*.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mcp_server.config import config
from mcp_server.services.git_diff_patcher import (
    OUTCOME_BOUNDED_PATCH,
    OUTCOME_FULL_REGEN,
    OUTCOME_TARGETED_REGEN,
)

logger = logging.getLogger(__name__)

# Outcomes that represent a confirmed, successful write of one or more
# documents. The other three normalized outcomes -- "skip", "manual_review",
# and "failed" -- never correspond to a confirmed disk write in this
# codebase's outcome model: "skip" touches nothing by definition; a
# "manual_review"/"failed" push's `touched_docs` (when present at all) names
# *candidate/affected* docs from the routing decision, not docs actually
# rewritten on disk (see plan.md "Design decision: which outcomes create a
# history entry" for the full rationale, including the one known edge case
# this intentionally under-reports: a partially-successful True Patch run
# that is later escalated to manual review).
WRITE_OUTCOMES = frozenset({OUTCOME_BOUNDED_PATCH, OUTCOME_TARGETED_REGEN, OUTCOME_FULL_REGEN})


@dataclass(frozen=True)
class CommitRange:
    """A ``before_sha..after_sha`` range, as returned by the rollback lookup."""

    before_sha: str
    after_sha: str

    def to_dict(self) -> dict[str, str]:
        return {"before_sha": self.before_sha, "after_sha": self.after_sha}


@dataclass(frozen=True)
class ChangeHistoryEntry:
    """One append-only record of a push's effect on a project's documents.

    ``failed_fact_checks`` and ``skipped_docs`` (Slice 5, US-8) are additive
    fields with defaults -- they feed
    ``services/drift_summary.compute_drift_summary`` and do not change any
    existing S4 gating/formatting behavior. Both default to "nothing to
    report" so pre-Slice-5 call sites (and JSONL lines written before this
    slice) remain valid.
    """

    project_id: str
    before_sha: str
    after_sha: str
    outcome: str
    touched_docs: tuple[str, ...]
    timestamp: str
    reason_code: str | None = None
    sections: dict[str, tuple[str, ...]] = field(default_factory=dict)
    failed_fact_checks: int = 0
    skipped_docs: tuple[str, ...] = ()

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "before_sha": self.before_sha,
            "after_sha": self.after_sha,
            "outcome": self.outcome,
            "touched_docs": list(self.touched_docs),
            "sections": {name: list(values) for name, values in sorted(self.sections.items())},
            "reason_code": self.reason_code,
            "timestamp": self.timestamp,
            "failed_fact_checks": self.failed_fact_checks,
            "skipped_docs": sorted(self.skipped_docs),
        }

    def to_json_line(self) -> str:
        """Render one ASCII-only JSON line (no trailing newline)."""
        # ensure_ascii=True is load-bearing: change-history output must stay
        # Windows cp1252 safe (AC6), same rule as generated Markdown docs.
        return json.dumps(self.to_json_dict(), ensure_ascii=True, sort_keys=True)


def build_entry(
    *,
    project_id: str,
    before_sha: str | None,
    after_sha: str | None,
    outcome: str | None,
    touched_docs: list[str] | None,
    reason_code: str | None = None,
    sections: dict[str, list[str]] | None = None,
    timestamp: str | None = None,
    failed_fact_checks: int = 0,
    skipped_docs: list[str] | None = None,
) -> ChangeHistoryEntry | None:
    """Pure decision + formatting step.

    Returns ``None`` when this push must NOT be recorded: any outcome other
    than a confirmed write outcome (``WRITE_OUTCOMES``), or an empty/missing
    ``touched_docs`` list. No I/O; fully deterministic given its inputs (pass
    a fixed ``timestamp`` in tests that assert exact formatting).

    ``failed_fact_checks`` / ``skipped_docs`` (Slice 5) are additive,
    optional inputs -- callers that do not pass them get the S4 baseline
    behavior (0 / empty) unchanged.
    """
    normalized_outcome = (outcome or "").strip().lower()
    docs = tuple(d for d in (touched_docs or []) if d)
    if normalized_outcome not in WRITE_OUTCOMES or not docs:
        return None
    return ChangeHistoryEntry(
        project_id=project_id,
        before_sha=before_sha or "",
        after_sha=after_sha or "",
        outcome=normalized_outcome,
        touched_docs=docs,
        sections={name: tuple(values) for name, values in (sections or {}).items()},
        reason_code=reason_code,
        timestamp=timestamp or datetime.now(timezone.utc).isoformat(),
        failed_fact_checks=max(0, int(failed_fact_checks or 0)),
        skipped_docs=tuple(d for d in (skipped_docs or []) if d),
    )


class DocVersionHistoryRepository:
    """Repository for one project's append-only change history + rollback index.

    Thin I/O boundary: callers never touch the filesystem layout directly.
    """

    def __init__(self, project_id: str, output_dir: Path | None = None) -> None:
        self.project_id = project_id
        base = output_dir if output_dir is not None else (config.project_root / "output" / project_id)
        self._dir = base / config.doc_version_history_dirname
        self._history_path = self._dir / config.doc_version_history_log_filename
        self._sidecar_path = self._dir / config.doc_version_history_sidecar_filename

    @property
    def history_path(self) -> Path:
        return self._history_path

    @property
    def sidecar_path(self) -> Path:
        return self._sidecar_path

    def append(self, entry: ChangeHistoryEntry) -> None:
        """Append one entry to the JSONL log and refresh the rollback sidecar.

        The JSONL append opens with mode ``"a"`` (O_APPEND) and only ever
        writes the new line at EOF -- prior bytes are never touched (AC4).
        The sidecar is a derived index, fully rewritten via a tmp file +
        ``os.replace`` for atomicity (same pattern as
        ``git_reingestion.persist_group_prev_scans``).
        """
        self._dir.mkdir(parents=True, exist_ok=True)
        with open(self._history_path, "a", encoding="ascii", newline="\n") as fh:
            fh.write(entry.to_json_line())
            fh.write("\n")
        self._update_sidecar(entry)

    def _read_sidecar(self) -> dict[str, Any]:
        empty: dict[str, Any] = {"project_id": self.project_id, "project_range": None, "documents": {}}
        if not self._sidecar_path.is_file():
            return empty
        try:
            data = json.loads(self._sidecar_path.read_text(encoding="ascii"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning(
                "DocVersionHistory: cannot read sidecar for %s: %s", self.project_id, exc
            )
            return empty
        if not isinstance(data, dict):
            return empty
        data.setdefault("project_id", self.project_id)
        data.setdefault("project_range", None)
        data.setdefault("documents", {})
        return data

    def _update_sidecar(self, entry: ChangeHistoryEntry) -> None:
        data = self._read_sidecar()
        range_dict = {
            "before_sha": entry.before_sha,
            "after_sha": entry.after_sha,
            "updated_at": entry.timestamp,
            "outcome": entry.outcome,
        }
        data["project_id"] = self.project_id
        data["project_range"] = range_dict
        documents: dict[str, Any] = dict(data.get("documents") or {})
        for doc in entry.touched_docs:
            documents[doc] = dict(range_dict)
        data["documents"] = documents

        tmp_path = self._sidecar_path.with_name(self._sidecar_path.name + ".tmp")
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            tmp_path.write_text(
                json.dumps(data, ensure_ascii=True, sort_keys=True, indent=2),
                encoding="ascii",
            )
            os.replace(tmp_path, self._sidecar_path)
        except OSError as exc:
            logger.warning(
                "DocVersionHistory: cannot write sidecar for %s: %s", self.project_id, exc
            )

    def resolve(self, doc_name: str | None = None) -> CommitRange | None:
        """Rollback lookup.

        Resolve a document (or the whole project, when ``doc_name`` is
        ``None``) to the commit range of its last recorded update. Returns
        ``None`` when there is no history yet for that scope.
        """
        data = self._read_sidecar()
        record = data.get("project_range") if doc_name is None else data.get("documents", {}).get(doc_name)
        if not record:
            return None
        return CommitRange(
            before_sha=str(record.get("before_sha", "")),
            after_sha=str(record.get("after_sha", "")),
        )

    def read_all(self) -> list[dict[str, Any]]:
        """Read all history entries in append order (test/audit helper)."""
        if not self._history_path.is_file():
            return []
        entries: list[dict[str, Any]] = []
        for line in self._history_path.read_text(encoding="ascii").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return entries


def record_push_outcome(
    project_id: str,
    summary: dict[str, Any],
    *,
    output_dir: Path | None = None,
) -> ChangeHistoryEntry | None:
    """Single call-site helper for the persist choke point.

    Builds and appends a history entry from a finalized ``git_update_summary``
    dict -- the existing outcome summary already carries ``before``/``after``
    SHAs, ``outcome``, ``touched_docs``, and ``reason_code``, so it is used
    directly as the source of truth rather than recomputed. Best-effort: I/O
    failures are logged and swallowed, never raised, matching every other
    project-tree side-write in this module (``persist_group_prev_scans``, the
    fingerprint baseline writer). Returns the appended entry, or ``None`` when
    this push must not be recorded (AC3).

    Slice 5 (US-8): also derives ``failed_fact_checks`` from the summary's
    ``fallback_reasons`` / ``synthesis_fallback_docs`` maps (via the pure
    ``drift_summary.count_fact_check_failures``) and ``skipped_docs`` from
    ``synthesis_fallback_docs`` keys -- the summary documents the S3 mapper
    could not safely amend, left byte-unchanged pending a scoped-local
    re-derive (see ``true_patch_executor.py`` docstring at
    ``synthesis_fallback_docs``). NOTE: ``fallback_groups`` (scan groups
    rerouted from bounded_patch to scoped_regen) are deliberately NOT
    included here -- a rerouted group still gets its docs regenerated via
    the scoped-local path, so it is not "skipped" in the AC-8 sense: only
    ``synthesis_fallback_docs`` are left un-amended.
    """
    from mcp_server.services.drift_summary import count_fact_check_failures

    synthesis_fallback_docs = summary.get("synthesis_fallback_docs")
    skipped_docs = sorted(synthesis_fallback_docs.keys()) if isinstance(synthesis_fallback_docs, dict) else []
    entry = build_entry(
        project_id=project_id,
        before_sha=summary.get("before"),
        after_sha=summary.get("after"),
        outcome=summary.get("outcome"),
        touched_docs=summary.get("touched_docs"),
        reason_code=summary.get("reason_code"),
        sections=summary.get("doc_sections") if isinstance(summary.get("doc_sections"), dict) else None,
        failed_fact_checks=count_fact_check_failures(summary),
        skipped_docs=skipped_docs,
    )
    if entry is None:
        return None
    try:
        DocVersionHistoryRepository(project_id, output_dir).append(entry)
    except OSError as exc:
        logger.warning("DocVersionHistory: failed to record push outcome for %s: %s", project_id, exc)
        return None
    return entry
