"""Grounded-role binding for the in-process OpenAI Agents SDK runtime."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import importlib
import json
import mimetypes
from pathlib import Path, PurePosixPath
from typing import Any

from mcp_server.experiments.application.grounded_runtime import (
    BOUNDED_BUDGET_MONITORING_CAPABILITY,
    GroundedRoleRuntimeContext,
)
from mcp_server.experiments.domain.runtime import (
    ArtifactReference,
    OutputContract,
    RuntimeBudgets,
    RuntimeTaskRequest,
)
from mcp_server.experiments.mcp_tools.config_compiler import NEXUS_EXPERIMENT_TOOLS
from mcp_server.experiments.infrastructure.storage.canonical_json import canonical_json_bytes
from mcp_server.experiments.infrastructure.openai_agents.scoped_evidence import (
    MAX_READ_BYTES,
    ScopedGroundedEvidence,
)


_CONTEXT_ROOTS = ("input", "topology")
_CONTEXT_FILES = ("PACKET.json", "ROLE_PROMPT.txt")


@dataclass(frozen=True, slots=True)
class OpenAIGroundedTaskBinding:
    """Private filesystem binding behind one hash-addressed runtime request."""

    packet_root: Path
    owned_artifacts: tuple[str, ...]
    references: Mapping[str, ArtifactReference]
    evidence: ScopedGroundedEvidence
    max_output_bytes: int


class OpenAIAgentsGroundedTaskBuilder:
    """Bind a Grounded packet to scoped Nexus functions, never filesystem tools.

    Context bytes remain Nexus-owned. The SDK sees immutable references and can
    obtain bytes only through the exact functions returned by ``build_tools``.
    Candidate files can only be created at paths declared by the role pack.
    """

    def __init__(
        self,
        *,
        now: Callable[[], datetime] | None = None,
        sdk: Any | None = None,
    ) -> None:
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._sdk = sdk
        self._bindings: dict[tuple[str, str], OpenAIGroundedTaskBinding] = {}

    def build(self, context: GroundedRoleRuntimeContext) -> RuntimeTaskRequest:
        task_id = f"grounded-role:{context.role_id}:attempt:{context.attempt}"
        references = _packet_references(context)
        # This is the complete read surface for this role.  It is derived from
        # the immutable packet that Nexus created for the role, never from a
        # repository/workspace path supplied by the model.
        evidence = ScopedGroundedEvidence.from_packet(
            context.packet_root,
            max_context_bytes=context.max_context_bytes,
        )
        binding = OpenAIGroundedTaskBinding(
            packet_root=context.packet_root,
            owned_artifacts=_validate_owned_artifacts(context.owned_artifacts),
            references={reference.logical_name: reference for reference in references},
            evidence=evidence,
            max_output_bytes=context.max_context_bytes,
        )
        key = (context.experiment_id, task_id)
        existing = self._bindings.get(key)
        if existing is not None and existing != binding:
            raise ValueError("OpenAI Grounded task identity is already bound to another packet")
        self._bindings[key] = binding
        semantic_prompt = _nexus_tool_prompt(context.prompt)
        return RuntimeTaskRequest(
            experiment_id=context.experiment_id,
            task_id=task_id,
            idempotency_key=f"{context.experiment_id}:{task_id}",
            provider_id=context.provider_id,
            model_id=context.model_id,
            semantic_prompt=semantic_prompt,
            semantic_prompt_sha256=sha256(semantic_prompt.encode("utf-8")).hexdigest(),
            context_refs=references,
            output_contract=OutputContract(
                artifact_name=f"roles/{context.role_id}/candidate-bundle",
                media_type="application/vnd.nexus.grounded-role-bundle",
                max_bytes=context.max_context_bytes,
            ),
            allowed_tool_names=NEXUS_EXPERIMENT_TOOLS,
            budgets=RuntimeBudgets(
                max_input_tokens=context.max_input_tokens,
                max_tool_calls=context.max_tool_calls,
                max_tool_bytes=context.max_context_bytes,
                max_elapsed_ms=max(1, int(context.timeout_seconds * 1000)),
                max_billed_cost_usd=context.max_billed_cost_usd,
            ),
            deadline_at=self._now() + timedelta(seconds=context.timeout_seconds),
            required_capabilities=frozenset(
                {
                    "tools",
                    "streaming",
                    "usage-reporting",
                    BOUNDED_BUDGET_MONITORING_CAPABILITY,
                }
            ),
            agent_id=context.agent_id,
        )

    def build_tools(
        self,
        *,
        request: RuntimeTaskRequest,
        allowed_tool_names: tuple[str, ...],
    ) -> tuple[Any, ...]:
        if allowed_tool_names != NEXUS_EXPERIMENT_TOOLS:
            raise ValueError("OpenAI Grounded tasks require the exact Nexus tool surface")
        binding = self._binding(request)
        sdk = self._sdk or importlib.import_module("agents")
        return tuple(
            sdk.FunctionTool(
                name=name,
                description=_TOOL_DESCRIPTIONS[name],
                params_json_schema=_TOOL_SCHEMAS[name],
                on_invoke_tool=_tool_invoker(name, binding),
                strict_json_schema=True,
            )
            for name in allowed_tool_names
        )

    def resolve_output_references(
        self,
        request: RuntimeTaskRequest,
        _final_output: Any,
    ) -> tuple[ArtifactReference, ...]:
        binding = self._binding(request)
        entries: list[dict[str, object]] = []
        total_bytes = 0
        for logical_name in binding.owned_artifacts:
            path = _owned_path(binding, logical_name)
            content = _read_bound_regular(path, binding.packet_root)
            total_bytes += len(content)
            entries.append(
                {
                    "byte_size": len(content),
                    "logical_name": logical_name,
                    "media_type": _media_type(logical_name),
                    "sha256": sha256(content).hexdigest(),
                }
            )
        receipt = canonical_json_bytes({"artifacts": entries, "task_id": request.task_id})
        # The role bundle is a receipt over individually written Nexus-owned
        # artifacts. GroundedRuntimeRunner validates and persists those files.
        return (
            ArtifactReference(
                logical_name=request.output_contract.artifact_name,
                sha256=sha256(receipt).hexdigest(),
                media_type=request.output_contract.media_type,
                byte_size=total_bytes,
            ),
        )

    def _binding(self, request: RuntimeTaskRequest) -> OpenAIGroundedTaskBinding:
        binding = self._bindings.get((request.experiment_id, request.task_id))
        if binding is None:
            raise ValueError("OpenAI Grounded request has no Nexus packet binding")
        if tuple(binding.references.values()) != request.context_refs:
            raise ValueError("OpenAI Grounded request context differs from its packet binding")
        return binding


def _packet_references(
    context: GroundedRoleRuntimeContext,
) -> tuple[ArtifactReference, ...]:
    root = context.packet_root.resolve(strict=True)
    candidates: set[Path] = set()
    for name in _CONTEXT_FILES:
        path = root / name
        if path.exists():
            candidates.add(path)
    for name in _CONTEXT_ROOTS:
        path = root / name
        if not path.exists():
            continue
        if path.is_symlink() or not path.is_dir():
            raise ValueError("OpenAI Grounded context root must be a regular directory")
        for nested in path.rglob("*"):
            if nested.is_symlink():
                raise ValueError("OpenAI Grounded context must not contain symlinks")
            if nested.is_file():
                candidates.add(nested)
    references: list[ArtifactReference] = []
    total_bytes = 0
    for path in sorted(candidates, key=lambda value: value.as_posix()):
        content = _read_bound_regular(path, root)
        total_bytes += len(content)
        if total_bytes > context.max_context_bytes:
            raise ValueError("OpenAI Grounded packet exceeds the role context budget")
        logical_name = path.relative_to(root).as_posix()
        references.append(
            ArtifactReference(
                logical_name=logical_name,
                sha256=sha256(content).hexdigest(),
                media_type=_media_type(logical_name),
                byte_size=len(content),
            )
        )
    if not references or references[0].logical_name != "PACKET.json":
        if not any(reference.logical_name == "PACKET.json" for reference in references):
            raise ValueError("OpenAI Grounded packet is missing PACKET.json")
    return tuple(references)


def _validate_owned_artifacts(values: tuple[str, ...]) -> tuple[str, ...]:
    if not values or len(values) != len(set(values)):
        raise ValueError("OpenAI Grounded role outputs must be non-empty and unique")
    for value in values:
        path = PurePosixPath(value)
        if path.is_absolute() or not value.startswith("candidate/") or ".." in path.parts:
            raise ValueError("OpenAI Grounded role output escapes the candidate root")
    return values


def _nexus_tool_prompt(role_prompt: str) -> str:
    return (
        "Nexus tool binding: you have no filesystem tools. Logical paths named below are "
        "immutable evidence IDs. Use nexus_experiment_list_evidence, "
        "nexus_experiment_search_evidence, and nexus_experiment_read_evidence instead "
        "of read/glob/grep. Use nexus_experiment_write_artifact for each contract-owned "
        "path, then nexus_experiment_submit_task. Any role wording about reading or "
        "writing files means these scoped Nexus functions only.\n\n"
        f"{role_prompt}"
    )


def _tool_invoker(name: str, binding: OpenAIGroundedTaskBinding):
    async def invoke(_context: Any, arguments_json: str) -> Any:
        try:
            arguments = json.loads(arguments_json or "{}")
        except json.JSONDecodeError as error:
            raise ValueError("Nexus tool arguments must be valid JSON") from error
        if not isinstance(arguments, dict):
            raise ValueError("Nexus tool arguments must be an object")
        return _invoke_bound_tool(name, binding, arguments)

    return invoke


def _invoke_bound_tool(
    name: str,
    binding: OpenAIGroundedTaskBinding,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    if name == "nexus_experiment_get_contract":
        return {
            "allowed_tools": list(NEXUS_EXPERIMENT_TOOLS),
            "output_names": list(binding.owned_artifacts),
        }
    if name == "nexus_experiment_list_evidence":
        return binding.evidence.list(limit=_optional_positive_int(arguments, "limit", 200))
    if name == "nexus_experiment_read_evidence":
        return binding.evidence.read(
            evidence_id=_required_text(arguments, "evidence_id"),
            max_bytes=_required_positive_int(arguments, "max_bytes"),
        )
    if name == "nexus_experiment_search_evidence":
        return binding.evidence.search(
            query=_required_text(arguments, "query"),
            limit=_optional_positive_int(arguments, "limit", 20),
        )
    if name == "nexus_experiment_traverse_relationships":
        return binding.evidence.traverse(
            subject_id=_required_text(arguments, "subject_id"),
            limit=_optional_positive_int(arguments, "limit", 100),
        )
    if name == "nexus_experiment_write_artifact":
        logical_name = _required_text(arguments, "logical_name")
        media_type = _required_text(arguments, "media_type")
        content = _required_text(arguments, "content").encode("utf-8")
        if logical_name not in binding.owned_artifacts or media_type != _media_type(logical_name):
            raise PermissionError("artifact is outside the Grounded role contract")
        if (
            sum(
                path.stat().st_size
                for value in binding.owned_artifacts
                if (path := _owned_path(binding, value)).is_file()
            )
            + len(content)
            > binding.max_output_bytes
        ):
            raise PermissionError("Grounded role output exceeds its bounded packet allowance")
        destination = _owned_path(binding, logical_name)
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            with destination.open("xb") as handle:
                handle.write(content)
        except FileExistsError as error:
            raise PermissionError("Grounded role artifacts are write-once") from error
        return {
            "byte_size": len(content),
            "logical_name": logical_name,
            "media_type": media_type,
            "sha256": sha256(content).hexdigest(),
        }
    if name == "nexus_experiment_submit_task":
        missing = [
            logical_name
            for logical_name in binding.owned_artifacts
            if not _owned_path(binding, logical_name).is_file()
        ]
        if missing:
            raise PermissionError("Grounded role cannot submit before all artifacts exist")
        return {"status": "submitted"}
    raise PermissionError("unknown Nexus experiment tool")


def _read_reference(
    binding: OpenAIGroundedTaskBinding,
    logical_name: str,
) -> tuple[ArtifactReference, bytes]:
    reference = binding.references.get(logical_name)
    if reference is None:
        raise PermissionError("evidence reference is outside the Grounded packet")
    path = binding.packet_root.joinpath(*PurePosixPath(logical_name).parts)
    content = _read_bound_regular(path, binding.packet_root)
    if len(content) != reference.byte_size or sha256(content).hexdigest() != reference.sha256:
        raise PermissionError("Grounded packet changed after request binding")
    return reference, content


def _owned_path(binding: OpenAIGroundedTaskBinding, logical_name: str) -> Path:
    root = binding.packet_root.resolve(strict=True)
    path = root.joinpath(*PurePosixPath(logical_name).parts)
    try:
        path.resolve(strict=False).relative_to(root)
    except ValueError as error:
        raise PermissionError("Grounded artifact path escapes its packet") from error
    return path


def _read_bound_regular(path: Path, root: Path) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise ValueError("OpenAI Grounded context item must be a regular file")
    resolved_root = root.resolve(strict=True)
    resolved = path.resolve(strict=True)
    try:
        resolved.relative_to(resolved_root)
    except ValueError as error:
        raise ValueError("OpenAI Grounded context item escapes its packet") from error
    return resolved.read_bytes()


def _reference_payload(reference: ArtifactReference) -> dict[str, Any]:
    return {
        "evidence_id": reference.logical_name,
        "logical_name": reference.logical_name,
        "media_type": reference.media_type,
        "sha256": reference.sha256,
        "byte_size": reference.byte_size,
    }


def _required_text(arguments: dict[str, Any], name: str) -> str:
    value = arguments.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _required_positive_int(arguments: dict[str, Any], name: str) -> int:
    value = arguments.get(name)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _optional_positive_int(arguments: dict[str, Any], name: str, default: int) -> int:
    if name not in arguments:
        return default
    return _required_positive_int(arguments, name)


def _media_type(logical_name: str) -> str:
    guessed, _ = mimetypes.guess_type(logical_name)
    return guessed or "application/octet-stream"


_EMPTY_SCHEMA = {
    "type": "object",
    "properties": {},
    "required": [],
    "additionalProperties": False,
}
_TOOL_SCHEMAS: dict[str, dict[str, Any]] = {
    "nexus_experiment_get_contract": _EMPTY_SCHEMA,
    "nexus_experiment_list_evidence": {
        "type": "object",
        "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 200}},
        "required": [],
        "additionalProperties": False,
    },
    "nexus_experiment_search_evidence": {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 50},
        },
        "required": ["query"],
        "additionalProperties": False,
    },
    "nexus_experiment_read_evidence": {
        "type": "object",
        "properties": {
            "evidence_id": {"type": "string"},
            "max_bytes": {"type": "integer", "minimum": 1, "maximum": MAX_READ_BYTES},
        },
        "required": ["evidence_id", "max_bytes"],
        "additionalProperties": False,
    },
    "nexus_experiment_traverse_relationships": {
        "type": "object",
        "properties": {
            "subject_id": {"type": "string"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 200},
        },
        "required": ["subject_id"],
        "additionalProperties": False,
    },
    "nexus_experiment_write_artifact": {
        "type": "object",
        "properties": {
            "logical_name": {"type": "string"},
            "media_type": {"type": "string"},
            "content": {"type": "string"},
        },
        "required": ["logical_name", "media_type", "content"],
        "additionalProperties": False,
    },
    "nexus_experiment_submit_task": _EMPTY_SCHEMA,
}
_TOOL_DESCRIPTIONS = {
    "nexus_experiment_get_contract": "Read this immutable Grounded role contract.",
    "nexus_experiment_list_evidence": "List immutable evidence visible to this role only.",
    "nexus_experiment_search_evidence": "Search bounded, immutable evidence visible to this role only.",
    "nexus_experiment_read_evidence": "Read bounded bytes from one immutable evidence item visible to this role only.",
    "nexus_experiment_traverse_relationships": "Traverse scanner-derived technical relationships using IDs such as unit:JobName or connection:ConnectionName.",
    "nexus_experiment_write_artifact": "Write one declared Grounded role artifact exactly once.",
    "nexus_experiment_submit_task": "Submit after all declared artifacts have been written.",
}
