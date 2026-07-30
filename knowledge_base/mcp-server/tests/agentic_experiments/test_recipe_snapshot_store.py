from __future__ import annotations

import json
from pathlib import Path

from mcp_server.experiments.application.snapshot_recipe import (
    RecipeSnapshotArtifactSink,
)
from mcp_server.experiments.infrastructure.recipes.compiler import compile_recipe
from mcp_server.experiments.infrastructure.recipes.loader import load_recipe
from mcp_server.experiments.infrastructure.storage.artifact_store import ArtifactStore


def test_recipe_snapshot_freezes_source_semantic_prompts_and_graph(tmp_path: Path) -> None:
    root = Path(__file__).parents[2] / "data" / "agentic_recipes" / "logistics_operations"
    source = root / "recipe.yaml"
    manifest = load_recipe(source)
    graph = compile_recipe(
        manifest,
        recipe_root=root,
        model_capabilities=frozenset({"tools"}),
    )
    store = ArtifactStore(
        output_root=tmp_path / "output",
        project_id="project-a",
        experiment_id="2a966e39-45b9-4d7d-a02b-c0ac12697b1b",
    )

    persistence = RecipeSnapshotArtifactSink(store).save(
        experiment_id=store.experiment_id,
        snapshot_id="recipe-snapshot-a",
        manifest=manifest,
        graph=graph,
    )

    compiled = json.loads(
        (store.experiment_root / persistence.compiled_graph_relative_path).read_text(
            encoding="utf-8"
        )
    )
    assert compiled["sha256"] == graph.sha256
    assert [node["node_id"] for node in compiled["nodes"]] == list(
        graph.topological_order
    )
    assert all(node["semantic_prompt"] for node in compiled["nodes"])
    assert (
        store.experiment_root / persistence.manifest_relative_path
    ).is_file()
