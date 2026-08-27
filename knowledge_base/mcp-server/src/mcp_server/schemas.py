"""Request/response models for MCP server."""

from __future__ import annotations

import datetime
import hashlib
import json

from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

StrippedStr = Annotated[str, BeforeValidator(lambda v: v.strip() if isinstance(v, str) else v)]


# --- Governed live connectors ---

class ConnectorCapabilityResponse(BaseModel):
    id: str
    label: str
    description: str
    read_only: bool
    gather_eligible: bool
    risk: str
    accepted_entity_ref_types: list[str] = Field(default_factory=list)
    use_when: str = ""
    do_not_use_when: str = ""


class ConnectorProviderResponse(BaseModel):
    provider_id: str
    label: str
    version: str
    authentication_types: list[str]
    capabilities: list[ConnectorCapabilityResponse]
    redaction_policy_version: str
    # Directory presentation, served from the manifest so adding a provider is a
    # backend-only change: `kind` is the Type column, `build_state` decides
    # whether a Connect action is offered at all.
    kind: str = "live_evidence"
    build_state: str = "available"
    # Which store owns this provider's credential -- a statement of FACT about
    # where the secret is written, not a veto on connecting here. "connector_store"
    # is this service's own encrypted store; "runtime_sources" is the RAG
    # backend's tables, next to the scheduler that reads the credential every 60
    # seconds. Either way the dashboard may collect the credential; the value
    # tells it which lane to post to (and lets the UI say so honestly).
    managed_by: str = "connector_store"
    description: str = ""
    # Field names and shapes for a manifest-driven credential form. Never any
    # stored secret value -- `secret_fields` marks which inputs are write-only.
    configuration_schema: dict[str, object] = Field(default_factory=dict)
    secret_fields: list[str] = Field(default_factory=list)


class ConnectorCatalogResponse(BaseModel):
    providers: list[ConnectorProviderResponse]


class ConnectorCreateRequest(BaseModel):
    provider_id: StrippedStr = Field(..., min_length=1, max_length=100)
    display_name: StrippedStr = Field(..., min_length=1, max_length=255)
    safe_config: dict[str, object] = Field(default_factory=dict)
    credential_type: Literal["api_key", "basic"] = "api_key"
    credential: str = Field(..., min_length=1, max_length=20_000, repr=False)
    kibana_credential: str | None = Field(None, min_length=1, max_length=20_000, repr=False)


class ConnectorUpdateRequest(BaseModel):
    display_name: StrippedStr | None = Field(None, min_length=1, max_length=255)
    safe_config: dict[str, object] | None = None
    enabled: bool | None = None


class ConnectorRotateCredentialRequest(BaseModel):
    credential: str = Field(..., min_length=1, max_length=20_000, repr=False)
    credential_type: Literal["api_key", "basic"] | None = None
    kibana_credential: str | None = Field(None, min_length=1, max_length=20_000, repr=False)
    reuse_elasticsearch_credential: bool = True


class ConnectorResponse(BaseModel):
    id: str
    provider_id: str
    display_name: str
    ownership_scope: str
    enabled: bool
    status: str
    last_checked_at: datetime.datetime | None = None
    health_code: str | None = None
    provider_version: str
    redaction_policy_version: str
    has_credential: bool = True
    has_kibana_credential: bool = False
    created_at: datetime.datetime | None = None
    updated_at: datetime.datetime | None = None


class ConnectorListResponse(BaseModel):
    connectors: list[ConnectorResponse]


class ConnectorTestResponse(BaseModel):
    status: str
    message: str | None = None
    checked_at: datetime.datetime | None = None
    details: dict[str, object] = Field(default_factory=dict)


class ConnectorSourceResponse(BaseModel):
    id: str
    connector_instance_id: str
    source_type: str
    display_label: str
    field_profile: dict[str, object] = Field(default_factory=dict)
    discovered_at: datetime.datetime | None = None
    discovery_digest: str | None = None
    enabled: bool


class ConnectorSourceListResponse(BaseModel):
    connector_instance_id: str
    sources: list[ConnectorSourceResponse]
    total: int = 0


class ConnectorDiscoverRequest(BaseModel):
    source_ids: list[str] = Field(default_factory=list, max_length=100)
    include_kibana: bool = False
    kibana_space: StrippedStr = Field(default="default", min_length=1, max_length=200)
    data_view_id: StrippedStr | None = Field(None, min_length=1, max_length=200)


class KibanaSpaceResponse(BaseModel):
    id: str
    name: str


class KibanaSpaceListResponse(BaseModel):
    spaces: list[KibanaSpaceResponse]


class KibanaDataViewResponse(BaseModel):
    id: str
    title: str


class KibanaDataViewListResponse(BaseModel):
    data_views: list[KibanaDataViewResponse]


class ProjectConnectorBindingRequest(BaseModel):
    source_ids: list[str] = Field(..., min_length=1, max_length=100)
    capability_ids: list[str] = Field(..., min_length=1, max_length=20)
    budgets: dict[str, int] = Field(default_factory=dict)
    max_time_window_days: int | None = Field(None, ge=1, le=30)
    enabled: bool = True


class ProjectConnectorBindingResponse(BaseModel):
    id: str
    project_id: str
    connector_instance_id: str
    source_ids: list[str]
    capability_ids: list[str]
    budgets: dict[str, int] = Field(default_factory=dict)
    max_time_window_days: int | None = None
    enabled: bool
    version: int
    created_at: datetime.datetime | None = None
    updated_at: datetime.datetime | None = None


class ProjectConnectorBindingListResponse(BaseModel):
    project_id: str
    bindings: list[ProjectConnectorBindingResponse]


class ProjectAvailableConnector(BaseModel):
    connector: ConnectorResponse
    sources: list[ConnectorSourceResponse]


class ProjectAvailableConnectorsResponse(BaseModel):
    connectors: list[ProjectAvailableConnector]


# --- Runtime sources (proxied to the RAG backend) ---

class RuntimeSourceCredentialField(BaseModel):
    """One input in a runtime provider's credential form.

    Mirrors ``RuntimeSourceProvider.credential_schema()`` so the dashboard
    renders the provider's own fields and cannot invent one it does not have.
    """

    name: str
    label: str = ""
    # secret | url | text. A plain string, not a Literal: a new field type from
    # a newer backend must render as text, not blank the form.
    type: str = "text"
    required: bool = True


