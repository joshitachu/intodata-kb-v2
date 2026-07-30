"""Durable write-once storage for approved source-snapshot content."""

from __future__ import annotations

import hashlib

from mcp_server.experiments.application.snapshot_sources import SourceSnapshotPersistence
from mcp_server.experiments.domain.sources import SourceSnapshot
from mcp_server.experiments.infrastructure.storage.artifact_store import ArtifactStore
from mcp_server.experiments.infrastructure.storage.canonical_json import canonical_json_bytes


class SourceSnapshotArtifactSink:
    """Persist every approved payload before atomically publishing its index."""

    def __init__(self, store: ArtifactStore) -> None:
        self._store = store

    def save_snapshot(self, snapshot: SourceSnapshot) -> SourceSnapshotPersistence:
        snapshot_directory = hashlib.sha256(snapshot.id.encode("utf-8")).hexdigest()
        contents_by_id = {content.item_id: content for content in snapshot.contents}
        content_paths: dict[str, str] = {}
        content_entries: list[dict[str, object]] = []

        for item in snapshot.items:
            content = contents_by_id[item.id]
            item_filename = hashlib.sha256(item.id.encode("utf-8")).hexdigest()
            relative_path = (
                f"source_snapshots/{snapshot_directory}/content/{item_filename}.bin"
            )
            stored = self._store.write_bytes(
                branch="shared",
                logical_name=f"source_snapshot/{snapshot.id}/{item.id}",
                content=content.extracted_bytes,
                relative_path=relative_path,
                media_type=content.media_type,
                kind="source_content",
            )
            if (
                stored.sha256 != item.extracted_sha256
                or stored.size_bytes != item.extracted_size_bytes
            ):
                raise ValueError("persisted source content differs from its immutable index")
            content_paths[item.id] = stored.relative_path
            content_entries.append(
                {
                    "item_id": item.id,
                    "media_type": stored.media_type,
                    "relative_path": stored.relative_path,
                    "sha256": stored.sha256,
                    "size_bytes": stored.size_bytes,
                }
            )

        receipt = {
            "content": content_entries,
            "snapshot": snapshot.manifest(),
            "snapshot_manifest_sha256": snapshot.manifest_sha256,
        }
        manifest = self._store.write_bytes(
            branch="shared",
            logical_name=f"source_snapshot/{snapshot.id}/manifest",
            content=canonical_json_bytes(receipt),
            relative_path=f"source_snapshots/{snapshot_directory}/manifest.json",
            media_type="application/json",
            kind="source_snapshot_manifest",
        )
        return SourceSnapshotPersistence(
            snapshot_id=snapshot.id,
            content_relative_paths=content_paths,
            manifest_relative_path=manifest.relative_path,
        )
