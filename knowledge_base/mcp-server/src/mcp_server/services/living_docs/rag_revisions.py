"""Revision-scoped RAG staging for incremental documentation patches.

The live vector backend is deliberately behind :class:`RevisionedRagPort`.
This module owns the invariants which make a candidate safe to prepare before
it is visible: a prepared revision has an immutable membership snapshot, each
artifact carries a content hash, and activation is a compare-and-swap against
the revision it was prepared from.

``InMemoryRevisionedRagStore`` is a deterministic fake/reference adapter.  It
is suitable for local integration tests and provides the behaviour a durable
vector-store adapter must preserve.  It never calls an embedding provider.
"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol


def content_sha256(content: str) -> str:
    """Return the stable content identity used for embedding reuse."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


class RagRevisionError(RuntimeError):
    """Base error for a revision lifecycle invariant violation."""


class RevisionNotPreparedError(RagRevisionError):
    """Raised when validation/activation names an unknown candidate."""


class RevisionValidationError(RagRevisionError):
    """Raised when a candidate cannot be made active."""


class StaleRagRevisionError(RagRevisionError):
    """Raised when activation no longer has the candidate's prepared base."""


class RagMutationKind(StrEnum):
    UPSERT = "upsert"
    DELETE = "delete"
    ALIAS = "alias"


@dataclass(frozen=True, slots=True)
class RagArtifact:
    """One canonical or internal item belonging to a knowledge revision."""

    logical_id: str
    content: str
    source_layer: str
    artifact_type: str
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.logical_id:
            raise ValueError("RAG artifact logical_id is required")
        if not self.content:
            raise ValueError(f"RAG artifact {self.logical_id!r} has empty content")
        if not self.source_layer:
            raise ValueError(f"RAG artifact {self.logical_id!r} has no source_layer")
        if not self.artifact_type:
            raise ValueError(f"RAG artifact {self.logical_id!r} has no artifact_type")

    @property
    def content_hash(self) -> str:
        return content_sha256(self.content)


@dataclass(frozen=True, slots=True)
class RagMutation:
    """A targeted membership change; whole-layer replacement is not modelled."""

    kind: RagMutationKind
    logical_id: str
    artifact: RagArtifact | None = None
    alias_target: str | None = None

    def __post_init__(self) -> None:
        if not self.logical_id:
            raise ValueError("RAG mutation logical_id is required")
        if self.kind is RagMutationKind.UPSERT:
            if self.artifact is None or self.artifact.logical_id != self.logical_id:
                raise ValueError("An upsert must carry an artifact with the same logical_id")
            if self.alias_target is not None:
                raise ValueError("An upsert cannot also declare an alias")
        elif self.kind is RagMutationKind.DELETE:
            if self.artifact is not None or self.alias_target is not None:
                raise ValueError("A delete carries only its logical_id")
        elif self.kind is RagMutationKind.ALIAS:
            if self.artifact is not None or not self.alias_target:
                raise ValueError("An alias requires alias_target and no artifact")

    @classmethod
    def upsert(cls, artifact: RagArtifact) -> "RagMutation":
        return cls(kind=RagMutationKind.UPSERT, logical_id=artifact.logical_id, artifact=artifact)

    @classmethod
    def delete(cls, logical_id: str) -> "RagMutation":
        return cls(kind=RagMutationKind.DELETE, logical_id=logical_id)

    @classmethod
    def alias(cls, logical_id: str, alias_target: str) -> "RagMutation":
        return cls(kind=RagMutationKind.ALIAS, logical_id=logical_id, alias_target=alias_target)


@dataclass(frozen=True, slots=True)
class PreparedRagRevision:
    """A receipt for an inactive candidate with observable content reuse."""

    project_id: str
    knowledge_revision_id: str
    base_knowledge_revision_id: str | None
    membership_hashes: Mapping[str, str]
    deleted_logical_ids: frozenset[str]
    aliases: Mapping[str, str]
    embedded_content_hashes: frozenset[str]
    reused_content_hashes: frozenset[str]

    @property
    def new_embedding_count(self) -> int:
        return len(self.embedded_content_hashes)

    @property
    def reused_content_count(self) -> int:
        return len(self.reused_content_hashes)


@dataclass(frozen=True, slots=True)
class RagValidationReceipt:
    project_id: str
    knowledge_revision_id: str
    membership_hashes: Mapping[str, str]
    deleted_logical_ids: frozenset[str]
    aliases: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class RagActivationReceipt:
    project_id: str
    knowledge_revision_id: str
    previous_knowledge_revision_id: str | None


@dataclass(frozen=True, slots=True)
class RetrievedRagArtifact:
    """A fake retrieval result which always identifies the active revision."""

    artifact: RagArtifact
    knowledge_revision_id: str


