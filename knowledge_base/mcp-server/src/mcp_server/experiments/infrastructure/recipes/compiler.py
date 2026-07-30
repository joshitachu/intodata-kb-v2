"""Deterministic compiler for bounded declarative task DAGs."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import asdict, is_dataclass, replace
from datetime import datetime
from pathlib import Path

from mcp_server.experiments.domain.recipes import AgenticRecipeManifest
from mcp_server.experiments.domain.scope import ScopeSpec
from mcp_server.experiments.domain.tasks import CompiledTask, CompiledTaskGraph
from mcp_server.experiments.infrastructure.recipes.prompt_renderer import (
    resolve_text,
    render_semantic_prompt,
)
from mcp_server.experiments.infrastructure.storage.canonical_json import canonical_json_bytes


NEXUS_MCP_TOOL_NAMES = frozenset(
    {
        "nexus_experiment_get_contract",
        "nexus_experiment_list_evidence",
        "nexus_experiment_search_evidence",
        "nexus_experiment_read_evidence",
        "nexus_experiment_traverse_relationships",
        "nexus_experiment_write_artifact",
        "nexus_experiment_submit_task",
    }
)


def compile_recipe(
    manifest: AgenticRecipeManifest,
    *,
    recipe_root: Path,
    model_capabilities: frozenset[str],
    shared_prompt_roots: tuple[Path, ...] = (),
    available_source_capabilities: Mapping[str, frozenset[str]] | None = None,
    caller_scope: ScopeSpec | None = None,
) -> CompiledTaskGraph:
    """Compile without runtime/provider branches or unbounded source expansion."""
    if not manifest.required_model_capabilities.issubset(model_capabilities):
        raise ValueError("model capability mismatch")
    if not set(manifest.allowed_tools).issubset(NEXUS_MCP_TOOL_NAMES):
        raise ValueError("recipe requires only exact Nexus MCP tool names")
    _validate_source_requirements(manifest, available_source_capabilities)
    selected_output_ids = _validate_caller_scope(manifest, caller_scope)
    effective_scope = caller_scope or manifest.default_scope

    outputs = {item.id for item in manifest.output_contracts}
    expanded: dict[str, tuple[str, ...]] = {}
    nodes: dict[str, CompiledTask] = {}
    for template in manifest.task_templates:
        values = _expansion_values(template.for_each, template.output_ids, manifest, selected_output_ids)
        if len(values) > manifest.budgets.max_fanout:
            raise ValueError("recipe fanout cap exceeded")
        node_ids: list[str] = []
        for value in values:
            node_id = template.id if value is None else f"{template.id}:{value}"
            if node_id in nodes:
                raise ValueError("duplicate expanded task node")
            output_ids = (value,) if template.for_each == "outputs" else template.output_ids
            if not set(output_ids).issubset(outputs):
                raise ValueError("unknown output contract")
            prompt = render_semantic_prompt(
                resolve_text(
                    template.prompt_ref, recipe_root=recipe_root, shared_roots=shared_prompt_roots
                ),
                output_id=value if template.for_each == "outputs" else None,
            )
            nodes[node_id] = CompiledTask(
                node_id=node_id,
                task_type=template.task_type,
                role=template.role,
                dependencies=(),
                output_ids=output_ids,
                semantic_prompt=prompt,
                dependency_output_refs=template.dependency_output_refs,
                metadata={"template_id": template.id, "validators": template.validators},
            )
            node_ids.append(node_id)
        expanded[template.id] = tuple(node_ids)
    if len(nodes) > manifest.budgets.max_nodes:
        raise ValueError("recipe node cap exceeded")

    for template in manifest.task_templates:
        dependency_nodes = tuple(
            node_id for dependency in template.depends_on for node_id in expanded[dependency]
        )
        dependency_outputs = {
            output_id for node_id in dependency_nodes for output_id in nodes[node_id].output_ids
        }
        if not set(template.dependency_output_refs).issubset(dependency_outputs):
            raise ValueError("dependency output reference is not produced by a dependency")
        for node_id in expanded[template.id]:
            nodes[node_id] = replace(nodes[node_id], dependencies=tuple(sorted(dependency_nodes)))

    order = _topological(nodes, manifest.budgets.max_depth)
    produced = [output for node in nodes.values() for output in node.output_ids]
    if len(produced) != len(set(produced)):
        raise ValueError("duplicate output producer")
    receipt = _receipt(manifest, nodes, order, selected_output_ids, effective_scope)
    return CompiledTaskGraph(
        nodes=tuple(nodes[node_id] for node_id in order),
        topological_order=tuple(order),
        sha256=receipt["graph_sha256"],
        receipt=receipt,
    )


def _expansion_values(
    for_each: str | None,
    output_ids: tuple[str, ...],
    manifest: AgenticRecipeManifest,
    selected_output_ids: tuple[str, ...],
) -> tuple[str | None, ...]:
    if for_each is None:
        return (None,)
    if for_each == "outputs":
        allowed = set(output_ids) if output_ids else set(selected_output_ids)
        return tuple(output_id for output_id in selected_output_ids if output_id in allowed)
    if for_each == "subject_partitions":
        return manifest.subject_partitions or ("all",)
    if for_each == "evidence_profiles":
        return manifest.evidence_profiles or ("default",)
    raise ValueError("unsupported declarative for_each expansion")


def _validate_source_requirements(
    manifest: AgenticRecipeManifest,
    available: Mapping[str, frozenset[str]] | None,
) -> None:
    if available is None:
        return
    for requirement in manifest.source_requirements:
        capabilities = available.get(requirement.source_role)
        if capabilities is None:
            if not requirement.optional:
                raise ValueError(f"required source role is unavailable: {requirement.source_role}")
            continue
        if not requirement.required_capabilities.issubset(capabilities):
            raise ValueError(f"source capability mismatch for role: {requirement.source_role}")


def _validate_caller_scope(
    manifest: AgenticRecipeManifest, caller_scope: ScopeSpec | None
) -> tuple[str, ...]:
    output_ids = tuple(sorted(item.id for item in manifest.output_contracts))
    if caller_scope is None:
        return output_ids
    default = manifest.default_scope
    if not _contains_all(default.include, caller_scope.include):
        raise ValueError("caller scope cannot remove recipe include predicates")
    if not _contains_all(default.exclude, caller_scope.exclude):
        raise ValueError("caller scope cannot remove recipe exclude predicates")
    if not default.required_capabilities.issubset(caller_scope.required_capabilities):
        raise ValueError("caller scope cannot remove required capabilities")
    if not caller_scope.optional_capabilities.issubset(default.optional_capabilities):
        raise ValueError("caller scope cannot add optional capabilities")
    if caller_scope.traversal.max_depth > default.traversal.max_depth:
        raise ValueError("caller scope cannot broaden traversal depth")
    if caller_scope.traversal.direction != default.traversal.direction:
        raise ValueError("caller scope cannot change traversal direction")
    if not caller_scope.traversal.relationship_predicates.issubset(
        default.traversal.relationship_predicates
    ):
        raise ValueError("caller scope cannot broaden relationship predicates")
    if not _within_time_window(default.start_at, default.end_at, caller_scope.start_at, caller_scope.end_at):
        raise ValueError("caller scope cannot broaden time window")
    if caller_scope.unknown_policy != default.unknown_policy:
        raise ValueError("caller scope cannot change unknown policy")
    for field_name in asdict(default.budgets):
        if getattr(caller_scope.budgets, field_name) > getattr(default.budgets, field_name):
            raise ValueError("caller scope cannot broaden budgets")
    requested = caller_scope.requested_output_ids or output_ids
    if not set(requested).issubset(output_ids):
        raise ValueError("caller scope requested unknown output")
    return tuple(sorted(requested))


def _within_time_window(
    default_start: datetime | None,
    default_end: datetime | None,
    caller_start: datetime | None,
    caller_end: datetime | None,
) -> bool:
    if default_start is not None and (caller_start is None or caller_start < default_start):
        return False
    if default_end is not None and (caller_end is None or caller_end > default_end):
        return False
    return True


def _contains_all(required: tuple[object, ...], actual: tuple[object, ...]) -> bool:
    """Equality-based containment supports predicate values such as lists or mappings."""
    return all(any(candidate == item for candidate in actual) for item in required)


def _topological(nodes: Mapping[str, CompiledTask], max_depth: int) -> list[str]:
    remaining = {node_id: set(node.dependencies) for node_id, node in nodes.items()}
    order: list[str] = []
    while remaining:
        ready = sorted(node_id for node_id, dependencies in remaining.items() if not dependencies)
        if not ready:
            raise ValueError("recipe task graph contains a cycle")
        order.extend(ready)
        for node_id in ready:
            remaining.pop(node_id)
        for dependencies in remaining.values():
            dependencies.difference_update(ready)
    depth: dict[str, int] = {}
    for node_id in order:
        depth[node_id] = 1 + max((depth[parent] for parent in nodes[node_id].dependencies), default=0)
    if max(depth.values(), default=0) > max_depth:
        raise ValueError("recipe depth cap exceeded")
    return order


def _receipt(
    manifest: AgenticRecipeManifest,
    nodes: Mapping[str, CompiledTask],
    order: list[str],
    selected_output_ids: tuple[str, ...],
    effective_scope: ScopeSpec,
) -> dict[str, str]:
    prompt_payload = [(node_id, nodes[node_id].semantic_prompt) for node_id in order]
    source_payload = [
        (item.source_role, sorted(item.required_capabilities), item.optional)
        for item in manifest.source_requirements
    ]
    output_payload = [
        (item.id, item.path, item.required_sections, item.media_type)
        for item in manifest.output_contracts
    ]
    graph_payload = {
        "nodes": [
            {
                "dependencies": nodes[node_id].dependencies,
                "dependency_output_refs": nodes[node_id].dependency_output_refs,
                "metadata": dict(nodes[node_id].metadata),
                "node_id": node_id,
                "output_ids": nodes[node_id].output_ids,
                "role": nodes[node_id].role,
                "semantic_prompt": nodes[node_id].semantic_prompt,
                "task_type": nodes[node_id].task_type,
            }
            for node_id in order
        ],
        "selected_output_ids": selected_output_ids,
        "effective_scope": _scope_payload(effective_scope),
    }
    graph_sha = hashlib.sha256(canonical_json_bytes(graph_payload)).hexdigest()
    return {
        "graph_sha256": graph_sha,
        "manifest_sha256": hashlib.sha256(
            canonical_json_bytes(recipe_manifest_payload(manifest))
        ).hexdigest(),
        "output_contracts_sha256": hashlib.sha256(canonical_json_bytes(output_payload)).hexdigest(),
        "semantic_prompts_sha256": hashlib.sha256(canonical_json_bytes(prompt_payload)).hexdigest(),
        "source_requirements_sha256": hashlib.sha256(canonical_json_bytes(source_payload)).hexdigest(),
        "effective_scope_sha256": hashlib.sha256(
            canonical_json_bytes(_scope_payload(effective_scope))
        ).hexdigest(),
    }


def recipe_manifest_payload(manifest: AgenticRecipeManifest) -> dict[str, object]:
    """Return the canonical, execution-relevant resolved recipe document."""

    return {
        "allowed_tools": manifest.allowed_tools,
        "budgets": asdict(manifest.budgets),
        "certification_id": manifest.certification_id,
        "default_scope": _jsonable(manifest.default_scope),
        "evidence_profiles": manifest.evidence_profiles,
        "id": manifest.id,
        "metadata": dict(manifest.metadata),
        "output_contracts": [_jsonable(item) for item in manifest.output_contracts],
        "quality_gates": manifest.quality_gates,
        "required_model_capabilities": sorted(manifest.required_model_capabilities),
        "roles": manifest.roles,
        "schema_version": manifest.schema_version,
        "source_requirements": [_jsonable(item) for item in manifest.source_requirements],
        "subject_partitions": manifest.subject_partitions,
        "task_templates": [_jsonable(item) for item in manifest.task_templates],
        "validators": manifest.validators,
        "version": manifest.version,
    }


def _scope_payload(scope: ScopeSpec) -> dict[str, object]:
    return _jsonable(scope)  # type: ignore[return-value]


def _jsonable(value: object) -> object:
    if is_dataclass(value):
        return _jsonable(asdict(value))
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))}
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return [_jsonable(item) for item in sorted(value)]
    return value
