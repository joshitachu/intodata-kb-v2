"""Strict, data-only contracts for Nexus orchestration packs."""

from __future__ import annotations

from enum import StrEnum
from pathlib import PurePosixPath
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


PackId = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_.-]{1,79}$")]
ReferenceId = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_.:-]{1,159}$")]


class StrictPackModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class PackType(StrEnum):
    DOCUMENTATION = "documentation"
    DOMAIN = "domain"
    STYLE = "style"


class PackPhase(StrEnum):
    """Governance layer for a Pack's declarative content."""

    FOUNDATION = "foundation"
    FUNCTIONAL = "functional"
    STYLE = "style"


class PackOwner(StrEnum):
    INTODATA = "intodata"
    ORGANIZATION = "organization"
    PROJECT = "project"
    USER = "user"


class PackAuthority(StrEnum):
    INTODATA_CERTIFIED = "intodata_certified"
    ORGANIZATION_CERTIFIED = "organization_certified"
    PROJECT_MANAGED = "project_managed"
    USER_MANAGED = "user_managed"


class PackScope(StrEnum):
    GLOBAL = "global"
    ORGANIZATION = "organization"
    PROJECT = "project"
    USER = "user"


class PackMutability(StrEnum):
    IMMUTABLE = "immutable"
    EDITABLE = "editable"


class PackTrust(StrEnum):
    CERTIFIED = "certified"
    UNREVIEWED = "unreviewed"


class StyleAudience(StrEnum):
    EXECUTIVE = "executive"
    BUSINESS = "business"
    ENGINEERING = "engineering"
    OPERATIONS = "operations"


class StyleTone(StrEnum):
    NEUTRAL = "neutral"
    CONCISE = "concise"
    EXPLANATORY = "explanatory"


class TechnicalDepth(StrEnum):
    SUMMARY = "summary"
    STANDARD = "standard"
    DEEP = "deep"


class OutputOrganization(StrEnum):
    DIRECT_ANSWER = "direct_answer"
    SECTIONED = "sectioned"
    INCIDENT_REPORT = "incident_report"


class FormattingOption(StrEnum):
    MARKDOWN = "markdown"
    BULLETED_LISTS = "bulleted_lists"
    TABLES = "tables"
    CODE_BLOCKS = "code_blocks"


class CommunicationOption(StrEnum):
    QUALIFY_UNCERTAINTY = "qualify_uncertainty"
    ACTION_ORIENTED = "action_oriented"
    INCLUDE_SOURCES = "include_sources"


class StyleField(StrEnum):
    AUDIENCE = "audience"
    TONE = "tone"
    TECHNICAL_DEPTH = "technical_depth"
    OUTPUT_ORGANIZATION = "output_organization"
    FORMATTING = "formatting"
    COMMUNICATION = "communication"


class ProtectedInvariant(StrEnum):
    EVIDENCE_POLICY = "evidence_policy"
    FINDINGS = "findings"
    ENTITY_SEMANTICS = "entity_semantics"
    REQUIRED_ANALYSIS = "required_analysis"
    SECURITY_REDACTION = "security_redaction"
    PERMISSIONS = "permissions"
    RISK_TIERS = "risk_tiers"
    QUALITY_THRESHOLDS = "quality_thresholds"
    CAPABILITIES = "capabilities"


_PROTECTED_INVARIANTS = frozenset(ProtectedInvariant)


class PackQuality(StrictPackModel):
    quality_score: float = Field(ge=0, le=1)
    evaluation_score: float = Field(ge=0, le=1)


