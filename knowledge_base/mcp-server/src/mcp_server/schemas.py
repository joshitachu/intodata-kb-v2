"""Request/response models for MCP server."""

from __future__ import annotations

import datetime

from typing import Annotated

from pydantic import BaseModel, Field, BeforeValidator

StrippedStr = Annotated[str, BeforeValidator(lambda v: v.strip() if isinstance(v, str) else v)]


# --- Project ---

class ProjectResponse(BaseModel):
    id: str
    display_name: str
    repo_url: str | None = None
    local_path: str
    platform: str
    source_type: str
    status: str
    last_scan_at: datetime.datetime | None = None
    last_generate_at: datetime.datetime | None = None
    last_rag_push_at: datetime.datetime | None = None
    job_count: int = 0
    doc_count: int = 0
    error_message: str | None = None
    created_at: datetime.datetime | None = None
    updated_at: datetime.datetime | None = None


class ProjectListResponse(BaseModel):
    projects: list[ProjectResponse]
    total: int


PROJECT_ID_PATTERN = r"^[a-zA-Z0-9][a-zA-Z0-9_-]*$"


class AddRepoRequest(BaseModel):
    url: str = Field(..., description="Git URL, local path, or 'zip'")
    project_id: StrippedStr = Field(
        ..., min_length=1, max_length=100, pattern=PROJECT_ID_PATTERN,
        description="Unique project identifier",
    )
    platform: str = Field(default="talend", description="Integration platform")
    display_name: str | None = None


class ScanRequest(BaseModel):
    project_id: StrippedStr = Field(..., min_length=1, max_length=100, pattern=PROJECT_ID_PATTERN)
    force: bool = False


class GenerateRequest(BaseModel):
    project_id: StrippedStr = Field(..., min_length=1, max_length=100, pattern=PROJECT_ID_PATTERN)


class PushToRagRequest(BaseModel):
    project_id: StrippedStr = Field(..., min_length=1, max_length=100, pattern=PROJECT_ID_PATTERN)


class ResetStatusRequest(BaseModel):
    project_id: StrippedStr = Field(..., min_length=1, max_length=100, pattern=PROJECT_ID_PATTERN)


class UpdateProjectRequest(BaseModel):
    display_name: str | None = None
    platform: str | None = None


# --- Scan Status ---

class ScanStatusResponse(BaseModel):
    project_id: str
    status: str
    last_scan_at: datetime.datetime | None = None
    cache_age_minutes: float | None = None
    job_count: int = 0
    doc_count: int = 0
    error_message: str | None = None


# --- MCP Tool Responses ---

class ScanSummary(BaseModel):
    project_id: str
    status: str
    job_count: int = 0
    scan_file: str | None = None


class RagPushResult(BaseModel):
    project_id: str
    pushed: int = 0
    errors: list[str] = []


# --- Markdown Ingest (for RAG bridge) ---

class MarkdownDoc(BaseModel):
    filename: str
    content: str


# --- Scan Run ---

class ScanRunResponse(BaseModel):
    id: int
    project_id: str
    triggered_by: str
    status: str
    started_at: datetime.datetime | None = None
    completed_at: datetime.datetime | None = None
    job_count: int = 0
    log_file: str | None = None


class ScanRunListResponse(BaseModel):
    runs: list[ScanRunResponse]
    total: int


# --- Generate Run ---

class GenerateRunResponse(BaseModel):
    id: int
    project_id: str
    triggered_by: str
    status: str
    started_at: datetime.datetime | None = None
    completed_at: datetime.datetime | None = None
    cost_usd: float | None = None
    duration_ms: int | None = None
    duration_api_ms: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    num_turns: int | None = None
    session_id: str | None = None
    doc_count: int = 0
    error_message: str | None = None
    log_file: str | None = None


class GenerateRunListResponse(BaseModel):
    runs: list[GenerateRunResponse]
    total: int


class LogFileResponse(BaseModel):
    filename: str
    project_id: str
    timestamp: str
    size_bytes: int


class LogFileListResponse(BaseModel):
    files: list[LogFileResponse]
    total: int


# --- Generic ---

class StatusResponse(BaseModel):
    status: str
    message: str = ""


# --- Documentation (PDF) ---

class DocFileInfo(BaseModel):
    name: str
    type: str
    size_bytes: int
    has_pdf: bool = False
    modified_at: str


class DocListResponse(BaseModel):
    project_id: str
    docs: list[DocFileInfo]
    total: int


class PdfGenerateResponse(BaseModel):
    project_id: str
    status: str
    converted: int = 0
    failed: int = 0
    errors: list[str] = []


# --- Deep Dive ---

class DeepDiveRequest(BaseModel):
    project_id: StrippedStr = Field(..., min_length=1, max_length=100, pattern=PROJECT_ID_PATTERN)
    question: str = Field(..., min_length=3)
    session_id: str | None = None


class DeepDiveProject(BaseModel):
    id: str
    display_name: str
    platform: str
    job_count: int = 0
    last_scan_at: datetime.datetime | None = None


class DeepDiveProjectsResponse(BaseModel):
    projects: list[DeepDiveProject]
    total: int


# --- Project Usage ---

class ProjectUsageResponse(BaseModel):
    project_id: str
    total_generate_runs: int = 0
    completed_generate_runs: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cost_usd: float = 0.0
    total_scan_runs: int = 0
    completed_scan_runs: int = 0
