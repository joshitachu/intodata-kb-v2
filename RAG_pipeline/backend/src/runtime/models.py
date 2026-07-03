"""Runtime status and generic tool contract — provider-agnostic, no
Talend-specific fields or operation names."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, model_validator


class RuntimeConnectionStatus(str, Enum):
    DISABLED = "disabled"
    CONNECTED = "connected"
    UNAVAILABLE = "unavailable"


class RuntimeStatusResponse(BaseModel):
    status: RuntimeConnectionStatus
    provider_key: str | None = None
    provider_label: str
    message: str
    checked_at: datetime


class RuntimeSyncStatus(str, Enum):
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"


class RuntimeEngineStatus(str, Enum):
    ONLINE = "online"
    OFFLINE = "offline"
    DEGRADED = "degraded"
    UNKNOWN = "unknown"
    STALE = "stale"


class RuntimeExecutionState(str, Enum):
    SUCCESS = "success"
    FAILED = "failed"
    RUNNING = "running"
    UNKNOWN = "unknown"


class RuntimeSyncState(BaseModel):
    source_key: str
    project_id: str
    status: RuntimeSyncStatus
    last_synced_at: datetime | None = None
    failure_reason_code: str | None = None
    error_summary: str | None = None
    updated_at: datetime


class RuntimeSource(BaseModel):
    project_id: str
    source_key: str
    source_type: str
    label: str
    status: RuntimeSyncStatus
    is_demo: bool = False
    last_synced_at: datetime | None = None
    last_seen_at: datetime | None = None
    ingested_at: datetime


class RuntimeEnvironment(BaseModel):
    environment_id: str
    source_key: str
    name: str
    description: str | None = None
    last_seen_at: datetime | None = None


class RuntimeWorkspace(BaseModel):
    workspace_id: str
    source_key: str
    name: str
    environment_id: str | None = None
    environment_name: str | None = None
    owner: str | None = None
    workspace_type: str | None = None
    last_seen_at: datetime | None = None


class RuntimeEngine(BaseModel):
    engine_id: str
    source_key: str
    name: str
    status: RuntimeEngineStatus
    raw_status: str | None = None
    availability: str | None = None
    version: str | None = None
    environment_id: str | None = None
    environment_name: str | None = None
    workspace_id: str | None = None
    workspace_name: str | None = None
    last_seen_at: datetime | None = None
    source_updated_at: datetime | None = None


class RuntimeJob(BaseModel):
    job_id: str
    source_key: str
    name: str
    display_name: str | None = None
    job_type: str | None = None
    status: str | None = None
    environment_id: str | None = None
    environment_name: str | None = None
    workspace_id: str | None = None
    workspace_name: str | None = None
    engine_id: str | None = None
    engine_name: str | None = None
    owner: str | None = None
    artifact_name: str | None = None
    last_seen_at: datetime | None = None
    source_updated_at: datetime | None = None


class RuntimeArtifact(BaseModel):
    artifact_id: str
    source_key: str
    name: str
    artifact_type: str | None = None
    version: str | None = None
    workspace_id: str | None = None
    workspace_name: str | None = None
    owner: str | None = None
    last_seen_at: datetime | None = None
    source_updated_at: datetime | None = None


class RuntimeConnection(BaseModel):
    connection_id: str
    source_key: str
    name: str
    connection_type: str | None = None
    workspace_id: str | None = None
    workspace_name: str | None = None
    owner: str | None = None
    last_seen_at: datetime | None = None
    source_updated_at: datetime | None = None


class RuntimeJobDependency(BaseModel):
    job_id: str
    job_name: str
    source_key: str
    dependency_type: str
    dependency_id: str
    dependency_name: str | None = None
    dependency_kind: str | None = None
    workspace_id: str | None = None
    workspace_name: str | None = None
    last_seen_at: datetime | None = None


class RuntimeExtendedMetadataAvailability(BaseModel):
    artifacts_supported: bool = False
    connections_supported: bool = False
    schedules_supported: bool = False
    promotions_supported: bool = False
    clusters_supported: bool = False
    unavailable: list[str] = Field(default_factory=list)


class RuntimeJobRun(BaseModel):
    run_id: str
    job_id: str
    job_name: str
    source_key: str
    execution_id: str | None = None
    state: RuntimeExecutionState
    environment_id: str | None = None
    environment_name: str | None = None
    workspace_id: str | None = None
    workspace_name: str | None = None
    engine_id: str | None = None
    engine_name: str | None = None
    started_at: datetime
    ended_at: datetime | None = None
    duration_ms: int | None = None


class RuntimeJobHealth(BaseModel):
    project_id: str
    source_key: str
    job_id: str
    job_name: str
    environment_name: str | None = None
    workspace_name: str | None = None
    engine_name: str | None = None
    total_runs: int
    failed_runs: int
    failure_rate: float
    current_failure_streak: int
    latest_run_at: datetime | None = None
    latest_state: RuntimeExecutionState | None = None


class RuntimeEngineHealth(BaseModel):
    project_id: str
    source_key: str
    engine_id: str
    engine_name: str
    engine_status: RuntimeEngineStatus | None = None
    environment_name: str | None = None
    workspace_name: str | None = None
    total_runs: int
    failed_runs: int
    failure_rate: float
    current_failure_streak: int
    latest_run_at: datetime | None = None
    latest_state: RuntimeExecutionState | None = None
    p50_duration_ms: int | None = None
    p95_duration_ms: int | None = None


class RuntimePlanStep(BaseModel):
    step_id: str
    name: str
    job_id: str | None = None
    job_name: str | None = None
    order: int | None = None


class RuntimePlan(BaseModel):
    plan_id: str
    source_key: str
    name: str
    display_name: str | None = None
    status: str | None = None
    environment_id: str | None = None
    environment_name: str | None = None
    workspace_id: str | None = None
    workspace_name: str | None = None
    owner: str | None = None
    step_count: int | None = None
    steps: list[RuntimePlanStep] = Field(default_factory=list)
    last_seen_at: datetime | None = None
    source_updated_at: datetime | None = None


class RuntimePlanRunStepResult(BaseModel):
    step_id: str
    job_id: str | None = None
    job_name: str | None = None
    state: RuntimeExecutionState | None = None
    execution_id: str | None = None


class RuntimePlanRun(BaseModel):
    run_id: str
    plan_id: str
    plan_name: str
    source_key: str
    state: RuntimeExecutionState
    environment_id: str | None = None
    environment_name: str | None = None
    workspace_id: str | None = None
    workspace_name: str | None = None
    started_at: datetime
    ended_at: datetime | None = None
    duration_ms: int | None = None
    step_results: list[RuntimePlanRunStepResult] = Field(default_factory=list)


class RuntimePlanHealth(BaseModel):
    project_id: str
    source_key: str
    plan_id: str
    plan_name: str
    environment_name: str | None = None
    workspace_name: str | None = None
    total_runs: int
    failed_runs: int
    failure_rate: float
    current_failure_streak: int
    latest_run_at: datetime | None = None
    latest_finish_at: datetime | None = None
    latest_state: RuntimeExecutionState | None = None


class RuntimeEstateOverview(BaseModel):
    project_id: str | None = None
    sources_total: int
    sources_failed: int
    sources_partial: int
    sources_demo: int
    engines_total: int
    engines_online: int
    engines_degraded: int
    engines_offline: int
    engines_unknown: int
    engines_unhealthy: int = 0
    jobs_unhealthy: int = 0
    plans_unhealthy: int = 0
    sources_stale: int = 0
    environments_total: int
    workspaces_total: int
    last_synced_at: datetime | None = None


class RuntimeUnhealthySummary(BaseModel):
    """Compact runtime estate health summary for chat/runtime tool answers.

    It contains only DB-derived inventory and health facts. Raw runtime log rows
    and source-platform payloads are intentionally excluded.
    """

    overview: RuntimeEstateOverview
    sources: list[RuntimeSource] = Field(default_factory=list)
    unhealthy_engines: list[RuntimeEngineHealth] = Field(default_factory=list)
    unhealthy_jobs: list[RuntimeJobHealth] = Field(default_factory=list)
    unhealthy_plans: list[RuntimePlanHealth] = Field(default_factory=list)


class RuntimeDependencySummary(BaseModel):
    availability: RuntimeExtendedMetadataAvailability
    artifacts_total: int
    connections_total: int
    dependencies_total: int
    stale_artifacts: list[RuntimeArtifact] = Field(default_factory=list)
    unowned_artifacts: list[RuntimeArtifact] = Field(default_factory=list)
    unowned_connections: list[RuntimeConnection] = Field(default_factory=list)


class RuntimeComponentMetric(BaseModel):
    execution_id: str
    component_key: str
    component_type: str | None = None
    source_connector_type: str | None = None
    target_connector_type: str | None = None
    rows_total: int | None = None
    rows_rejected: int | None = None
    duration_ms: int | None = None
    measured_at: datetime | None = None


class RuntimeHealthSummary(BaseModel):
    project_id: str
    source_key: str | None = None
    job_name: str
    total_executions: int
    failed_executions: int
    failure_rate: float
    current_failure_streak: int
    last_failure_at: datetime | None = None
    last_execution_at: datetime | None = None
    p50_duration_ms: int | None = None
    p95_duration_ms: int | None = None
    rows_total: int | None = None
    rows_rejected: int | None = None
    reject_ratio: float | None = None
    recurrence_count: int | None = None
    signature_hash: str | None = None


# --- Normalized execution/log models (EP-004 slice 2) ---


class RuntimeLogSeverity(str, Enum):
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class RuntimeExecution(BaseModel):
    execution_id: str
    job_name: str
    project_name: str | None = None
    state: RuntimeExecutionState
    started_at: datetime
    ended_at: datetime | None = None
    duration_ms: int | None = None


class RuntimeFailure(BaseModel):
    execution_id: str
    job_name: str
    failed_at: datetime
    error_summary: str


class RuntimeLogEvent(BaseModel):
    execution_id: str
    timestamp: datetime
    severity: RuntimeLogSeverity
    message: str


# --- Generic tool input models ---


class ListExecutionsInput(BaseModel):
    project_name: str | None = None
    job_name: str | None = None
    state: RuntimeExecutionState | None = None
    since: datetime | None = None
    until: datetime | None = None
    limit: int = Field(default=20, ge=1, le=200)


class GetExecutionDetailsInput(BaseModel):
    execution_id: str


class DetectFailuresInput(BaseModel):
    since: datetime | None = None
    until: datetime | None = None
    limit: int = Field(default=20, ge=1, le=200)


class GetExecutionLogsInput(BaseModel):
    execution_id: str
    limit: int = Field(default=100, ge=1, le=1000)
    min_severity: RuntimeLogSeverity | None = None


class AnalyzeExecutionFailureInput(BaseModel):
    execution_id: str


class GetExecutionTimelineInput(BaseModel):
    execution_id: str
    min_severity: RuntimeLogSeverity | None = None
    limit: int = Field(default=50, ge=1, le=200)


class GetRuntimeIncidentInput(BaseModel):
    incident_id: str
    include_evidence: bool = True
    evidence_limit: int = Field(default=5, ge=1, le=20)


class FindSimilarFailuresInput(BaseModel):
    incident_id: str | None = None
    execution_id: str | None = None
    limit: int = Field(default=10, ge=1, le=50)

    @model_validator(mode="after")
    def check_one_provided(self) -> "FindSimilarFailuresInput":
        if self.incident_id is None and self.execution_id is None:
            raise ValueError("exactly one of incident_id or execution_id is required")
        if self.incident_id is not None and self.execution_id is not None:
            raise ValueError("provide exactly one of incident_id or execution_id, not both")
        return self


class ListRuntimeSyncStatusInput(BaseModel):
    source_key: str | None = None
    project_id: str | None = None
    limit: int = Field(default=20, ge=1, le=100)


class ListRuntimeHealthInput(BaseModel):
    source_key: str | None = None
    project_id: str | None = None
    limit: int = Field(default=10, ge=1, le=100)


class ListRuntimeInventoryInput(BaseModel):
    source_key: str | None = None
    project_id: str | None = None
    limit: int = Field(default=100, ge=1, le=500)


class ListRuntimeEnginesInput(ListRuntimeInventoryInput):
    status: RuntimeEngineStatus | None = None


class ListRuntimeJobsInput(ListRuntimeInventoryInput):
    environment_id: str | None = None
    workspace_id: str | None = None
    engine_id: str | None = None
    status: str | None = None


class ListRuntimeJobRunsInput(ListRuntimeInventoryInput):
    job_id: str | None = None
    engine_id: str | None = None
    state: RuntimeExecutionState | None = None
    since: datetime | None = None
    until: datetime | None = None


class ListRuntimePlansInput(ListRuntimeInventoryInput):
    environment_id: str | None = None
    workspace_id: str | None = None
    status: str | None = None


class ListRuntimePlanRunsInput(ListRuntimeInventoryInput):
    plan_id: str | None = None
    state: RuntimeExecutionState | None = None
    since: datetime | None = None
    until: datetime | None = None


class ListRuntimeArtifactsInput(ListRuntimeInventoryInput):
    workspace_id: str | None = None
    artifact_type: str | None = None


class ListRuntimeConnectionsInput(ListRuntimeInventoryInput):
    workspace_id: str | None = None
    connection_type: str | None = None


class ListRuntimeDependenciesInput(ListRuntimeInventoryInput):
    dependency_type: str | None = Field(default=None, pattern="^(artifact|connection)$")
    dependency_id: str | None = None
    job_id: str | None = None


# --- Generic tool envelope models ---


class RuntimeToolDefinition(BaseModel):
    name: str
    description: str
    input_schema: dict[str, Any]


class RuntimeToolCall(BaseModel):
    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class RuntimeToolErrorCode(str, Enum):
    DISABLED = "disabled"
    UNAVAILABLE = "unavailable"
    UNKNOWN_TOOL = "unknown_tool"
    VALIDATION_ERROR = "validation_error"
    PROVIDER_ERROR = "provider_error"
    TIMEOUT = "timeout"


class RuntimeToolError(BaseModel):
    code: RuntimeToolErrorCode
    message: str


class RuntimeToolResult(BaseModel):
    tool_name: str
    success: bool
    data: Any | None = None
    error: RuntimeToolError | None = None
    latency_ms: int
    truncated: bool = False
