"""Atomic swap + maintenance for per-project docs directories.

Each project keeps three sibling dirs under `output/{project_id}/`:

- `docs/`         -- current published docs (read by RAG push, dashboard, users)
- `docs_staging/` -- in-flight generation target (Claude Code writes here)
- `docs_prev/`    -- transient backup kept only during a swap; used for rollback
                    on mid-swap failure and for startup crash-recovery.

`swap_docs()` replaces the old non-atomic `rmtree + rename` pair (which left
`docs/` missing if anything failed between the two calls). Both `os.rename`
operations are POSIX-atomic on the same filesystem, so a crash at any point
leaves a recoverable state.

`recover_stale_backups()` and `gc_abandoned_staging()` run at startup (see
`api/app.py` lifespan) to clean up anything left over from previous crashes.
"""

from __future__ import annotations

import logging
import os
import shutil
import time
from pathlib import Path

logger = logging.getLogger(__name__)


def swap_docs(output_dir: Path) -> None:
    """Atomically replace `docs/` with the contents of `docs_staging/`.

    Invariants
    ----------
    - On success: `docs/` contains the new generation, `docs_staging/` and
      `docs_prev/` no longer exist.
    - On mid-swap failure: `docs/` is restored to its pre-swap contents. The
      caller sees the original exception (re-raised).
    - On crash between the two renames: `docs_prev/` exists without `docs/`;
      `recover_stale_backups()` restores it on next startup.
    """
    docs_dir = output_dir / "docs"
    staging_dir = output_dir / "docs_staging"
    backup_dir = output_dir / "docs_prev"

    if not staging_dir.exists():
        raise FileNotFoundError(f"No staging dir to swap in: {staging_dir}")

    # Clear any leftover backup from a previous run so the rename target is free.
    if backup_dir.exists():
        shutil.rmtree(backup_dir, ignore_errors=True)

    # Rename-away old docs (atomic on same filesystem).
    had_old_docs = docs_dir.exists()
    if had_old_docs:
        os.rename(docs_dir, backup_dir)

    try:
        # Rename-in new docs (atomic on same filesystem).
        os.rename(staging_dir, docs_dir)
    except Exception:
        # Rollback — put the old docs back under `docs/`.
        if had_old_docs and backup_dir.exists() and not docs_dir.exists():
            try:
                os.rename(backup_dir, docs_dir)
            except Exception:
                logger.exception(
                    "Rollback failed; docs dir may be missing: %s", docs_dir
                )
        raise

    # Success path — best-effort cleanup of the backup.
    if backup_dir.exists():
        shutil.rmtree(backup_dir, ignore_errors=True)


def recover_stale_backups(output_root: Path) -> int:
    """Clean up `docs_prev/` dirs left over from previous crashes.

    Walks `output_root/*/` (each project) and:
    - If `docs_prev/` exists but `docs/` does NOT -> rename backup to docs
      (partial-swap recovery -- we crashed between the two renames).
    - If `docs_prev/` exists AND `docs/` exists -> delete backup (leftover
      from a previous successful swap that didn't get cleaned).

    Returns the number of project directories acted on.
    """
    if not output_root.exists():
        return 0

    handled = 0
    for project_dir in output_root.iterdir():
        if not project_dir.is_dir():
            continue
        backup_dir = project_dir / "docs_prev"
        if not backup_dir.exists():
            continue
        docs_dir = project_dir / "docs"
        if not docs_dir.exists():
            try:
                os.rename(backup_dir, docs_dir)
                logger.info("Recovered docs from backup: %s", docs_dir)
                handled += 1
            except Exception:
                logger.exception("Failed to recover backup at %s", backup_dir)
        else:
            shutil.rmtree(backup_dir, ignore_errors=True)
            logger.info("Removed stale backup: %s", backup_dir)
            handled += 1
    return handled


def gc_abandoned_staging(output_root: Path, max_age_days: int) -> int:
    """Delete `docs_staging/` dirs older than `max_age_days`.

    Resume-in-progress dirs get their mtime bumped when Claude Code writes to
    them, so anything older than the cutoff is a truly abandoned run.

    Returns the number of staging dirs removed.
    """
    if not output_root.exists() or max_age_days <= 0:
        return 0

    cutoff = time.time() - (max_age_days * 86400)
    removed = 0
    for project_dir in output_root.iterdir():
        if not project_dir.is_dir():
            continue
        staging_dir = project_dir / "docs_staging"
        if not staging_dir.exists():
            continue
        try:
            mtime = staging_dir.stat().st_mtime
        except OSError:
            continue
        if mtime < cutoff:
            shutil.rmtree(staging_dir, ignore_errors=True)
            logger.info(
                "GC removed abandoned staging (age %.1f days): %s",
                (time.time() - mtime) / 86400,
                staging_dir,
            )
            removed += 1
    return removed
