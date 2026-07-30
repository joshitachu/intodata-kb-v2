"""Strict, provider-neutral Grounded orchestration contract compiled from recipe data."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Mapping

from mcp_server.experiments.domain.recipes import AgenticRecipeManifest


@dataclass(frozen=True)
class GroundedRole:
    id: str
    agent: str
    stage: str
    owns: tuple[str, ...]
    depends_on: tuple[str, ...]
    source_globs: tuple[str, ...]
    max_context_bytes: int
    model_policy: str = "inherit"


@dataclass(frozen=True)
class GroundedExecutionPack:
    recipe_id: str
    recipe_version: str
    roles: tuple[GroundedRole, ...]
    required_primary_artifacts: tuple[str, ...]
    validators: tuple[str, ...]
    workflow_mode: str
    max_parallelism: int
    repair_limit: int
    max_source_items: int
    max_source_bytes: int
    max_context_bytes: int
    max_input_tokens: int
    max_tool_calls: int

    @property
    def owned_artifacts(self) -> tuple[str, ...]:
        return tuple(path for role in self.roles for path in role.owns)

    @property
    def required_artifacts(self) -> tuple[str, ...]:
        return self.owned_artifacts + self.required_primary_artifacts


def load_grounded_execution_pack(
    manifest: AgenticRecipeManifest,
) -> GroundedExecutionPack:
    """Parse Grounded metadata with closed keys and graph/ownership validation."""

    raw = manifest.metadata.get("grounded_execution")
    data = _mapping(raw, "metadata.grounded_execution")
    _strict_keys(
        data,
        {
            "roles",
            "required_primary_artifacts",
            "validators",
            "max_parallelism",
            "repair_limit",
            "max_context_bytes",
            "max_input_tokens",
            "max_tool_calls",
            "workflow_mode",
        },
        "grounded execution",
    )
    roles = tuple(_role(item) for item in _object_list(data, "roles"))
    if not roles:
        raise ValueError("grounded execution must declare roles")
    role_ids = tuple(role.id for role in roles)
    if len(role_ids) != len(set(role_ids)):
        raise ValueError("grounded role ids must be unique")
    known_roles = set(role_ids)
    if any(not set(role.depends_on).issubset(known_roles) for role in roles):
        raise ValueError("grounded role dependency references an unknown role")
    if any(role.id in role.depends_on for role in roles):
        raise ValueError("grounded role cannot depend on itself")
    _topological_roles(roles)

    primary = _strings(data.get("required_primary_artifacts"), "required_primary_artifacts")
    validators = _strings(data.get("validators"), "validators")
    if not validators:
        raise ValueError("grounded execution must declare validators")
    owned = tuple(path for role in roles for path in role.owns)
    all_artifacts = owned + primary
    if len(all_artifacts) != len(set(all_artifacts)):
        raise ValueError("grounded artifact ownership must be unique")

    declared_documents = {
        f"candidate/docs/{output.path}"
        for output in manifest.output_contracts
        if output.media_type == "text/markdown"
    }
    owned_documents = {path for path in owned if path.startswith("candidate/docs/")}
    if owned_documents != declared_documents:
        raise ValueError(
            "grounded document ownership must exactly match recipe Markdown outputs"
        )

    max_parallelism = _positive_int(data, "max_parallelism")
    if max_parallelism > manifest.budgets.max_concurrency:
        raise ValueError("grounded parallelism exceeds recipe concurrency budget")
    repair_limit = _nonnegative_int(data, "repair_limit")
    if repair_limit >= manifest.budgets.max_attempts:
        raise ValueError("grounded repair limit exceeds recipe attempt budget")
    max_context_bytes = _positive_int(data, "max_context_bytes")
    max_input_tokens = _positive_int(data, "max_input_tokens")
    max_tool_calls = _positive_int(data, "max_tool_calls")
    if max_context_bytes > manifest.default_scope.budgets.max_bytes:
        raise ValueError("grounded context budget exceeds recipe source budget")
    if max_input_tokens > manifest.default_scope.budgets.max_input_tokens:
        raise ValueError("grounded token budget exceeds recipe scope budget")
    if max_tool_calls > manifest.default_scope.budgets.max_tool_calls:
        raise ValueError("grounded tool budget exceeds recipe scope budget")
    if sum(role.max_context_bytes for role in roles) > max_context_bytes:
        raise ValueError("grounded role context budgets exceed aggregate context budget")

    return GroundedExecutionPack(
        recipe_id=manifest.id,
        recipe_version=manifest.version,
        roles=roles,
        required_primary_artifacts=primary,
        validators=validators,
        workflow_mode=_workflow_mode(data),
        max_parallelism=max_parallelism,
        repair_limit=repair_limit,
        max_source_items=manifest.default_scope.budgets.max_source_items,
        max_source_bytes=manifest.default_scope.budgets.max_bytes,
        max_context_bytes=max_context_bytes,
        max_input_tokens=max_input_tokens,
        max_tool_calls=max_tool_calls,
    )


def grounded_pack_payload(pack: GroundedExecutionPack) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "recipe_id": pack.recipe_id,
        "recipe_version": pack.recipe_version,
        "max_parallelism": pack.max_parallelism,
        "repair_limit": pack.repair_limit,
        "budgets": {
            "max_source_items": pack.max_source_items,
            "max_source_bytes": pack.max_source_bytes,
            "max_context_bytes": pack.max_context_bytes,
            "max_input_tokens": pack.max_input_tokens,
            "max_tool_calls": pack.max_tool_calls,
        },
        "validators": list(pack.validators),
        "workflow_mode": pack.workflow_mode,
        "roles": [
            {
                "id": role.id,
                "agent": role.agent,
                "stage": role.stage,
                "owns": list(role.owns),
                "depends_on": list(role.depends_on),
                "source_globs": list(role.source_globs),
                "max_context_bytes": role.max_context_bytes,
                "model_policy": role.model_policy,
            }
            for role in pack.roles
        ],
        "required_primary_artifacts": list(pack.required_primary_artifacts),
    }


def _role(value: Mapping[str, object]) -> GroundedRole:
    data = dict(value)
    _strict_keys(
        data,
        {
            "id",
            "agent",
            "stage",
            "owns",
            "depends_on",
            "source_globs",
            "max_context_bytes",
            "model_policy",
        },
        "grounded role",
    )
    role = GroundedRole(
        id=_string(data, "id"),
        agent=_string(data, "agent"),
        stage=_string(data, "stage"),
        owns=_strings(data.get("owns"), "owns"),
        depends_on=_strings(data.get("depends_on", []), "depends_on"),
        source_globs=_strings(data.get("source_globs"), "source_globs"),
        max_context_bytes=_positive_int(data, "max_context_bytes"),
        model_policy=str(data.get("model_policy") or "inherit"),
    )
    if not role.owns or not role.source_globs:
        raise ValueError("grounded role must own artifacts and declare source globs")
    if role.model_policy != "inherit":
        raise ValueError("grounded role model policy must be provider-neutral 'inherit'")
    for path in role.owns:
        _safe_relative(path, "grounded owned artifact")
        if not path.startswith("candidate/"):
            raise ValueError("grounded owned artifact must be candidate-local")
    for pattern in role.source_globs:
        _safe_glob(pattern)
    return role


def _topological_roles(roles: tuple[GroundedRole, ...]) -> None:
    remaining = {role.id: set(role.depends_on) for role in roles}
    while remaining:
        ready = [role_id for role_id, dependencies in remaining.items() if not dependencies]
        if not ready:
            raise ValueError("grounded role graph contains a cycle")
        for role_id in ready:
            remaining.pop(role_id)
        for dependencies in remaining.values():
            dependencies.difference_update(ready)


def _safe_relative(value: str, field_name: str) -> None:
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError(f"{field_name} must be a contained POSIX path")


def _safe_glob(value: str) -> None:
    if value.startswith("/") or "\\" in value or ".." in PurePosixPath(value).parts:
        raise ValueError("grounded source glob must remain workspace-relative")


def _mapping(value: object, field_name: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} must be an object")
    return value


def _object_list(data: Mapping[str, object], field_name: str) -> tuple[dict[str, object], ...]:
    value = data.get(field_name)
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise ValueError(f"{field_name} must be a list of objects")
    return tuple(value)


def _strings(value: object, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
        raise ValueError(f"{field_name} must be a list of non-blank strings")
    result = tuple(value)
    if len(result) != len(set(result)):
        raise ValueError(f"{field_name} must be unique")
    return result


def _string(data: Mapping[str, object], field_name: str) -> str:
    value = data.get(field_name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-blank string")
    return value


def _positive_int(data: Mapping[str, object], field_name: str) -> int:
    value = data.get(field_name)
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError(f"{field_name} must be a positive integer")
    return value


def _nonnegative_int(data: Mapping[str, object], field_name: str) -> int:
    value = data.get(field_name)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{field_name} must be a non-negative integer")
    return value


def _workflow_mode(data: Mapping[str, object]) -> str:
    value = data.get("workflow_mode")
    if value not in {"legacy_replay", "recipe"}:
        raise ValueError(
            "grounded workflow_mode must be 'legacy_replay' or 'recipe'"
        )
    return str(value)


def _strict_keys(data: Mapping[str, object], allowed: set[str], context: str) -> None:
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise ValueError(f"{context} contains unsupported keys: {unknown}")
