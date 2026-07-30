from __future__ import annotations

from pathlib import Path

import pytest

from mcp_server.experiments.application.grounded_pack import (
    load_grounded_execution_pack,
)
from mcp_server.experiments.infrastructure.recipes.loader import load_recipe


_RECIPE_ROOT = Path(__file__).parents[2] / "data" / "agentic_recipes"


@pytest.mark.parametrize(
    ("recipe_id", "expected_documents"),
    (
        ("alitalend_legacy_equivalence", 10),
        ("logistics_operations", 2),
    ),
)
def test_grounded_execution_is_recipe_driven_and_platform_neutral(
    recipe_id: str,
    expected_documents: int,
) -> None:
    manifest = load_recipe(_RECIPE_ROOT / recipe_id / "recipe.yaml")

    pack = load_grounded_execution_pack(manifest)

    assert pack.recipe_id == manifest.id
    assert (
        len(
            [
                path
                for path in pack.owned_artifacts
                if path.startswith("candidate/docs/")
            ]
        )
        == expected_documents
    )
    assert all(role.model_policy == "inherit" for role in pack.roles)
    assert all(role.source_globs for role in pack.roles)


def test_grounded_execution_rejects_undeclared_document_ownership() -> None:
    manifest = load_recipe(
        _RECIPE_ROOT / "logistics_operations" / "recipe.yaml"
    )
    grounded = dict(manifest.metadata["grounded_execution"])  # type: ignore[arg-type]
    roles = [dict(item) for item in grounded["roles"]]  # type: ignore[index]
    roles[2]["owns"] = ["candidate/docs/wrong.md"]
    grounded["roles"] = roles
    object.__setattr__(manifest, "metadata", {"grounded_execution": grounded})

    with pytest.raises(
        ValueError,
        match="ownership must exactly match",
    ):
        load_grounded_execution_pack(manifest)
