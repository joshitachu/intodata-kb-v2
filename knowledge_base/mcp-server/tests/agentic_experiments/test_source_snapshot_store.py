from __future__ import annotations

import hashlib
from pathlib import Path

from mcp_server.experiments.domain.sources import (
    RedactionState,
    SourceSnapshot,
    SourceSnapshotContent,
    SourceSnapshotItem,
)
from mcp_server.experiments.infrastructure.storage.artifact_store import ArtifactStore
from mcp_server.experiments.infrastructure.storage.source_snapshot_store import (
    SourceSnapshotArtifactSink,
)


def test_source_snapshot_store_writes_content_before_manifest(tmp_path: Path) -> None:
    payload = b"approved evidence"
    digest = hashlib.sha256(payload).hexdigest()
    item = SourceSnapshotItem(
        id="item-a",
        source_descriptor_id="descriptor-a",
        logical_identity="evidence://item-a",
        source_kind="test",
        source_role="implementation",
        capabilities=frozenset({"searchable_text"}),
        locator="fixture://item-a",
        raw_sha256=digest,
        extracted_sha256=digest,
        media_type="text/plain",
        size_bytes=len(payload),
        extracted_size_bytes=len(payload),
        trust_level="trusted",
        redaction_state=RedactionState.APPROVED,
        adapter_id="test",
        adapter_version="1",
    )
    snapshot = SourceSnapshot.build(
        snapshot_id="snapshot-a",
        items=(item,),
        contents=(
            SourceSnapshotContent(
                item_id=item.id,
                extracted_bytes=payload,
                extracted_sha256=digest,
                media_type=item.media_type,
                size_bytes=len(payload),
            ),
        ),
    )
    store = ArtifactStore(
        output_root=tmp_path / "output",
        project_id="project-a",
        experiment_id="2a966e39-45b9-4d7d-a02b-c0ac12697b1b",
    )

    receipt = SourceSnapshotArtifactSink(store).save_snapshot(snapshot)

    content_path = store.experiment_root / receipt.content_relative_paths[item.id]
    manifest_path = store.experiment_root / receipt.manifest_relative_path
    assert content_path.read_bytes() == payload
    assert manifest_path.is_file()
    assert receipt.content_relative_paths[item.id].startswith("shared/source_snapshots/")
