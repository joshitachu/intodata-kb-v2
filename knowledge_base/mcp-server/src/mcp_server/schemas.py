"""Request/response models for MCP server."""

from __future__ import annotations

import datetime
import hashlib
import json

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

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
    connection_kind: str = "pat"  # "pat" | "app"
    github_account_login: str | None = None
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


class GithubAppInstallationItem(BaseModel):
    installation_id: int
    account_login: str | None = None


class GithubAppInstallationsResponse(BaseModel):
    installations: list[GithubAppInstallationItem] = Field(default_factory=list)


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
NEXUS_PRIVATE_ANALYSIS_PACKS_MAX_BYTES = 64 * 1024
NEXUS_PRIVATE_REFERENCE_ATTACHMENTS_MAX_COUNT = 20
NEXUS_PRIVATE_REFERENCE_ATTACHMENT_TEXT_MAX_CHARS = 12_000
NEXUS_PRIVATE_REFERENCE_ATTACHMENTS_TEXT_MAX_CHARS = 32_000


class NexusContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class NexusPrivateContractModel(NexusContractModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


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


class NexusPrivatePackReceipt(NexusContractModel):
    version_id: int = Field(ge=1)
    pack_id: NexusPackId
    version: str = Field(min_length=1, max_length=64)
    content_hash: NexusHash
    audience_render: NexusAudienceRender = Field(default_factory=NexusAudienceRender)

    @model_validator(mode="after")
    def require_exact_identity(self) -> "NexusPrivatePackReceipt":
        if not self.audience_render.model_fields_set:
            # An empty normalized profile is valid, but raw display/audit values
            # are intentionally not part of the MCP execution contract.
            return self
        return self


class NexusPrivateReferenceAttachment(NexusPrivateContractModel):
    """Metadata and bounded extracted text for a Pack reference attachment.

    Raw attachment bytes are intentionally outside the MCP execution contract.
    """

    purpose: str = Field(min_length=1, max_length=240)
    filename: str = Field(min_length=1, max_length=255)
    content_type: str = Field(min_length=1, max_length=120)
    sha256: NexusHash
    extracted_text: str | None = Field(
        default=None,
        min_length=1,
        max_length=NEXUS_PRIVATE_REFERENCE_ATTACHMENT_TEXT_MAX_CHARS,
    )

    @field_validator("purpose", "filename", "content_type")
    @classmethod
    def validate_non_blank_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("reference attachment text fields must not be blank")
        return value

    @field_validator("extracted_text")
    @classmethod
    def validate_extracted_text(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("reference attachment extracted_text must not be blank")
        return value

    @field_validator("filename")
    @classmethod
    def validate_filename(cls, value: str) -> str:
        if "/" in value or "\\" in value or any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError("reference attachment filename must be a safe basename")
        return value


class NexusPrivateAnalysis(NexusPrivateContractModel):
    id: NexusReferenceId
    label: str = Field(min_length=1, max_length=120)
    purpose: str = Field(min_length=1, max_length=500)


class NexusPrivateEvidenceRequirement(NexusPrivateContractModel):
    id: NexusReferenceId
    label: str = Field(min_length=1, max_length=120)
    required: bool = True
    weight: int = Field(default=1, ge=1, le=100)


class NexusPrivateOutputSection(NexusPrivateContractModel):
    id: NexusReferenceId
    heading: str = Field(min_length=1, max_length=160)
    order: int = Field(ge=1, le=100)


class NexusPrivateOutputDocument(NexusPrivateContractModel):
    id: NexusReferenceId
    title: str = Field(min_length=1, max_length=160)
    sections: list[NexusPrivateOutputSection] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def validate_section_order(self) -> "NexusPrivateOutputDocument":
        orders = [section.order for section in self.sections]
        if len(orders) != len(set(orders)) or orders != sorted(orders):
            raise ValueError("private output sections must have unique ascending order")
        return self


class NexusPrivateScoringDimension(NexusPrivateContractModel):
    id: NexusReferenceId
    label: str = Field(min_length=1, max_length=120)
    weight: int = Field(ge=1, le=100)


class NexusPrivateCapabilityRequirement(NexusPrivateContractModel):
    capability_id: NexusReferenceId
    required: bool = True
    purpose: str = Field(min_length=1, max_length=240)


class NexusPrivateCompatibility(NexusPrivateContractModel):
    supported_project_kinds: list[str] = Field(default_factory=list, max_length=20)
    required_domains: list[str] = Field(default_factory=list, max_length=20)


class NexusPrivateAudience(NexusPrivateContractModel):
    label: str = Field(min_length=1, max_length=160)
    roles: list[str] = Field(default_factory=list, max_length=12)
    decision_needs: list[str] = Field(default_factory=list, max_length=12)
    domain_familiarity: str | None = Field(default=None, max_length=160)
    desired_outcome: str | None = Field(default=None, max_length=300)
    render: NexusAudienceRender = Field(default_factory=NexusAudienceRender)

    @field_validator("roles", "decision_needs")
    @classmethod
    def validate_words(cls, value: list[str]) -> list[str]:
        if any(not item.strip() or len(item) > 120 for item in value):
            raise ValueError("private audience values must be non-empty bounded text")
        return value

    @model_validator(mode="after")
    def reject_suppression(self) -> "NexusPrivateAudience":
        text = " ".join((
            self.label, *self.roles, *self.decision_needs,
            self.domain_familiarity or "", self.desired_outcome or "",
        )).casefold()
        forbidden = (
            "hide finding", "suppress finding", "omit finding", "hide evidence",
            "suppress evidence", "omit evidence", "hide uncertainty", "suppress uncertainty",
            "omit uncertainty", "hide source", "suppress source", "omit source",
        )
        if any(term in text for term in forbidden):
            raise ValueError("private audience cannot suppress findings, evidence, uncertainty, or sources")
        return self


class NexusPrivateAnalysisArtifactV3(NexusPrivateContractModel):
    """Strict V3 declarative transport contract; it has no executable namespace."""

    schema_version: Literal[3] = 3
    purpose: str = Field(min_length=1, max_length=1000)
    analyses: list[NexusPrivateAnalysis] = Field(min_length=1, max_length=30)
    evidence_requirements: list[NexusPrivateEvidenceRequirement] = Field(min_length=1, max_length=30)
    output_documents: list[NexusPrivateOutputDocument] = Field(min_length=1, max_length=12)
    scoring_dimensions: list[NexusPrivateScoringDimension] = Field(default_factory=list, max_length=20)
    capability_requirements: list[NexusPrivateCapabilityRequirement] = Field(default_factory=list, max_length=30)
    compatibility: NexusPrivateCompatibility = Field(default_factory=NexusPrivateCompatibility)
    audience: NexusPrivateAudience

    @model_validator(mode="after")
    def validate_unique_ids(self) -> "NexusPrivateAnalysisArtifactV3":
        for label, values in (
            ("analysis", self.analyses), ("evidence", self.evidence_requirements),
            ("output", self.output_documents), ("scoring", self.scoring_dimensions),
        ):
            ids = [item.id for item in values]
            if len(ids) != len(set(ids)):
                raise ValueError(f"duplicate private {label} id")
        if sum(item.weight for item in self.scoring_dimensions) > 1000:
            raise ValueError("private scoring weights are too large")
        return self


def compute_nexus_private_artifact_hash(
    artifact: NexusPrivateAnalysisArtifactV3,
    reference_attachments: list[NexusPrivateReferenceAttachment] | tuple[NexusPrivateReferenceAttachment, ...] = (),
) -> str:
    """Hash artifact content and, when present, sorted attachment receipt identities."""
    if reference_attachments:
        payload: object = {
            "artifact": artifact.model_dump(mode="json"),
            "reference_attachments": sorted(
                (
                    {"sha256": item.sha256, "purpose": item.purpose}
                    for item in reference_attachments
                ),
                key=lambda item: (item["sha256"], item["purpose"]),
            ),
        }
    else:
        payload = artifact.model_dump(mode="json")
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class NexusPrivateAnalysisPack(NexusPrivateContractModel):
    receipt: NexusPrivatePackReceipt
    artifact: NexusPrivateAnalysisArtifactV3
    missing_optional_capability_ids: list[NexusReferenceId] = Field(default_factory=list, max_length=30)
    reference_attachments: list[NexusPrivateReferenceAttachment] = Field(
        default_factory=list,
        max_length=NEXUS_PRIVATE_REFERENCE_ATTACHMENTS_MAX_COUNT,
        description="Optional metadata and bounded extracted text only; raw binary is never accepted",
    )

    @model_validator(mode="after")
    def validate_exact_artifact(self) -> "NexusPrivateAnalysisPack":
        if self.receipt.content_hash != compute_nexus_private_artifact_hash(
            self.artifact, self.reference_attachments
        ):
            raise ValueError(
                "private Analysis Pack artifact/reference content_hash does not match receipt"
            )
        if self.receipt.audience_render != self.artifact.audience.render:
            raise ValueError("private Analysis Pack audience render does not match receipt")
        if len(self.missing_optional_capability_ids) != len(set(self.missing_optional_capability_ids)):
            raise ValueError("duplicate missing optional capability")
        optional_ids = {
            requirement.capability_id
            for requirement in self.artifact.capability_requirements
            if not requirement.required
        }
        if not set(self.missing_optional_capability_ids) <= optional_ids:
            raise ValueError("missing optional capability is not declared optional")
        if sum(len(item.extracted_text or "") for item in self.reference_attachments) > NEXUS_PRIVATE_REFERENCE_ATTACHMENTS_TEXT_MAX_CHARS:
            raise ValueError(
                "private reference attachments exceed "
                f"{NEXUS_PRIVATE_REFERENCE_ATTACHMENTS_TEXT_MAX_CHARS} extracted-text characters"
            )
        attachment_keys = [(item.filename.casefold(), item.sha256) for item in self.reference_attachments]
        if len(attachment_keys) != len(set(attachment_keys)):
            raise ValueError("private reference attachments must have unique filename and sha256 pairs")
        return self


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


class GenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: StrippedStr = Field(..., min_length=1, max_length=100, pattern=PROJECT_ID_PATTERN)
    investigation_model: str | None = Field(None, description="Investigation model override (Phase 1) — 'provider/model' format")
    model: str | None = Field(None, description="Analysis model override (Phase 2) — 'provider/model' format")
    write_model: str | None = Field(None, description="Doc-writing model override (Phase 3) — 'provider/model' format")
    plugin_pack_id: int | None = Field(None, ge=1, description="Plugin pack override for this run. If set, takes precedence over the project's saved plugin_pack_id. Pass an int ID, or omit/null to use the project default.")
    nexus_pack_ids: list[NexusPackId] = Field(default_factory=list, max_length=201, description="Selected governed Nexus Pack IDs for this generation request")
    nexus_pack_snapshot: NexusPackSnapshot | None = Field(None, description="Immutable governed Pack provenance receipt; never executable input")
    nexus_presentation: NexusPresentation | None = Field(None, description="Bounded Phase 3 presentation overlay")
    nexus_audience_render: NexusAudienceRender | None = Field(None, description="Normalized custom V3 audience render dimensions")
    nexus_private_pack_receipts: list[NexusPrivatePackReceipt] = Field(default_factory=list, max_length=20, description="Exact additive private Pack version receipts; never authority grants")
    nexus_private_analysis_packs: list[NexusPrivateAnalysisPack] = Field(default_factory=list, max_length=20, description="Verified private V3 data for additive Phase 3 documents only")
    resume: bool = Field(True, description="Resume from staging checkpoint if available")
    mode: str = Field("enterprise", description="'enterprise' blocks publish on all quality gates; 'draft' allows looser output")

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
        if len({receipt.content_hash for receipt in self.nexus_private_pack_receipts}) != len(self.nexus_private_pack_receipts):
            raise ValueError("nexus_private_pack_receipts must contain unique exact versions")
        private_hashes = [item.receipt.content_hash for item in self.nexus_private_analysis_packs]
        if len(private_hashes) != len(set(private_hashes)):
            raise ValueError("nexus_private_analysis_packs must contain unique exact versions")
        encoded_private = json.dumps(
            [item.model_dump(mode="json") for item in self.nexus_private_analysis_packs],
            sort_keys=True, separators=(",", ":"), ensure_ascii=True,
        ).encode("utf-8")
        if len(encoded_private) > NEXUS_PRIVATE_ANALYSIS_PACKS_MAX_BYTES:
            raise ValueError(
                f"nexus_private_analysis_packs exceeds {NEXUS_PRIVATE_ANALYSIS_PACKS_MAX_BYTES} bytes"
            )
        if self.nexus_private_analysis_packs:
            exact_receipts = [item.receipt for item in self.nexus_private_analysis_packs]
            if self.nexus_private_pack_receipts and self.nexus_private_pack_receipts != exact_receipts:
                raise ValueError("nexus_private_pack_receipts must exactly match private Analysis Pack payload")
            self.nexus_private_pack_receipts = exact_receipts
            if self.nexus_audience_render is not None and len(exact_receipts) == 1:
                if self.nexus_audience_render != exact_receipts[0].audience_render:
                    raise ValueError("nexus_audience_render must match the private Analysis Pack receipt")
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
    component_type: str
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
