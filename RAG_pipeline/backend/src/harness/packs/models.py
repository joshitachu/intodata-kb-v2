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
    schema_version: Annotated[int, Field(ge=1, le=1)] = 1
    id: PackId
    name: str = Field(min_length=1, max_length=120)
    version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][a-zA-Z0-9.-]+)?$")
    pack_type: PackType
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
