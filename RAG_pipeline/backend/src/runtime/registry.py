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
                "List recent job/pipeline executions (id, job name, state, timestamps), "
                "optionally filtered by project, job name, or time window. Use this to "
                "find executions or to obtain an execution_id for a job the user named "
                "before calling get_execution_details or get_execution_logs."
            ),
            input_model=ListExecutionsInput,
        ),
        RuntimeToolSpec(
            name="get_execution_details",
            description=(
                "Get full details for ONE execution. Requires execution_id. If you do "
                "not have the id, first call list_executions or detect_failures to get it."
            ),
            input_model=GetExecutionDetailsInput,
        ),
        RuntimeToolSpec(
            name="detect_failures",
            description=(
                "List failed executions (id, job name, failed-at, error summary), "
                "optionally within a time window. Use this for 'what failed' questions; "
                "its results also give you the execution_id needed for get_execution_logs."
            ),
            input_model=DetectFailuresInput,
        ),
        RuntimeToolSpec(
            name="get_execution_logs",
            description=(
                "Get the log lines for ONE execution. Requires execution_id. If the user "
                "refers to a run by name or as 'that execution' without an id, first call "
                "detect_failures or list_executions to obtain the execution_id."
            ),
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