class RuntimeScopeSelection(BaseModel):
    """A provider's selectable ingest scope, e.g. Talend environments.

    Mirrors ``RuntimeSourceProvider.scope_selection()``. ``field`` names the
    credential key holding the chosen ids, so the connect dialog stays
    provider-neutral instead of branching on the connector.
    """

    field: str
    label: str = "Scope"
    description: str | None = None
    required: bool = True
    multiple: bool = True


class RuntimeScopeOption(BaseModel):
    """One scope a live connection reported as reachable."""

    id: str
    label: str = ""
    detail: str | None = None
    default_selected: bool = False


class RuntimeSourceTypeResponse(BaseModel):
    provider_key: str
    provider_label: str = ""
    credential_schema: list[RuntimeSourceCredentialField] = Field(default_factory=list)
    scope_selection: RuntimeScopeSelection | None = None


class RuntimeSourceTypeListResponse(BaseModel):
    source_types: list[RuntimeSourceTypeResponse]


class RuntimeSourceScopeDiscoveryRequest(BaseModel):
    """Test unsaved credentials and ask which scopes they reach.

    ``provider_id`` is the CONNECTOR id, matching
    :class:`RuntimeSourceCreateRequest`; the bridge maps it to the runtime
    provider key so the dashboard never sends the runtime spelling.
    """

    provider_id: str
    credentials: dict[str, Any] = Field(default_factory=dict)


class RuntimeSourceScopesResponse(BaseModel):
    scopes: list[RuntimeScopeOption] = Field(default_factory=list)


class RuntimeSourceResponse(BaseModel):
    """A registered runtime source, as the RAG backend reports it.

    Deliberately a subset of ``RuntimeSourceRead``: enough for the connector row
    to be honest about which project is covered and when it last synced. No
    credential field exists on the RAG model either -- secrets are write-only on
    both sides of this proxy.
    """

    id: int
    project_id: str
    source_type: str
    label: str = ""
    # success | failed | pending | ... -- a plain string so an unfamiliar status
    # renders as itself rather than failing validation.
    status: str = "unknown"
    enabled: bool = True
    sync_interval_seconds: int = 60
    disabled_reason: str | None = None
    last_synced_at: datetime.datetime | None = None


class RuntimeSourceListResponse(BaseModel):
    sources: list[RuntimeSourceResponse]
    total: int = 0


class RuntimeDocReadScopeResponse(BaseModel):
    """The slice of a project's runtime mirror that documentation runs read.

    Every field is optional with a safe default: this is a status surface, and
    an older or newer RAG backend must degrade the generate dialog's scope line,
    never blank the panel it sits in.
    """

    # None (or absent) means every mirrored environment.
    environment_ids: list[str] | None = None
    environment_names: list[str] = Field(default_factory=list)
    window_days: int | None = None
    configured: bool = False
    unavailable_environment_ids: list[str] = Field(default_factory=list)
    # True when this project reads a mirror it does not own, through a read
    # grant on another project's runtime source. Surfaced in the generate
    # dialog so an operator can see that the runtime figures in the resulting
    # documents come from a tenant shared with other projects.
    shared_read: bool = False
    shared_from: list[str] = Field(default_factory=list)
    read_project_ids: list[str] = Field(default_factory=list)


class RuntimeSourceReadGrant(BaseModel):
    """One project's permission to read a runtime source it does not own.

    Not a workspace mapping. A mapping SPLITS a source's rows between projects
    at write time; a grant SHARES the same rows with a second reader. The UI
    keeps them apart for that reason.
    """

    id: int = 0
    source_id: int = 0
    project_id: str = ""
    source_project_id: str = ""
    source_label: str = ""
    environment_ids: list[str] | None = None
    window_days: int | None = None
    # Every project whose rows the grant exposes: the source's own project plus
    # any project its workspace map re-points rows to. Shown in the UI so a
    # grant cannot quietly widen past what the operator intended.
    exposes_project_ids: list[str] = Field(default_factory=list)


class RuntimeSourceReadGrantRequest(BaseModel):
    project_id: str
    environment_ids: list[str] | None = None
    window_days: int | None = None


class RuntimeSourceReadGrantResponse(BaseModel):
    grant: RuntimeSourceReadGrant


class RuntimeSourceReadGrantListResponse(BaseModel):
    grants: list[RuntimeSourceReadGrant] = Field(default_factory=list)
    total: int = 0


class RuntimeSourceCreateRequest(BaseModel):
    """Create a runtime source for a connector catalogue provider.

    ``provider_id`` is the CONNECTOR id (e.g. ``talend_tmc``); the bridge maps it
    to the RAG runtime ``provider_key`` (``tmc``). The dashboard never sends the
    runtime key, so the two spellings cannot drift apart in the browser.

    ``project_id`` binds the mirrored history to an intodoc project and must be
    an id the dashboard's own project list returned. A runtime source filed
    under a project that does not exist mirrors real executions into a namespace
    documentation never reads.
    """

    provider_id: StrippedStr = Field(..., min_length=1, max_length=100)
    project_id: StrippedStr = Field(..., min_length=1, max_length=255)
    label: StrippedStr = Field(..., min_length=1, max_length=255)
    # Values are write-only: never logged here, never returned by either service.
    # A value may be a list: the provider's ingest-scope field (``scope_selection``,
    # e.g. Talend ``environment_ids``) carries the chosen ids as an array, and a
    # ``dict[str, str]`` rejected the whole save with "Input should be a valid
    # string" after the connection test had already succeeded.
    credentials: dict[str, str | list[str]] = Field(default_factory=dict, repr=False)
    # Matches the RAG model's floor; the scheduler polls no faster than this.
    sync_interval_seconds: int = Field(default=60, ge=30, le=86_400)


class RuntimeSourceSyncResponse(BaseModel):
    source: RuntimeSourceResponse | None = None
    status: str = "unknown"
    records_ingested: int = 0