class PackGovernance(StrictPackModel):
    """Authority metadata. It governs a Pack; it is not Pack content."""

    phase: PackPhase
    owner: PackOwner
    authority: PackAuthority
    scope: PackScope
    mutability: PackMutability
    trust: PackTrust
    quality: PackQuality | None = None

    @model_validator(mode="after")
    def validate_phase_authority(self) -> PackGovernance:
        if self.phase is PackPhase.FOUNDATION:
            expected = (
                self.owner is PackOwner.INTODATA
                and self.authority is PackAuthority.INTODATA_CERTIFIED
                and self.scope is PackScope.GLOBAL
                and self.mutability is PackMutability.IMMUTABLE
                and self.trust is PackTrust.CERTIFIED
                and self.quality is not None
            )
            if not expected:
                raise ValueError("foundation Packs must be global, IntoData-certified, immutable")
        elif self.phase is PackPhase.FUNCTIONAL:
            certified = (
                (self.owner is PackOwner.INTODATA and self.authority is PackAuthority.INTODATA_CERTIFIED)
                or (
                    self.owner is PackOwner.ORGANIZATION
                    and self.authority is PackAuthority.ORGANIZATION_CERTIFIED
                )
            )
            if not (
                certified
                and self.scope in {PackScope.GLOBAL, PackScope.ORGANIZATION}
                and self.mutability is PackMutability.IMMUTABLE
                and self.trust is PackTrust.CERTIFIED
                and self.quality is not None
            ):
                raise ValueError("functional Packs must be certified and immutable")
        elif not (
            self.owner in {PackOwner.PROJECT, PackOwner.USER}
            and (
                (self.owner is PackOwner.PROJECT and self.authority is PackAuthority.PROJECT_MANAGED)
                or (self.owner is PackOwner.USER and self.authority is PackAuthority.USER_MANAGED)
            )
            and self.scope in {PackScope.PROJECT, PackScope.USER}
            and self.mutability is PackMutability.EDITABLE
            and self.trust is PackTrust.UNREVIEWED
            and self.quality is None
        ):
            raise ValueError("style Packs must be user/project-managed editable overlays")
        return self


class StylePresentation(StrictPackModel):
    """The complete Phase 3 namespace: display-only, bounded enum choices."""

    audience: StyleAudience | None = None
    tone: StyleTone | None = None
    technical_depth: TechnicalDepth | None = None
    output_organization: OutputOrganization | None = None
    formatting: tuple[FormattingOption, ...] = Field(default=(), max_length=4)
    communication: tuple[CommunicationOption, ...] = Field(default=(), max_length=3)

    @model_validator(mode="after")
    def validate_unique_options(self) -> StylePresentation:
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
            raise ValueError("style overlay must set at least one presentation field")
        return self


class StyleGovernancePolicy(StrictPackModel):
    permitted_fields: frozenset[StyleField] = Field(default_factory=frozenset)
    allowed_audiences: frozenset[StyleAudience] = Field(default_factory=frozenset)
    allowed_tones: frozenset[StyleTone] = Field(default_factory=frozenset)
    allowed_technical_depths: frozenset[TechnicalDepth] = Field(default_factory=frozenset)
    allowed_output_organizations: frozenset[OutputOrganization] = Field(default_factory=frozenset)
    allowed_formatting: frozenset[FormattingOption] = Field(default_factory=frozenset)
    allowed_communication: frozenset[CommunicationOption] = Field(default_factory=frozenset)


class MinimumQualityThresholds(StrictPackModel):
    quality_score: float = Field(ge=0, le=1)
    evaluation_score: float = Field(ge=0, le=1)


class OrganizationGovernancePolicy(StrictPackModel):
    id: ReferenceId
    required_certified_pack_ids: frozenset[PackId] = Field(default_factory=frozenset)
    allowed_functional_pack_ids: frozenset[PackId] = Field(default_factory=frozenset)
    permitted_style: StyleGovernancePolicy = Field(default_factory=StyleGovernancePolicy)
    minimum_quality: MinimumQualityThresholds
    protected_invariants: frozenset[ProtectedInvariant] = Field(
        default_factory=lambda: _PROTECTED_INVARIANTS
    )

    @model_validator(mode="after")
    def validate_protected_invariants(self) -> OrganizationGovernancePolicy:
        missing = _PROTECTED_INVARIANTS - self.protected_invariants
        if missing:
            raise ValueError(
                "governance policy cannot omit protected invariants: "
                + ", ".join(sorted(item.value for item in missing))
            )
        return self


class VocabularyTerm(StrictPackModel):
    term: str = Field(min_length=1, max_length=80)
    aliases: tuple[str, ...] = Field(default=(), max_length=20)
    description: str = Field(min_length=1, max_length=500)


class EntityTypeDefinition(StrictPackModel):
    id: ReferenceId
    label: str = Field(min_length=1, max_length=100)
    plural_label: str = Field(min_length=1, max_length=100)
    aliases: tuple[str, ...] = Field(default=(), max_length=20)


