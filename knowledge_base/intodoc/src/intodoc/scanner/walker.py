"""File-system walker — walks repo and classifies files via platform plugin."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import TYPE_CHECKING

from intodoc.models import FileType, ScannedFile
from intodoc.scanner.fswalk import WalkErrorHandler, walk_files

if TYPE_CHECKING:
    from intodoc.platforms.base import PlatformPlugin


def walk_repo(
    repo_root: Path,
    plugin: PlatformPlugin,
    *,
    inventory: list[dict[str, str]] | None = None,
    on_error: WalkErrorHandler | None = None,
) -> list[ScannedFile]:
    """Walk the repository and classify every file using the platform plugin.

    Returns a list of ScannedFile with SHA-256 hashes computed.

    A directory that cannot be enumerated is recorded in ``inventory`` with an
    explicit outcome and reported through ``on_error`` rather than aborting the
    walk: one unreadable subtree is a named gap, not a failed scan.
    """
    results: list[ScannedFile] = []

    def handle(directory: Path, exc: OSError) -> None:
        if inventory is not None:
            try:
                relative = directory.relative_to(repo_root).as_posix()
            except ValueError:
                relative = str(directory)
            inventory.append({
                "path": relative,
                "kind": "unknown",
                "outcome": "excluded",
                "reason": f"directory_unreadable_{type(exc).__name__}",
            })
        if on_error is not None:
            on_error(directory, exc)

    for file_path in sorted(walk_files(repo_root, on_error=handle)):
        # Skip hidden directories and common noise
        rel = file_path.relative_to(repo_root).as_posix()
        if file_path.is_symlink() or not file_path.resolve().is_relative_to(repo_root.resolve()):
            if inventory is not None:
                inventory.append({"path": rel, "kind": "unknown", "outcome": "excluded", "reason": "symlink_or_outside_root"})
            continue
        if any(part.startswith(".") for part in rel.split("/")):
            if inventory is not None:
                inventory.append({"path": rel, "kind": "unknown", "outcome": "excluded", "reason": "hidden_path"})
            continue

        file_type = plugin.classify_file(file_path)
        if inventory is not None:
            inventory.append({"path": rel, "kind": file_type.value, "outcome": "indexed" if file_type != FileType.UNKNOWN else "unsupported", "reason": "classified_not_proof_of_parse" if file_type != FileType.UNKNOWN else "unrecognized_file_type"})

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
