from __future__ import annotations

from pathlib import Path

import pytest

from mcp_server.experiments.application.grounded_pack import (
    grounded_pack_payload,
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


def test_grounded_execution_partitions_required_and_optional_artifacts() -> None:
    manifest = load_recipe(
        _RECIPE_ROOT / "alitalend_legacy_equivalence" / "recipe.yaml"
    )

    pack = load_grounded_execution_pack(manifest)
    topology = next(role for role in pack.roles if role.id == "business-topology")

    assert topology.agent == "nexus-doc-topology"
    assert topology.depends_on == ("analysis", "analysis-scorecard")
    assert topology.failure_policy == "warn"
    assert "candidate/docs/**" not in topology.source_globs
    assert "candidate/intermediates/**" in topology.source_globs
    assert "candidate/orchestration/evidence_packets/**" in topology.source_globs
    assert pack.optional_artifacts == (
        "candidate/topology/business_topology.json",
    )
    assert "candidate/topology/business_topology.json" in pack.owned_artifacts
    assert "candidate/topology/business_topology.json" not in pack.required_artifacts
    assert all(
        role.failure_policy == "fail"
        for role in pack.roles
        if role.id != "business-topology"
    )
    payload_role = next(
        role
        for role in grounded_pack_payload(pack)["roles"]
        if role["id"] == "business-topology"
    )
    assert payload_role["failure_policy"] == "warn"
    topology_output = next(
        output for output in manifest.output_contracts if output.id == "business_topology"
    )
    assert topology_output.path == "topology/business_topology.json"
    assert topology_output.media_type == "application/json"


def test_grounded_execution_rejects_unknown_failure_policy() -> None:
    manifest = load_recipe(
        _RECIPE_ROOT / "logistics_operations" / "recipe.yaml"
    )
    grounded = dict(manifest.metadata["grounded_execution"])  # type: ignore[arg-type]
    roles = [dict(item) for item in grounded["roles"]]  # type: ignore[index]
    roles[0]["failure_policy"] = "ignore"
    grounded["roles"] = roles
    object.__setattr__(manifest, "metadata", {"grounded_execution": grounded})

    with pytest.raises(ValueError, match="failure policy must be 'fail' or 'warn'"):
        load_grounded_execution_pack(manifest)


def test_grounded_execution_rejects_warn_role_with_downstream_dependency() -> None:
    manifest = load_recipe(_RECIPE_ROOT / "logistics_operations" / "recipe.yaml")
    grounded = dict(manifest.metadata["grounded_execution"])  # type: ignore[arg-type]
    roles = [dict(item) for item in grounded["roles"]]  # type: ignore[index]
    roles[0]["failure_policy"] = "warn"
    grounded["roles"] = roles
    object.__setattr__(manifest, "metadata", {"grounded_execution": grounded})

    with pytest.raises(ValueError, match="warn roles must be leaves"):
        load_grounded_execution_pack(manifest)


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
