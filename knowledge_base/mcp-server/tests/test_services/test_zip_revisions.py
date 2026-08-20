"""Focused fake-backed coverage for immutable ZIP replacement candidates."""

from __future__ import annotations

import hashlib
import itertools
from pathlib import Path

import pytest

from mcp_server.services.living_docs.zip_revisions import (
    ZipRevisionService,
    ZipRevisionSubmission,
)
from mcp_server.services.living_docs.zip_patch_lifecycle import LiveZipPatchCoordinator
from mcp_server.services.log_manager import LogManager

_upload_counter = itertools.count()


def _extract_fixture(_archive: Path, destination: Path) -> None:
    (destination / "source.txt").write_text("candidate source", encoding="utf-8")


def _stage_bytes(uploads_dir: Path, content: bytes) -> tuple[Path, str]:
    """Simulate a streamed upload: write bytes to a temp file, hash them.

    ``stage()`` now takes ownership of an already-on-disk file plus its
    precomputed hash (matching how the HTTP handlers stream uploads to disk),
    instead of a raw ``bytes`` buffer.
    """
    uploads_dir.mkdir(parents=True, exist_ok=True)
    path = uploads_dir / f"upload-{next(_upload_counter)}.zip"
    path.write_bytes(content)
    return path, hashlib.sha256(content).hexdigest()


class _Coordinator:
    def __init__(self, result: ZipRevisionSubmission | Exception) -> None:
        self.result = result
        self.seen = []

    async def submit_zip_revision(self, candidate):
        self.seen.append(candidate)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


async def test_zip_bytes_stage_in_an_immutable_candidate_and_replay_is_noop(tmp_path) -> None:
    service = ZipRevisionService(tmp_path / "candidates")
    uploads = tmp_path / "uploads"

    path1, sha1 = _stage_bytes(uploads, b"archive bytes")
    first = await service.stage(
        project_id="project-a",
        filename="export.zip",
        staged_archive_path=path1,
        archive_sha256=sha1,
        extractor=_extract_fixture,
    )
    path2, sha2 = _stage_bytes(uploads, b"archive bytes")
    replay = await service.stage(
        project_id="project-a",
        filename="renamed.zip",
        staged_archive_path=path2,
        archive_sha256=sha2,
        extractor=_extract_fixture,
    )

    assert first.no_op is False
    assert first.source_root != tmp_path / "active"
    assert (first.source_root / "source.txt").read_text(encoding="utf-8") == "candidate source"
    assert first.manifest_path.is_file()
    assert replay.no_op is True
    assert replay.candidate_id == first.candidate_id
    assert replay.source_revision.content_sha256 == first.archive_sha256
    # stage() takes ownership of the caller's staged file: moved on success,
    # discarded when a no-op dedup short-circuit fires.
    assert not path1.exists()
    assert not path2.exists()


async def test_unconfigured_or_failed_handoff_never_changes_active_source(tmp_path) -> None:
    active = tmp_path / "active"
    active.mkdir()
    active_file = active / "source.txt"
    active_file.write_text("active source", encoding="utf-8")

    uploads = tmp_path / "uploads"
    held_service = ZipRevisionService(tmp_path / "held")
    path1, sha1 = _stage_bytes(uploads, b"new archive")
    candidate = await held_service.stage(
        project_id="project-a",
        filename="export.zip",
        staged_archive_path=path1,
        archive_sha256=sha1,
        extractor=_extract_fixture,
    )
    held = await held_service.submit(candidate)
    assert held == ZipRevisionSubmission(status="held", reason_code="zip_coordinator_not_configured")
    assert active_file.read_text(encoding="utf-8") == "active source"

    coordinator = _Coordinator(RuntimeError("S4 unavailable"))
    failed_service = ZipRevisionService(tmp_path / "failed", coordinator=coordinator)
    path2, sha2 = _stage_bytes(uploads, b"other archive")
    failed_candidate = await failed_service.stage(
        project_id="project-a",
        filename="export.zip",
        staged_archive_path=path2,
        archive_sha256=sha2,
        extractor=_extract_fixture,
    )
    failed = await failed_service.submit(failed_candidate)
    assert failed == ZipRevisionSubmission(status="failed", reason_code="zip_coordinator_failed")
    assert coordinator.seen == [failed_candidate]
    assert active_file.read_text(encoding="utf-8") == "active source"


async def test_failed_extraction_does_not_leave_a_candidate_root(tmp_path) -> None:
    service = ZipRevisionService(tmp_path / "candidates")

    def bad_extractor(_archive: Path, _destination: Path) -> None:
        raise ValueError("bad archive")

    path, sha = _stage_bytes(tmp_path / "uploads", b"bad archive")
    with pytest.raises(ValueError, match="bad archive"):
        await service.stage(
            project_id="project-a",
            filename="export.zip",
            staged_archive_path=path,
            archive_sha256=sha,
            extractor=bad_extractor,
        )

    assert not list((tmp_path / "candidates" / "project-a").glob("zip-*"))
    # The caller's staged file is cleaned up on failure, not left behind.
    assert not path.exists()


async def test_live_zip_lifecycle_holds_while_candidate_patching_is_disabled(tmp_path, monkeypatch) -> None:
    """The route can be installed safely before the controlled rollout flag."""
    from mcp_server.config import config

    monkeypatch.setattr(config, "living_doc_patching_enabled", False)
    service = ZipRevisionService(
        tmp_path / "candidates",
        coordinator=LiveZipPatchCoordinator(log_mgr=LogManager()),
    )
    path, sha = _stage_bytes(tmp_path / "uploads", b"candidate archive")
    candidate = await service.stage(
        project_id="project-a",
        filename="export.zip",
        staged_archive_path=path,
        archive_sha256=sha,
        extractor=_extract_fixture,
    )

    assert await service.submit(candidate) == ZipRevisionSubmission(
        status="held", reason_code="living_doc_patching_disabled"
    )
