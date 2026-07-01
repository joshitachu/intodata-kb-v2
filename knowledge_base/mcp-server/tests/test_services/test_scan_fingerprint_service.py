"""Unit tests for ScanFingerprintService.

Pure-function tests (compute_group_hash, compute_project_hashes) use only
tmp_path and require no DB. DB tests (load_baseline, diff_against_baseline,
persist_baseline) use the session_factory fixture from conftest.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path

import pytest

from mcp_server.services.project_registry import ProjectRegistry
from mcp_server.services.scan_fingerprint_service import ScanFingerprintService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_json(group_dir: Path, filename: str, content: str) -> None:
    group_dir.mkdir(parents=True, exist_ok=True)
    (group_dir / filename).write_text(content, encoding="utf-8")


def _make_scan_jobs(tmp_path: Path) -> Path:
    """Return a scan_jobs_dir with groups A and B."""
    jobs = tmp_path / "scan" / "jobs"
    _write_json(jobs / "A", "job1.json", '{"id": "a1"}')
    _write_json(jobs / "A", "job2.json", '{"id": "a2"}')
    _write_json(jobs / "B", "job3.json", '{"id": "b1"}')
    return jobs


_NOW = datetime(2026, 6, 30, 12, 0, 0, tzinfo=timezone.utc)


async def _create_project(session_factory, project_id: str, tmp_path: Path) -> None:
    """Create a minimal project row so FK constraints pass."""
    async with session_factory() as db:
        registry = ProjectRegistry(db)
        await registry.add_project(
            project_id,
            str(tmp_path / "repo"),
            repo_url=None,
            source_type="local",
        )
        await db.commit()


# ---------------------------------------------------------------------------
# compute_group_hash -- pure, no DB
# ---------------------------------------------------------------------------


def test_compute_group_hash_determinism(tmp_path: Path) -> None:
    """Same directory contents must always produce the same hash (RD-01 guard)."""
    group_dir = tmp_path / "A"
    _write_json(group_dir, "job.json", '{"key": "value"}')

    h1 = ScanFingerprintService.compute_group_hash(group_dir)
    h2 = ScanFingerprintService.compute_group_hash(group_dir)
    assert h1 == h2, "Identical directory must produce identical hashes"
    assert len(h1) == 64, "SHA-256 hex digest is 64 chars"


def test_compute_group_hash_changes_on_content_change(tmp_path: Path) -> None:
    """Hash must change when a file's content changes."""
    group_dir = tmp_path / "A"
    _write_json(group_dir, "job.json", '{"key": "value1"}')
    h1 = ScanFingerprintService.compute_group_hash(group_dir)

    (group_dir / "job.json").write_text('{"key": "value2"}', encoding="utf-8")
    h2 = ScanFingerprintService.compute_group_hash(group_dir)
    assert h1 != h2


def test_compute_group_hash_changes_on_file_rename(tmp_path: Path) -> None:
    """Renaming a file (same content) must produce a different hash."""
    group_dir = tmp_path / "A"
    content = '{"key": "value"}'
    _write_json(group_dir, "old_name.json", content)
    h1 = ScanFingerprintService.compute_group_hash(group_dir)

    (group_dir / "old_name.json").rename(group_dir / "new_name.json")
    h2 = ScanFingerprintService.compute_group_hash(group_dir)
    assert h1 != h2


def test_compute_group_hash_raises_on_missing_dir(tmp_path: Path) -> None:
    """FileNotFoundError must be raised when the group directory does not exist."""
    with pytest.raises(FileNotFoundError):
        ScanFingerprintService.compute_group_hash(tmp_path / "nonexistent")


def test_compute_group_hash_empty_dir(tmp_path: Path) -> None:
    """Empty group directory produces a stable zero-input hash."""
    group_dir = tmp_path / "A"
    group_dir.mkdir()
    h = ScanFingerprintService.compute_group_hash(group_dir)
    assert h == hashlib.sha256().hexdigest()


# ---------------------------------------------------------------------------
# compute_project_hashes -- pure, no DB
# ---------------------------------------------------------------------------


def test_compute_project_hashes_returns_correct_groups(tmp_path: Path) -> None:
    jobs = _make_scan_jobs(tmp_path)
    hashes = ScanFingerprintService.compute_project_hashes(jobs)
    assert set(hashes.keys()) == {"A", "B"}
    for h in hashes.values():
        assert len(h) == 64


def test_compute_project_hashes_returns_empty_for_nonexistent_dir(tmp_path: Path) -> None:
    """Non-existent scan_jobs_dir must return an empty dict."""
    hashes = ScanFingerprintService.compute_project_hashes(tmp_path / "missing")
    assert hashes == {}