class RunRuntimeEvidenceSummary(BaseModel):
    """Coverage of the mirrored runtime lane, read from a staged artifact.

    Counts only. The coverage artifact carries unit and job *names*; those stay
    server-side because this endpoint is a status surface, not an evidence
    surface, and orphan job names are customer-identifying.
    """

    # Which sub-project of a composite (partitioned) upload this describes.
    # None on a single-project run and on the aggregate of a composite upload.
    # Sub-project names already cross this boundary as
    # `GenerateRunResponse.partition_name`; no new disclosure.
    subproject: str | None = None
    # False whenever no coverage artifact could be read: a run that predates the
    # artifact, a project whose docs were never published, or a malformed file.
    available: bool = False
    # One of data | opted_out | unavailable | partial | binding_suspect.
    # Deliberately a plain string: an unknown mode from a newer writer must
    # degrade in the client, not blank the panel.
    mode: str = "unavailable"
    window_days: int | None = None
    units_requested: int = 0
    units_matched: int = 0
    orphan_runtime_job_count: int = 0
    # Build-plan A4: mode and completeness are different axes. `partial` means
    # not every batch was retrieved; `scope_mismatch` means the runtime source
    # lists many jobs with no scanned counterpart -- which reads either as a
    # partial scan or as a wrong project binding, and is reported as a fact
    # rather than an accusation (A3).
    partial: bool = False
    scope_mismatch: bool = False
    batches: int | None = None
    source_key: str | None = None
    # How many sub-projects an aggregate covers. 0 on a single artifact, so the
    # client can tell "this upload has one zip" from "this is not composite".
    subproject_count: int = 0


class RunConnectorEvidenceFinding(BaseModel):
    evidence_id: str
    classification: str
    summary: str | None = None


class RunConnectorEvidenceResponse(BaseModel):
    """Per-run evidence status for the run-history popover."""

    run_id: int
    # Live-connector call counters. No in-run connector evidence store exists
    # yet, so these keep their defaults; only `runtime` is populated today.
    available: bool = False
    connector_count: int = 0
    successful_calls: int = 0
    failed_calls: int = 0
    denied_calls: int = 0
    truncated_calls: int = 0
    observation_from: datetime.datetime | None = None
    observation_to: datetime.datetime | None = None
    limitations: list[str] = Field(default_factory=list)
    findings: list[RunConnectorEvidenceFinding] = Field(default_factory=list)
    # Headline coverage. For a composite (partitioned) upload this is the
    # aggregate across every sub-project, never one arbitrary sub-project.
    runtime: RunRuntimeEvidenceSummary = Field(default_factory=RunRuntimeEvidenceSummary)
    # The per-sub-project rows the aggregate was built from, so the total can be
    # read back apart. Empty for non-composite runs and for a composite child
    # run, which reports its own sub-project in `runtime` directly.
    runtime_subprojects: list[RunRuntimeEvidenceSummary] = Field(default_factory=list)


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
    # Additive scan-derived taxonomy. ``job_count`` remains the legacy total
    # design count until persistence callers migrate.
    artifact_counts: dict[str, int] = Field(default_factory=dict)
    doc_count: int = 0
    error_message: str | None = None
    last_ingested_commit_sha: str | None = None
    auto_reingest_enabled: bool = True
    auto_reingest_last_checked_at: datetime.datetime | None = None
    auto_reingest_error: str | None = None
    git_update_summary: dict | None = None
    has_github_token: bool = False
    connection_kind: str = "pat"  # "pat" | "app" | "azure_devops"
    github_account_login: str | None = None
    azure_devops_connection_id: str | None = None
    webhook_status: str | None = None  # "active" | "not_registered" | "unknown" | None
    instruction_pack_dir: str | None = None
    plugin_pack_id: int | None = None
    created_at: datetime.datetime | None = None
    updated_at: datetime.datetime | None = None


class ZipUploadReceipt(BaseModel):
    """Metadata-only proof that MCP accepted a ZIP source upload."""

    receipt: str
    filename: str
    size_bytes: int
    sha256: str


class ZipUploadResponse(ProjectResponse):
    upload_receipt: ZipUploadReceipt


class VerifyZipUploadRequest(BaseModel):
    receipt: str = Field(..., min_length=32, max_length=4096)


class ZipReplacementResponse(BaseModel):
    """A staged replacement ZIP; it never means active source was replaced."""

    project_id: str
    candidate_id: str
    source_revision_id: str
    sha256: str
    status: Literal["queued", "held", "failed", "noop"]
    reason_code: str | None = None


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
    available: bool = True
    unavailable_reason: str | None = None
    rag_capable: bool = True


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
    # Ref to check out. None follows the remote's default branch, which then
    # tracks upstream changes to that default. Pinning matters when the
    # interesting code is not on the default branch -- e.g. a Talend project
    # still on an older release branch.
    branch: str | None = Field(
        None, max_length=255, description="Branch to clone; default branch when omitted"
    )
    github_token: str | None = Field(None, description="GitHub PAT (fallback connection for private repos and webhook auto-registration)")
    installation_id: int | None = Field(
        None,
        description="GitHub App installation id (preferred connection; short-lived tokens are minted on demand)",
    )
    azure_devops_connection_id: str | None = Field(
        None,
        max_length=36,
        description="Azure DevOps organization connection id supplying the clone/fetch PAT",
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


class GithubAppInstallationItem(BaseModel):
    installation_id: int
    account_login: str | None = None


class GithubAppInstallationsResponse(BaseModel):
    installations: list[GithubAppInstallationItem] = Field(default_factory=list)


# --- Azure DevOps -------------------------------------------------------------


class AzureDevOpsConnectRequest(BaseModel):
    """Connect an Azure DevOps organization with a Personal Access Token.

    ``organization_url`` accepts anything identifying the organization: the
    organization URL, a project URL, or a repository/clone URL. The server
    normalizes it before verifying the token.
    """

    organization_url: StrippedStr = Field(
        ...,
        min_length=1,
        max_length=500,
        description="e.g. https://dev.azure.com/my-org (project and repo URLs are accepted too)",
    )
    pat: str = Field(
        ...,
        min_length=1,
        max_length=1000,
        description="Azure DevOps Personal Access Token; needs Code: Read",
    )


class AzureDevOpsConnectionItem(BaseModel):
    """A stored Azure DevOps connection. Never carries the token."""

    id: str
    organization_url: str
    organization_name: str
    account_name: str | None = None
    last_verified_at: datetime.datetime | None = None


class AzureDevOpsConnectionsResponse(BaseModel):
    connections: list[AzureDevOpsConnectionItem] = Field(default_factory=list)


class AzureDevOpsRepoItem(BaseModel):
    id: str
    name: str
    project: str
    clone_url: str | None = None
    web_url: str | None = None
    default_branch: str | None = None
    is_disabled: bool = False
    size_bytes: int = 0


class AzureDevOpsReposResponse(BaseModel):
    connection_id: str
    organization_name: str
    repositories: list[AzureDevOpsRepoItem] = Field(default_factory=list)


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


NexusPackId = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_.-]{1,79}$")]
NexusReferenceId = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_.:-]{1,159}$")]
NexusHash = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
NEXUS_PACK_SNAPSHOT_MAX_BYTES = 64 * 1024


class NexusContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class NexusStyleAudience(StrEnum):
    EXECUTIVE = "executive"
    BUSINESS = "business"
    ENGINEERING = "engineering"
    OPERATIONS = "operations"


class NexusStyleTone(StrEnum):
    NEUTRAL = "neutral"
    CONCISE = "concise"
    EXPLANATORY = "explanatory"


class NexusTechnicalDepth(StrEnum):
    SUMMARY = "summary"
    STANDARD = "standard"
    DEEP = "deep"


class NexusOutputOrganization(StrEnum):
    DIRECT_ANSWER = "direct_answer"
    SECTIONED = "sectioned"
    INCIDENT_REPORT = "incident_report"


class NexusFormattingOption(StrEnum):
    MARKDOWN = "markdown"
    BULLETED_LISTS = "bulleted_lists"
    TABLES = "tables"
    CODE_BLOCKS = "code_blocks"


class NexusCommunicationOption(StrEnum):
    QUALIFY_UNCERTAINTY = "qualify_uncertainty"
    ACTION_ORIENTED = "action_oriented"
    INCLUDE_SOURCES = "include_sources"


class NexusPresentation(NexusContractModel):
    """The complete, presentation-only Phase 3 namespace."""

    audience: NexusStyleAudience | None = None
    tone: NexusStyleTone | None = None
    technical_depth: NexusTechnicalDepth | None = None
    output_organization: NexusOutputOrganization | None = None
    formatting: list[NexusFormattingOption] = Field(default_factory=list, max_length=4)
    communication: list[NexusCommunicationOption] = Field(default_factory=list, max_length=3)

    @model_validator(mode="after")
    def validate_bounded_style(self) -> "NexusPresentation":
        if len(self.formatting) != len(set(self.formatting)):
            raise ValueError("duplicate formatting option")
        if len(self.communication) != len(set(self.communication)):
            raise ValueError("duplicate communication option")
        if not any(
            (
                self.audience,
                self.tone,
                self.technical_depth,
                self.output_organization,
                self.formatting,
                self.communication,
            )
        ):
            raise ValueError("nexus_presentation must set at least one presentation field")
        return self


class NexusAudienceRender(NexusContractModel):
    """Normalized V3 audience dimensions; raw audience text is never accepted."""

    seniority: Literal["executive", "director", "manager", "practitioner"] | None = None
    domain_familiarity: Literal["introductory", "working", "expert"] | None = None
    decision_horizon: Literal["immediate", "near_term", "strategic"] | None = None
    detail_level: Literal["brief", "standard", "detailed"] | None = None


class NexusResolvedPack(NexusContractModel):
    id: NexusPackId
    version: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9][A-Za-z0-9.+_-]{0,79}$")
    content_hash: NexusHash


class NexusPackSnapshot(NexusContractModel):
    """Validated provenance receipt; never interpreted as executable instructions."""

    policy_id: NexusReferenceId
    policy_hash: NexusHash
    foundation_pack_id: NexusPackId
    functional_pack_ids: list[NexusPackId] = Field(default_factory=list, max_length=100)
    style_pack_ids: list[NexusPackId] = Field(default_factory=list, max_length=100)
    resolved_packs: list[NexusResolvedPack] = Field(min_length=1, max_length=201)
    snapshot_hash: NexusHash

    @model_validator(mode="after")
    def validate_snapshot_shape(self) -> "NexusPackSnapshot":
        selected = [
            self.foundation_pack_id,
            *self.functional_pack_ids,
            *self.style_pack_ids,
        ]
        if len(selected) != len(set(selected)):
            raise ValueError("snapshot Pack selections must be unique")
        resolved_ids = [pack.id for pack in self.resolved_packs]
        if len(resolved_ids) != len(set(resolved_ids)):
            raise ValueError("resolved_packs must contain unique Pack IDs")
        if set(resolved_ids) != set(selected):
            raise ValueError("resolved_packs must exactly match the selected Pack IDs")
        payload = self.model_dump(mode="json")
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        if len(encoded) > NEXUS_PACK_SNAPSHOT_MAX_BYTES:
            raise ValueError(
                f"nexus_pack_snapshot exceeds {NEXUS_PACK_SNAPSHOT_MAX_BYTES} bytes"
            )
        return self


def compute_nexus_snapshot_hash(
    snapshot: NexusPackSnapshot,
    presentation: NexusPresentation | None,
) -> str:
    """Reproduce Nexus PackStackCompiler's canonical consistency hash."""
    payload = snapshot.model_dump(mode="json", exclude={"snapshot_hash"})
    payload["presentation"] = presentation.model_dump(mode="json") if presentation else None
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class PackAuditRecord(BaseModel):
    """The pack identity bound to one ``GenerateRun`` (spec section 9).

    Not a wire model; the resolved audit values a generate request implies.
    Every field is optional -- a run with no active pack records all NULL.
    """

    model_config = ConfigDict(frozen=True)

    pack_id: str | None = None
    pack_version: str | None = None
    pack_content_hash: str | None = None
    pack_activated_by: str | None = None
    pack_activated_at: datetime.datetime | None = None


class GenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: StrippedStr = Field(..., min_length=1, max_length=100, pattern=PROJECT_ID_PATTERN)
    doc_generation_provider: Literal["custom", "opencode-cli"] | None = Field(
        None,
        description="One-off generation method override; does not change project settings",
    )
    investigation_model: str | None = Field(None, description="Investigation model override (Phase 1) — 'provider/model' format")
    model: str | None = Field(None, description="Analysis model override (Phase 2) — 'provider/model' format")
    write_model: str | None = Field(None, description="Doc-writing model override (Phase 3) — 'provider/model' format")
    plugin_pack_id: int | None = Field(None, ge=1, description="Plugin pack override for this run. If set, takes precedence over the project's saved plugin_pack_id. Pass an int ID, or omit/null to use the project default.")
    nexus_pack_ids: list[NexusPackId] = Field(default_factory=list, max_length=201, description="Selected governed Nexus Pack IDs for this generation request")
    nexus_pack_snapshot: NexusPackSnapshot | None = Field(None, description="Immutable governed Pack provenance receipt; never executable input")
    nexus_presentation: NexusPresentation | None = Field(None, description="Bounded Phase 3 presentation overlay")
    # --- Pack audit record (spec section 9) -------------------------------
    # Identity fields are a FALLBACK only. When ``nexus_pack_snapshot`` is
    # present the identity is derived from it (see ``resolved_pack_audit``),
    # because the snapshot is hash-verified below and a duplicated client
    # field is not. Actor and timestamp are not expressible in the snapshot,
    # so they are always taken from the request.
    pack_id: str | None = Field(
        None,
        max_length=200,
        description="Active pack id for the audit record. Ignored when nexus_pack_snapshot is supplied (derived from it instead).",
    )
    pack_version: str | None = Field(
        None,
        max_length=64,
        description="Active pack version for the audit record. Ignored when nexus_pack_snapshot is supplied.",
    )
    pack_content_hash: str | None = Field(
        None,
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-f]{64}$",
        description="sha256 of the canonical pack manifest JSON. Ignored when nexus_pack_snapshot is supplied.",
    )
    pack_activated_by: str | None = Field(
        None,
        max_length=200,
        description="Actor identity that activated the pack, as supplied by the calling service",
    )
    pack_activated_at: datetime.datetime | None = Field(
        None, description="Activation timestamp of the pack version used for this run"
    )
    live_evidence: Literal["off", "project_default"] = Field(
        "project_default",
        description=(
            "Whether this run may use live runtime evidence. 'project_default' "
            "uses exactly the runtime sources the project already has; 'off' "
            "produces a code-only run. A run can never widen the project's "
            "grant, only decline it."
        ),
    )
    resume: bool = Field(True, description="Resume from staging checkpoint if available")
    mode: str = Field("enterprise", description="'enterprise' blocks publish on all quality gates; 'draft' allows looser output")

    def resolved_pack_audit(self) -> PackAuditRecord:
        """Resolve the pack audit record to persist on the ``GenerateRun``.

        Identity resolution order:

        1. ``nexus_pack_snapshot`` -- the snapshot's ``resolved_packs`` entry
           for the first functional pack (the user-authored lens, which is the
           thing Git review was dropped for), falling back to the foundation
           pack when the stack declares no functional pack. The snapshot's
           ``snapshot_hash`` is verified against the canonical
           snapshot/presentation payload by ``align_pack_ids_with_snapshot``,
           so these three values are tamper-evident.
        2. the explicit ``pack_id`` / ``pack_version`` / ``pack_content_hash``
           request fields -- used only when no snapshot is supplied (DB-authored
           packs activated outside the Nexus snapshot path).

        ``pack_activated_by`` / ``pack_activated_at`` always come from the
        request: neither is part of the snapshot payload.
        """
        pack_id = self.pack_id
        pack_version = self.pack_version
        content_hash = self.pack_content_hash
        snapshot = self.nexus_pack_snapshot
        if snapshot is not None:
            preferred = (
                snapshot.functional_pack_ids[0]
                if snapshot.functional_pack_ids
                else snapshot.foundation_pack_id
            )
            resolved = next(
                (pack for pack in snapshot.resolved_packs if pack.id == preferred),
                None,
            )
            if resolved is not None:
                pack_id = resolved.id
                pack_version = resolved.version
                content_hash = resolved.content_hash
        return PackAuditRecord(
            pack_id=pack_id,
            pack_version=pack_version,
            pack_content_hash=content_hash,
            pack_activated_by=self.pack_activated_by,
            pack_activated_at=self.pack_activated_at,
        )

    @field_validator("mode")
    @classmethod
    def validate_mode(cls, v: str) -> str:
        if v not in {"enterprise", "draft"}:
            raise ValueError("mode must be 'enterprise' or 'draft'")
        return v

    @field_validator("nexus_pack_ids")
    @classmethod
    def validate_nexus_pack_ids(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("nexus_pack_ids must be unique")
        return value

    @model_validator(mode="after")
    def align_pack_ids_with_snapshot(self) -> "GenerateRequest":
        if self.nexus_pack_snapshot is None:
            return self
        expected = [
            self.nexus_pack_snapshot.foundation_pack_id,
            *self.nexus_pack_snapshot.functional_pack_ids,
            *self.nexus_pack_snapshot.style_pack_ids,
        ]
        if self.nexus_pack_ids and set(self.nexus_pack_ids) != set(expected):
            raise ValueError("nexus_pack_ids must exactly match nexus_pack_snapshot selections")
        self.nexus_pack_ids = expected
        expected_hash = compute_nexus_snapshot_hash(
            self.nexus_pack_snapshot,
            self.nexus_presentation,
        )
        if self.nexus_pack_snapshot.snapshot_hash != expected_hash:
            raise ValueError(
                "nexus_pack_snapshot.snapshot_hash does not match the canonical snapshot/presentation payload"
            )
        return self


class PushToRagRequest(BaseModel):
    project_id: StrippedStr = Field(..., min_length=1, max_length=100, pattern=PROJECT_ID_PATTERN)


class EvidenceDocUpdateRequest(BaseModel):
    evidence_text: StrippedStr = Field(..., min_length=1, max_length=12000)
    evidence_refs: list[str] = Field(default_factory=list, max_length=25)
    workflow_run_id: str | None = Field(None, max_length=100)
    section: str | None = Field(None, min_length=1, max_length=200)
    expected_base_sha: str | None = Field(None, min_length=64, max_length=71)


class ChatDocPatchPreviewRequest(BaseModel):
    replacement_text: StrippedStr = Field(..., min_length=1, max_length=12000)
    evidence_refs: list[str] = Field(default_factory=list, max_length=25)
    section: StrippedStr = Field(..., min_length=1, max_length=200)
    assertion_kind: Literal["source_backed", "editorial"] = "editorial"


class ChatDocPatchActivateRequest(BaseModel):
    proposal_id: StrippedStr = Field(..., min_length=1, max_length=132)
    patch_plan_hash: StrippedStr = Field(..., min_length=64, max_length=64)


class EvidenceDocRevertRequest(BaseModel):
    receipt_path: StrippedStr = Field(..., min_length=1, max_length=500)
    expected_base_sha: str | None = Field(None, min_length=64, max_length=71)
    workflow_run_id: str | None = Field(None, max_length=100)


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
    partition_name: str | None = None
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
    run_id: int | None = None


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


class EvidenceDocUpdateResponse(BaseModel):
    project_id: str
    filename: str
    status: str
    message: str
    workflow_run_id: str | None = None
    evidence_refs: list[str]
    before_sha: str
    after_sha: str
    receipt_path: str
    docs_path: str
    rag_result: dict
    version_id: str = ""
    section: str | None = None
    revert_path: str = ""
    reconciliation_status: str = "indexed"


class ChatDocPatchPreviewResponse(BaseModel):
    proposal_id: str
    project_id: str
    filename: str
    section: str
    assertion_kind: Literal["source_backed", "editorial"]
    base_document_sha256: str
    base_knowledge_revision_id: str | None
    evidence_refs: list[str]
    patch_plan_hash: str
    compact_diff: str
    changed_scope: list[str]
    estimated_input_tokens: int
    estimated_output_tokens: int
    expires_at: str
    requires_owner_approval: bool


class ChatDocPatchActivateResponse(BaseModel):
    proposal_id: str
    candidate_id: str
    knowledge_revision_id: str
    filename: str
    before_sha: str
    after_sha: str
    assertion_kind: Literal["source_backed", "editorial"]
    changed_logical_ids: list[str]
    embedded_content_count: int
    reused_content_count: int


class EvidenceDocRevertResponse(BaseModel):
    project_id: str
    filename: str
    status: str
    message: str
    workflow_run_id: str | None = None
    before_sha: str
    after_sha: str
    version_id: str
    reverted_receipt_path: str
    receipt_path: str
    docs_path: str
    rag_result: dict
    reconciliation_status: str


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
    artifact_counts: dict[str, int] = Field(default_factory=dict)
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
    artifact_kind: str = "job"
    artifact_role: str = "standalone"


class ProjectGraphResponse(BaseModel):
    project_id: str
    manifest: dict
    connections: list[GraphConnectionInfo] = []
    job_metadata: dict[str, JobScanMetadata] = {}
    artifact_counts: dict[str, int] = Field(default_factory=dict)


# --- Project Landscape (L1/L2 topology rollup) ---

class LandscapeSystemEvidenceResponse(BaseModel):
    signal_type: str
    label: str
    detail: dict = Field(default_factory=dict)


class LandscapeSystemResponse(BaseModel):
    id: str
    name: str
    kind: str = "unknown"
    evidence: list[LandscapeSystemEvidenceResponse] = Field(default_factory=list)
    source_roots: list[str] = Field(default_factory=list)


class LandscapeApplicationResponse(BaseModel):
    id: str
    source_root: str
    source_roots: list[str] = Field(default_factory=list)
    project_id: str = ""
    label: str = ""
    description: str = ""
    role: str = "execution"
    artifact_count: int = 0
    kinds_breakdown: dict[str, int] = Field(default_factory=dict)


class LandscapeProvenanceResponse(BaseModel):
    artifact_id: str
    qualified_artifact_id: str
    source_root: str
    component_name: str = ""
    fact_type: str


class LandscapeRedactionDescriptorResponse(BaseModel):
    """Safe structural context retained for a masked URI or endpoint."""

    scheme: str | None = None
    category: str | None = None
    auth_present: bool | None = None
    auth_type: Literal[
        "none", "basic", "bearer", "api_key", "oauth", "unknown"
    ] | None = None
    tls: bool | None = None
    host_present: bool | None = None


class LandscapeEdgeResponse(BaseModel):
    source: str
    target: str
    source_kind: str
    target_kind: str
    dependency_type: str
    mechanism: str = "unknown"
    direction: str = "unknown"
    reliability: str = "unknown"
    confidence: str = "unknown"
    evidence_count: int = 0
    provenance: list[LandscapeProvenanceResponse] = Field(default_factory=list)


class LandscapeChannelResponse(BaseModel):
    channel_type: str
    channel: str
    direction: str
    artifact_id: str
    qualified_artifact_id: str
    source_root: str
    component_name: str = ""
    fact_type: str = "artifact_channel"
    redaction_descriptor: LandscapeRedactionDescriptorResponse | None = None


class LandscapeInterfaceResponse(BaseModel):
    endpoint_type: str
    direction: str
    artifact_id: str
    qualified_artifact_id: str
    source_root: str
    component_name: str = ""
    fact_type: str = "artifact_endpoint"
    redaction_descriptor: LandscapeRedactionDescriptorResponse | None = None


class LandscapeDiagnosticResponse(BaseModel):
    kind: str
    description: str
    evidence: list[str] = Field(default_factory=list)
    severity: str = "info"


class BusinessTopologyStateResponse(BaseModel):
    """Publication state for the optional AI-authored business overlay.

    The scanner landscape remains usable when this overlay is absent, stale,
    or invalid.  Keeping the state explicit prevents the UI from presenting a
    previous generation as current business truth.
    """

    available: bool = False
    validated: bool = False
    stale: bool = False
    source: str = "none"
    message: str = "Business topology has not been generated."


class ProjectLandscapeResponse(BaseModel):
    """Scanner facts plus an optional validated business-oriented overlay.

    This mirrors ``intodoc.models.Landscape`` without importing the scanner
    package into the MCP API process. Keeping an explicit response contract
    makes scanner schema drift fail loudly. ``business_topology`` is authored
    after documentation generation and is deliberately kept separate from the
    deterministic fields below.
    """

    systems: list[LandscapeSystemResponse] = Field(default_factory=list)
    applications: list[LandscapeApplicationResponse] = Field(default_factory=list)
    edges: list[LandscapeEdgeResponse] = Field(default_factory=list)
    channels: list[LandscapeChannelResponse] = Field(default_factory=list)
    interfaces: list[LandscapeInterfaceResponse] = Field(default_factory=list)
    diagnostics: list[LandscapeDiagnosticResponse] = Field(default_factory=list)
    business_topology: dict | None = None
    # Bounded factual ID catalogue used to resolve business annotations back
    # to the scanner-authored L3/L4 artifacts. It contains identities only,
    # never another semantic interpretation layer.
    topology_evidence: dict = Field(default_factory=dict)
    topology_state: BusinessTopologyStateResponse = Field(
        default_factory=BusinessTopologyStateResponse
    )
    unmapped: list[dict] = Field(default_factory=list)
    summary: dict = Field(default_factory=dict)


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


# --- Job Graph (Level 2 topology drill-down) ---

class JobGraphNode(BaseModel):
    unique_name: str
    # Talend's author-assigned LABEL parameter. The technical UNIQUE_NAME
    # remains the stable identity used for graph edges and drill-down.
    display_label: str = ""
    component_type: str
    type_explanation: str = ""
    role: str  # "input" | "transform" | "output" | "orchestration" | "logging"
    has_subgraph: bool = False       # true when a deeper level exists (tMap today)
    subjob_target: str | None = None  # tRunJob child job name (portal node)
    connection_ref: str = ""          # metadata_connection_ref for system badge
    param_summary: dict[str, str] = {}  # bounded, credential-filtered high-signal params
    has_code: bool = False
    # Plain-English description parsed from 02_COMPONENT_INVENTORY.md (already
    # credential-filtered docs). Empty when no doc row matches this component --
    # the anti-fabrication default; never synthesized.
    description: str = ""


class JobGraphEdge(BaseModel):
    name: str
    source: str
    target: str
    connector_name: str = ""
    line_style: int = 0
    # Trigger links (COMPONENT_OK / RUN_IF / SUBJOB_OK, line_style != 0) are
    # control flow; FLOW/LOOKUP rows are data flow. Frontend styles them apart.
    is_control_flow: bool = False
    # Deterministic explanation of the Talend connector semantics.
    explanation: str = ""


class TMapTableSummary(BaseModel):
    input_tables: list[str] = []
    output_tables: list[str] = []


class JobGraphResponse(BaseModel):
    project_id: str
    job_name: str
    group: str
    nodes: list[JobGraphNode] = []
    edges: list[JobGraphEdge] = []
    tmap_tables: dict[str, TMapTableSummary] = {}


# --- Component Graph (Level 3 topology drill-down: tMap column graph) ---
#
# tMap only today (see job_graph_service module docstring + topology-
# lineage-runtime-architecture.md S2). Every other component type is a 404
# with a {"fallback": "inspector"} detail -- never an empty/fabricated graph.

class ComponentGraphTable(BaseModel):
    table_name: str
    columns: list[str] = []
    # Upstream component feeding this row, resolved via the job's
    # `connections` array (input tables only; None for Var/output tables).
    source_component: str | None = None


class ComponentGraphEdge(BaseModel):
    target_table: str
    target_column: str
    expression: str
    # Real reference resolution lands in slice S4 (deterministic expression
    # tokenizer). Until then this stays empty and confidence stays "opaque" --
    # only the raw expression is meaningful at this stage.
    resolved_refs: list[str] = []
    confidence: str = "opaque"  # widens to certain|likely|dynamic|opaque in S4


class ComponentGraphResponse(BaseModel):
    project_id: str
    job_name: str
    component_unique_name: str
    input_tables: list[ComponentGraphTable] = []
    var_table: ComponentGraphTable = ComponentGraphTable(table_name="Var")
    output_tables: list[ComponentGraphTable] = []
    edges: list[ComponentGraphEdge] = []


# --- Field/column lineage query (S4: GET /lineage/field) ---
#
# Served from `scan/lineage.json` (intodoc.lineage deterministic pass, see
# topology-lineage-runtime-architecture.md sec 2.2). Lineage does not cross
# job boundaries yet (cross-job stitching is S5), so every node in a single
# response belongs to the same `job_name`.

class LineageTransformationStep(BaseModel):
    transformation_type: str = ""
    expression: str = ""
    human_explanation: str = ""


class LineageFieldNode(BaseModel):
    job_name: str
    table: str
    column: str
    # Component owning this table/column, resolved via the job's connections
    # (best-effort -- empty when no entry in the lineage graph pins it down).
    component: str = ""


class LineageFieldEdge(BaseModel):
    job_name: str
    source_table: str = ""
    source_column: str = ""
    source_component: str = ""
    target_table: str
    target_column: str
    target_component: str = ""
    # certain | likely | dynamic | opaque (LineageConfidence, intodoc.models)
    confidence: str = "opaque"
    # True only when source_table/source_column are a real resolved reference;
    # false for constant/dynamic-only entries, which still carry the edge
    # (empty source) rather than being dropped.
    resolved: bool = False
    dynamic_refs: list[str] = []
    transformations: list[LineageTransformationStep] = []


class LineageFieldResponse(BaseModel):
    project_id: str
    job_name: str
    start: LineageFieldNode
    direction: Literal["up", "down", "both"]
    max_depth: int
    nodes: list[LineageFieldNode] = []
    edges: list[LineageFieldEdge] = []
    # True when the BFS hit max_depth with further edges still unexplored.
    truncated: bool = False


# --- Job/group lineage rollup (GET /lineage/job) ---
#
# The field API answers "where does THIS column come from". The rollup answers
# the question one level up -- "what data moves out of these jobs, into which
# tables, and how is it transformed" -- which is what an LLM or a landscape UI
# needs before it can even name a column worth tracing.
#
# Same source file (`scan/lineage.json`), different projection: FLAT. A field
# response nests `transformations` inside an edge because a BFS hop is one
# entry; a rollup row is one (entry, transformation) pair, so a two-step tMap
# mapping shows up as two rows with the same source/target and different
# expressions. That keeps the payload tabular for paging and for prompt
# rendering, at the cost of repeating the endpoints of multi-step mappings.
#
# `expression` is raw generated Java. It is credential-redacted
# (`source_context.redact_inline_credentials`) and hard-clipped before it ever
# reaches this model -- see `lineage_service._safe_expression`.

class LineageRollupEdge(BaseModel):
    job_name: str
    source_component: str = ""
    source_table: str = ""
    source_column: str = ""
    target_component: str = ""
    target_table: str = ""
    target_column: str = ""
    # One transformation step, flattened out of the entry's `transformations`
    # list. Empty strings when the entry carried no transformation at all --
    # the row is still emitted, never dropped.
    transformation_type: str = ""
    expression: str = ""
    human_explanation: str = ""
    # certain | likely | dynamic | inferred | opaque (LineageConfidence)
    confidence: str = "opaque"
    resolved: bool = False
    dynamic_refs: list[str] = []


class LineageConfidenceTotals(BaseModel):
    """Per-confidence entry counts. A closed set of five buckets.

    Any confidence value the scanner emits outside this set is counted under
    `opaque`, matching `lineage_service._DEFAULT_CONFIDENCE` (the fallback used
    when an entry has no confidence at all). The raw value still survives
    verbatim on each `LineageRollupEdge.confidence`, so nothing is lost --
    only the aggregate is bucketed.
    """

    certain: int = 0
    likely: int = 0
    dynamic: int = 0
    inferred: int = 0
    opaque: int = 0


class LineageRollupTotals(BaseModel):
    # Counted over ENTRIES, not over emitted rows: a multi-step mapping is one
    # entry and several rows, so `entries` != `total_edges` is expected.
    entries: int = 0
    resolved: int = 0
    unresolved: int = 0
    confidence: LineageConfidenceTotals = LineageConfidenceTotals()


class LineageErrorDetail(BaseModel):
    """Structured body of a lineage 404, carried as the HTTPException `detail`.

    `reason` is the stable machine-readable discriminator; `message` is the
    human-readable prose. Both 404s of `/lineage/job` mean different things and
    drive opposite user-facing outcomes:

    * `lineage_artifact_absent`  -- no `scan/lineage.json`; the project needs a
      (re)scan.
    * `lineage_selection_empty`  -- lineage data exists, the requested job or
      group is not in it; the NAME is wrong. Do not tell the user to rescan.

    A consumer must branch on `reason` ONLY. `message` is prose and may be
    reworded at any time; treating it as a discriminator would make a copy edit
    silently flip downstream behaviour, which is exactly what this field
    prevents. The Literal is closed and mirrors
    `lineage_service.LineageQueryError.reason` on every subclass (asserted by
    `tests/test_services/test_lineage_job_rollup.py`), so a new failure mode
    cannot ship without a code declared here.
    """

    reason: Literal["lineage_artifact_absent", "lineage_selection_empty"]
    message: str


class LineageErrorResponse(BaseModel):
    """OpenAPI shape of a lineage 404 -- FastAPI wraps `detail` around the body."""

    detail: LineageErrorDetail


class LineageJobResponse(BaseModel):
    project_id: str
    # Job names that actually matched lineage entries. A requested job that
    # matched nothing is absent here -- the caller diffs its request against
    # this list rather than being told a wrong job matched.
    jobs: list[str] = []
    totals: LineageRollupTotals
    # Distinct target tables across the WHOLE selection (not just this page),
    # capped -- see lineage_service.TARGET_TABLE_CAP.
    target_tables: list[str] = []
    edges: list[LineageRollupEdge] = []
    offset: int
    limit: int
    returned: int
    total_edges: int
    has_more: bool


# --------------------------------------------------------------------------- #
# Multi-repo projects
#
# A project may be backed by N member repositories instead of one. Each member
# becomes an origin-marked clone directory, which the partition planner turns
# into its own scoped generation -- one doc set per repo -- plus the shared
# cross-repo topology layer. See services/repo_sources.py.
# --------------------------------------------------------------------------- #


class MultiRepoMemberRequest(BaseModel):
    """One repository the caller selected in the picker."""

    # Provider-stable identity: "owner/name" (GitHub) or "org/project/repo"
    # (Azure DevOps). This is the webhook join key, so it must be the
    # provider's own name for the repo, not a display label.
    repo_identity: str = Field(..., min_length=1, max_length=500)
    repo_name: str = Field(..., min_length=1, max_length=255)
    clone_url: str = Field(..., min_length=1, max_length=500)
    # None means "whatever the clone resolves to" -- the pre-multi-repo
    # behaviour. Naming a branch pins the member to it.
    branch: str | None = Field(default=None, max_length=255)


class MultiRepoCreateRequest(BaseModel):
    project_id: str = Field(..., min_length=1, max_length=100)
    display_name: str | None = Field(default=None, max_length=255)
    platform: str = Field(default="talend", max_length=50)
    provider: str = Field(..., min_length=1, max_length=32)  # github | azure_devops
    members: list[MultiRepoMemberRequest] = Field(..., min_length=1, max_length=100)
    # Credential references, exactly one of which applies. Both are supplied by
    # the client and are ownership-checked before use.
    installation_id: int | None = None
    azure_devops_connection_id: str | None = None


class MultiRepoMemberResponse(BaseModel):
    id: str
    provider: str
    repo_identity: str
    repo_name: str
    clone_url: str
    branch: str | None = None
    # The partition name: this member's doc set lands in
    # output/{project_id}/{subproject_name}/docs.
    subproject_name: str
    local_path: str
    last_ingested_commit_sha: str | None = None
    last_ingested_at: datetime.datetime | None = None
    enabled: bool = True


class MultiRepoSyncOutcomeResponse(BaseModel):
    subproject_name: str
    repo_identity: str
    action: str  # cloned | pulled | unchanged | skipped | failed
    head_sha: str | None = None
    previous_sha: str | None = None
    changed: bool = False
    error: str | None = None


class MultiRepoProjectResponse(BaseModel):
    project_id: str
    members: list[MultiRepoMemberResponse] = Field(default_factory=list)
    sync: list[MultiRepoSyncOutcomeResponse] = Field(default_factory=list)