class ConnectorRequirement(StrictPackModel):
    id: ReferenceId
    provider_key: ReferenceId
    capability_ids: tuple[ReferenceId, ...] = Field(default=(), max_length=50)
    required: bool = True


class ArtifactTemplate(StrictPackModel):
    id: ReferenceId
    label: str = Field(min_length=1, max_length=120)
    relative_path: str = Field(min_length=1, max_length=240)
    media_type: str = Field(default="text/markdown", pattern=r"^[a-z0-9.+-]+/[a-z0-9.+-]+$")

    @field_validator("relative_path")
    @classmethod
    def validate_relative_path(cls, value: str) -> str:
        if "\\" in value:
            raise ValueError("artifact path must use POSIX separators")
        path = PurePosixPath(value)
        if path.is_absolute() or value in {".", ".."} or ".." in path.parts:
            raise ValueError("artifact path must be a safe relative path")
        if path.as_posix() != value or any(part in {"", "."} for part in path.parts):
            raise ValueError("artifact path must be normalized")
        return value


class EvidenceRankingRule(StrictPackModel):
    evidence_type: ReferenceId
    weight: int = Field(ge=-100, le=100)
    source_kind: ReferenceId | None = None


class WorkflowView(StrictPackModel):
    id: ReferenceId
    label: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=500)
    capability_ids: tuple[ReferenceId, ...] = Field(default=(), max_length=50)
    entity_type_ids: tuple[ReferenceId, ...] = Field(default=(), max_length=20)
    connector_requirement_ids: tuple[ReferenceId, ...] = Field(default=(), max_length=20)
    artifact_template_ids: tuple[ReferenceId, ...] = Field(default=(), max_length=20)


class DefaultScopes(StrictPackModel):
    project_required: bool = True
    permissions: tuple[ReferenceId, ...] = Field(default=(), max_length=50)
    domains: tuple[ReferenceId, ...] = Field(default=(), max_length=50)


class PolicyHints(StrictPackModel):
    approval_required_for: tuple[ReferenceId, ...] = Field(default=(), max_length=50)
    evidence_required: bool = True
    read_only: bool = False


class OutputRules(StrictPackModel):
    required_sections: tuple[str, ...] = Field(default=(), max_length=30)
    include_confidence: bool = True
    include_artifact_links: bool = True


class PackManifest(StrictPackModel):
    schema_version: Annotated[int, Field(ge=1, le=2)] = 2
    id: PackId
    name: str = Field(min_length=1, max_length=120)
    version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][a-zA-Z0-9.-]+)?$")
    pack_type: PackType
    # ``None`` is a deliberate migration state for persisted V1 manifests.
    # The PackStack compiler rejects it, so it never grants user authority.
    governance: PackGovernance | None = None
    includes: tuple[PackId, ...] = Field(default=(), max_length=50)
    documentation_provider_ref: ReferenceId | None = None
    vocabulary: tuple[VocabularyTerm, ...] = Field(default=(), max_length=200)
    entities: tuple[EntityTypeDefinition, ...] = Field(default=(), max_length=100)
    workflows: tuple[WorkflowView, ...] = Field(default=(), max_length=100)
    evidence_ranking: tuple[EvidenceRankingRule, ...] = Field(default=(), max_length=100)
    artifact_templates: tuple[ArtifactTemplate, ...] = Field(default=(), max_length=100)
    connector_requirements: tuple[ConnectorRequirement, ...] = Field(default=(), max_length=100)
    default_scopes: DefaultScopes = Field(default_factory=DefaultScopes)
    policy_hints: PolicyHints = Field(default_factory=PolicyHints)
    output_rules: OutputRules = Field(default_factory=OutputRules)
    style: StylePresentation | None = None

    @model_validator(mode="after")
    def validate_unique_local_ids(self) -> PackManifest:
        for label, values in (
            ("entity", self.entities),
            ("workflow", self.workflows),
            ("artifact template", self.artifact_templates),
            ("connector requirement", self.connector_requirements),
        ):
            ids = [item.id for item in values]
            if len(ids) != len(set(ids)):
                raise ValueError(f"duplicate {label} id")
        if self.id in self.includes:
            raise ValueError("pack cannot include itself")
        if self.governance is None:
            return self
        if self.governance.phase is PackPhase.STYLE:
            if self.pack_type is not PackType.STYLE or self.style is None:
                raise ValueError("style Packs require pack_type style and a style presentation")
            if self.includes:
                raise ValueError("style Packs cannot include or activate semantic Packs")
            protected_content = (
                self.documentation_provider_ref is not None
                or self.vocabulary
                or self.entities
                or self.workflows
                or self.evidence_ranking
                or self.artifact_templates
                or self.connector_requirements
                or self.default_scopes != DefaultScopes()
                or self.policy_hints != PolicyHints()
                or self.output_rules != OutputRules()
            )
            if protected_content:
                raise ValueError("style Packs cannot declare protected semantic, policy, or capability content")
        elif self.pack_type is PackType.STYLE or self.style is not None:
            raise ValueError("only style Packs may declare a style presentation")
        return self


