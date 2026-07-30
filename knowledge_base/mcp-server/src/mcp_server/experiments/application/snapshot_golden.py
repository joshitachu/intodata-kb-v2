"""Freeze one verified historical parity bundle inside an experiment."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from types import MappingProxyType
from typing import Mapping

from mcp_server.experiments.application.import_golden import (
    GoldenImportedBundle,
)
from mcp_server.experiments.domain.parity import thaw_json
from mcp_server.experiments.infrastructure.storage.artifact_store import (
    ArtifactStore,
    StoredArtifact,
)
from mcp_server.experiments.infrastructure.storage.canonical_json import (
    canonical_json_bytes,
)


_ARTIFACT_FILES = {
    "prompt": ("prompt.md", "text/markdown"),
    "context": ("context_bundle.md", "text/markdown"),
    "request": ("request.json", "application/json"),
    "response": ("response.json", "application/json"),
}


@dataclass(frozen=True, slots=True)
class GoldenSnapshotPersistence:
    task_id: str
    aggregate_receipt_sha256: str
    manifest_relative_path: str
    artifact_relative_paths: Mapping[str, str]
    artifacts: tuple[StoredArtifact, ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "artifact_relative_paths",
            MappingProxyType(dict(self.artifact_relative_paths)),
        )
        object.__setattr__(self, "artifacts", tuple(self.artifacts))


class GoldenParityArtifactSink:
    """Write exact verified source bytes first and their receipt manifest last."""

    def __init__(self, store: ArtifactStore) -> None:
        self._store = store

    def save(
        self,
        *,
        bundle: GoldenImportedBundle,
    ) -> GoldenSnapshotPersistence:
        receipt = bundle.receipt
        task_key = hashlib.sha256(
            receipt.request.task_id.encode("utf-8")
        ).hexdigest()
        artifact_paths: dict[str, str] = {}
        artifact_index: dict[str, dict[str, object]] = {}
        stored_artifacts: list[StoredArtifact] = []
        for name in ("prompt", "context", "request", "response"):
            filename, media_type = _ARTIFACT_FILES[name]
            stored = self._store.write_bytes(
                branch="shared",
                logical_name=f"golden/{task_key}/{name}",
                content=bundle.artifacts[name],
                relative_path=f"golden/{task_key}/{filename}",
                media_type=media_type,
                kind=f"golden_{name}",
            )
            if stored.sha256 != receipt.source_hashes[name]:
                raise ValueError(
                    "stored golden artifact does not match imported receipt"
                )
            artifact_paths[name] = stored.relative_path
            stored_artifacts.append(stored)
            artifact_index[name] = {
                "logical_name": stored.logical_name,
                "media_type": stored.media_type,
                "relative_path": stored.relative_path,
                "sha256": stored.sha256,
                "size_bytes": stored.size_bytes,
            }

        lineage = receipt.request.lineage
        manifest_bytes = canonical_json_bytes(
            {
                "aggregate_receipt_sha256": (
                    receipt.aggregate_receipt_sha256
                ),
                "artifacts": artifact_index,
                "baseline_run_id": lineage.baseline_run_id,
                "inherited_checkpoint_artifacts": [
                    {
                        "attributable_to_baseline_run": (
                            item.attributable_to_baseline_run
                        ),
                        "logical_id": item.logical_id,
                        "raw_model_route": item.raw_model_route,
                        "sha256": item.sha256,
                    }
                    for item in lineage.inherited_checkpoint_artifacts
                ],
                "model_options": thaw_json(
                    receipt.request.model_options
                ),
                "output_contract": thaw_json(
                    receipt.request.output_contract
                ),
                "output_contract_sha256": (
                    receipt.request.output_contract_sha256
                ),
                "resume_requested": lineage.resume_requested,
                "schema_version": 1,
                "task_id": receipt.request.task_id,
                "task_model_precedence": lineage.task_model_precedence,
                "task_model_route": {
                    "canonical_route": (
                        receipt.request.canonical_model_route
                    ),
                    "model_id": receipt.request.model_id,
                    "provider_id": receipt.request.provider_id,
                    "raw_provider_route": (
                        receipt.request.raw_provider_route
                    ),
                },
            }
        )
        manifest = self._store.write_bytes(
            branch="shared",
            logical_name=f"golden/{task_key}/manifest",
            content=manifest_bytes,
            relative_path=f"golden/{task_key}/manifest.json",
            media_type="application/json",
            kind="golden_manifest",
        )
        return GoldenSnapshotPersistence(
            task_id=receipt.request.task_id,
            aggregate_receipt_sha256=(
                receipt.aggregate_receipt_sha256
            ),
            manifest_relative_path=manifest.relative_path,
            artifact_relative_paths=artifact_paths,
            artifacts=(*stored_artifacts, manifest),
        )
