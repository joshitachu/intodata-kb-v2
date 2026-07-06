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
    connection_kind: str = "pat"  # "pat" | "app"
    github_account_login: str | None = None
    webhook_status: str | None = None  # "active" | "not_registered" | "unknown" | None
    instruction_pack_dir: str | None = None
    plugin_pack_id: int | None = None
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
    is_default_investigation: bool = False
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
    github_token: str | None = Field(None, description="GitHub PAT (fallback connection for private repos and webhook auto-registration)")
    installation_id: int | None = Field(
        None,
        description="GitHub App installation id (preferred connection; short-lived tokens are minted on demand)",
    )

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


class GithubAppConnectResponse(BaseModel):
    """Where to send the user to install/authorize the GitHub App."""
    install_url: str = Field(..., description="GitHub App install URL carrying a signed state token")


class GithubAppRepoItem(BaseModel):
    full_name: str | None = None
    clone_url: str | None = None
    private: bool | None = None
    default_branch: str | None = None


class GithubAppReposResponse(BaseModel):
    installation_id: int
    account_login: str | None = None
    repositories: list[GithubAppRepoItem] = Field(default_factory=list)


class ReingestionReadinessResponse(BaseModel):
    project_id: str
    source_type: str
    status: str
    ready: bool
    repairable: bool
    repaired: bool = False
    issues: list[str] = Field(default_factory=list)
    local_path_exists: bool = False
    is_git_repo: bool = False
    repo_url: str | None = None
    origin_url: str | None = None
    repo_url_had_credentials: bool = False
    origin_had_credentials: bool = False
    origin_matches_repo_url: bool | None = None
    has_github_token: bool = False
    has_last_ingested_commit: bool = False
    has_docs: bool = False
    doc_count: int = 0
    has_rag_support: bool = False
    rag_support_analysis_count: int = 0
    has_analysis_cache: bool = False
    analysis_cache_count: int = 0
    cache_support_match: bool = False
    targeted_regen_ready: bool = False
    requires_full_rebaseline: bool = False
    reason_codes: list[str] = Field(default_factory=list)
    can_incremental_regen: bool = False
    auth_failed: bool = False
    error: str | None = None


class ReingestionWebhookStatus(BaseModel):
    registered: bool = False
    status: str | None = None
    webhook_id: int | None = None
    last_observation: dict[str, object] | None = None


class ReingestionLastUpdateStatus(BaseModel):
    action: str | None = None
    outcome: str | None = None
    reason_code: str | None = None
    reason: str | None = None
    manual_review_reason: str | None = None
    error: str | None = None
    before_sha: str | None = None
    after_sha: str | None = None
    decision_strategy: str | None = None
    fallback_used: bool | None = None
    targeted_regen_ready: bool | None = None
    requires_full_rebaseline: bool | None = None
    held: bool | None = None
    touched_docs: list[str] = Field(default_factory=list)
    patched_docs: list[str] = Field(default_factory=list)
    matched_docs: list[str] = Field(default_factory=list)
    rag_result: dict[str, object] | None = None
    regen_result: dict[str, object] | None = None
    gate: dict[str, object] | None = None
    affected_docs_preview: list[str] | None = None


class ReingestionRemoteHygieneStatus(BaseModel):
    status: str
    ready: bool
    repairable: bool
    issues: list[str] = Field(default_factory=list)
    repo_url: str | None = None
    origin_url: str | None = None
    repo_url_had_credentials: bool = False
    origin_had_credentials: bool = False
    origin_matches_repo_url: bool | None = None
    has_github_token: bool = False
    auth_failed: bool = False
    error: str | None = None


class ReingestionCacheStatus(BaseModel):
    has_last_ingested_commit: bool = False
    has_docs: bool = False
    doc_count: int = 0
    has_rag_support: bool = False
    rag_support_analysis_count: int = 0
    has_analysis_cache: bool = False
    analysis_cache_count: int = 0
    cache_support_match: bool = False
    targeted_regen_ready: bool = False
    requires_full_rebaseline: bool = False
    reason_codes: list[str] = Field(default_factory=list)
    can_incremental_regen: bool = False


class ReingestionDriftStatus(BaseModel):
    """Slice 5 (US-7/US-8): mechanical drift counters since the last fully
    reconciled (manually rebuilt) commit. Read-only -- computed by folding
    the Slice 4 append-only change history; querying this never triggers
    scan/generation/patch/RAG-sync work.

    ``last_reconciled_after_sha`` is ``None`` when the project's change
    history contains no manual-full-rebuild entry yet (never reconciled
    since history began), not a fabricated zero SHA.
    """

    amendments_since_rebuild: int = 0
    failed_fact_checks_since_rebuild: int = 0
    skipped_since_rebuild: int = 0
    last_reconciled_before_sha: str | None = None
    last_reconciled_after_sha: str | None = None
    last_reconciled_at: str | None = None


class FullRebuildRequest(BaseModel):
    """Slice 5 (US-7): body for the manual full-rebuild trigger.

    ``after_sha`` is optional -- when omitted the route resolves the repo's
    current HEAD after a fast-forward-only pull, matching how the automatic
    webhook/poller paths resolve ``after_sha`` today.
    """

    after_sha: str | None = None


class ReingestionStatusResponse(BaseModel):
    project_id: str
    project_status: str
    source_type: str
    auto_reingest_enabled: bool
    auto_reingest_last_checked_at: datetime.datetime | None = None
    last_ingested_commit_sha: str | None = None
    last_rag_push_at: datetime.datetime | None = None
    webhook: ReingestionWebhookStatus
    last_update: ReingestionLastUpdateStatus
    remote_hygiene: ReingestionRemoteHygieneStatus
    cache: ReingestionCacheStatus
    drift: ReingestionDriftStatus


class ScanRequest(BaseModel):
    project_id: StrippedStr = Field(..., min_length=1, max_length=100, pattern=PROJECT_ID_PATTERN)
    force: bool = False


class GenerateRequest(BaseModel):
    project_id: StrippedStr = Field(..., min_length=1, max_length=100, pattern=PROJECT_ID_PATTERN)
    investigation_model: str | None = Field(None, description="Investigation model override (Phase 1) — 'provider/model' format")
    model: str | None = Field(None, description="Analysis model override (Phase 2) — 'provider/model' format")
    write_model: str | None = Field(None, description="Doc-writing model override (Phase 3) — 'provider/model' format")
    plugin_pack_id: int | None = Field(None, ge=1, description="Plugin pack override for this run. If set, takes precedence over the project's saved plugin_pack_id. Pass an int ID, or omit/null to use the project default.")
    resume: bool = Field(True, description="Resume from staging checkpoint if available")
    mode: str = Field("enterprise", description="'enterprise' blocks publish on all quality gates; 'draft' allows looser output")

    @field_validator("mode")
    @classmethod
    def validate_mode(cls, v: str) -> str:
        if v not in {"enterprise", "draft"}:
            raise ValueError("mode must be 'enterprise' or 'draft'")
        return v


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
    auto_verdict: str | None = None
    auto_baseline_run_id: int | None = None
    run_verdict: str | None = None
    gate_results: dict | None = None


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
