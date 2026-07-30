from pathlib import Path
import pytest
from mcp_server.experiments.infrastructure.recipes.compiler import compile_recipe
from mcp_server.experiments.infrastructure.recipes.loader import load_recipe

ROOT = Path(__file__).parents[2] / "data" / "agentic_recipes"


@pytest.mark.parametrize(
    "name,document_count,total_nodes", [("alitalend_legacy_equivalence", 10, 12), ("logistics_operations", 2, 4)]
)
def test_generic_recipes_compile_deterministically(
    name: str, document_count: int, total_nodes: int
) -> None:
    root = ROOT / name
    manifest = load_recipe(root / "recipe.yaml")
    first = compile_recipe(manifest, recipe_root=root, model_capabilities=frozenset({"tools"}))
    second = compile_recipe(manifest, recipe_root=root, model_capabilities=frozenset({"tools"}))
    final_documents = [node for node in first.nodes if node.task_type == "synthesis"]
    assert len(final_documents) == document_count
    assert len(first.nodes) == total_nodes and first.sha256 == second.sha256


def test_capability_and_path_guards() -> None:
    root = ROOT / "logistics_operations"
    manifest = load_recipe(root / "recipe.yaml")
    with pytest.raises(ValueError, match="capability"):
        compile_recipe(manifest, recipe_root=root, model_capabilities=frozenset())
