"""Acceptance coverage for declarative recipes and deterministic task compilation."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from mcp_server.experiments.domain.recipes import (
    AgenticRecipeManifest,
    OutputContract,
    RecipeBudgets,
    SourceRequirement,
    TaskTemplate,
)
from mcp_server.experiments.domain.scope import ScopeBudgets, ScopePredicate, ScopeSpec
from mcp_server.experiments.infrastructure.recipes.compiler import (
    NEXUS_MCP_TOOL_NAMES,
    compile_recipe,
)
from mcp_server.experiments.infrastructure.recipes.loader import load_recipe
from mcp_server.experiments.infrastructure.recipes.pack_discovery import discover_agentic_recipe
from mcp_server.experiments.infrastructure.recipes.prompt_renderer import resolve_text


RECIPE_ROOT = Path(__file__).parents[2] / "data" / "agentic_recipes"


def _manifest(
    *,
    templates: tuple[TaskTemplate, ...] | None = None,
    output_contracts: tuple[OutputContract, ...] | None = None,
) -> AgenticRecipeManifest:
    return AgenticRecipeManifest(
        id="generic-recipe",
        version="1",
        schema_version=1,
        source_requirements=(SourceRequirement("evidence", frozenset({"searchable_text"})),),
        default_scope=ScopeSpec(
            required_capabilities=frozenset({"searchable_text"}),
            budgets=ScopeBudgets(max_source_items=10, max_evidence_items=10, max_bytes=1000, max_input_tokens=1000, max_tool_calls=10, max_elapsed_seconds=10),
        ),
        task_templates=templates
        or (
            TaskTemplate(
                "write", "synthesis", "writer", "prompts/document.md", ("document",), validators=("markdown",)
            ),
        ),
        output_contracts=output_contracts
        or (OutputContract("document", "document.md", ("Overview",)),),
        roles=("writer", "analyst"),
        required_model_capabilities=frozenset({"tools"}),
        allowed_tools=("nexus_experiment_read_evidence", "nexus_experiment_write_artifact"),
        budgets=RecipeBudgets(max_nodes=20, max_depth=4, max_fanout=10),
        validators=("markdown",),
        quality_gates=("complete",),
    )


def _recipe_root(tmp_path: Path) -> Path:
    root = tmp_path / "recipe"
    (root / "prompts").mkdir(parents=True)
    (root / "prompts" / "document.md").write_text("Write {output_id}", encoding="utf-8")
    return root


def _available_sources() -> dict[str, frozenset[str]]:
    return {"evidence": frozenset({"searchable_text", "structured_records"})}


def test_production_recipes_declare_generic_requirements_and_exact_tools() -> None:
    expected_outputs = {"alitalend_legacy_equivalence": 10, "logistics_operations": 2}
    for recipe_name, output_count in expected_outputs.items():
        manifest = load_recipe(RECIPE_ROOT / recipe_name / "recipe.yaml")
        assert len([item for item in manifest.output_contracts if item.media_type == "text/markdown"]) == output_count
        assert manifest.source_requirements
        assert manifest.validators and manifest.quality_gates
        assert manifest.budgets.max_nodes > 0
        assert set(manifest.allowed_tools) == NEXUS_MCP_TOOL_NAMES
        assert all("talend" not in requirement.source_role for requirement in manifest.source_requirements)
        graph = compile_recipe(
            manifest,
            recipe_root=RECIPE_ROOT / recipe_name,
            model_capabilities=frozenset({"tools"}),
        )
        evidence = next(node for node in graph.nodes if node.node_id == "evidence")
        analysis = next(node for node in graph.nodes if node.node_id == "analysis")
        final_documents = [node for node in graph.nodes if node.task_type == "synthesis"]
        assert evidence.output_ids == ("evidence_profile",)
        assert analysis.dependencies == ("evidence",)
        assert analysis.dependency_output_refs == ("evidence_profile",)
        assert len(final_documents) == output_count
        assert all(node.dependencies == ("analysis",) for node in final_documents)
        assert evidence.semantic_prompt.strip() and analysis.semantic_prompt.strip()
        assert evidence.semantic_prompt != analysis.semantic_prompt
        assert "{output_id}" not in evidence.semantic_prompt
        assert "{output_id}" not in analysis.semantic_prompt
        assert all(document.semantic_prompt != evidence.semantic_prompt for document in final_documents)
        assert all(document.semantic_prompt != analysis.semantic_prompt for document in final_documents)


def test_loader_rejects_invalid_unknown_and_executable_manifest_content(tmp_path: Path) -> None:
    path = tmp_path / "recipe.yaml"
    path.write_text(json.dumps({"id": "x", "version": "1", "roles": [], "unknown": 1}), encoding="utf-8")
    with pytest.raises(ValueError, match="unsupported keys"):
        load_recipe(path)
    path.write_text(
        json.dumps(
            {
                "id": "x", "version": "1", "roles": ["writer"], "source_requirements": [{"source_role": "evidence", "required_capabilities": ["text"]}],
                "allowed_tools": ["nexus_experiment_read_evidence"], "output_contracts": [{"id": "doc", "path": "doc.md"}],
                "task_templates": [{"id": "write", "task_type": "python", "role": "writer", "prompt_ref": "x.md"}],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="executable"):
        load_recipe(path)


def test_source_capabilities_and_caller_scope_cannot_broaden(tmp_path: Path) -> None:
    root = _recipe_root(tmp_path)
    manifest = _manifest()
    with pytest.raises(ValueError, match="source capability"):
        compile_recipe(
            manifest,
            recipe_root=root,
            model_capabilities=frozenset({"tools"}),
            available_source_capabilities={"evidence": frozenset({"structured_records"})},
        )
    widened = replace(manifest.default_scope, traversal=replace(manifest.default_scope.traversal, max_depth=1))
    with pytest.raises(ValueError, match="traversal"):
        compile_recipe(
            manifest,
            recipe_root=root,
            model_capabilities=frozenset({"tools"}),
            available_source_capabilities=_available_sources(),
            caller_scope=widened,
        )
    narrowed = replace(
        manifest.default_scope,
        requested_output_ids=("document",),
        budgets=replace(manifest.default_scope.budgets, max_source_items=5),
    )
    graph = compile_recipe(
        manifest,
        recipe_root=root,
        model_capabilities=frozenset({"tools"}),
        available_source_capabilities=_available_sources(),
        caller_scope=narrowed,
    )
    assert graph.topological_order == ("write",)


def test_true_dag_dependency_output_binding_and_topological_order(tmp_path: Path) -> None:
    root = _recipe_root(tmp_path)
    manifest = _manifest(
        templates=(
            TaskTemplate("analyse", "analysis", "analyst", "prompts/document.md", ("analysis",)),
            TaskTemplate(
                "write", "synthesis", "writer", "prompts/document.md", ("document",), ("analyse",), dependency_output_refs=("analysis",)
            ),
        ),
        output_contracts=(
            OutputContract("analysis", "analysis.json", media_type="application/json"),
            OutputContract("document", "document.md"),
        ),
    )
    graph = compile_recipe(manifest, recipe_root=root, model_capabilities=frozenset({"tools"}))

    assert graph.topological_order == ("analyse", "write")
    assert graph.nodes[1].dependencies == ("analyse",)
    assert graph.nodes[1].dependency_output_refs == ("analysis",)
    invalid = replace(manifest, task_templates=(manifest.task_templates[0], replace(manifest.task_templates[1], dependency_output_refs=("document",))))
    with pytest.raises(ValueError, match="dependency output"):
        compile_recipe(invalid, recipe_root=root, model_capabilities=frozenset({"tools"}))


def test_cycle_fanout_duplicate_output_and_model_mismatch_are_rejected(tmp_path: Path) -> None:
    root = _recipe_root(tmp_path)
    cycle = _manifest(
        templates=(
            TaskTemplate("a", "analysis", "analyst", "prompts/document.md", depends_on=("b",)),
            TaskTemplate("b", "analysis", "analyst", "prompts/document.md", depends_on=("a",)),
        )
    )
    with pytest.raises(ValueError, match="cycle"):
        compile_recipe(cycle, recipe_root=root, model_capabilities=frozenset({"tools"}))
    fanout = replace(_manifest(), subject_partitions=tuple(f"p{index}" for index in range(11)), task_templates=(TaskTemplate("write", "synthesis", "writer", "prompts/document.md", ("document",), for_each="subject_partitions"),))
    with pytest.raises(ValueError, match="fanout"):
        compile_recipe(fanout, recipe_root=root, model_capabilities=frozenset({"tools"}))
    duplicate = _manifest(templates=(TaskTemplate("a", "synthesis", "writer", "prompts/document.md", ("document",)), TaskTemplate("b", "synthesis", "writer", "prompts/document.md", ("document",))))
    with pytest.raises(ValueError, match="duplicate output"):
        compile_recipe(duplicate, recipe_root=root, model_capabilities=frozenset({"tools"}))
    with pytest.raises(ValueError, match="model capability"):
        compile_recipe(_manifest(), recipe_root=root, model_capabilities=frozenset())


def test_safe_asset_symlink_and_certified_pack_discovery_rules(tmp_path: Path) -> None:
    root = _recipe_root(tmp_path)
    outside = tmp_path / "outside.md"
    outside.write_text("outside", encoding="utf-8")
    (root / "prompts" / "link.md").symlink_to(outside)
    with pytest.raises(ValueError, match="approved"):
        resolve_text("prompts/link.md", recipe_root=root)
    pack_root = tmp_path / "pack"
    pack_root.mkdir()
    assert discover_agentic_recipe(pack_root=pack_root, certified=True) is None
    (pack_root / "agentic.yaml").write_text("{}", encoding="utf-8")
    assert discover_agentic_recipe(pack_root=pack_root, certified=False) is None
    assert discover_agentic_recipe(pack_root=pack_root, certified=True) == pack_root / "agentic.yaml"
    (pack_root / "agentic.yaml").unlink()
    (pack_root / "agentic.yaml").symlink_to(outside)
    assert discover_agentic_recipe(pack_root=pack_root, certified=True) is None


def test_full_receipt_is_deterministic_and_contains_all_hashes(tmp_path: Path) -> None:
    root = _recipe_root(tmp_path)
    manifest = _manifest()
    first = compile_recipe(manifest, recipe_root=root, model_capabilities=frozenset({"tools"}))
    second = compile_recipe(manifest, recipe_root=root, model_capabilities=frozenset({"tools"}))

    assert first.sha256 == second.sha256
    assert first.receipt == second.receipt
    assert set(first.receipt) == {
        "graph_sha256", "manifest_sha256", "output_contracts_sha256", "semantic_prompts_sha256", "source_requirements_sha256"
        , "effective_scope_sha256"
    }
    assert all(len(value) == 64 for value in first.receipt.values())


def test_effective_scope_is_hashed_and_supports_unhashable_predicate_values(tmp_path: Path) -> None:
    root = _recipe_root(tmp_path)
    base_scope = replace(
        _manifest().default_scope,
        include=(ScopePredicate("metadata.labels", "contains", ["approved"]),),
        exclude=(ScopePredicate("metadata.flags", "contains", {"draft": True}),),
    )
    manifest = replace(_manifest(), default_scope=base_scope)
    narrowed_scope = replace(
        base_scope,
        include=base_scope.include + (ScopePredicate("source_role", "equals", "evidence"),),
        budgets=replace(base_scope.budgets, max_tool_calls=5),
    )
    first = compile_recipe(manifest, recipe_root=root, model_capabilities=frozenset({"tools"}))
    second = compile_recipe(
        manifest,
        recipe_root=root,
        model_capabilities=frozenset({"tools"}),
        caller_scope=narrowed_scope,
    )

    assert first.receipt["effective_scope_sha256"] != second.receipt["effective_scope_sha256"]
    assert first.sha256 != second.sha256
