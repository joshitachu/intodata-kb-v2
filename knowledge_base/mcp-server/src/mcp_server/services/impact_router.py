"""Pure impact resolution: fingerprint dirty-set -> affected document set.

Living-Document Reingestion, Slice 1. This module is the single home of the
``docs_for_group`` union ``cross_cutting_docs`` provenance mapping. Given the set
of scan groups the fingerprint diff marked dirty, it resolves which documents are
affected, separating **group-scoped** documents from whole-system **summary**
(cross-cutting) documents.

The separation matters for later slices: Slice 3 amends summary documents in
place from group deltas, so the two sets must be distinguishable here rather than
unioned away.

Design constraints (Strategy fed by the dirty-set):

- Pure: no I/O, no config, no thresholds. Every input arrives as an argument.
- Deterministic: sorted outputs, set-based membership.
- Never raises on missing provenance keys (delegates to ``GroupDocDependency``,
  which returns empty collections for unknown keys).

The legacy ``_affected_docs_from_dependency`` (git_reingestion) and
``ReingestionRouter._affected_docs`` delegate here so the union logic lives in one
place.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from mcp_server.services.group_doc_dependency import GroupDocDependency


@dataclass(frozen=True)
class RegenPlan:
    """The resolved impact of a dirty-set against the provenance map.

    ``group_scoped_docs`` and ``summary_docs`` are disjoint. ``targeted_docs`` is
    their union (the Phase 3 document filter). Doc names carry no ``.md`` suffix,
    matching the harness ``_required_docs`` and the provenance index keys.

    ``manifest_missing`` is True only when no provenance map exists (pre-baseline);
    callers treat that as "traceability unavailable" and take their own reasoned
    full-regeneration path rather than silently regenerating zero documents.
    """

    group_scoped_docs: list[str] = field(default_factory=list)
    summary_docs: list[str] = field(default_factory=list)
    manifest_missing: bool = False

    @property
    def targeted_docs(self) -> list[str]:
        return sorted(set(self.group_scoped_docs) | set(self.summary_docs))


class ImpactRouter:
    """Resolve a dirty-set into a :class:`RegenPlan` via the provenance map."""

    @staticmethod
    def plan(
        dirty_groups: set[str] | frozenset[str],
        dependency: GroupDocDependency | None,
    ) -> RegenPlan:
        """Map dirty groups to their affected documents.

        - ``dependency is None`` -> empty plan flagged ``manifest_missing`` (the
          caller decides the reasoned full path).
        - empty dirty-set -> empty plan (nothing to do).
        - otherwise -> group-scoped docs (``docs_for_group`` union, minus summary
          docs) plus the always-affected summary/cross-cutting docs.
        """
        if dependency is None:
            return RegenPlan(manifest_missing=True)

        groups = {g for g in dirty_groups if g}
        if not groups:
            return RegenPlan()

        summary = dependency.cross_cutting_docs()
        group_scoped: set[str] = set()
        for group in groups:
            group_scoped |= dependency.docs_for_group(group)
        # Summary docs are reported in their own bucket, never double-counted as
        # group-scoped even when a group's pack also feeds a cross-cutting doc.
        group_scoped -= summary

        return RegenPlan(
            group_scoped_docs=sorted(group_scoped),
            summary_docs=sorted(summary),
        )