class RevisionedRagPort(Protocol):
    """Port implemented by a revision-aware RAG backend adapter."""

    async def prepare(
        self,
        project_id: str,
        knowledge_revision_id: str,
        mutations: Sequence[RagMutation],
        *,
        base_knowledge_revision_id: str | None,
    ) -> PreparedRagRevision: ...

    async def validate(
        self,
        project_id: str,
        knowledge_revision_id: str,
        *,
        required_artifact_hashes: Mapping[str, str] | None = None,
        required_deletes: Sequence[str] = (),
        required_aliases: Mapping[str, str] | None = None,
    ) -> RagValidationReceipt: ...

    async def activate(
        self, project_id: str, knowledge_revision_id: str
    ) -> RagActivationReceipt: ...

    async def active_revision(self, project_id: str) -> str | None: ...

    async def rollback(
        self,
        project_id: str,
        knowledge_revision_id: str,
        *,
        expected_active_revision_id: str,
    ) -> RagActivationReceipt: ...


@dataclass(slots=True)
class _Candidate:
    base_knowledge_revision_id: str | None
    membership: dict[str, RagArtifact]
    aliases: dict[str, str]
    deleted_logical_ids: set[str]
    prepared: PreparedRagRevision
    validated: bool = False


class InMemoryRevisionedRagStore:
    """Fake/reference implementation with atomic revision activation.

    The store keeps content blobs separately from revision membership.  A
    candidate can therefore reuse an unchanged content hash while referencing
    it from a different revision without an embed operation.  It has no live
    vector client and is intentionally process-local.
    """

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._content_blobs: dict[str, str] = {}
        self._candidates: dict[tuple[str, str], _Candidate] = {}
        self._revisions: dict[tuple[str, str], _Candidate] = {}
        self._active: dict[str, str] = {}

    async def prepare(
        self,
        project_id: str,
        knowledge_revision_id: str,
        mutations: Sequence[RagMutation],
        *,
        base_knowledge_revision_id: str | None,
    ) -> PreparedRagRevision:
        """Create/replay an isolated candidate without changing active reads."""
        if not project_id or not knowledge_revision_id:
            raise ValueError("project_id and knowledge_revision_id are required")
        if len({mutation.logical_id for mutation in mutations}) != len(mutations):
            raise RevisionValidationError("A revision may mutate each logical_id only once")

        key = (project_id, knowledge_revision_id)
        async with self._lock:
            current_active = self._active.get(project_id)
            if base_knowledge_revision_id != current_active:
                raise StaleRagRevisionError(
                    f"Cannot prepare {knowledge_revision_id}: expected active base "
                    f"{base_knowledge_revision_id!r}, found {current_active!r}"
                )

            existing = self._candidates.get(key) or self._revisions.get(key)
            if existing is not None:
                if existing.base_knowledge_revision_id != base_knowledge_revision_id:
                    raise RevisionValidationError("Prepared revision has a different base")
                return existing.prepared

            base_candidate = (
                self._revisions.get((project_id, base_knowledge_revision_id))
                if base_knowledge_revision_id is not None
                else None
            )
            if base_knowledge_revision_id is not None and base_candidate is None:
                raise RevisionNotPreparedError(
                    f"Base revision {base_knowledge_revision_id!r} is not retained"
                )

            membership = dict(base_candidate.membership) if base_candidate else {}
            aliases = dict(base_candidate.aliases) if base_candidate else {}
            deleted: set[str] = set()
            embedded: set[str] = set()
            reused = {artifact.content_hash for artifact in membership.values()}

            for mutation in mutations:
                if mutation.kind is RagMutationKind.UPSERT:
                    assert mutation.artifact is not None
                    artifact = mutation.artifact
                    content_hash = artifact.content_hash
                    if content_hash in self._content_blobs:
                        reused.add(content_hash)
                    else:
                        # This records the one content identity a real adapter
                        # must embed.  It is still invisible until activation.
                        self._content_blobs[content_hash] = artifact.content
                        embedded.add(content_hash)
                    membership[artifact.logical_id] = artifact
                    aliases.pop(artifact.logical_id, None)
                elif mutation.kind is RagMutationKind.DELETE:
                    membership.pop(mutation.logical_id, None)
                    aliases.pop(mutation.logical_id, None)
                    deleted.add(mutation.logical_id)
                else:
                    assert mutation.alias_target is not None
                    aliases[mutation.logical_id] = mutation.alias_target

            receipt = PreparedRagRevision(
                project_id=project_id,
                knowledge_revision_id=knowledge_revision_id,
                base_knowledge_revision_id=base_knowledge_revision_id,
                membership_hashes={key: value.content_hash for key, value in membership.items()},
                deleted_logical_ids=frozenset(deleted),
                aliases=dict(aliases),
                embedded_content_hashes=frozenset(embedded),
                reused_content_hashes=frozenset(reused),
            )
            candidate = _Candidate(
                base_knowledge_revision_id=base_knowledge_revision_id,
                membership=membership,
                aliases=aliases,
                deleted_logical_ids=deleted,
                prepared=receipt,
            )
            self._candidates[key] = candidate
            return receipt

    async def validate(
        self,
        project_id: str,
        knowledge_revision_id: str,
        *,
        required_artifact_hashes: Mapping[str, str] | None = None,
        required_deletes: Sequence[str] = (),
        required_aliases: Mapping[str, str] | None = None,
    ) -> RagValidationReceipt:
        """Prove expected canonical/internal membership before activation."""
        key = (project_id, knowledge_revision_id)
        async with self._lock:
            candidate = self._candidates.get(key) or self._revisions.get(key)
            if candidate is None:
                raise RevisionNotPreparedError(f"Revision {knowledge_revision_id!r} is not prepared")
            expected_hashes = dict(required_artifact_hashes or {})
            actual_hashes = {
                logical_id: artifact.content_hash
                for logical_id, artifact in candidate.membership.items()
            }
            missing_or_changed = {
                logical_id: expected
                for logical_id, expected in expected_hashes.items()
                if actual_hashes.get(logical_id) != expected
            }
            if missing_or_changed:
                raise RevisionValidationError(
                    f"Artifact membership mismatch: {sorted(missing_or_changed)}"
                )
            missing_deletes = set(required_deletes) - candidate.deleted_logical_ids
            if missing_deletes:
                raise RevisionValidationError(
                    f"Delete membership mismatch: {sorted(missing_deletes)}"
                )
            expected_aliases = dict(required_aliases or {})
            if any(candidate.aliases.get(alias) != target for alias, target in expected_aliases.items()):
                raise RevisionValidationError("Alias membership mismatch")
            candidate.validated = True
            return RagValidationReceipt(
                project_id=project_id,
                knowledge_revision_id=knowledge_revision_id,
                membership_hashes=actual_hashes,
                deleted_logical_ids=frozenset(candidate.deleted_logical_ids),
                aliases=dict(candidate.aliases),
            )

    async def activate(
        self, project_id: str, knowledge_revision_id: str
    ) -> RagActivationReceipt:
        """Atomically make one fully validated membership visible to reads."""
        key = (project_id, knowledge_revision_id)
        async with self._lock:
            candidate = self._candidates.get(key) or self._revisions.get(key)
            if candidate is None:
                raise RevisionNotPreparedError(f"Revision {knowledge_revision_id!r} is not prepared")
            if not candidate.validated:
                raise RevisionValidationError("A revision must validate before activation")
            previous = self._active.get(project_id)
            if candidate.base_knowledge_revision_id != previous and previous != knowledge_revision_id:
                raise StaleRagRevisionError(
                    f"Cannot activate {knowledge_revision_id}: expected base "
                    f"{candidate.base_knowledge_revision_id!r}, found {previous!r}"
                )
            self._active[project_id] = knowledge_revision_id
            self._revisions[key] = candidate
            self._candidates.pop(key, None)
            return RagActivationReceipt(
                project_id=project_id,
                knowledge_revision_id=knowledge_revision_id,
                previous_knowledge_revision_id=previous,
            )

    async def active_revision(self, project_id: str) -> str | None:
        async with self._lock:
            return self._active.get(project_id)

    async def rollback(
        self,
        project_id: str,
        knowledge_revision_id: str,
        *,
        expected_active_revision_id: str,
    ) -> RagActivationReceipt:
        """CAS-restore a retained complete revision without re-embedding."""

        async with self._lock:
            active = self._active.get(project_id)
            if active != expected_active_revision_id:
                raise StaleRagRevisionError(
                    f"Cannot roll back: expected active {expected_active_revision_id!r}, found {active!r}"
                )
            if (project_id, knowledge_revision_id) not in self._revisions:
                raise RevisionNotPreparedError(
                    f"Rollback revision {knowledge_revision_id!r} is not retained"
                )
            self._active[project_id] = knowledge_revision_id
            return RagActivationReceipt(
                project_id=project_id,
                knowledge_revision_id=knowledge_revision_id,
                previous_knowledge_revision_id=active,
            )

    async def retrieve(
        self,
        project_id: str,
        *,
        source_layers: set[str] | None = None,
    ) -> list[RetrievedRagArtifact]:
        """Return only artifacts belonging to the project's active revision."""
        async with self._lock:
            revision_id = self._active.get(project_id)
            if revision_id is None:
                return []
            active = self._revisions[(project_id, revision_id)]
            return [
                RetrievedRagArtifact(artifact=artifact, knowledge_revision_id=revision_id)
                for _, artifact in sorted(active.membership.items())
                if source_layers is None or artifact.source_layer in source_layers
            ]
