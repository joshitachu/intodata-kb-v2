"""Request/response models for MCP server."""

from __future__ import annotations

import datetime

from typing import Annotated

from pydantic import BaseModel, Field, BeforeValidator, field_validator

StrippedStr = Annotated[str, BeforeValidator(lambda v: v.strip() if isinstance(v, str) else v)]


# --- Project ---

class ProjectResponse(BaseModel):
    id: str
    display_name: str
    repo_url: str | None = None
    local_path: str
    platform: str
    doc_generation_provider: str | None = None
    source_type: str
    status: str
    last_scan_at: datetime.datetime | None = None
    last_generate_at: datetime.datetime | None = None
    last_rag_push_at: datetime.datetime | None = None
    job_count: int = 0
    doc_count: int = 0
    error_message: str | None = None
    last_ingested_commit_sha: str | None = None
    auto_reingest_enabled: bool = True
    auto_reingest_last_checked_at: datetime.datetime | None = None
    auto_reingest_error: str | None = None
    git_update_summary: dict | None = None
    has_github_token: bool = False
    webhook_status: str | None = None  # "active" | "not_registered" | "unknown" | None
    instruction_pack_dir: str | None = None
    created_at: datetime.datetime | None = None
    updated_at: datetime.datetime | None = None


class ProjectListResponse(BaseModel):
    projects: list[ProjectResponse]
    total: int


class PlatformDescriptorResponse(BaseModel):
    id: str
    label: str
    unit_label: str
    unit_label_plural: str
    badge_color: str
    accent_color: str


class PlatformListResponse(BaseModel):
    platforms: list[PlatformDescriptorResponse]


class ModelDescriptorResponse(BaseModel):
    id: str
    provider: str
    label: str
    supports_analysis: bool
    supports_writing: bool
    is_default_analysis: bool = False
    is_default_writing: bool = False
    is_local: bool = False


class ModelListResponse(BaseModel):
    models: list[ModelDescriptorResponse]


PROJECT_ID_PATTERN = r"^[a-zA-Z0-9][a-zA-Z0-9_-]*$"


class AddRepoRequest(BaseModel):
    url: str = Field(..., description="Git URL, local path, or 'zip'")
    project_id: StrippedStr = Field(
        ..., min_length=1, max_length=100, pattern=PROJECT_ID_PATTERN,
        description="Unique project identifier",
    )
    platform: str = Field(default="talend", description="Integration platform (talend|workato|lobster|generic)")
    display_name: str | None = None
    github_token: str | None = Field(None, description="GitHub PAT for private repos and webhook auto-registration")

    @field_validator("platform")
    @classmethod
    def validate_platform(cls, v: str) -> str:
        from mcp_server.config import SUPPORTED_PLATFORMS
        if v not in SUPPORTED_PLATFORMS:
            raise ValueError(
                f"Unsupported platform: {v!r}. Must be one of {sorted(SUPPORTED_PLATFORMS)}"
            )
        return v


class UpdateGithubTokenRequest(BaseModel):
    github_token: str = Field(..., min_length=1, description="GitHub Personal Access Token")


class ScanRequest(BaseModel):
    project_id: StrippedStr = Field(..., min_length=1, max_length=100, pattern=PROJECT_ID_PATTERN)
    force: bool = False


class GenerateRequest(BaseModel):
    project_id: StrippedStr = Field(..., min_length=1, max_length=100, pattern=PROJECT_ID_PATTERN)
    template_profile_id: int | None = None
    model: str | None = Field(None, description="Analysis model override (Phase 2) — 'provider/model' format")
    write_model: str | None = Field(None, description="Doc-writing model override (Phase 3) — 'provider/model' format")
    resume: bool = Field(True, description="Resume from staging checkpoint if available")


class PushToRagRequest(BaseModel):
    project_id: StrippedStr = Field(..., min_length=1, max_length=100, pattern=PROJECT_ID_PATTERN)


class ResetStatusRequest(BaseModel):
    project_id: StrippedStr = Field(..., min_length=1, max_length=100, pattern=PROJECT_ID_PATTERN)


class UpdateProjectRequest(BaseModel):
    display_name: str | None = None
    platform: str | None = None
    doc_generation_provider: str | None = None
    instruction_pack_dir: str | None = None

    @field_validator("platform")
    @classmethod
    def validate_platform(cls, v: str | None) -> str | None:
        if v is None:
            return v
        from mcp_server.config import SUPPORTED_PLATFORMS
        if v not in SUPPORTED_PLATFORMS:
            raise ValueError(
                f"Unsupported platform: {v!r}. Must be one of {sorted(SUPPORTED_PLATFORMS)}"
            )
        return v


# --- Scan Status ---

class ScanStatusResponse(BaseModel):
    project_id: str
    status: str
    last_scan_at: datetime.datetime | None = None
    cache_age_minutes: float | None = None
    job_count: int = 0
    doc_count: int = 0
    error_message: str | None = None
    previous_job_count: int | None = None
    job_delta: int | None = None
    warnings: list[dict] = Field(default_factory=list)
    warnings_count: int = 0


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
    model_name: str | None = None
    write_model_name: str | None = None
    quality_score: float | None = None
    template_profile_id: int | None = None
    template_snapshot: dict | None = None
    auto_verdict: str | None = None
    auto_baseline_run_id: int | None = None


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