def test_compute_project_hashes_determinism(tmp_path: Path) -> None:
    """Two calls on the same directory must return identical dicts."""
    jobs = _make_scan_jobs(tmp_path)
    h1 = ScanFingerprintService.compute_project_hashes(jobs)
    h2 = ScanFingerprintService.compute_project_hashes(jobs)
    assert h1 == h2


def test_compute_project_hashes_isolates_changed_group(tmp_path: Path) -> None:
    """Changing one group must only alter that group's hash."""
    jobs = _make_scan_jobs(tmp_path)
    before = ScanFingerprintService.compute_project_hashes(jobs)

    # Mutate group A only.
    (jobs / "A" / "job1.json").write_text('{"id": "changed"}', encoding="utf-8")
    after = ScanFingerprintService.compute_project_hashes(jobs)

    assert after["A"] != before["A"], "modified group hash must differ"
    assert after["B"] == before["B"], "unmodified group hash must be stable"


def test_compute_project_hashes_skips_files_at_top_level(tmp_path: Path) -> None:
    """A file directly under scan_jobs_dir (not a directory) is ignored."""
    jobs = tmp_path / "scan" / "jobs"
    jobs.mkdir(parents=True)
    _write_json(jobs / "A", "job.json", "{}")
    (jobs / "not_a_group.json").write_text("{}", encoding="utf-8")  # stray file

    hashes = ScanFingerprintService.compute_project_hashes(jobs)
    assert set(hashes.keys()) == {"A"}


# ---------------------------------------------------------------------------
# load_baseline + diff_against_baseline (DB tests)
# ---------------------------------------------------------------------------


async def test_load_baseline_returns_empty_when_no_rows(session_factory) -> None:
    async with session_factory() as db:
        svc = ScanFingerprintService(db)
        result = await svc.load_baseline("no_project")
    assert result == {}


async def test_diff_against_baseline_first_push_all_groups_dirty(
    session_factory, tmp_path: Path
) -> None:
    """No baseline rows -> is_first_push=True, all current groups in dirty set."""
    await _create_project(session_factory, "proj", tmp_path)
    current = {"A": "aaa", "B": "bbb"}
    async with session_factory() as db:
        svc = ScanFingerprintService(db)
        dirty, is_first = await svc.diff_against_baseline("proj", current)
    assert is_first is True
    assert dirty == {"A", "B"}


async def test_diff_against_baseline_no_change_after_baseline(
    session_factory, tmp_path: Path
) -> None:
    """Identical hashes vs stored baseline -> empty dirty set, not first push."""
    await _create_project(session_factory, "proj", tmp_path)
    current = {"A": "aaa", "B": "bbb"}
    async with session_factory() as db:
        svc = ScanFingerprintService(db)
        await svc.persist_baseline(db, "proj", current, _NOW)
        await db.commit()

    async with session_factory() as db:
        svc = ScanFingerprintService(db)
        dirty, is_first = await svc.diff_against_baseline("proj", current)
    assert is_first is False
    assert dirty == set()


async def test_diff_against_baseline_changed_group_detected(
    session_factory, tmp_path: Path
) -> None:
    """A group whose hash changed must appear in the dirty set."""
    await _create_project(session_factory, "proj", tmp_path)
    baseline = {"A": "old_hash", "B": "bbb"}
    current = {"A": "new_hash", "B": "bbb"}
    async with session_factory() as db:
        svc = ScanFingerprintService(db)
        await svc.persist_baseline(db, "proj", baseline, _NOW)
        await db.commit()

    async with session_factory() as db:
        svc = ScanFingerprintService(db)
        dirty, is_first = await svc.diff_against_baseline("proj", current)
    assert is_first is False
    assert dirty == {"A"}


async def test_diff_against_baseline_deleted_group_detected(
    session_factory, tmp_path: Path
) -> None:
    """A group absent from current but present in baseline is dirty (deletion)."""
    await _create_project(session_factory, "proj", tmp_path)
    baseline = {"A": "aaa", "B": "bbb"}
    current = {"A": "aaa"}  # B removed
    async with session_factory() as db:
        svc = ScanFingerprintService(db)
        await svc.persist_baseline(db, "proj", baseline, _NOW)
        await db.commit()

    async with session_factory() as db:
        svc = ScanFingerprintService(db)
        dirty, is_first = await svc.diff_against_baseline("proj", current)
    assert is_first is False
    assert dirty == {"B"}


