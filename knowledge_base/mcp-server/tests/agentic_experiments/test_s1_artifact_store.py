from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from mcp_server.experiments.infrastructure.storage.artifact_store import (
    ArtifactStore,
    ImmutableArtifactError,
    UnsafeArtifactPathError,
)


def _store(tmp_path: Path) -> ArtifactStore:
    return ArtifactStore(
        output_root=tmp_path / "output",
        project_id="project-a",
        experiment_id="2a966e39-45b9-4d7d-a02b-c0ac12697b1b",
    )


def test_copy_is_hashed_relative_and_branch_finalization_is_write_once(tmp_path: Path) -> None:
    source = tmp_path / "source.md"
    source.write_text("# immutable\n", encoding="utf-8")
    store = _store(tmp_path)

    stored = store.copy_file(
        branch="baseline",
        logical_name="docs/00_AGENT_BRIEF.md",
        source=source,
        relative_path="docs/00_AGENT_BRIEF.md",
        media_type="text/markdown",
        kind="document",
    )

    assert stored.relative_path == "baseline/docs/00_AGENT_BRIEF.md"
    assert stored.sha256 == hashlib.sha256(source.read_bytes()).hexdigest()
    assert (store.experiment_root / stored.relative_path).read_bytes() == source.read_bytes()

    finalized = store.finalize_branch(branch="baseline", manifest={"schema_version": 1})
    assert finalized.manifest_relative_path == "baseline/manifest.json"
    with pytest.raises(ImmutableArtifactError):
        store.copy_file(
            branch="baseline",
            logical_name="docs/01.md",
            source=source,
            relative_path="docs/01.md",
            media_type="text/markdown",
            kind="document",
        )


@pytest.mark.parametrize("relative_path", ["../escape.md", "/absolute.md", "docs/../../escape.md"])
def test_copy_rejects_path_escape(tmp_path: Path, relative_path: str) -> None:
    source = tmp_path / "source.md"
    source.write_text("safe", encoding="utf-8")

    with pytest.raises(UnsafeArtifactPathError):
        _store(tmp_path).copy_file(
            branch="baseline",
            logical_name="bad",
            source=source,
            relative_path=relative_path,
            media_type="text/markdown",
            kind="document",
        )


def test_copy_rejects_symlink_source_and_symlink_target_parent(tmp_path: Path) -> None:
    source = tmp_path / "source.md"
    source.write_text("safe", encoding="utf-8")
    source_link = tmp_path / "source-link.md"
    os.symlink(source, source_link)
    store = _store(tmp_path)

    with pytest.raises(UnsafeArtifactPathError):
        store.copy_file(
            branch="baseline",
            logical_name="docs/link.md",
            source=source_link,
            relative_path="docs/link.md",
            media_type="text/markdown",
            kind="document",
        )

    branch = store.experiment_root / "baseline"
    branch.mkdir()
    os.symlink(tmp_path, branch / "docs")
    with pytest.raises(UnsafeArtifactPathError):
        store.copy_file(
            branch="baseline",
            logical_name="docs/escape.md",
            source=source,
            relative_path="docs/escape.md",
            media_type="text/markdown",
            kind="document",
        )


def test_write_bytes_is_hashed_and_write_once(tmp_path: Path) -> None:
    store = _store(tmp_path)
    stored = store.write_bytes(
        branch="shared",
        logical_name="evidence/item-a",
        content=b"approved evidence",
        relative_path="evidence/item-a.bin",
        media_type="application/octet-stream",
        kind="source_content",
    )

    assert stored.sha256 == hashlib.sha256(b"approved evidence").hexdigest()
    assert (store.experiment_root / stored.relative_path).read_bytes() == b"approved evidence"
    with pytest.raises(ImmutableArtifactError):
        store.write_bytes(
            branch="shared",
            logical_name="evidence/item-a",
            content=b"replacement",
            relative_path="evidence/item-a.bin",
            media_type="application/octet-stream",
            kind="source_content",
        )