class DeleteProjectDetails(BaseModel):
    rag_cleaned: bool = False
    files_cleaned: bool = False
    rag_error: str | None = None


class DeleteProjectResponse(BaseModel):
    status: str
    message: str = ""
    details: DeleteProjectDetails = DeleteProjectDetails()


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


# --- Validation (US-105) ---

class ReviewQuestion(BaseModel):
    id: str
    question: str
    score: int = 0  # 1-5; 0 means not yet scored (questionnaire shell)


class DocReview(BaseModel):
    run_id: int
    reviewer: str
    overall: int  # 1-10
    questions: list[ReviewQuestion]
    feedback: str = ""
    reviewed_at: str  # ISO timestamp


class ReviewAggregateResponse(BaseModel):
    project_id: str
    run_id: int | None
    total_reviews: int
    average_overall: float
    average_per_question: dict[str, float]
    pass_result: bool  # average_overall >= 7.0
    reviews: list[dict]


# --- Assessment ---

class AssessmentGenerateResponse(BaseModel):
    project_id: str
    status: str  # "completed" | "failed" | "no_docs"
    html_path: str | None = None
    error: str | None = None


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


# --- Templates ---

class TemplateResponse(BaseModel):
    id: int
    slug: str
    category: str
    platform: str | None = None
    display_name: str
    description: str | None = None
    is_active: bool = True
    tier: str = "configurable"
    created_at: datetime.datetime | None = None
    updated_at: datetime.datetime | None = None
    latest_version: int | None = None


class TemplateListResponse(BaseModel):
    templates: list[TemplateResponse]
    total: int


class CreateTemplateRequest(BaseModel):
    slug: str = Field(..., min_length=1, max_length=120)
    category: str = Field(..., min_length=1, max_length=40)
    platform: str | None = None
    display_name: str = Field(..., min_length=1, max_length=255)
    description: str | None = None
    tier: str | None = None


class UpdateTemplateRequest(BaseModel):
    display_name: str | None = None
    description: str | None = None
    is_active: bool | None = None


class TemplateVersionResponse(BaseModel):
    id: int
    template_id: int
    version_number: int
    content: str | None = None  # omitted in list responses
    change_summary: str
    created_by: str | None = None
    content_hash: str | None = None
    byte_size: int | None = None
    created_at: datetime.datetime | None = None


class TemplateVersionListResponse(BaseModel):
    versions: list[TemplateVersionResponse]
    total: int


class CreateVersionRequest(BaseModel):
    content: str = Field(..., min_length=1)
    change_summary: str = Field(..., min_length=1, max_length=500)
    created_by: str | None = None


class DiffResponse(BaseModel):
    slug: str
    from_version: int
    to_version: int
    diff: str


# --- Profiles ---

class ProfileResponse(BaseModel):
    id: int
    name: str
    description: str | None = None
    platform: str | None = None
    scope: str = "global"
    scope_ref: str | None = None
    is_default: bool = False
    created_by: str | None = None
    created_at: datetime.datetime | None = None
    updated_at: datetime.datetime | None = None


class ProfileDetailResponse(ProfileResponse):
    assignments: list["AssignmentResponse"] = []


class ProfileListResponse(BaseModel):
    profiles: list[ProfileResponse]
    total: int


class CreateProfileRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    platform: str | None = None
    description: str | None = None
    scope: str = "global"
    scope_ref: str | None = None
    created_by: str | None = None


class UpdateProfileRequest(BaseModel):
    name: str | None = None
    description: str | None = None


class CloneProfileRequest(BaseModel):
    new_name: str = Field(..., min_length=1, max_length=200)
    created_by: str | None = None


# --- Assignments ---

class AssignmentResponse(BaseModel):
    id: int
    profile_id: int
    template_slug: str
    template_display_name: str
    version_id: int
    version_number: int
    created_at: datetime.datetime | None = None


class AssignmentListResponse(BaseModel):
    assignments: list[AssignmentResponse]
    total: int


class SetAssignmentRequest(BaseModel):
    slug: str
    version_id: int


class BulkSetAssignmentsRequest(BaseModel):
    assignments: list[SetAssignmentRequest]


# --- AI Summaries ---

class GenerateSummariesResponse(BaseModel):
    project_id: str
    status: str  # "started" | "completed" | "no_scan"
    summary_count: int = 0
    error_count: int = 0
    errors: list[str] = []


# --- Project Graph (Topology) ---

class GraphConnectionInfo(BaseModel):
    name: str
    connection_type: str = ""
    host: str = ""
    database: str = ""


class JobScanMetadata(BaseModel):
    purpose: str = ""
    author: str = ""
    modification_date: str = ""
    component_count: int = 0
    component_types: list[str] = []
    subgroup: str = ""
    has_docs: bool = False
    external_systems: list[str] = []
    summary: str = ""
    direction: str = ""


class ProjectGraphResponse(BaseModel):
    project_id: str
    manifest: dict
    connections: list[GraphConnectionInfo] = []
    job_metadata: dict[str, JobScanMetadata] = {}


class JobDocSection(BaseModel):
    heading: str
    content: str


class JobDetailResponse(BaseModel):
    project_id: str
    job_name: str
    group: str
    scan_data: dict = {}
    doc_sections: list[JobDocSection] = []
    summary: str = ""
    direction: str = ""
