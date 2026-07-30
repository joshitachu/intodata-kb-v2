"""Strict, declarative recipe contracts with no runtime/provider implementation types."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

from mcp_server.experiments.domain.scope import ScopeSpec


@dataclass(frozen=True)
class SourceRequirement:
    source_role: str
    required_capabilities: frozenset[str] = field(default_factory=frozenset)
    optional: bool = False

    def __post_init__(self) -> None:
        _unique_nonblank((self.source_role,), "source requirement roles")
        _unique_nonblank(self.required_capabilities, "source requirement capabilities")


@dataclass(frozen=True)
class OutputContract:
    id: str
    path: str
    required_sections: tuple[str, ...] = ()
    media_type: str = "text/markdown"

    def __post_init__(self) -> None:
        _unique_nonblank((self.id, self.path, self.media_type), "output contract fields")
        _unique_nonblank(self.required_sections, "required sections")
        if self.path.startswith("/") or ".." in self.path.split("/"):
            raise ValueError("output contract path must be contained")


@dataclass(frozen=True)
class TaskTemplate:
    id: str
    task_type: str
    role: str
    prompt_ref: str
    output_ids: tuple[str, ...] = ()
    depends_on: tuple[str, ...] = ()
    for_each: str | None = None
    dependency_output_refs: tuple[str, ...] = ()
    validators: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _nonblank((self.id, self.task_type, self.role, self.prompt_ref), "task fields")
        _unique_nonblank(self.output_ids, "task output ids")
        _unique_nonblank(self.depends_on, "task dependencies")
        _unique_nonblank(self.dependency_output_refs, "dependency output refs")
        _unique_nonblank(self.validators, "task validators")


@dataclass(frozen=True)
class RecipeBudgets:
    max_nodes: int = 200
    max_depth: int = 20
    max_fanout: int = 50
    max_attempts: int = 2
    max_concurrency: int = 4

    def __post_init__(self) -> None:
        if any(
            value < 1
            for value in (
                self.max_nodes,
                self.max_depth,
                self.max_fanout,
                self.max_attempts,
                self.max_concurrency,
            )
        ):
            raise ValueError("recipe budgets must be positive")


@dataclass(frozen=True)
class AgenticRecipeManifest:
    id: str
    version: str
    schema_version: int
    source_requirements: tuple[SourceRequirement, ...]
    default_scope: ScopeSpec
    task_templates: tuple[TaskTemplate, ...]
    output_contracts: tuple[OutputContract, ...]
    roles: tuple[str, ...]
    required_model_capabilities: frozenset[str]
    allowed_tools: tuple[str, ...]
    budgets: RecipeBudgets = field(default_factory=RecipeBudgets)
    subject_partitions: tuple[str, ...] = ()
    evidence_profiles: tuple[str, ...] = ()
    certification_id: str | None = None
    validators: tuple[str, ...] = ()
    quality_gates: tuple[str, ...] = ()
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for value, name in ((self.id, "recipe id"), (self.version, "recipe version")):
            if not value.strip():
                raise ValueError(f"{name} must not be blank")
        if self.schema_version != 1:
            raise ValueError("unsupported agentic recipe schema version")
        _unique_nonblank(self.roles, "roles")
        _unique_nonblank((item.id for item in self.task_templates), "task template ids")
        _unique_nonblank((item.id for item in self.output_contracts), "output contract ids")
        if any(template.role not in self.roles for template in self.task_templates):
            raise ValueError("every task template role must be declared")
        if any(
            template.for_each not in {None, "outputs", "subject_partitions", "evidence_profiles"}
            for template in self.task_templates
        ):
            raise ValueError("unsupported declarative for_each expansion")
        _unique_nonblank(self.allowed_tools, "allowed tools")
        _unique_nonblank(self.required_model_capabilities, "model capabilities")
        _unique_nonblank(
            (item.source_role for item in self.source_requirements), "source requirement roles"
        )
        paths = tuple(item.path for item in self.output_contracts)
        if len(paths) != len(set(paths)):
            raise ValueError("output contract paths must be safe and unique")
        output_ids = {item.id for item in self.output_contracts}
        template_ids = {item.id for item in self.task_templates}
        if any(not set(task.output_ids).issubset(output_ids) for task in self.task_templates):
            raise ValueError("task output ids must reference declared output contracts")
        if any(not set(task.depends_on).issubset(template_ids) for task in self.task_templates):
            raise ValueError("task dependencies must reference declared templates")
        if any(task.id in task.depends_on for task in self.task_templates):
            raise ValueError("task template may not depend on itself")
        _unique_nonblank(self.validators, "recipe validators")
        _unique_nonblank(self.quality_gates, "recipe quality gates")
        if not self.source_requirements:
            raise ValueError("recipe must declare source requirements")
        if not self.allowed_tools:
            raise ValueError("recipe must declare allowed Nexus MCP tools")


def _unique_nonblank(values: object, name: str) -> None:
    items = tuple(values)  # type: ignore[arg-type]
    if any(not isinstance(item, str) or not item.strip() for item in items) or len(items) != len(
        set(items)
    ):
        raise ValueError(f"{name} must be non-blank and unique")


def _nonblank(values: object, name: str) -> None:
    if any(not isinstance(item, str) or not item.strip() for item in tuple(values)):  # type: ignore[arg-type]
        raise ValueError(f"{name} must be non-blank")
