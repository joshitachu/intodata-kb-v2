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
    GetExecutionTimelineInput,
    GetRuntimeIncidentInput,
    ListExecutionsInput,
    ListRuntimeArtifactsInput,
    ListRuntimeConnectionsInput,
    ListRuntimeDependenciesInput,
    ListRuntimeEnginesInput,
    ListRuntimeHealthInput,
    ListRuntimeInventoryInput,
    ListRuntimeJobsInput,
    ListRuntimePlansInput,
    ListRuntimeSyncStatusInput,
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
        RuntimeToolSpec(
            name="list_runtime_sync_status",
            description=(
                "Return source/project runtime freshness and sync health from the central DB. "
                "Use this when answering runtime status or failure questions so the final "
                "answer can say how fresh the data is."
            ),
            input_model=ListRuntimeSyncStatusInput,
        ),
        RuntimeToolSpec(
            name="list_runtime_health",
            description=(
                "Return DB-derived unhealthy job/source rollups: failure rate, current failure "
                "streak, last failure, duration percentiles, recurrence, and row reject ratio "
                "when component metrics exist."
            ),
            input_model=ListRuntimeHealthInput,
        ),
        RuntimeToolSpec(
            name="list_runtime_sources",
            description=(
                "List connected runtime sources from the central DB, including source type, "
                "sync status, demo flag, last_synced_at, and last_seen_at. Use for source "
                "freshness and 'what runtime data do we have?' questions."
            ),
            input_model=ListRuntimeInventoryInput,
        ),
        RuntimeToolSpec(
            name="list_runtime_engines",
            description=(
                "List runtime engines from the central DB with normalized status, source, "
                "environment, workspace, version, and last_seen_at. Use for 'which engines "
                "do we have?' and engine inventory questions."
            ),
            input_model=ListRuntimeEnginesInput,
        ),
        RuntimeToolSpec(
            name="list_runtime_jobs",
            description=(
                "List runtime jobs/tasks from the central DB with source, environment, "
                "workspace, assigned engine, owner, artifact, and last_seen_at. Use for "
                "job/task inventory questions."
            ),
            input_model=ListRuntimeJobsInput,
        ),
        RuntimeToolSpec(
            name="list_runtime_artifacts",
            description=(
                "List runtime artifacts/packages from the central DB with source, workspace, "
                "type, version, owner, and timestamps. Use for artifact inventory and "
                "outdated or unowned artifact questions."
            ),
            input_model=ListRuntimeArtifactsInput,
        ),
        RuntimeToolSpec(
            name="list_runtime_connections",
            description=(
                "List runtime connection metadata from the central DB. Returns only "
                "sanitized labels/types/workspace/owner fields, never connection parameters "
                "or credentials."
            ),
            input_model=ListRuntimeConnectionsInput,
        ),
        RuntimeToolSpec(
            name="list_runtime_dependencies",
            description=(
                "Answer dependency questions from DB facts, such as which jobs use a "
                "connection or artifact. Filter by dependency_type ('connection' or "
                "'artifact'), dependency_id, job_id, source_key, or project_id."
            ),
            input_model=ListRuntimeDependenciesInput,
        ),
        RuntimeToolSpec(
            name="get_runtime_dependency_summary",
            description=(
                "Return extended metadata availability and hygiene facts: artifact count, "
                "connection count, dependency edge count, unowned metadata, and which "
                "metadata categories are unavailable for the current source."
            ),
            input_model=ListRuntimeInventoryInput,
        ),
        RuntimeToolSpec(
            name="list_runtime_plans",
            description=(
                "List orchestration plans from the central DB with source, environment, "
                "workspace, steps, owner, status, and last_seen_at. Use for plan inventory "
                "questions."
            ),
            input_model=ListRuntimePlansInput,
        ),
        RuntimeToolSpec(
            name="list_runtime_unhealthy",
            description=(
                "Return one DB-derived estate health summary: overview counts, source "
                "freshness, unhealthy engines, unhealthy jobs, and unhealthy plans. Use for "
                "'what is broken/unhealthy?' questions before diagnosing a specific run."
            ),
            input_model=ListRuntimeInventoryInput,
        ),
        # --- Low-level lookup tools (use to find execution IDs) ---
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
