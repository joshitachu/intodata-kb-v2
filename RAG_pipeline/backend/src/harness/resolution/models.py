"""Typed contracts for deterministic conversational entity resolution."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class EntityType(str, Enum):
    PROJECT = "project"
    SOURCE = "source"
    REPOSITORY = "repository"
    DOCUMENT = "document"
    WORKFLOW = "workflow"
    WORKFLOW_RUN = "workflow_run"
    RUNTIME_ENTITY = "runtime_entity"
    ENVIRONMENT = "environment"


class WorkflowKind(str, Enum):
    ONBOARDING = "onboarding"
    SCAN = "scan"
    DOCUMENTATION_GENERATION = "documentation_generation"
    INDEXING = "indexing"
    RUNTIME_HEALTH = "runtime_health"
    DOCUMENTATION_QUALITY = "documentation_quality"


class ResolutionStatus(str, Enum):
    RESOLVED = "resolved"
    AMBIGUOUS = "ambiguous"
    UNRESOLVED = "unresolved"


class ConfidenceLevel(str, Enum):
    EXACT = "exact"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class MatchReason(str, Enum):
    STABLE_ID = "stable_id"
    CANONICAL_NAME = "canonical_name"
    ALIAS = "alias"
    EMBEDDED_NAME = "embedded_name"
    PREFIX = "prefix"
    TOKEN_SUBSET = "token_subset"
    CONVERSATIONAL_FOCUS = "conversational_focus"
    ONLY_AVAILABLE = "only_available"


class TimeWindowKind(str, Enum):
    RELATIVE = "relative"
    CALENDAR_DAY = "calendar_day"
    EXPLICIT = "explicit"


class SlotValueResolutionStatus(str, Enum):
    RESOLVED = "resolved"
    INVALID = "invalid"
    NOT_APPLICABLE = "not_applicable"


class _FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, str_strip_whitespace=True, extra="forbid")


class EntityReference(_FrozenModel):
    """A stable, safe reference suitable for workflow and evidence snapshots."""

    entity_type: EntityType
    stable_id: str = Field(min_length=1, max_length=255)
    canonical_name: str = Field(min_length=1, max_length=500)
    workflow_kind: WorkflowKind | None = None


class EntityCandidate(_FrozenModel):
    """Caller-supplied candidate from an already authorized inventory."""

    entity_type: EntityType
    stable_id: str = Field(min_length=1, max_length=255)
    canonical_name: str = Field(min_length=1, max_length=500)
    aliases: tuple[str, ...] = Field(default_factory=tuple, max_length=50)
    workflow_kind: WorkflowKind | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    def to_reference(self) -> EntityReference:
        return EntityReference(
            entity_type=self.entity_type,
            stable_id=self.stable_id,
            canonical_name=self.canonical_name,
            workflow_kind=self.workflow_kind,
        )


class Confidence(_FrozenModel):
    score: float = Field(ge=0, le=1)
    level: ConfidenceLevel
    reason: MatchReason


class EntityMatch(_FrozenModel):
    reference: EntityReference
    confidence: Confidence


class AmbiguityOption(_FrozenModel):
    value: str = Field(min_length=1, max_length=255)
    label: str = Field(min_length=1, max_length=500)
    score: float = Field(ge=0, le=1)


class ResolutionAmbiguity(_FrozenModel):
    prompt: str = Field(min_length=1, max_length=1000)
    options: tuple[AmbiguityOption, ...] = Field(min_length=2, max_length=10)


class EntityResolution(_FrozenModel):
    query: str = Field(min_length=1, max_length=1000)
    entity_type: EntityType
    status: ResolutionStatus
    reference: EntityReference | None = None
    confidence: Confidence | None = None
    matches: tuple[EntityMatch, ...] = Field(default_factory=tuple, max_length=10)
    ambiguity: ResolutionAmbiguity | None = None

    @model_validator(mode="after")
    def validate_state(self) -> EntityResolution:
        if self.status is ResolutionStatus.RESOLVED:
            if self.reference is None or self.confidence is None or self.ambiguity is not None:
                raise ValueError("resolved results require a reference and confidence only")
        elif self.status is ResolutionStatus.AMBIGUOUS:
            if self.ambiguity is None or self.reference is not None:
                raise ValueError("ambiguous results require ambiguity and no reference")
        elif self.reference is not None or self.ambiguity is not None:
            raise ValueError("unresolved results cannot contain a reference or ambiguity")
        return self

class WorkflowKindResolution(_FrozenModel):
    status: ResolutionStatus
    workflow_kind: WorkflowKind | None = None
    confidence: Confidence | None = None
    ambiguity: ResolutionAmbiguity | None = None

    @model_validator(mode="after")
    def validate_state(self) -> WorkflowKindResolution:
        if self.status is ResolutionStatus.RESOLVED:
            if self.workflow_kind is None or self.confidence is None:
                raise ValueError("resolved workflow kinds require a kind and confidence")
        elif self.status is ResolutionStatus.AMBIGUOUS:
            if self.ambiguity is None or self.workflow_kind is not None:
                raise ValueError("ambiguous workflow kinds require ambiguity and no kind")
        elif self.workflow_kind is not None or self.ambiguity is not None:
            raise ValueError("unresolved workflow kinds cannot contain a kind or ambiguity")
        return self


class TimeWindow(_FrozenModel):
    """UTC half-open interval: ``start <= timestamp < end``."""

    start: datetime
    end: datetime
    kind: TimeWindowKind
    expression: str = Field(min_length=1, max_length=500)

    @field_validator("start", "end")
    @classmethod
    def require_timezone_and_normalize_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("time window bounds must include a timezone")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def bounds_are_ordered(self) -> TimeWindow:
        if self.start >= self.end:
            raise ValueError("time window start must be before end")
        return self

    def contains(self, value: datetime) -> bool:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("candidate timestamp must include a timezone")
        utc_value = value.astimezone(UTC)
        return self.start <= utc_value < self.end


class ContinuationSlotValueResolution(_FrozenModel):
    """Typed result from resolving one pending continuation slot."""

    slot_name: str = Field(min_length=1, max_length=80)
    status: SlotValueResolutionStatus
    value: TimeWindow | None = None
    clarification: str | None = Field(default=None, min_length=1, max_length=1000)

    @model_validator(mode="after")
    def validate_state(self) -> ContinuationSlotValueResolution:
        if self.status is SlotValueResolutionStatus.RESOLVED:
            if self.value is None or self.clarification is not None:
                raise ValueError("resolved slot values require a value only")
        elif self.status is SlotValueResolutionStatus.INVALID:
            if self.value is not None or self.clarification is None:
                raise ValueError("invalid slot values require clarification only")
        elif self.value is not None or self.clarification is not None:
            raise ValueError("non-applicable slot values cannot contain a value or clarification")
        return self


class ConversationalFocus(_FrozenModel):
    """Typed references carried between turns; no raw user guesses are retained."""

    project: EntityReference | None = None
    workflow_kind: WorkflowKind | None = None
    workflow_run: EntityReference | None = None
    document: EntityReference | None = None
    runtime_entity: EntityReference | None = None
    environment: EntityReference | None = None
    time_window: TimeWindow | None = None

    @model_validator(mode="after")
    def references_have_expected_types(self) -> ConversationalFocus:
        expected = {
            "project": EntityType.PROJECT,
            "workflow_run": EntityType.WORKFLOW_RUN,
            "document": EntityType.DOCUMENT,
            "runtime_entity": EntityType.RUNTIME_ENTITY,
            "environment": EntityType.ENVIRONMENT,
        }
        for field_name, entity_type in expected.items():
            reference = getattr(self, field_name)
            if reference is not None and reference.entity_type is not entity_type:
                raise ValueError(f"{field_name} must reference {entity_type.value}")
        return self
