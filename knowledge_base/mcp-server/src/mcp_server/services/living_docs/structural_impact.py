"""Pure structural impact planning for living-document candidates.

The coordinator supplies scanner-derived structural deltas and the persisted
``GroupDocDependency`` provenance map.  This module returns only the exact
candidate targets and stale RAG identity mutations; it performs no I/O, model
call, generation, or publication.  An unisolatable cross-cutting document is a
terminal hold, never a reason to widen scope to a whole-project rebuild.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum

from mcp_server.services.group_doc_dependency import GroupDocDependency
from mcp_server.services.living_docs.contracts import (
    ArtifactKind,
    PatchTarget,
    RagMutationIntent,
    RagMutationKind,
)


class StructuralChangeKind(StrEnum):
    """Scanner-level structural changes that require bounded re-derivation."""

    UNIT_ADDED = "unit_added"
    UNIT_DELETED = "unit_deleted"
    UNIT_RENAMED = "unit_renamed"
    GROUP_ADDED = "group_added"
    GROUP_DELETED = "group_deleted"
    GROUP_RENAMED = "group_renamed"


@dataclass(frozen=True, slots=True)
class StructuralChange:
    """One validated unit or group identity transition.

    ``previous_*`` values are required only for a rename.  The planner does not
    derive identities from paths: callers must pass scanner-backed identities.
    """

    kind: StructuralChangeKind
    group: str
    unit: str | None = None
    previous_group: str | None = None
    previous_unit: str | None = None

    def __post_init__(self) -> None:
        _require_identity(self.group, "group")
        is_unit = self.kind in {
            StructuralChangeKind.UNIT_ADDED,
            StructuralChangeKind.UNIT_DELETED,
            StructuralChangeKind.UNIT_RENAMED,
        }
        if is_unit:
            _require_identity(self.unit, "unit")
        elif self.unit is not None:
            raise ValueError("group changes must not declare unit")

        if self.kind is StructuralChangeKind.UNIT_RENAMED:
            _require_identity(self.previous_unit, "previous_unit")
            if self.previous_group is not None:
                _require_identity(self.previous_group, "previous_group")
        elif self.kind is StructuralChangeKind.GROUP_RENAMED:
            _require_identity(self.previous_group, "previous_group")
            if self.previous_unit is not None:
                raise ValueError("group rename must not declare previous_unit")
        elif self.previous_group is not None or self.previous_unit is not None:
            raise ValueError("previous identity is only valid for renamed changes")


@dataclass(frozen=True, slots=True)
class StructuralImpactPlan:
    """A bounded structural impact plan, or a fail-closed hold receipt."""

    changed_groups: tuple[str, ...]
    analysis_groups: tuple[str, ...]
    affected_docs: tuple[str, ...]
    targets: tuple[PatchTarget, ...]
    rag_mutations: tuple[RagMutationIntent, ...]
    reason_code: str | None = None
    reason: str | None = None

    @property
    def held(self) -> bool:
        return self.reason_code is not None

    @classmethod
    def hold(cls, *, reason_code: str, reason: str) -> "StructuralImpactPlan":
        return cls((), (), (), (), (), reason_code=reason_code, reason=reason)


class StructuralImpactPlanner:
    """Build exact structural targets from scanner identities plus provenance.

    ``synthesis_sections`` is deliberately supplied by a prior deterministic
    section mapper.  Every cross-cutting document must have at least one exact
    section before this planner will authorize a candidate mutation.
    """

    @staticmethod
    def plan(
        changes: Iterable[StructuralChange],
        dependency: GroupDocDependency | None,
        *,
        synthesis_sections: Mapping[str, Iterable[str]] | None = None,
    ) -> StructuralImpactPlan:
        items = tuple(changes)
        if not items:
            return StructuralImpactPlan.hold(
                reason_code="empty_structural_scope",
                reason="Structural impact planning requires at least one scanner change",
            )
        if dependency is None:
            return StructuralImpactPlan.hold(
                reason_code="traceability_required",
                reason="Structural impact planning requires a provenance map",
            )

        changed_groups = _changed_groups(items)
        group_docs: set[str] = set()
        for group in changed_groups:
            group_docs.update(dependency.docs_for_group(group))

        # A rename can legitimately map only through its old baseline group;
        # its existing documents are the exact artifacts that must lose the old
        # identity and gain the new one. Other structural changes need a mapped
        # direct document rather than silently relying on a summary document.
        required_direct_groups = _required_direct_groups(items)
        missing_direct = sorted(
            group for group in required_direct_groups if not dependency.docs_for_group(group)
        )
        if missing_direct:
            return StructuralImpactPlan.hold(
                reason_code="traceability_required",
                reason=(
                    "Structural changes have no direct provenance mapping for: "
                    + ", ".join(missing_direct)
                ),
            )

        summary_docs = dependency.cross_cutting_docs()
        sections_by_doc = _normalise_sections(synthesis_sections)
        unisolatable = sorted(
            doc for doc in summary_docs if not sections_by_doc.get(doc)
        )
        if unisolatable:
            return StructuralImpactPlan.hold(
                reason_code="synthesis_mapping_required",
                reason=(
                    "Cross-cutting documentation has no isolated target section: "
                    + ", ".join(unisolatable)
                ),
            )

        canonical_targets: set[PatchTarget] = set()
        for doc in sorted(group_docs - summary_docs):
            for group in changed_groups:
                if doc in dependency.docs_for_group(group):
                    canonical_targets.add(
                        PatchTarget(
                            artifact_id=f"docs/{doc}.md",
                            section_id=f"group:{group}",
                            artifact_kind=ArtifactKind.CANONICAL_DOCUMENT,
                        )
                    )
        for doc in sorted(summary_docs):
            for heading in sections_by_doc[doc]:
                canonical_targets.add(
                    PatchTarget(
                        artifact_id=f"docs/{doc}.md",
                        section_id=f"synthesis:{heading}",
                        artifact_kind=ArtifactKind.CANONICAL_DOCUMENT,
                    )
                )

        analysis_targets = {
            PatchTarget(
                artifact_id=f"analysis/{group}.md",
                section_id=f"group:{group}",
                artifact_kind=ArtifactKind.INTERNAL_ANALYSIS,
            )
            for group in changed_groups
        }
        targets = tuple(sorted(analysis_targets | canonical_targets, key=lambda target: target.key))
        rag_mutations = tuple(sorted(_stale_identity_mutations(items), key=_mutation_key))
        return StructuralImpactPlan(
            changed_groups=tuple(sorted(changed_groups)),
            analysis_groups=tuple(sorted(changed_groups)),
            affected_docs=tuple(sorted(group_docs | summary_docs)),
            targets=targets,
            rag_mutations=rag_mutations,
        )


def _changed_groups(changes: tuple[StructuralChange, ...]) -> set[str]:
    groups: set[str] = set()
    for change in changes:
        groups.add(change.group)
        if change.kind is StructuralChangeKind.GROUP_RENAMED:
            assert change.previous_group is not None
            groups.add(change.previous_group)
        elif change.kind is StructuralChangeKind.UNIT_RENAMED and change.previous_group:
            groups.add(change.previous_group)
    return groups


def _required_direct_groups(changes: tuple[StructuralChange, ...]) -> set[str]:
    required: set[str] = set()
    for change in changes:
        if change.kind is StructuralChangeKind.GROUP_RENAMED:
            assert change.previous_group is not None
            required.add(change.previous_group)
        else:
            required.add(change.group)
    return required


def _normalise_sections(
    sections: Mapping[str, Iterable[str]] | None,
) -> dict[str, tuple[str, ...]]:
    return {
        doc: tuple(sorted({heading.strip() for heading in headings if heading and heading.strip()}))
        for doc, headings in (sections or {}).items()
        if doc
    }


def _stale_identity_mutations(
    changes: tuple[StructuralChange, ...],
) -> set[RagMutationIntent]:
    mutations: set[RagMutationIntent] = set()
    for change in changes:
        if change.kind is StructuralChangeKind.UNIT_DELETED:
            assert change.unit is not None
            mutations.update(_delete_entity(change.group, change.unit))
        elif change.kind is StructuralChangeKind.GROUP_DELETED:
            mutations.update(_delete_group(change.group))
        elif change.kind is StructuralChangeKind.UNIT_RENAMED:
            assert change.unit is not None and change.previous_unit is not None
            old_group = change.previous_group or change.group
            old = _entity_id(old_group, change.previous_unit)
            new = _entity_id(change.group, change.unit)
            mutations.update(_delete_entity(old_group, change.previous_unit))
            mutations.add(
                RagMutationIntent(
                    logical_id=f"aliases/{old}",
                    artifact_kind=ArtifactKind.INTERNAL_ANALYSIS,
                    mutation=RagMutationKind.ALIAS,
                    alias_of=new,
                )
            )
        elif change.kind is StructuralChangeKind.GROUP_RENAMED:
            assert change.previous_group is not None
            old = _group_id(change.previous_group)
            new = _group_id(change.group)
            mutations.update(_delete_group(change.previous_group))
            mutations.add(
                RagMutationIntent(
                    logical_id=f"aliases/{old}",
                    artifact_kind=ArtifactKind.INTERNAL_ANALYSIS,
                    mutation=RagMutationKind.ALIAS,
                    alias_of=new,
                )
            )
    return mutations


def _delete_entity(group: str, unit: str) -> set[RagMutationIntent]:
    logical_id = _entity_id(group, unit)
    return {
        RagMutationIntent(logical_id, ArtifactKind.INTERNAL_ANALYSIS, RagMutationKind.DELETE),
        RagMutationIntent(f"aliases/{logical_id}", ArtifactKind.INTERNAL_ANALYSIS, RagMutationKind.DELETE),
    }


def _delete_group(group: str) -> set[RagMutationIntent]:
    logical_id = _group_id(group)
    return {
        RagMutationIntent(logical_id, ArtifactKind.INTERNAL_ANALYSIS, RagMutationKind.DELETE),
        RagMutationIntent(f"aliases/{logical_id}", ArtifactKind.INTERNAL_ANALYSIS, RagMutationKind.DELETE),
    }


def _entity_id(group: str, unit: str) -> str:
    return f"entities/{group}/{unit}"


def _group_id(group: str) -> str:
    return f"analysis/{group}.md"


def _mutation_key(mutation: RagMutationIntent) -> tuple[str, str, str]:
    return (mutation.logical_id, mutation.mutation.value, mutation.alias_of or "")


def _require_identity(value: str | None, field_name: str) -> None:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or any(char in value for char in ("/", "\\", "\x00"))
    ):
        raise ValueError(f"{field_name} must be a safe non-empty identity")
