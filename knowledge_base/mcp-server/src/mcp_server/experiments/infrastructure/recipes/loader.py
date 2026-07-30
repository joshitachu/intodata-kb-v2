"""Strict JSON-subset YAML loader; recipes are data, never executable configuration."""

from __future__ import annotations
import json
import stat
from datetime import datetime
from pathlib import Path
from mcp_server.experiments.domain.recipes import (
    AgenticRecipeManifest,
    OutputContract,
    RecipeBudgets,
    SourceRequirement,
    TaskTemplate,
)
from mcp_server.experiments.domain.scope import ScopeBudgets, ScopePredicate, ScopeSpec, TraversalSpec

_FORBIDDEN = ("python", "bash", "shell", "subprocess", "apply_patch")
_MANIFEST_KEYS = {
    "id", "version", "schema_version", "source_requirements", "default_scope", "roles",
    "required_model_capabilities", "allowed_tools", "budgets", "output_contracts",
    "task_templates", "subject_partitions", "evidence_profiles", "certification_id",
    "validators", "quality_gates", "metadata",
}


def load_recipe(path: Path) -> AgenticRecipeManifest:
    try:
        mode = path.lstat().st_mode
    except OSError as error:
        raise ValueError("recipe asset is unavailable") from error
    if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
        raise ValueError("recipe asset must be a regular non-symlink file")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("recipe must be valid UTF-8 JSON-subset YAML") from error
    if not isinstance(data, dict):
        raise ValueError("recipe manifest must be an object")
    _strict_keys(data, _MANIFEST_KEYS, "recipe manifest")
    serialized = json.dumps(data).lower()
    if any(word in serialized for word in _FORBIDDEN):
        raise ValueError("agentic recipe may not name executable code or shell")
    return AgenticRecipeManifest(
        id=data["id"],
        version=data["version"],
        schema_version=data.get("schema_version", 1),
        source_requirements=tuple(_source_requirement(item) for item in _list(data, "source_requirements")),
        default_scope=parse_scope(data.get("default_scope", {})),
        roles=_strings(data.get("roles", []), "roles"),
        required_model_capabilities=frozenset(
            _strings(data.get("required_model_capabilities", []), "required_model_capabilities")
        ),
        allowed_tools=_strings(data.get("allowed_tools", []), "allowed_tools"),
        budgets=RecipeBudgets(**_mapping(data.get("budgets", {}), "recipe budgets")),
        output_contracts=tuple(_output_contract(item) for item in _list(data, "output_contracts")),
        task_templates=tuple(_task_template(item) for item in _list(data, "task_templates")),
        subject_partitions=_strings(data.get("subject_partitions", []), "subject_partitions"),
        evidence_profiles=_strings(data.get("evidence_profiles", []), "evidence_profiles"),
        certification_id=_optional_string(data.get("certification_id"), "certification_id"),
        validators=_strings(data.get("validators", []), "validators"),
        quality_gates=_strings(data.get("quality_gates", []), "quality_gates"),
        metadata=_mapping(data.get("metadata", {}), "metadata"),
    )


def _strict_keys(data: dict[str, object], allowed: set[str], context: str) -> None:
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise ValueError(f"{context} contains unsupported keys: {unknown}")


def _mapping(value: object, field_name: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} must be an object")
    return value


def _list(data: dict[str, object], field_name: str) -> tuple[dict[str, object], ...]:
    value = data.get(field_name, [])
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise ValueError(f"{field_name} must be a list of objects")
    return tuple(value)


def _strings(value: object, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError(f"{field_name} must be a list of strings")
    return tuple(value)


def _source_requirement(data: dict[str, object]) -> SourceRequirement:
    _strict_keys(data, {"source_role", "required_capabilities", "optional"}, "source requirement")
    optional = data.get("optional", False)
    if not isinstance(optional, bool):
        raise ValueError("source requirement optional must be boolean")
    return SourceRequirement(
        _string(data, "source_role"), frozenset(_strings(data.get("required_capabilities", []), "required_capabilities")), optional
    )


def _output_contract(data: dict[str, object]) -> OutputContract:
    _strict_keys(data, {"id", "path", "required_sections", "media_type"}, "output contract")
    return OutputContract(
        _string(data, "id"),
        _string(data, "path"),
        _strings(data.get("required_sections", []), "required_sections"),
        _string(data, "media_type") if "media_type" in data else "text/markdown",
    )


def _task_template(data: dict[str, object]) -> TaskTemplate:
    _strict_keys(
        data,
        {"id", "task_type", "role", "prompt_ref", "output_ids", "depends_on", "for_each", "dependency_output_refs", "validators"},
        "task template",
    )
    for_each = data.get("for_each")
    if for_each is not None and not isinstance(for_each, str):
        raise ValueError("task for_each must be a string or null")
    return TaskTemplate(
        _string(data, "id"), _string(data, "task_type"), _string(data, "role"), _string(data, "prompt_ref"),
        _strings(data.get("output_ids", []), "output_ids"), _strings(data.get("depends_on", []), "depends_on"),
        for_each, _strings(data.get("dependency_output_refs", []), "dependency_output_refs"),
        _strings(data.get("validators", []), "validators"),
    )


def parse_scope(value: object) -> ScopeSpec:
    """Parse the same strict generic scope schema used by recipe manifests."""

    data = _mapping(value, "default_scope")
    _strict_keys(data, {"include", "exclude", "traversal", "required_capabilities", "optional_capabilities", "requested_output_ids", "budgets", "unknown_policy", "start_at", "end_at"}, "default_scope")
    def predicates(name: str) -> tuple[ScopePredicate, ...]:
        result = []
        for item in _list(data, name):
            _strict_keys(item, {"field", "operator", "value"}, name)
            result.append(ScopePredicate(_string(item, "field"), _string(item, "operator"), item.get("value")))
        return tuple(result)
    traversal_data = _mapping(data.get("traversal", {}), "traversal")
    _strict_keys(traversal_data, {"direction", "max_depth", "relationship_predicates"}, "traversal")
    budgets_data = _mapping(data.get("budgets", {}), "scope budgets")
    _strict_keys(budgets_data, {"max_source_items", "max_evidence_items", "max_bytes", "max_input_tokens", "max_tool_calls", "max_elapsed_seconds"}, "scope budgets")
    return ScopeSpec(
        include=predicates("include"), exclude=predicates("exclude"),
        traversal=TraversalSpec(
            direction=traversal_data.get("direction", "outbound"),
            max_depth=traversal_data.get("max_depth", 0),
            relationship_predicates=frozenset(_strings(traversal_data.get("relationship_predicates", []), "relationship_predicates")),
        ),
        start_at=_datetime(data.get("start_at")), end_at=_datetime(data.get("end_at")),
        required_capabilities=frozenset(_strings(data.get("required_capabilities", []), "required_capabilities")),
        optional_capabilities=frozenset(_strings(data.get("optional_capabilities", []), "optional_capabilities")),
        requested_output_ids=_strings(data.get("requested_output_ids", []), "requested_output_ids"),
        budgets=ScopeBudgets(**budgets_data), unknown_policy=data.get("unknown_policy", "disclose"),
    )


def _datetime(value: object) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("scope timestamp must be an ISO-8601 string")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("scope timestamp must be ISO-8601") from error


def _string(data: dict[str, object], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str):
        raise ValueError(f"{key} must be a string")
    return value


def _optional_string(value: object, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string or null")
    return value
