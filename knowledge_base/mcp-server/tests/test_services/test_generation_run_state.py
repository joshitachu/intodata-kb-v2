from __future__ import annotations

import pytest

from mcp_server.services.generation_run_state import (
    GenerationLockError,
    acquire_generation_lock,
    manifest_path,
    record_manifest_chunks,
    release_generation_lock,
    write_run_manifest,
)


def test_generation_lock_blocks_different_active_run(tmp_path):
    staging = tmp_path / "docs_staging"
    acquire_generation_lock(staging, project_id="proj", run_id=1, stale_after_seconds=3600)

    with pytest.raises(GenerationLockError):
        acquire_generation_lock(staging, project_id="proj", run_id=2, stale_after_seconds=3600)

    release_generation_lock(staging, run_id=1)
    acquire_generation_lock(staging, project_id="proj", run_id=2, stale_after_seconds=3600)


def test_run_manifest_records_chunks(tmp_path):
    staging = tmp_path / "docs_staging"
    chunk = staging / "_investigation" / "_chunks" / "A_chunk_1.md"
    chunk.parent.mkdir(parents=True)
    chunk.write_text("chunk body", encoding="utf-8")

    write_run_manifest(staging, run_id=7, project_id="proj", phase="startup", status="running")
    record_manifest_chunks(staging, phase="investigation", group="A", chunks=[chunk])

    text = manifest_path(staging).read_text(encoding="utf-8")
    assert '"run_id": 7' in text
    assert "_investigation/_chunks/A_chunk_1.md" in text
