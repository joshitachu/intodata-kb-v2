"""Generic compiled task-DAG value objects."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping


@dataclass(frozen=True)
class CompiledTask:
    node_id: str
    task_type: str
    role: str
    dependencies: tuple[str, ...]
    output_ids: tuple[str, ...]
    semantic_prompt: str
    dependency_output_refs: tuple[str, ...] = ()
    metadata: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class CompiledTaskGraph:
    nodes: tuple[CompiledTask, ...]
    topological_order: tuple[str, ...]
    sha256: str
    receipt: Mapping[str, str] = field(default_factory=dict)