async def test_diff_against_baseline_new_group_detected(
    session_factory, tmp_path: Path
) -> None:
    """A group in current but absent from baseline is dirty (addition)."""
    await _create_project(session_factory, "proj", tmp_path)
    baseline = {"A": "aaa"}
    current = {"A": "aaa", "B": "bbb"}  # B is new
    async with session_factory() as db:
        svc = ScanFingerprintService(db)
        await svc.persist_baseline(db, "proj", baseline, _NOW)
        await db.commit()

    async with session_factory() as db:
        svc = ScanFingerprintService(db)
        dirty, is_first = await svc.diff_against_baseline("proj", current)
    assert is_first is False
    assert dirty == {"B"}


# ---------------------------------------------------------------------------
# persist_baseline -- round-trip and deletion (DB tests)
# ---------------------------------------------------------------------------


async def test_persist_baseline_round_trip(session_factory, tmp_path: Path) -> None:
    """Persisted hashes must be exactly loadable."""
    await _create_project(session_factory, "proj", tmp_path)
    hashes = {"A": "a" * 64, "B": "b" * 64}
    async with session_factory() as db:
        svc = ScanFingerprintService(db)
        await svc.persist_baseline(db, "proj", hashes, _NOW)
        await db.commit()

    async with session_factory() as db:
        svc = ScanFingerprintService(db)
        loaded = await svc.load_baseline("proj")
    assert loaded == hashes


async def test_persist_baseline_deletes_removed_group(
    session_factory, tmp_path: Path
) -> None:
    """After persisting {A}, a previously stored row for B must be absent."""
    await _create_project(session_factory, "proj", tmp_path)
    initial = {"A": "aaa", "B": "bbb"}
    async with session_factory() as db:
        svc = ScanFingerprintService(db)
        await svc.persist_baseline(db, "proj", initial, _NOW)
        await db.commit()

    updated = {"A": "aaa"}  # B removed
    async with session_factory() as db:
        svc = ScanFingerprintService(db)
        await svc.persist_baseline(db, "proj", updated, _NOW)
        await db.commit()

    async with session_factory() as db:
        svc = ScanFingerprintService(db)
        loaded = await svc.load_baseline("proj")
    assert loaded == {"A": "aaa"}, "B row must have been deleted"


async def test_persist_baseline_empty_hashes_clears_all(
    session_factory, tmp_path: Path
) -> None:
    """Persisting an empty dict must delete all rows for the project."""
    await _create_project(session_factory, "proj", tmp_path)
    initial = {"A": "aaa", "B": "bbb"}
    async with session_factory() as db:
        svc = ScanFingerprintService(db)
        await svc.persist_baseline(db, "proj", initial, _NOW)
        await db.commit()

    async with session_factory() as db:
        svc = ScanFingerprintService(db)
        await svc.persist_baseline(db, "proj", {}, _NOW)
        await db.commit()

    async with session_factory() as db:
        svc = ScanFingerprintService(db)
        loaded = await svc.load_baseline("proj")
    assert loaded == {}


async def test_persist_baseline_upsert_updates_hash(
    session_factory, tmp_path: Path
) -> None:
    """A second persist with a different hash must update the stored value."""
    await _create_project(session_factory, "proj", tmp_path)
    async with session_factory() as db:
        svc = ScanFingerprintService(db)
        await svc.persist_baseline(db, "proj", {"A": "old"}, _NOW)
        await db.commit()

    async with session_factory() as db:
        svc = ScanFingerprintService(db)
        await svc.persist_baseline(db, "proj", {"A": "new"}, _NOW)
        await db.commit()

    async with session_factory() as db:
        svc = ScanFingerprintService(db)
        loaded = await svc.load_baseline("proj")
    assert loaded == {"A": "new"}


async def test_persist_baseline_project_isolation(
    session_factory, tmp_path: Path
) -> None:
    """Rows for project X must not affect project Y."""
    await _create_project(session_factory, "proj_x", tmp_path / "x")
    await _create_project(session_factory, "proj_y", tmp_path / "y")
    async with session_factory() as db:
        svc = ScanFingerprintService(db)
        await svc.persist_baseline(db, "proj_x", {"A": "aaa"}, _NOW)
        await svc.persist_baseline(db, "proj_y", {"B": "bbb"}, _NOW)
        await db.commit()

    async with session_factory() as db:
        svc = ScanFingerprintService(db)
        x = await svc.load_baseline("proj_x")
        y = await svc.load_baseline("proj_y")
    assert x == {"A": "aaa"}
    assert y == {"B": "bbb"}
