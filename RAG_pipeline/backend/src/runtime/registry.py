"""Fixed allowlist of generic runtime tools (EP-004 slice 2).

Closed over four PoC tools. Unknown tool names are rejected here, before any
provider is reached — provider adapters only ever see validated input models.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ValidationError

from src.runtime.models import (
    DetectFailuresInput,
    GetExecutionDetailsInput,
    GetExecutionLogsInput,
    ListExecutionsInput,
    RuntimeToolDefinition,
)


class UnknownRuntimeToolError(Exception):
    """Raised when a tool name is outside the fixed allowlist."""


class RuntimeToolValidationError(Exception):
    """Raised when tool call arguments fail validation against the tool's input model."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@dataclass(frozen=True)
class RuntimeToolSpec:
    name: str
    description: str
    input_model: type[BaseModel]


_TOOL_SPECS: dict[str, RuntimeToolSpec] = {
    spec.name: spec
    for spec in (
        RuntimeToolSpec(
            name="list_executions",
            description=(
                "List recent job/pipeline executions, optionally filtered by "
                "project, job name, or time window."
            ),
            input_model=ListExecutionsInput,
        ),
        RuntimeToolSpec(
            name="get_execution_details",
            description="Get details for a single execution by ID.",
            input_model=GetExecutionDetailsInput,
        ),
        RuntimeToolSpec(
            name="detect_failures",
            description="List failed executions, optionally within a time window.",
            input_model=DetectFailuresInput,
        ),
        RuntimeToolSpec(
            name="get_execution_logs",
            description="Get log events for a single execution by ID.",
            input_model=GetExecutionLogsInput,
        ),
    )
}


def list_runtime_tool_definitions() -> list[RuntimeToolDefinition]:
    return [
        RuntimeToolDefinition(
            name=spec.name,
            description=spec.description,
            input_schema=spec.input_model.model_json_schema(),
        )
        for spec in _TOOL_SPECS.values()
    ]


def resolve_tool_call(tool_name: str, arguments: dict[str, Any]) -> BaseModel:
    spec = _TOOL_SPECS.get(tool_name)
    if spec is None:
        raise UnknownRuntimeToolError(tool_name)
    try:
        return spec.input_model.model_validate(arguments)
    except ValidationError as e:
        raise RuntimeToolValidationError(str(e)) from e
