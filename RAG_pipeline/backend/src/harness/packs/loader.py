"""File-backed JSON loading for Nexus pack definitions."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import ValidationError

from src.harness.packs.models import PackManifest


class PackLoadError(ValueError):
    """Raised when a pack file cannot be safely decoded or validated."""


def load_pack_file(path: Path) -> PackManifest:
    if path.suffix.lower() != ".json":
        raise PackLoadError(f"unsupported pack format for {path.name}; expected .json")
    if not path.is_file():
        raise PackLoadError(f"pack file does not exist: {path}")
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise PackLoadError(f"could not read pack file {path}: {exc}") from exc
    try:
        return PackManifest.model_validate_json(raw, strict=True)
    except (ValidationError, ValueError, json.JSONDecodeError) as exc:
        raise PackLoadError(f"invalid pack file {path.name}: {exc}") from exc


def load_pack_directory(directory: Path) -> tuple[PackManifest, ...]:
    if not directory.is_dir():
        raise PackLoadError(f"pack directory does not exist: {directory}")
    paths = sorted(directory.glob("*.json"), key=lambda path: path.name)
    symlinks = [path.name for path in paths if path.is_symlink()]
    if symlinks:
        raise PackLoadError(f"pack directory contains symbolic link(s): {', '.join(symlinks)}")
    return tuple(load_pack_file(path) for path in paths)
