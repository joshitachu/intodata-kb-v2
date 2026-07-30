"""Path-contained, append-only experiment artifact storage.

The store accepts a validated output directory rather than the repository root and
can only create ``output/{project}/experiments/{uuid}``.  It does not know about
legacy generation, publication, RAG, or database models.
"""

from __future__ import annotations

import os
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from mcp_server.experiments.domain.models import require_safe_identifier, require_uuid
from mcp_server.experiments.infrastructure.storage.canonical_json import canonical_json_bytes
from mcp_server.experiments.infrastructure.storage.hashing import sha256_bytes


class ArtifactStoreError(RuntimeError):
    """Base error for unsafe or non-immutable artifact operations."""


class UnsafeArtifactPathError(ArtifactStoreError):
    """The source or target path fails containment/regular-file validation."""


class ImmutableArtifactError(ArtifactStoreError):
    """An attempt would replace a finalized branch or existing artifact."""


@dataclass(frozen=True)
class StoredArtifact:
    branch: str
    logical_name: str
    relative_path: str
    sha256: str
    size_bytes: int
    media_type: str
    kind: str


@dataclass(frozen=True)
class FinalizedBranch:
    branch: str
    manifest_relative_path: str
    manifest_sha256: str


class ArtifactStore:
    """Write-once filesystem store for one experiment.

    Finalization is branch-scoped, allowing the baseline to become immutable before
    a later candidate branch exists.  All returned paths are relative to the
    experiment root, so callers never persist absolute paths in a database.
    """

    _ALLOWED_BRANCHES = frozenset({"shared", "baseline", "candidate", "tasks", "comparison"})
    _FINALIZED_MARKER = ".finalized.json"

    def __init__(self, *, output_root: Path, project_id: str, experiment_id: str) -> None:
        self._output_root = Path(output_root)
        self.project_id = require_safe_identifier(project_id, field_name="project_id")
        self.experiment_id = require_uuid(experiment_id)
        self._experiment_root = (
            self._output_root / self.project_id / "experiments" / self.experiment_id
        )

    @property
    def experiment_root(self) -> Path:
        """Return the physical root after verifying every existing ancestor."""

        self._ensure_directory_chain(self._experiment_root)
        return self._experiment_root

    @property
    def experiment_relative_root(self) -> str:
        return f"experiments/{self.experiment_id}"

    def copy_file(
        self,
        *,
        branch: str,
        logical_name: str,
        source: Path,
        relative_path: str,
        media_type: str,
        kind: str,
    ) -> StoredArtifact:
        """Atomically copy one regular file without allowing replacement.

        The final hard-link operation is an atomic create-if-absent operation.  It
        prevents a concurrent caller from replacing an artifact after validation.
        """

        self._validate_branch(branch)
        source = Path(source)
        self._require_regular_non_symlink(source, label="source")
        target = self._target_path(branch=branch, relative_path=relative_path)
        if self._is_branch_finalized(branch):
            raise ImmutableArtifactError(f"artifact branch is finalized: {branch}")
        if target.exists() or target.is_symlink():
            raise ImmutableArtifactError(f"artifact already exists: {relative_path}")

        self._ensure_directory_chain(target.parent)
        temporary_fd, temporary_name = tempfile.mkstemp(
            prefix=".artifact-", suffix=".tmp", dir=target.parent
        )
        temporary = Path(temporary_name)
        try:
            digest, size = self._copy_and_hash(source, temporary_fd)
            # A link is atomic and refuses to overwrite a pre-existing target.
            try:
                os.link(temporary, target)
            except FileExistsError as exc:
                raise ImmutableArtifactError(f"artifact already exists: {relative_path}") from exc
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

        return StoredArtifact(
            branch=branch,
            logical_name=logical_name,
            relative_path=(PurePosixPath(branch) / PurePosixPath(relative_path)).as_posix(),
            sha256=digest,
            size_bytes=size,
            media_type=media_type,
            kind=kind,
        )

    def write_bytes(
        self,
        *,
        branch: str,
        logical_name: str,
        content: bytes,
        relative_path: str,
        media_type: str,
        kind: str,
    ) -> StoredArtifact:
        """Atomically create one in-memory artifact without allowing replacement."""

        self._validate_branch(branch)
        if not isinstance(content, bytes):
            raise TypeError("artifact content must be bytes")
        target = self._target_path(branch=branch, relative_path=relative_path)
        if self._is_branch_finalized(branch):
            raise ImmutableArtifactError(f"artifact branch is finalized: {branch}")
        self._ensure_directory_chain(target.parent)
        self._atomic_create_bytes(target, content)
        return StoredArtifact(
            branch=branch,
            logical_name=logical_name,
            relative_path=(PurePosixPath(branch) / PurePosixPath(relative_path)).as_posix(),
            sha256=sha256_bytes(content),
            size_bytes=len(content),
            media_type=media_type,
            kind=kind,
        )

    def finalize_branch(self, *, branch: str, manifest: dict[str, Any]) -> FinalizedBranch:
        """Write a deterministic manifest and a finalization marker exactly once."""

        self._validate_branch(branch)
        branch_root = self._target_path(branch=branch, relative_path=".")
        self._ensure_directory_chain(branch_root)
        if self._is_branch_finalized(branch):
            raise ImmutableArtifactError(f"artifact branch is already finalized: {branch}")

        manifest_bytes = canonical_json_bytes(manifest)
        manifest_path = branch_root / "manifest.json"
        marker_path = branch_root / self._FINALIZED_MARKER
        self._atomic_create_bytes(manifest_path, manifest_bytes)
        manifest_sha256 = sha256_bytes(manifest_bytes)
        marker = canonical_json_bytes(
            {"manifest": "manifest.json", "manifest_sha256": manifest_sha256, "schema_version": 1}
        )
        try:
            self._atomic_create_bytes(marker_path, marker)
        except Exception:
            # A manifest without a final marker is deliberately not treated as a
            # finalized branch, but it must never be silently replaced. Operators
            # can inspect/reconcile this crash edge through the later S10 workflow.
            raise
        return FinalizedBranch(
            branch=branch,
            manifest_relative_path=f"{branch}/manifest.json",
            manifest_sha256=manifest_sha256,
        )

    def _target_path(self, *, branch: str, relative_path: str) -> Path:
        self._validate_branch(branch)
        relative = self._validate_relative_path(relative_path, allow_current=True)
        root = self.experiment_root
        target = root / branch / relative
        # ``relative`` was syntactically constrained, while this check also catches
        # unexpected platform/path-library behavior before any write happens.
        try:
            target.relative_to(root)
        except ValueError as exc:
            raise UnsafeArtifactPathError("artifact path escapes experiment root") from exc
        return target

    def _validate_branch(self, branch: str) -> None:
        if branch not in self._ALLOWED_BRANCHES:
            raise UnsafeArtifactPathError(f"unknown experiment artifact branch: {branch}")

    @staticmethod
    def _validate_relative_path(value: str, *, allow_current: bool = False) -> Path:
        candidate = PurePosixPath(value)
        if "\x00" in value or candidate.is_absolute():
            raise UnsafeArtifactPathError("artifact path must be a relative POSIX path")
        if value in {"", "."}:
            if allow_current:
                return Path()
            raise UnsafeArtifactPathError("artifact path must name a file")
        if any(part in {"", ".", ".."} for part in candidate.parts):
            raise UnsafeArtifactPathError("artifact path may not contain traversal components")
        return Path(*candidate.parts)

    def _is_branch_finalized(self, branch: str) -> bool:
        marker = self._experiment_root / branch / self._FINALIZED_MARKER
        if marker.is_symlink():
            raise UnsafeArtifactPathError("finalization marker may not be a symlink")
        return marker.exists()

    def _ensure_directory_chain(self, directory: Path) -> None:
        """Create directories one component at a time, rejecting symlinks/devices."""

        root = self._output_root
        if root.exists():
            self._require_directory_non_symlink(root, label="output root")
        else:
            root.mkdir(parents=True, exist_ok=False)
        try:
            relative = directory.relative_to(root)
        except ValueError as exc:
            raise UnsafeArtifactPathError("directory is outside configured output root") from exc
        current = root
        for part in relative.parts:
            current = current / part
            try:
                current.mkdir()
            except FileExistsError:
                pass
            self._require_directory_non_symlink(current, label="artifact directory")

    @staticmethod
    def _require_directory_non_symlink(path: Path, *, label: str) -> None:
        try:
            mode = path.lstat().st_mode
        except OSError as exc:
            raise UnsafeArtifactPathError(f"cannot inspect {label}: {path}") from exc
        if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
            raise UnsafeArtifactPathError(f"{label} must be a non-symlink directory")

    @staticmethod
    def _require_regular_non_symlink(path: Path, *, label: str) -> None:
        try:
            mode = path.lstat().st_mode
        except OSError as exc:
            raise UnsafeArtifactPathError(f"cannot inspect {label}: {path}") from exc
        if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
            raise UnsafeArtifactPathError(f"{label} must be a regular non-symlink file")

    @staticmethod
    def _copy_and_hash(source: Path, destination_fd: int) -> tuple[str, int]:
        import hashlib

        digest = hashlib.sha256()
        size = 0
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        try:
            source_fd = os.open(source, flags)
        except OSError as exc:
            os.close(destination_fd)
            raise UnsafeArtifactPathError(f"cannot safely open source artifact: {source}") from exc
        try:
            source_mode = os.fstat(source_fd).st_mode
            if not stat.S_ISREG(source_mode):
                raise UnsafeArtifactPathError("source must remain a regular file while copying")
        except Exception:
            os.close(source_fd)
            os.close(destination_fd)
            raise
        with os.fdopen(source_fd, "rb") as source_handle, os.fdopen(destination_fd, "wb") as destination:
            while chunk := source_handle.read(1024 * 1024):
                digest.update(chunk)
                size += len(chunk)
                destination.write(chunk)
            destination.flush()
            os.fsync(destination.fileno())
        return digest.hexdigest(), size

    @staticmethod
    def _atomic_create_bytes(target: Path, content: bytes) -> None:
        if target.exists() or target.is_symlink():
            raise ImmutableArtifactError(f"artifact already exists: {target.name}")
        fd, temporary_name = tempfile.mkstemp(prefix=".receipt-", suffix=".tmp", dir=target.parent)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.link(temporary, target)
            except FileExistsError as exc:
                raise ImmutableArtifactError(f"artifact already exists: {target.name}") from exc
        finally:
            temporary.unlink(missing_ok=True)
