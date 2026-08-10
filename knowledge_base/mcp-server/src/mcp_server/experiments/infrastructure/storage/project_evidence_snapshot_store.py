"""Write-once storage for project evidence manifests and prepared content."""

from __future__ import annotations

import hashlib
import json
import stat
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Mapping

from mcp_server.experiments.domain.project_evidence import (
    ProjectEvidencePreparedContent,
    ProjectEvidenceSnapshot,
)
from mcp_server.experiments.infrastructure.sources.registry import (
    SourceAdapterError,
    read_regular,
)
from mcp_server.experiments.infrastructure.storage.artifact_store import ArtifactStore
from mcp_server.experiments.infrastructure.storage.canonical_json import canonical_json_bytes


@dataclass(frozen=True)
class ProjectEvidenceSnapshotPersistence:
    """Durable linkage receipt suitable for an experiment repository field."""

    snapshot_id: str
    fingerprint_sha256: str
    content_relative_paths: Mapping[str, str]
    manifest_relative_path: str
    manifest_sha256: str
    manifest_size_bytes: int

    def __post_init__(self) -> None:
        if not isinstance(self.content_relative_paths, Mapping) or any(
            not isinstance(key, str) or not key or not isinstance(value, str) or not value
            for key, value in self.content_relative_paths.items()
        ):
            raise ValueError("project evidence persistence content paths are invalid")
        object.__setattr__(
            self,
            "content_relative_paths",
            MappingProxyType(dict(sorted(self.content_relative_paths.items()))),
        )
        if (
            not isinstance(self.snapshot_id, str)
            or not self.snapshot_id
            or not isinstance(self.manifest_relative_path, str)
            or not self.manifest_relative_path
            or not isinstance(self.manifest_size_bytes, int)
            or isinstance(self.manifest_size_bytes, bool)
            or self.manifest_size_bytes < 1
        ):
            raise ValueError("project evidence persistence manifest must not be empty")
        for value, label in (
            (self.fingerprint_sha256, "snapshot fingerprint"),
            (self.manifest_sha256, "persistence manifest hash"),
        ):
            if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
                raise ValueError(f"{label} must be a lowercase SHA-256 digest")


