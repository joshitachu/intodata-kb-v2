"""Runtime-neutral contracts for incremental living-document candidates.

The objects in this module deliberately describe a proposed documentation
revision, not a provider, repository checkout, or vector database.  Git, ZIP
and chat adapters can therefore create the same candidate and the later
publication coordinator can consume it without learning where the change came
from.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import datetime, timezone
from enum import StrEnum
from hashlib import sha256
import json
from typing import Any, Mapping


class LivingDocsContractError(ValueError):
    """A value violates a stable living-document contract."""


class SourceOrigin(StrEnum):
    GIT = "git"
    ZIP = "zip"
    CHAT = "chat"


class ChangeKind(StrEnum):
    ADDED = "added"
    MODIFIED = "modified"
    DELETED = "deleted"
    RENAMED = "renamed"
    SEMANTIC = "semantic"


class ArtifactKind(StrEnum):
    CANONICAL_DOCUMENT = "canonical_document"
    INTERNAL_ANALYSIS = "internal_analysis"
    SUPPORT = "support"
    CACHE = "cache"


class PatchOperationKind(StrEnum):
    REPLACE = "replace"
    APPEND = "append"
    REMOVE = "remove"
    CREATE = "create"


class PatchPlanProducer(StrEnum):
    DETERMINISTIC = "deterministic"


class RagMutationKind(StrEnum):
    UPSERT = "upsert"
    DELETE = "delete"
    ALIAS = "alias"


class CandidateState(StrEnum):
    QUEUED = "queued"
    PREPARING = "preparing"
    AGENT_RUNNING = "agent_running"
    VALIDATING = "validating"
    RAG_PREPARED = "rag_prepared"
    RECOVERY_REQUIRED = "recovery_required"
    ACTIVE = "active"
    HELD = "held"
    FAILED = "failed"
    SUPERSEDED = "superseded"


TERMINAL_CANDIDATE_STATES = frozenset(
    {CandidateState.ACTIVE, CandidateState.HELD, CandidateState.FAILED, CandidateState.SUPERSEDED}
)

_TRANSITIONS: Mapping[CandidateState, frozenset[CandidateState]] = {
    CandidateState.QUEUED: frozenset({CandidateState.PREPARING, CandidateState.HELD, CandidateState.FAILED, CandidateState.SUPERSEDED}),
    CandidateState.PREPARING: frozenset({CandidateState.AGENT_RUNNING, CandidateState.VALIDATING, CandidateState.HELD, CandidateState.FAILED, CandidateState.SUPERSEDED}),
    CandidateState.AGENT_RUNNING: frozenset({CandidateState.VALIDATING, CandidateState.HELD, CandidateState.FAILED, CandidateState.SUPERSEDED}),
    CandidateState.VALIDATING: frozenset({CandidateState.RAG_PREPARED, CandidateState.HELD, CandidateState.FAILED, CandidateState.SUPERSEDED}),
    CandidateState.RAG_PREPARED: frozenset({CandidateState.RECOVERY_REQUIRED, CandidateState.ACTIVE, CandidateState.HELD, CandidateState.FAILED, CandidateState.SUPERSEDED}),
    CandidateState.RECOVERY_REQUIRED: frozenset({CandidateState.ACTIVE, CandidateState.FAILED, CandidateState.SUPERSEDED}),
    CandidateState.ACTIVE: frozenset(),
    CandidateState.HELD: frozenset(),
    CandidateState.FAILED: frozenset(),
    CandidateState.SUPERSEDED: frozenset(),
}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def canonical_json_bytes(value: Any) -> bytes:
    """Serialize a value deterministically for IDs and durable receipts."""

    return json.dumps(
        _json_value(value), ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")


def content_sha256(content: bytes) -> str:
    return sha256(content).hexdigest()


def candidate_id_for(*, project_id: str, idempotency_key: str) -> str:
    """Return a stable opaque candidate identity for one project/idempotency key."""

    _require_identifier(project_id, "project_id")
    _require_nonblank(idempotency_key, "idempotency_key")
    digest = sha256(canonical_json_bytes({"project_id": project_id, "idempotency_key": idempotency_key})).hexdigest()
    return f"ldc-{digest[:32]}"


@dataclass(frozen=True, slots=True)
class SourceRevision:
    project_id: str
    origin: SourceOrigin
    source_identity: str
    revision_id: str
    content_sha256: str
    observed_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        _require_identifier(self.project_id, "project_id")
        _require_nonblank(self.source_identity, "source_identity")
        _require_nonblank(self.revision_id, "revision_id")
        _require_sha256(self.content_sha256, "content_sha256")
        _require_aware_datetime(self.observed_at, "observed_at")

    @property
    def key(self) -> str:
        return sha256(canonical_json_bytes({
            "project_id": self.project_id,
            "origin": self.origin,
            "source_identity": self.source_identity,
            "revision_id": self.revision_id,
            "content_sha256": self.content_sha256,
        })).hexdigest()


@dataclass(frozen=True, slots=True)
class SemanticChange:
    kind: ChangeKind
    logical_path: str
    field_id: str | None = None
    before_sha256: str | None = None
    after_sha256: str | None = None
    previous_logical_path: str | None = None

    def __post_init__(self) -> None:
        _require_logical_path(self.logical_path, "logical_path")
        if self.field_id is not None:
            _require_nonblank(self.field_id, "field_id")
        for field_name, value in (("before_sha256", self.before_sha256), ("after_sha256", self.after_sha256)):
            if value is not None:
                _require_sha256(value, field_name)
        if self.kind is ChangeKind.RENAMED:
            if self.previous_logical_path is None:
                raise LivingDocsContractError("renamed change requires previous_logical_path")
            _require_logical_path(self.previous_logical_path, "previous_logical_path")
        elif self.previous_logical_path is not None:
            raise LivingDocsContractError("previous_logical_path is only valid for renamed changes")


@dataclass(frozen=True, slots=True)
class ChangeSet:
    source_revision: SourceRevision
    base_source_revision_id: str | None
    idempotency_key: str
    changes: tuple[SemanticChange, ...]
    scanner_sha256: str | None = None

    def __post_init__(self) -> None:
        _require_nonblank(self.idempotency_key, "idempotency_key")
        if self.base_source_revision_id is not None:
            _require_nonblank(self.base_source_revision_id, "base_source_revision_id")
        if not self.changes:
            raise LivingDocsContractError("change set requires at least one semantic change")
        if self.scanner_sha256 is not None:
            _require_sha256(self.scanner_sha256, "scanner_sha256")

    @property
    def fingerprint(self) -> str:
        return sha256(canonical_json_bytes(self)).hexdigest()


@dataclass(frozen=True, slots=True, order=True)
class PatchTarget:
    artifact_id: str
    section_id: str
    artifact_kind: ArtifactKind

    def __post_init__(self) -> None:
        _require_logical_path(self.artifact_id, "artifact_id")
        _require_nonblank(self.section_id, "section_id")

    @property
    def key(self) -> str:
        return f"{self.artifact_kind}:{self.artifact_id}#{self.section_id}"


@dataclass(frozen=True, slots=True)
class DeterministicImpactFloor:
    source_revision_key: str
    targets: tuple[PatchTarget, ...]
    evidence_ids: tuple[str, ...] = ()
    mapper_version: str = "v1"

    def __post_init__(self) -> None:
        _require_sha256(self.source_revision_key, "source_revision_key")
        _require_nonblank(self.mapper_version, "mapper_version")
        if not self.targets:
            raise LivingDocsContractError("deterministic impact floor requires at least one target")
        if len(set(self.targets)) != len(self.targets):
            raise LivingDocsContractError("deterministic impact targets must be unique")
        _require_unique_nonblank(self.evidence_ids, "evidence_ids")


@dataclass(frozen=True, slots=True)
class EvidenceReference:
    evidence_id: str
    content_sha256: str
    locator: str

    def __post_init__(self) -> None:
        _require_nonblank(self.evidence_id, "evidence_id")
        _require_sha256(self.content_sha256, "content_sha256")
        _require_nonblank(self.locator, "locator")


@dataclass(frozen=True, slots=True)
class PatchOperation:
    target: PatchTarget
    kind: PatchOperationKind
    evidence_ids: tuple[str, ...]
    replacement_sha256: str | None = None
    expected_current_sha256: str | None = None

    def __post_init__(self) -> None:
        _require_unique_nonblank(self.evidence_ids, "operation evidence_ids")
        for field_name, value in (("replacement_sha256", self.replacement_sha256), ("expected_current_sha256", self.expected_current_sha256)):
            if value is not None:
                _require_sha256(value, field_name)
        if self.kind in {PatchOperationKind.REPLACE, PatchOperationKind.APPEND, PatchOperationKind.CREATE} and self.replacement_sha256 is None:
            raise LivingDocsContractError(f"{self.kind} patch operation requires replacement_sha256")


@dataclass(frozen=True, slots=True)
class PatchPlan:
    candidate_id: str
    source_revision_key: str
    base_knowledge_revision_id: str | None
    producer: PatchPlanProducer
    operations: tuple[PatchOperation, ...]
    full_rewrite_requested: bool = False
    publish_requested: bool = False
    rag_mutation_requested: bool = False
    schema_version: int = 1

    def __post_init__(self) -> None:
        _require_identifier(self.candidate_id, "candidate_id")
        _require_sha256(self.source_revision_key, "source_revision_key")
        if self.base_knowledge_revision_id is not None:
            _require_identifier(self.base_knowledge_revision_id, "base_knowledge_revision_id")
        if self.schema_version != 1:
            raise LivingDocsContractError("unsupported patch plan schema version")
        if not self.operations:
            raise LivingDocsContractError("patch plan requires at least one operation")
        if len({operation.target for operation in self.operations}) != len(self.operations):
            raise LivingDocsContractError("patch plan may contain only one operation per target")

    @property
    def targets(self) -> tuple[PatchTarget, ...]:
        return tuple(operation.target for operation in self.operations)

    @property
    def sha256(self) -> str:
        return sha256(canonical_json_bytes(self)).hexdigest()


@dataclass(frozen=True, slots=True)
class ArtifactMembership:
    logical_id: str
    artifact_kind: ArtifactKind
    content_sha256: str
    media_type: str
    size_bytes: int

    def __post_init__(self) -> None:
        _require_logical_path(self.logical_id, "logical_id")
        _require_sha256(self.content_sha256, "content_sha256")
        _require_nonblank(self.media_type, "media_type")
        if self.size_bytes < 0:
            raise LivingDocsContractError("artifact size_bytes must not be negative")


@dataclass(frozen=True, slots=True)
class RagMutationIntent:
    logical_id: str
    artifact_kind: ArtifactKind
    mutation: RagMutationKind
    content_sha256: str | None = None
    alias_of: str | None = None

    def __post_init__(self) -> None:
        _require_logical_path(self.logical_id, "logical_id")
        if self.mutation is RagMutationKind.UPSERT:
            if self.content_sha256 is None:
                raise LivingDocsContractError("RAG upsert requires content_sha256")
            _require_sha256(self.content_sha256, "content_sha256")
        elif self.content_sha256 is not None:
            _require_sha256(self.content_sha256, "content_sha256")
        if self.mutation is RagMutationKind.ALIAS:
            if self.alias_of is None:
                raise LivingDocsContractError("RAG alias requires alias_of")
            _require_logical_path(self.alias_of, "alias_of")
        elif self.alias_of is not None:
            raise LivingDocsContractError("alias_of is only valid for RAG aliases")


@dataclass(frozen=True, slots=True)
class ValidationReceipt:
    validator_id: str
    passed: bool
    checked_at: datetime = field(default_factory=utc_now)
    detail: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_nonblank(self.validator_id, "validator_id")
        _require_aware_datetime(self.checked_at, "checked_at")


@dataclass(frozen=True, slots=True)
class KnowledgeRevision:
    id: str
    project_id: str
    base_knowledge_revision_id: str | None
    source_revision_key: str
    expected_artifacts: tuple[ArtifactMembership, ...]
    rag_mutations: tuple[RagMutationIntent, ...]

    def __post_init__(self) -> None:
        _require_identifier(self.id, "knowledge revision id")
        _require_identifier(self.project_id, "project_id")
        if self.base_knowledge_revision_id is not None:
            _require_identifier(self.base_knowledge_revision_id, "base_knowledge_revision_id")
        _require_sha256(self.source_revision_key, "source_revision_key")
        if len({item.logical_id for item in self.expected_artifacts}) != len(self.expected_artifacts):
            raise LivingDocsContractError("knowledge revision artifact memberships must have unique logical ids")


@dataclass(frozen=True, slots=True)
class PatchCandidate:
    id: str
    change_set: ChangeSet
    impact_floor: DeterministicImpactFloor
    base_knowledge_revision_id: str | None
    knowledge_revision_id: str
    created_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        _require_identifier(self.id, "candidate_id")
        _require_identifier(self.knowledge_revision_id, "knowledge_revision_id")
        if self.base_knowledge_revision_id is not None:
            _require_identifier(self.base_knowledge_revision_id, "base_knowledge_revision_id")
        _require_aware_datetime(self.created_at, "created_at")
        if self.impact_floor.source_revision_key != self.change_set.source_revision.key:
            raise LivingDocsContractError("impact floor must be bound to the candidate source revision")

    @classmethod
    def new(
        cls,
        *,
        change_set: ChangeSet,
        impact_floor: DeterministicImpactFloor,
        base_knowledge_revision_id: str | None,
        knowledge_revision_id: str,
    ) -> "PatchCandidate":
        return cls(
            id=candidate_id_for(
                project_id=change_set.source_revision.project_id, idempotency_key=change_set.idempotency_key
            ),
            change_set=change_set,
            impact_floor=impact_floor,
            base_knowledge_revision_id=base_knowledge_revision_id,
            knowledge_revision_id=knowledge_revision_id,
        )


@dataclass(frozen=True, slots=True)
class CandidateStatus:
    candidate_id: str
    state: CandidateState
    version: int
    updated_at: datetime
    reason_code: str | None = None

    def __post_init__(self) -> None:
        _require_identifier(self.candidate_id, "candidate_id")
        if self.version < 0:
            raise LivingDocsContractError("candidate status version must not be negative")
        _require_aware_datetime(self.updated_at, "updated_at")
        if self.reason_code is not None:
            _require_nonblank(self.reason_code, "reason_code")


def can_transition(source: CandidateState, target: CandidateState) -> bool:
    return target in _TRANSITIONS[source]


def candidate_to_dict(candidate: PatchCandidate) -> dict[str, Any]:
    """Return the durable, versioned JSON form of a candidate."""

    return _json_value(candidate)


def candidate_from_dict(value: Mapping[str, Any]) -> PatchCandidate:
    """Restore a candidate written by :func:`candidate_to_dict`.

    This intentionally accepts only our own small durable schema, rather than
    accepting arbitrary dataclass-shaped JSON at the persistence boundary.
    """

    change_set_data = _mapping(value, "change_set")
    source_data = _mapping(change_set_data, "source_revision")
    source = SourceRevision(
        project_id=_string(source_data, "project_id"),
        origin=SourceOrigin(_string(source_data, "origin")),
        source_identity=_string(source_data, "source_identity"),
        revision_id=_string(source_data, "revision_id"),
        content_sha256=_string(source_data, "content_sha256"),
        observed_at=_datetime(source_data, "observed_at"),
    )
    changes = tuple(
        SemanticChange(
            kind=ChangeKind(_string(item, "kind")),
            logical_path=_string(item, "logical_path"),
            field_id=_optional_string(item, "field_id"),
            before_sha256=_optional_string(item, "before_sha256"),
            after_sha256=_optional_string(item, "after_sha256"),
            previous_logical_path=_optional_string(item, "previous_logical_path"),
        )
        for item in _mappings(change_set_data, "changes")
    )
    change_set = ChangeSet(
        source_revision=source,
        base_source_revision_id=_optional_string(change_set_data, "base_source_revision_id"),
        idempotency_key=_string(change_set_data, "idempotency_key"),
        changes=changes,
        scanner_sha256=_optional_string(change_set_data, "scanner_sha256"),
    )
    floor_data = _mapping(value, "impact_floor")
    floor = DeterministicImpactFloor(
        source_revision_key=_string(floor_data, "source_revision_key"),
        targets=tuple(
            PatchTarget(
                artifact_id=_string(item, "artifact_id"),
                section_id=_string(item, "section_id"),
                artifact_kind=ArtifactKind(_string(item, "artifact_kind")),
            )
            for item in _mappings(floor_data, "targets")
        ),
        evidence_ids=tuple(_strings(floor_data, "evidence_ids")),
        mapper_version=_string(floor_data, "mapper_version"),
    )
    return PatchCandidate(
        id=_string(value, "id"),
        change_set=change_set,
        impact_floor=floor,
        base_knowledge_revision_id=_optional_string(value, "base_knowledge_revision_id"),
        knowledge_revision_id=_string(value, "knowledge_revision_id"),
        created_at=_datetime(value, "created_at"),
    )


def _require_identifier(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not value or value != value.strip() or "\x00" in value or "/" in value or "\\" in value:
        raise LivingDocsContractError(f"{field_name} must be a non-empty safe identifier")


def _require_nonblank(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise LivingDocsContractError(f"{field_name} must be a non-blank NUL-free string")


def _require_logical_path(value: str, field_name: str) -> None:
    _require_nonblank(value, field_name)
    pieces = value.split("/")
    if value.startswith("/") or "\\" in value or any(piece in {"", ".", ".."} for piece in pieces):
        raise LivingDocsContractError(f"{field_name} must be a relative logical path")


def _require_sha256(value: str, field_name: str) -> None:
    if not isinstance(value, str) or len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise LivingDocsContractError(f"{field_name} must be a lowercase SHA-256 digest")


def _require_unique_nonblank(values: tuple[str, ...], field_name: str) -> None:
    if not values or len(set(values)) != len(values) or any(not isinstance(value, str) or not value.strip() for value in values):
        raise LivingDocsContractError(f"{field_name} must contain unique non-blank values")


def _require_aware_datetime(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise LivingDocsContractError(f"{field_name} must be timezone-aware")


def _json_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, StrEnum):
        return value.value
    if is_dataclass(value):
        return {key: _json_value(item) for key, item in asdict(value).items()}
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, tuple | list | set | frozenset):
        return [_json_value(item) for item in value]
    return value


def _mapping(value: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    nested = value.get(key)
    if not isinstance(nested, Mapping):
        raise LivingDocsContractError(f"{key} must be an object")
    return nested


def _mappings(value: Mapping[str, Any], key: str) -> tuple[Mapping[str, Any], ...]:
    nested = value.get(key)
    if not isinstance(nested, list) or not all(isinstance(item, Mapping) for item in nested):
        raise LivingDocsContractError(f"{key} must be an array of objects")
    return tuple(nested)


def _strings(value: Mapping[str, Any], key: str) -> tuple[str, ...]:
    nested = value.get(key)
    if not isinstance(nested, list) or not all(isinstance(item, str) for item in nested):
        raise LivingDocsContractError(f"{key} must be an array of strings")
    return tuple(nested)


def _string(value: Mapping[str, Any], key: str) -> str:
    nested = value.get(key)
    if not isinstance(nested, str):
        raise LivingDocsContractError(f"{key} must be a string")
    return nested


def _optional_string(value: Mapping[str, Any], key: str) -> str | None:
    nested = value.get(key)
    if nested is not None and not isinstance(nested, str):
        raise LivingDocsContractError(f"{key} must be a string or null")
    return nested


def _datetime(value: Mapping[str, Any], key: str) -> datetime:
    raw = _string(value, key)
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise LivingDocsContractError(f"{key} must be an ISO-8601 timestamp") from exc
    _require_aware_datetime(parsed, key)
    return parsed