class PackReferenceSets(StrictPackModel):
    capability_ids: frozenset[str] = frozenset()
    connector_provider_keys: frozenset[str] = frozenset()
    documentation_provider_refs: frozenset[str] = frozenset()
    evidence_types: frozenset[str] | None = None
    source_kinds: frozenset[str] | None = None


class PackActivation(StrictPackModel):
    enabled_pack_ids: frozenset[PackId] = Field(default_factory=frozenset, max_length=100)


class PackIdentity(StrictPackModel):
    id: PackId
    version: str
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class PackSnapshot(StrictPackModel):
    active_pack_ids: tuple[PackId, ...]
    resolved_packs: tuple[PackIdentity, ...]
    snapshot_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class ResolvedPackSet(StrictPackModel):
    manifests: tuple[PackManifest, ...]
    snapshot: PackSnapshot


class PackStackSelection(StrictPackModel):
    """Explicit Pack roots selected for a governed workflow scope."""

    foundation_pack_ids: frozenset[PackId] = Field(default_factory=frozenset, max_length=1)
    functional_pack_ids: frozenset[PackId] = Field(default_factory=frozenset, max_length=100)
    style_pack_ids: frozenset[PackId] = Field(default_factory=frozenset, max_length=100)

    @model_validator(mode="after")
    def validate_foundation_selection(self) -> PackStackSelection:
        if len(self.foundation_pack_ids) != 1:
            raise ValueError("exactly one foundation Pack must be selected")
        return self


class PackStackSnapshot(StrictPackModel):
    policy_id: ReferenceId
    policy_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    foundation_pack_id: PackId
    functional_pack_ids: tuple[PackId, ...]
    style_pack_ids: tuple[PackId, ...]
    resolved_packs: tuple[PackIdentity, ...]
    snapshot_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class CompiledPackStack(StrictPackModel):
    manifests: tuple[PackManifest, ...]
    presentation: StylePresentation | None = None
    snapshot: PackStackSnapshot


class PackView(StrictPackModel):
    id: PackId
    name: str
    version: str
    pack_type: PackType
    includes: tuple[PackId, ...]
    workflow_labels: tuple[str, ...]
    workflow_descriptions: tuple[str, ...]
    entity_labels: tuple[str, ...]
    documentation_provider_ref: ReferenceId | None
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class PackRecommendation(StrictPackModel):
    """A safe, explainable Pack suggestion for one project."""

    pack_id: PackId
    confidence: float = Field(ge=0, le=1)
    reasons: tuple[str, ...] = Field(min_length=1, max_length=5)


class ProjectPackActivation(StrictPackModel):
    """The immutable Pack selection used by a project/workflow scope."""

    project_id: str = Field(min_length=1, max_length=255)
    workflow_key: str = Field(pattern=r"^[a-z][a-z0-9_.-]{0,79}$")
    active_pack_ids: tuple[PackId, ...]
    snapshot: PackSnapshot


# V3 private Analysis Packs deliberately use a separate contract from the
# certified PackManifest.  They are additive declarations, never executable
# manifests or a route for changing registry authority.
class AudienceRenderProfile(StrictPackModel):
    """Bounded, normalized values that may be sent to a renderer or model."""

    seniority: str | None = Field(default=None, pattern=r"^(executive|director|manager|practitioner)$")
    domain_familiarity: str | None = Field(default=None, pattern=r"^(introductory|working|expert)$")
    decision_horizon: str | None = Field(default=None, pattern=r"^(immediate|near_term|strategic)$")
    detail_level: str | None = Field(default=None, pattern=r"^(brief|standard|detailed)$")
