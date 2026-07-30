"""Freeze a resolved declarative recipe and its deterministic task graph."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib

from mcp_server.experiments.domain.recipes import AgenticRecipeManifest
from mcp_server.experiments.domain.tasks import CompiledTaskGraph
from mcp_server.experiments.infrastructure.recipes.compiler import (
    recipe_manifest_payload,
)
from mcp_server.experiments.infrastructure.storage.artifact_store import ArtifactStore
from mcp_server.experiments.infrastructure.storage.canonical_json import canonical_json_bytes


@dataclass(frozen=True)
class RecipeSnapshotPersistence:
    snapshot_id: str
    manifest_relative_path: str
    recipe_relative_path: str
    compiled_graph_relative_path: str


class RecipeSnapshotArtifactSink:
    """Persist source recipe and resolved semantic graph before its receipt."""

    def __init__(self, store: ArtifactStore) -> None:
        self._store = store

    def save(
        self,
        *,
        experiment_id: str,
        snapshot_id: str,
        manifest: AgenticRecipeManifest,
        graph: CompiledTaskGraph,
    ) -> RecipeSnapshotPersistence:
        source_bytes = canonical_json_bytes(recipe_manifest_payload(manifest))
        if hashlib.sha256(source_bytes).hexdigest() != graph.receipt.get(
            "manifest_sha256"
        ):
            raise ValueError("compiled graph does not match resolved recipe manifest")
        directory = hashlib.sha256(snapshot_id.encode("utf-8")).hexdigest()
        source = self._store.write_bytes(
            branch="shared",
            logical_name=f"recipe_snapshot/{snapshot_id}/source",
            content=source_bytes,
            relative_path=f"recipe_snapshots/{directory}/recipe.json",
            media_type="application/json",
            kind="recipe_source",
        )
        graph_bytes = canonical_json_bytes(
            {
                "nodes": [
                    {
                        "dependencies": node.dependencies,
                        "dependency_output_refs": node.dependency_output_refs,
                        "metadata": dict(node.metadata),
                        "node_id": node.node_id,
                        "output_ids": node.output_ids,
                        "role": node.role,
                        "semantic_prompt": node.semantic_prompt,
                        "task_type": node.task_type,
                    }
                    for node in graph.nodes
                ],
                "receipt": dict(graph.receipt),
                "sha256": graph.sha256,
                "topological_order": graph.topological_order,
            }
        )
        compiled = self._store.write_bytes(
            branch="shared",
            logical_name=f"recipe_snapshot/{snapshot_id}/compiled_graph",
            content=graph_bytes,
            relative_path=f"recipe_snapshots/{directory}/compiled_graph.json",
            media_type="application/json",
            kind="compiled_task_graph",
        )
        receipt_bytes = canonical_json_bytes(
            {
                "compiled_graph": {
                    "relative_path": compiled.relative_path,
                    "sha256": compiled.sha256,
                    "size_bytes": compiled.size_bytes,
                },
                "experiment_id": experiment_id,
                "graph_receipt": dict(graph.receipt),
                "recipe": {
                    "id": manifest.id,
                    "relative_path": source.relative_path,
                    "schema_version": manifest.schema_version,
                    "sha256": source.sha256,
                    "size_bytes": source.size_bytes,
                    "version": manifest.version,
                },
                "snapshot_id": snapshot_id,
            }
        )
        receipt = self._store.write_bytes(
            branch="shared",
            logical_name=f"recipe_snapshot/{snapshot_id}/manifest",
            content=receipt_bytes,
            relative_path=f"recipe_snapshots/{directory}/manifest.json",
            media_type="application/json",
            kind="recipe_snapshot_manifest",
        )
        return RecipeSnapshotPersistence(
            snapshot_id=snapshot_id,
            manifest_relative_path=receipt.relative_path,
            recipe_relative_path=source.relative_path,
            compiled_graph_relative_path=compiled.relative_path,
        )
