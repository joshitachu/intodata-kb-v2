"""Unified persistence for in-pipeline transient artifacts.

Files written here exist solely to bridge data between steps inside a single
phase call (merge input, synthesis input, repair bundle, scorecard-slice
output, split-context chunks). They are never read by a later phase,
validator, gate, resume path, REST route, or RAG push. The directory is
deleted at the end of Phase 4 alongside `_preprocess/` and `_analysis/`.

This module is the un-gated sibling of `debug_artifacts.py` -- these files
are required for the pipeline to function, so the helper performs no flag
check; it centralizes the directory name and the mkdir-and-write idiom
only.
"""

from __future__ import annotations

from pathlib import Path


TRANSIENT_DIR_NAME = "_transient"


def transient_dir(staging_dir: Path, *parts: str) -> Path:
    """Path under the staging _transient root; not created until a write happens."""

    return staging_dir.joinpath(TRANSIENT_DIR_NAME, *parts)


def transient_write(path: Path, content: str) -> Path:
    """Write a transient artifact, creating parent directories as needed."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path
