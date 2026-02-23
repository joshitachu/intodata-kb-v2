"""File-system walker — walks repo and classifies files via platform plugin."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import TYPE_CHECKING

from intodoc.models import FileType, ScannedFile

if TYPE_CHECKING:
    from intodoc.platforms.base import PlatformPlugin


def walk_repo(repo_root: Path, plugin: PlatformPlugin) -> list[ScannedFile]:
    """Walk the repository and classify every file using the platform plugin.

    Returns a list of ScannedFile with SHA-256 hashes computed.
    """
    results: list[ScannedFile] = []

    for file_path in sorted(repo_root.rglob("*")):
        if not file_path.is_file():
            continue

        # Skip hidden directories and common noise
        rel = file_path.relative_to(repo_root).as_posix()
        if any(part.startswith(".") for part in rel.split("/")):
            continue

        file_type = plugin.classify_file(file_path)

        # Only compute SHA-256 for parseable files — skip binaries/images/unknowns
        sha = _sha256(file_path) if file_type != FileType.UNKNOWN else ""
        size = file_path.stat().st_size

        results.append(ScannedFile(
            path=file_path,
            relative_path=rel,
            file_type=file_type,
            sha256=sha,
            size_bytes=size,
        ))

    return results


def _sha256(path: Path) -> str:
    """Compute SHA-256 hash of a file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()
