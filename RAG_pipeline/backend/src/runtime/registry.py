"""Fixed allowlist of generic runtime tools (EP-004 slice 2).

Closed over four PoC tools. Unknown tool names are rejected here, before any
provider is reached — provider adapters only ever see validated input models.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ValidationError

from src.runtime.models import (
    AnalyzeExecutionFailureInput,
    DetectFailuresInput,
    FindSimilarFailuresInput,
    GetExecutionDetailsInput,
    GetExecutionLogsInput,
    GetExecutionTimelineInput,
    GetRuntimeIncidentInput,
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
        # --- High-level insight tools (prefer these for failure analysis) -----
        RuntimeToolSpec(
            name="analyze_execution_failure",
            description=(
                "PRIMARY tool for 'why did this job fail?' questions. Returns a bounded "
                "failure-analysis object: failed component, error code, exception class, "
                "root-cause candidate, confidence, next checks, and bounded evidence. "
                "Requires execution_id — call detect_failures or list_executions first if "
                "you do not have one."
            ),
            input_model=AnalyzeExecutionFailureInput,
        ),
        RuntimeToolSpec(
            name="get_execution_timeline",
            description=(
                "Return a chronological enriched event timeline for ONE execution: each "
                "log line with extracted component name, error code, and exception class. "
                "Use for 'what happened when?' questions. Requires execution_id. "
                "Optionally filter by min_severity (debug/info/warning/error) and limit "
                "(default 50, max 200)."
            ),
            input_model=GetExecutionTimelineInput,
        ),
        RuntimeToolSpec(
            name="get_runtime_incident",
            description=(
                "Get full details for ONE incident by incident_id, including bounded "
                "evidence. Use for incident follow-up when you already have an incident_id "
                "from analyze_execution_failure or a previous call."
            ),
            input_model=GetRuntimeIncidentInput,
        ),
        RuntimeToolSpec(
            name="find_similar_failures",
            description=(
                "Answer 'has this happened before?' and 'is this recurring?' questions. "
                "Returns recurrence metadata (occurrence count, first/last seen, is_recurring) "
                "and a bounded list of prior same-signature incidents in this project. "
                "Accepts exactly one of: incident_id (from analyze_execution_failure or "
                "get_runtime_incident) or execution_id. "
                "When you only know a job name or 'latest failure', call detect_failures or "
                "analyze_execution_failure first to obtain an execution_id or incident_id. "
                "Never pass a raw signature_hash — always start from an incident or execution."
            ),
            input_model=FindSimilarFailuresInput,
        ),
        # --- Low-level tools (use to find execution IDs or for raw log drill-down) ---
        RuntimeToolSpec(
            name="list_executions",
            description=(
                "List recent job/pipeline executions (id, job name, state, timestamps), "
                "optionally filtered by project, job name, or time window. Use this to "
                "find an execution_id before calling analyze_execution_failure or "
                "get_execution_timeline."
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
                "optionally within a time window. Use this for 'what failed recently?' "
                "questions, or to obtain the execution_id needed for "
                "analyze_execution_failure."
            ),
            input_model=DetectFailuresInput,
        ),
        RuntimeToolSpec(
            name="get_execution_logs",
            description=(
                "Get raw log lines for ONE execution. Requires execution_id. Use ONLY "
                "for explicit raw-log requests — for failure analysis, prefer "
                "analyze_execution_failure instead."
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
