"""Tests for atomic_swap (swap, recovery, GC)."""

from __future__ import annotations

import os
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from mcp_server.services.atomic_swap import (
    gc_abandoned_staging,
    recover_stale_backups,
    swap_docs,
)


def _write(dir_path: Path, name: str, content: str) -> None:
    dir_path.mkdir(parents=True, exist_ok=True)
    (dir_path / name).write_text(content)


# ---- swap_docs -------------------------------------------------------------


def test_swap_docs_happy_path(tmp_path: Path) -> None:
    output_dir = tmp_path / "output" / "proj1"
    output_dir.mkdir(parents=True)
    _write(output_dir / "docs", "intro.md", "old")
    _write(output_dir / "docs_staging", "intro.md", "new")

    swap_docs(output_dir)

    assert (output_dir / "docs" / "intro.md").read_text() == "new"
    assert not (output_dir / "docs_staging").exists()
    assert not (output_dir / "docs_prev").exists()


def test_swap_docs_no_existing_docs(tmp_path: Path) -> None:
    output_dir = tmp_path / "output" / "proj2"
    output_dir.mkdir(parents=True)
    _write(output_dir / "docs_staging", "intro.md", "first run")

    swap_docs(output_dir)

    assert (output_dir / "docs" / "intro.md").read_text() == "first run"
    assert not (output_dir / "docs_staging").exists()
    assert not (output_dir / "docs_prev").exists()


def test_swap_docs_rollback_on_failure(tmp_path: Path) -> None:
    output_dir = tmp_path / "output" / "proj3"
    output_dir.mkdir(parents=True)
    _write(output_dir / "docs", "intro.md", "old")
    _write(output_dir / "docs_staging", "intro.md", "new")

    real_rename = os.rename
    call_count = {"n": 0}

    def flaky_rename(src, dst):
        call_count["n"] += 1
        # Let the first rename (docs -> docs_prev) succeed, then fail the
        # second (docs_staging -> docs) so rollback kicks in.
        if call_count["n"] == 2:
            raise OSError("simulated mid-swap failure")
        return real_rename(src, dst)

    with patch("mcp_server.services.atomic_swap.os.rename", side_effect=flaky_rename):
        with pytest.raises(OSError, match="simulated mid-swap failure"):
            swap_docs(output_dir)

    # Rollback should have restored the old docs.
    assert (output_dir / "docs" / "intro.md").read_text() == "old"
    assert not (output_dir / "docs_prev").exists()
    # Staging still sits there because the swap failed before the staging
    # rename succeeded -- caller can retry.
    assert (output_dir / "docs_staging" / "intro.md").read_text() == "new"


def test_swap_docs_missing_staging_raises(tmp_path: Path) -> None:
    output_dir = tmp_path / "output" / "proj4"
    output_dir.mkdir(parents=True)
    _write(output_dir / "docs", "intro.md", "old")

    with pytest.raises(FileNotFoundError):
        swap_docs(output_dir)

    # docs/ untouched because we bailed before the first rename.
    assert (output_dir / "docs" / "intro.md").read_text() == "old"


def test_swap_docs_clears_leftover_backup(tmp_path: Path) -> None:
    output_dir = tmp_path / "output" / "proj5"
    output_dir.mkdir(parents=True)
    _write(output_dir / "docs", "intro.md", "old")
    _write(output_dir / "docs_staging", "intro.md", "new")
    _write(output_dir / "docs_prev", "stale.md", "leftover from crash")

    swap_docs(output_dir)

    assert (output_dir / "docs" / "intro.md").read_text() == "new"
    assert not (output_dir / "docs_prev").exists()


# ---- recover_stale_backups ------------------------------------------------


def test_recover_stale_backups_restores_when_docs_missing(tmp_path: Path) -> None:
    output_root = tmp_path / "output"
    proj = output_root / "projA"
    proj.mkdir(parents=True)
    _write(proj / "docs_prev", "intro.md", "pre-crash")

    count = recover_stale_backups(output_root)

    assert count == 1
    assert (proj / "docs" / "intro.md").read_text() == "pre-crash"
    assert not (proj / "docs_prev").exists()


def test_recover_stale_backups_deletes_when_both_exist(tmp_path: Path) -> None:
    output_root = tmp_path / "output"
    proj = output_root / "projB"
    proj.mkdir(parents=True)
    _write(proj / "docs", "intro.md", "current")
    _write(proj / "docs_prev", "intro.md", "leftover")

    count = recover_stale_backups(output_root)

    assert count == 1
    assert (proj / "docs" / "intro.md").read_text() == "current"
    assert not (proj / "docs_prev").exists()


def test_recover_stale_backups_noop_when_no_backup(tmp_path: Path) -> None:
    output_root = tmp_path / "output"
    proj = output_root / "projC"
    proj.mkdir(parents=True)
    _write(proj / "docs", "intro.md", "current")

    count = recover_stale_backups(output_root)

    assert count == 0
    assert (proj / "docs" / "intro.md").read_text() == "current"


def test_recover_stale_backups_missing_root_is_ok(tmp_path: Path) -> None:
    # Nothing blows up if output/ doesn't exist yet.
    count = recover_stale_backups(tmp_path / "nonexistent")
    assert count == 0


# ---- gc_abandoned_staging -------------------------------------------------


def test_gc_removes_old_staging(tmp_path: Path) -> None:
    output_root = tmp_path / "output"
    proj = output_root / "projX"
    proj.mkdir(parents=True)
    staging = proj / "docs_staging"
    _write(staging, "wip.md", "abandoned")
    # Backdate mtime to 10 days ago.
    old = time.time() - (10 * 86400)
    os.utime(staging, (old, old))

    removed = gc_abandoned_staging(output_root, max_age_days=7)

    assert removed == 1
    assert not staging.exists()


def test_gc_preserves_recent_staging(tmp_path: Path) -> None:
    output_root = tmp_path / "output"
    proj = output_root / "projY"
    proj.mkdir(parents=True)
    staging = proj / "docs_staging"
    _write(staging, "wip.md", "fresh")

    removed = gc_abandoned_staging(output_root, max_age_days=7)

    assert removed == 0
    assert staging.exists()


def test_gc_zero_age_is_noop(tmp_path: Path) -> None:
    output_root = tmp_path / "output"
    proj = output_root / "projZ"
    proj.mkdir(parents=True)
    _write(proj / "docs_staging", "wip.md", "fresh")

    removed = gc_abandoned_staging(output_root, max_age_days=0)

    assert removed == 0
    assert (proj / "docs_staging").exists()
