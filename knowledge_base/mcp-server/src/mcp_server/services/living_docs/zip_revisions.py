"""Immutable ZIP source candidates for incremental documentation updates.

The service deliberately never points a project at a candidate directory and
never modifies the project's active ``local_path``.  A later S4 coordinator may
scan and publish the candidate; only that governed lifecycle can promote it.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from mcp_server.services.living_docs.contracts import SourceOrigin, SourceRevision


class ZipRevisionError(RuntimeError):
    """The supplied ZIP could not become an immutable source candidate."""


@dataclass(frozen=True, slots=True)
class StagedZipRevision:
    project_id: str
    candidate_id: str
    source_revision: SourceRevision
    archive_sha256: str
    archive_path: Path
    source_root: Path
    manifest_path: Path
    no_op: bool = False


@dataclass(frozen=True, slots=True)
class ZipRevisionSubmission:
    status: str
    reason_code: str | None = None


class ZipRevisionCoordinator(Protocol):
    """S4 lifecycle handoff; it may scan/hold but must not activate inline."""

    async def submit_zip_revision(self, candidate: StagedZipRevision) -> ZipRevisionSubmission: ...


ZipExtractor = Callable[[Path, Path], None]


class ZipRevisionService:
    """Stage safely extracted ZIP bytes under a content-addressed candidate root."""

    def __init__(
        self,
        candidate_root: Path,
        *,
        coordinator: ZipRevisionCoordinator | None = None,
    ) -> None:
        self._candidate_root = candidate_root
        self._coordinator = coordinator
        self._lock = asyncio.Lock()

    async def stage(
        self,
        *,
        project_id: str,
        filename: str,
        staged_archive_path: Path,
        archive_sha256: str,
        extractor: ZipExtractor,
    ) -> StagedZipRevision:
        """Hash and safely extract a candidate without changing active source.

        ``staged_archive_path`` must already be a file on disk whose content
        matches ``archive_sha256`` (produced by streaming an upload to a temp
        file without buffering it in memory). This call takes ownership of that
        file: it is moved into the candidate root on success, discarded if an
        identical candidate already exists, and deleted on any failure.

        The extracted tree is made visible only after its archive, manifest and
        all extracted files are complete.  Replays of the exact archive reuse
        the immutable candidate and are explicitly reported as no-ops.
        """
        if not project_id or "/" in project_id or "\\" in project_id:
            staged_archive_path.unlink(missing_ok=True)
            raise ZipRevisionError("project_id must be a safe identifier")
        if not staged_archive_path.is_file() or staged_archive_path.stat().st_size == 0:
            staged_archive_path.unlink(missing_ok=True)
            raise ZipRevisionError("ZIP content is empty")

        candidate_id = f"zip-{archive_sha256[:32]}"
        candidate_dir = self._candidate_root / project_id / candidate_id
        archive_path = candidate_dir / "source.zip"
        source_root = candidate_dir / "source"
        manifest_path = candidate_dir / "manifest.json"

        async with self._lock:
            existing = self._load_existing(
                project_id=project_id,
                candidate_id=candidate_id,
                archive_sha256=archive_sha256,
                archive_path=archive_path,
                source_root=source_root,
                manifest_path=manifest_path,
            )
            if existing is not None:
                staged_archive_path.unlink(missing_ok=True)
                return existing

            candidate_dir.parent.mkdir(parents=True, exist_ok=True)
            staging = Path(tempfile.mkdtemp(prefix=f".{candidate_id}-", dir=candidate_dir.parent))
            try:
                staging_archive = staging / "source.zip"
                shutil.move(str(staged_archive_path), str(staging_archive))
                staging_source = staging / "source"
                staging_source.mkdir()
                extractor(staging_archive, staging_source)
                manifest = {
                    "schema_version": 1,
                    "project_id": project_id,
                    "candidate_id": candidate_id,
                    "source_origin": SourceOrigin.ZIP.value,
                    "source_identity": f"zip:{Path(filename).name}",
                    "revision_id": archive_sha256,
                    "archive_sha256": archive_sha256,
                    "tree_sha256": _tree_sha256(staging_source),
                }
                _write_bytes_atomically(
                    staging / "manifest.json",
                    json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8"),
                )
                try:
                    os.replace(staging, candidate_dir)
                except FileExistsError:
                    # Another worker finished the same content-addressed
                    # candidate while this one was extracting it.
                    shutil.rmtree(staging, ignore_errors=True)
                    existing = self._load_existing(
                        project_id=project_id,
                        candidate_id=candidate_id,
                        archive_sha256=archive_sha256,
                        archive_path=archive_path,
                        source_root=source_root,
                        manifest_path=manifest_path,
                    )
                    if existing is not None:
                        return existing
                    raise
            except Exception:
                shutil.rmtree(staging, ignore_errors=True)
                staged_archive_path.unlink(missing_ok=True)
                raise

        return self._staged_from_manifest(
            project_id=project_id,
            candidate_id=candidate_id,
            archive_sha256=archive_sha256,
            archive_path=archive_path,
            source_root=source_root,
            manifest_path=manifest_path,
            no_op=False,
        )

    async def submit(self, candidate: StagedZipRevision) -> ZipRevisionSubmission:
        """Hand a newly staged candidate to S4, never replacing active source."""
        if candidate.no_op:
            return ZipRevisionSubmission(status="noop")
        if self._coordinator is None:
            return ZipRevisionSubmission(status="held", reason_code="zip_coordinator_not_configured")
        try:
            return await self._coordinator.submit_zip_revision(candidate)
        except Exception:
            # Candidate evidence remains available for recovery; active source
            # is untouched because this service never writes project.local_path.
            return ZipRevisionSubmission(status="failed", reason_code="zip_coordinator_failed")

    def _load_existing(
        self,
        *,
        project_id: str,
        candidate_id: str,
        archive_sha256: str,
        archive_path: Path,
        source_root: Path,
        manifest_path: Path,
    ) -> StagedZipRevision | None:
        if not (archive_path.is_file() and source_root.is_dir() and manifest_path.is_file()):
            return None
        try:
            if hashlib.sha256(archive_path.read_bytes()).hexdigest() != archive_sha256:
                return None
            return self._staged_from_manifest(
                project_id=project_id,
                candidate_id=candidate_id,
                archive_sha256=archive_sha256,
                archive_path=archive_path,
                source_root=source_root,
                manifest_path=manifest_path,
                no_op=True,
            )
        except (OSError, ValueError, json.JSONDecodeError):
            return None

    @staticmethod
    def _staged_from_manifest(
        *,
        project_id: str,
        candidate_id: str,
        archive_sha256: str,
        archive_path: Path,
        source_root: Path,
        manifest_path: Path,
        no_op: bool,
    ) -> StagedZipRevision:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if (
            manifest.get("project_id") != project_id
            or manifest.get("candidate_id") != candidate_id
            or manifest.get("archive_sha256") != archive_sha256
            or manifest.get("source_origin") != SourceOrigin.ZIP.value
        ):
            raise ZipRevisionError("candidate manifest does not match staged ZIP")
        return StagedZipRevision(
            project_id=project_id,
            candidate_id=candidate_id,
            source_revision=SourceRevision(
                project_id=project_id,
                origin=SourceOrigin.ZIP,
                source_identity=str(manifest["source_identity"]),
                revision_id=str(manifest["revision_id"]),
                content_sha256=archive_sha256,
            ),
            archive_sha256=archive_sha256,
            archive_path=archive_path,
            source_root=source_root,
            manifest_path=manifest_path,
            no_op=no_op,
        )


def _tree_sha256(root: Path) -> str:
    """Hash the extracted candidate tree deterministically without following links."""
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        if not path.is_file() or path.is_symlink():
            continue
        relative = path.relative_to(root).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    return digest.hexdigest()


def _write_bytes_atomically(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
        handle.write(content)
        temp_path = Path(handle.name)
    try:
        os.replace(temp_path, path)
    finally:
        temp_path.unlink(missing_ok=True)
