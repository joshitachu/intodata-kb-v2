"""Safe task-output writing under a prevalidated experiment task root."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import stat


class ArtifactWriteDenied(PermissionError):
    pass


@dataclass(frozen=True, slots=True)
class AllowedArtifact:
    logical_name: str
    media_type: str
    max_bytes: int


class TaskArtifactWriter:
    def __init__(self, *, root: Path, allowed_artifacts: tuple[AllowedArtifact, ...]) -> None:
        if not root.is_absolute() or not allowed_artifacts:
            raise ValueError("absolute task artifact root and declared artifacts are required")
        self._root = root
        self._allowed = {artifact.logical_name: artifact for artifact in allowed_artifacts}
        if len(self._allowed) != len(allowed_artifacts):
            raise ValueError("declared artifact names must be unique")
        self._ensure_root()

    def write(self, *, logical_name: str, media_type: str, content: bytes) -> dict[str, object]:
        receipt = self.preview(logical_name=logical_name, media_type=media_type, content=content)
        destination = self._safe_destination(logical_name)
        try:
            descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError as exc:
            raise ArtifactWriteDenied("task artifacts are write-once") from exc
        try:
            with os.fdopen(descriptor, "wb") as output:
                output.write(content)
                output.flush()
                os.fsync(output.fileno())
        except BaseException:
            # A partial file remains write-once and requires operator inspection;
            # never replace it silently with a retry's different bytes.
            raise
        return receipt

    def preview(self, *, logical_name: str, media_type: str, content: bytes) -> dict[str, object]:
        artifact = self._allowed.get(logical_name)
        if artifact is None or artifact.media_type != media_type:
            raise ArtifactWriteDenied("artifact is not declared for this task contract")
        if len(content) > artifact.max_bytes:
            raise ArtifactWriteDenied("artifact exceeds task media/size contract")
        destination = self._safe_destination(logical_name)
        if destination.exists():
            raise ArtifactWriteDenied("task artifacts are write-once")
        return {
            "byte_size": len(content),
            "logical_name": logical_name,
            "media_type": media_type,
            "sha256": hashlib.sha256(content).hexdigest(),
        }

    def exists(self, logical_name: str) -> bool:
        return self._safe_destination(logical_name).exists()

    def _ensure_root(self) -> None:
        if not self._root.exists() or self._root.is_symlink() or not self._root.is_dir():
            raise ArtifactWriteDenied("task artifact root must be a pre-created non-symlink directory")
        for ancestor in (self._root, *self._root.parents):
            if ancestor.exists():
                mode = ancestor.lstat().st_mode
                if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
                    raise ArtifactWriteDenied("task artifact root has an unsafe ancestor")

    def _safe_destination(self, logical_name: str) -> Path:
        relative = Path(logical_name)
        if relative.is_absolute() or not logical_name or any(part in {"", ".", ".."} for part in relative.parts):
            raise ArtifactWriteDenied("unsafe task artifact logical name")
        destination = self._root / relative
        self._ensure_safe_parents(destination.parent)
        root_real = self._root.resolve(strict=True)
        try:
            destination.resolve(strict=False).relative_to(root_real)
        except ValueError as exc:
            raise ArtifactWriteDenied("task artifact path escapes its root") from exc
        if destination.exists() and destination.is_symlink():
            raise ArtifactWriteDenied("symlink artifact destinations are forbidden")
        return destination

    def _ensure_safe_parents(self, parent: Path) -> None:
        relative = parent.relative_to(self._root)
        current = self._root
        for part in relative.parts:
            current = current / part
            if current.exists():
                mode = current.lstat().st_mode
                if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
                    raise ArtifactWriteDenied("artifact parent is not a safe directory")
            else:
                current.mkdir(mode=0o700)