class ProjectEvidenceSnapshotArtifactSink:
    """Persist prepared bytes before atomically publishing their manifest."""

    def __init__(self, store: ArtifactStore) -> None:
        self._store = store

    def save_snapshot(
        self, snapshot: ProjectEvidenceSnapshot
    ) -> ProjectEvidenceSnapshotPersistence:
        directory = hashlib.sha256(snapshot.id.encode("utf-8")).hexdigest()
        content_paths: dict[str, str] = {}
        content_entries: list[dict[str, object]] = []
        for content in snapshot.prepared_content_items:
            filename = hashlib.sha256(content.item_id.encode("utf-8")).hexdigest()
            stored = self._store.write_bytes(
                branch="shared",
                logical_name=f"project_evidence/{snapshot.id}/{content.item_id}",
                content=content.extracted_bytes,
                relative_path=(f"project_evidence_snapshots/{directory}/content/{filename}.bin"),
                media_type=content.media_type,
                kind="project_evidence_prepared_content",
            )
            if stored.sha256 != content.extracted_sha256 or stored.size_bytes != content.size_bytes:
                raise ValueError("persisted project evidence content differs from snapshot")
            content_paths[content.item_id] = stored.relative_path
            content_entries.append(
                {
                    "item_id": content.item_id,
                    "media_type": stored.media_type,
                    "relative_path": stored.relative_path,
                    "sha256": stored.sha256,
                    "size_bytes": stored.size_bytes,
                }
            )

        receipt = {
            "content": content_entries,
            "schema_version": 1,
            "snapshot": snapshot.manifest(),
        }
        manifest = self._store.write_bytes(
            branch="shared",
            logical_name=f"project_evidence/{snapshot.id}/manifest",
            content=canonical_json_bytes(receipt),
            relative_path=f"project_evidence_snapshots/{directory}/manifest.json",
            media_type="application/json",
            kind="project_evidence_snapshot_manifest",
        )
        return ProjectEvidenceSnapshotPersistence(
            snapshot_id=snapshot.id,
            fingerprint_sha256=snapshot.fingerprint_sha256,
            content_relative_paths=content_paths,
            manifest_relative_path=manifest.relative_path,
            manifest_sha256=manifest.sha256,
            manifest_size_bytes=manifest.size_bytes,
        )

    def load_snapshot(
        self, persistence: ProjectEvidenceSnapshotPersistence
    ) -> ProjectEvidenceSnapshot:
        """Reconstruct and verify a persisted snapshot through its linkage receipt."""

        manifest_bytes = self._read_verified(
            persistence.manifest_relative_path,
            expected_sha256=persistence.manifest_sha256,
            expected_size=persistence.manifest_size_bytes,
        )
        try:
            envelope = json.loads(manifest_bytes)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("persisted project evidence manifest is invalid JSON") from error
        if (
            not isinstance(envelope, dict)
            or set(envelope) != {"content", "schema_version", "snapshot"}
            or envelope["schema_version"] != 1
            or canonical_json_bytes(envelope) != manifest_bytes
            or not isinstance(envelope["content"], list)
            or not isinstance(envelope["snapshot"], dict)
        ):
            raise ValueError("persisted project evidence manifest schema is invalid")

        entries: dict[str, dict[str, object]] = {}
        prepared: list[ProjectEvidencePreparedContent] = []
        for value in envelope["content"]:
            if not isinstance(value, dict) or set(value) != {
                "item_id",
                "media_type",
                "relative_path",
                "sha256",
                "size_bytes",
            }:
                raise ValueError("persisted prepared content entry schema is invalid")
            item_id = value["item_id"]
            if not isinstance(item_id, str) or not item_id or item_id in entries:
                raise ValueError("persisted prepared content ids must be unique")
            relative_path = value["relative_path"]
            digest = value["sha256"]
            size = value["size_bytes"]
            media_type = value["media_type"]
            if (
                not isinstance(relative_path, str)
                or not isinstance(digest, str)
                or not isinstance(size, int)
                or isinstance(size, bool)
                or not isinstance(media_type, str)
            ):
                raise ValueError("persisted prepared content entry types are invalid")
            content = self._read_verified(
                relative_path,
                expected_sha256=digest,
                expected_size=size,
            )
            entries[item_id] = value
            prepared.append(
                ProjectEvidencePreparedContent(
                    item_id=item_id,
                    extracted_bytes=content,
                    extracted_sha256=digest,
                    media_type=media_type,
                    size_bytes=size,
                )
            )
        expected_paths = dict(persistence.content_relative_paths)
        actual_paths = {item_id: str(value["relative_path"]) for item_id, value in entries.items()}
        if expected_paths != actual_paths:
            raise ValueError("persistence receipt content paths differ from manifest")

        snapshot_manifest = canonical_json_bytes(envelope["snapshot"])
        snapshot = ProjectEvidenceSnapshot.from_manifest_json(
            manifest_json=snapshot_manifest,
            prepared_content_items=tuple(sorted(prepared, key=lambda value: value.item_id)),
        )
        if (
            snapshot.id != persistence.snapshot_id
            or snapshot.fingerprint_sha256 != persistence.fingerprint_sha256
        ):
            raise ValueError("persistence receipt identity differs from reconstructed snapshot")
        return snapshot

    def _read_verified(
        self,
        relative_path: str,
        *,
        expected_sha256: str,
        expected_size: int,
    ) -> bytes:
        if (
            not isinstance(expected_size, int)
            or isinstance(expected_size, bool)
            or expected_size < 0
            or len(expected_sha256) != 64
            or any(character not in "0123456789abcdef" for character in expected_sha256)
        ):
            raise ValueError("persisted project evidence verification metadata is invalid")
        relative = PurePosixPath(relative_path)
        if (
            relative.is_absolute()
            or any(part in {"", ".", ".."} for part in relative.parts)
            or not relative.parts
        ):
            raise ValueError("persisted project evidence path is unsafe")
        root = self._store.experiment_root.resolve(strict=True)
        path = root.joinpath(*relative.parts)
        try:
            path.resolve(strict=True).relative_to(root)
            mode = path.lstat().st_mode
        except (OSError, ValueError) as error:
            raise ValueError("persisted project evidence path is unavailable") from error
        if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
            raise ValueError("persisted project evidence artifact must be a regular file")
        try:
            content = read_regular(Path(path), max_bytes=expected_size)
        except SourceAdapterError as error:
            raise ValueError("persisted project evidence artifact failed verification") from error
        if len(content) != expected_size or hashlib.sha256(content).hexdigest() != expected_sha256:
            raise ValueError("persisted project evidence artifact failed verification")
        return content
