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


# --- Normalized execution/log models (EP-004 slice 2) ---


class RuntimeExecutionState(str, Enum):
    SUCCESS = "success"
    FAILED = "failed"
    RUNNING = "running"
    UNKNOWN = "unknown"


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
