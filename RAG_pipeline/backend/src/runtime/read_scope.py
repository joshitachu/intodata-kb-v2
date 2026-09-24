"""What one runtime read is allowed to see, resolved once per request.

Why this module exists
----------------------
`runtime_sources.project_id` conflated three relations, and they are now three
separate things: `physical_identity` guarantees one tenant is mirrored once,
`estate_key` is the stamp every mirrored row carries, and
`RuntimeSourceReadGrantRow` records which projects may READ that estate. One
vendor tenant commonly backs several scanned projects; registration correctly
refuses the same credential twice, so a second project reaches that tenant by
being attached to it rather than by mirroring it again.

What a term names
-----------------
A term's `project_id` is a ROW STAMP, not necessarily a project. For a source
registered against a project the estate key IS that project's id, so a legacy
source resolves to exactly the terms it always did -- and a project reading only
its own mirror still resolves to ONE unrestricted term, which
`_apply_read_terms` emits as the bare equality it emitted before any of this
existed. For a source registered with no project the stamp is a derived estate
key, and naming it here is the only way an attached project can reach those
rows at all.

Before this module, grant widening was applied in exactly three read paths --
`GET /runtime/doc-evidence`, `GET /runtime/doc-read-scope` and the Elastic
aggregates -- each resolving it inline. Every other runtime read (the ~40
`/runtime/*` list endpoints and both agent runtime tools) filtered on the raw
row stamp and therefore returned NOTHING to a granted reader: a project could
generate documents from a shared tenant but could not ask a single question
about it. Resolving the read set in one place, and applying it through one
predicate builder, is what stops the next read path from being added without
it.

The rule, in one sentence
-------------------------
A caller that names no project is not widened at all; a caller that names a
project sees its own rows in full, plus each attached estate's rows narrowed by
that attachment's own environment and window.

Per-project terms, not a flat union
-----------------------------------
`RuntimeDocReadScope` unions every environment selection into one flat list and
applies it to the whole read. That is defensible for documentation -- an
operator's own selection may only narrow their own contribution, so an
intersection would let one source suppress another's data -- but it is the
wrong rule for a grant. A grant scoped to `prod` is a boundary the grantor set
on THEIR estate; applying it flatly would either leak the grantor's non-prod
rows (union) or hide the reader's own (intersection). So the read set is a list
of per-project terms, and `RuntimeStoreRepository._apply_read_terms` emits them
as a disjunction:

    (project_id = reader)
    OR (project_id = grantor AND environment in (...) AND started_at >= ...)

A reader's OWN term is deliberately unrestricted. The per-project documentation
read scope stored in a source's credential blob narrows what documentation
reads out of the mirror; it is not an access boundary, and applying it here
would silently shrink the logs console for a project that owns its data
outright.
"""

from __future__ import annotations

import datetime
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

if TYPE_CHECKING:  # pragma: no cover - import cycle guard
    from src.runtime.store.repository import RuntimeStoreRepository

# `list[str] | None`, re-declared rather than imported: `src.runtime.store`
# imports the repository, which imports this module.
ProjectScope = list[str] | None


class RuntimeReadTerm(BaseModel):
    """One project's contribution to a read, with the limits attached to it.

    `environment_ids` None means every environment; an empty list is a
    deliberate "nothing" and is preserved as such, matching the distinction
    `RuntimeSourceReadGrantRow` and `RuntimeDocReadScope` already draw. `since`
    is an absolute floor computed from the grant's `window_days` at resolution
    time, so every predicate downstream compares against a fixed instant rather
    than re-deriving "now" per query and drifting between a page and its count.
    """

    model_config = {"frozen": True}

    project_id: str
    environment_ids: list[str] | None = None
    since: datetime.datetime | None = None

    @property
    def unrestricted(self) -> bool:
        """True when this term adds no limit beyond the project stamp itself."""
        return self.environment_ids is None and self.since is None


class RuntimeReadScope(BaseModel):
    """The resolved answer to "what may this request read?".

    Carries both halves so they can never be applied apart: `scope` is the RBAC
    boundary (widened by grants, because a grant names a granting project the
    reader may have no other permission to see -- that is the point of it, and
    it was created by someone holding both sides), and `terms` is the row-stamp
    filter.
    """

    model_config = {"frozen": True}

    scope: ProjectScope = None
    terms: list[RuntimeReadTerm] = Field(default_factory=list)
    shared_read: bool = False
    shared_from: list[str] = Field(default_factory=list)

    @property
    def project_ids(self) -> list[str] | None:
        """Every project this read may draw rows from, reader first.

        None when the caller named no project, which means "no row-stamp
        filter" -- not "no projects". `scope` is still the RBAC boundary in that
        case.
        """
        if not self.terms:
            return None
        return [term.project_id for term in self.terms]

    @property
    def denies_all(self) -> bool:
        return self.scope is not None and len(self.scope) == 0

    @classmethod
    def unscoped(cls, scope: ProjectScope) -> "RuntimeReadScope":
        """A read with an RBAC boundary but no project filter and no widening.

        What a caller that named no `project_id` gets. Widening only ever
        follows from naming a project, so a listing across everything behaves
        exactly as it did before grants existed.
        """
        return cls(scope=scope, terms=[])


def _merge_term(existing: RuntimeReadTerm, incoming: RuntimeReadTerm) -> RuntimeReadTerm:
    """Combine two grants that reach the same project, least restrictive wins.

    Two grants exposing one project is unusual but reachable (a workspace map
    re-points a second source's rows to the same place). Taking the widest of
    each limit matches `resolve_doc_read_scope`, which takes the widest window
    for the same reason: a grant may narrow its own contribution, and letting
    one grant suppress what another deliberately allowed would be a silent,
    invisible denial.
    """
    if existing.environment_ids is None or incoming.environment_ids is None:
        environments = None
    else:
        environments = list(existing.environment_ids)
        environments.extend(
            item for item in incoming.environment_ids if item not in environments
        )
    if existing.since is None or incoming.since is None:
        since = None
    else:
        since = min(existing.since, incoming.since)
    return RuntimeReadTerm(
        project_id=existing.project_id, environment_ids=environments, since=since
    )


async def resolve_runtime_read_scope(
    repo: "RuntimeStoreRepository",
    *,
    user_scope: ProjectScope,
    project_id: str | None,
    now: datetime.datetime | None = None,
) -> RuntimeReadScope:
    """Resolve one request's read set from the user's scope and stored attachments.

    `user_scope` is checked FIRST, against the named project, exactly as
    `_intersect_scope` did: a project the caller cannot see is denied here and
    never reaches the attachment lookup. Widening then comes ONLY from stored
    attachment rows -- no caller may pass a project set in, because on the
    machine-key documentation path there is no second gate behind it.

    `resolve_read_grants_for_project` returns only the attachments that reach a
    FOREIGN estate, so a project reading its own mirror widens to nothing and
    reports `shared_read=False`, exactly as it did when a source had an owner.

    Returns an empty-scope result (`denies_all`) rather than raising, so a route
    can decide between 404 and an empty page; the repository refuses to read on
    an empty scope either way.
    """
    if project_id is None:
        return RuntimeReadScope.unscoped(user_scope)
    if user_scope is not None and project_id not in user_scope:
        return RuntimeReadScope(scope=[], terms=[])

    reference = now or datetime.datetime.now(datetime.UTC)
    terms: dict[str, RuntimeReadTerm] = {
        # The reader's own rows, unrestricted. See the module docstring.
        project_id: RuntimeReadTerm(project_id=project_id)
    }
    shared_from: list[str] = []
    for grant in await repo.resolve_read_grants_for_project(project_id):
        shared_from.append(grant.source_label)
        since = (
            reference - datetime.timedelta(days=grant.window_days)
            if grant.window_days and grant.window_days > 0
            else None
        )
        for exposed in grant.exposes:
            candidate = RuntimeReadTerm(
                project_id=exposed, environment_ids=grant.environment_ids, since=since
            )
            existing = terms.get(exposed)
            # The reader's own project can appear in `exposes` when a workspace
            # map re-points a granted source's rows back to it. Its own term
            # already covers those rows in full and must not be narrowed by a
            # grant it did not need.
            if existing is None:
                terms[exposed] = candidate
            elif exposed != project_id:
                terms[exposed] = _merge_term(existing, candidate)

    ordered = [terms[project_id]] + [
        term for key, term in terms.items() if key != project_id
    ]
    return RuntimeReadScope(
        # Scope is widened to the same set the terms name: a grant is the
        # permission, and the terms are what it permits.
        scope=[term.project_id for term in ordered],
        terms=ordered,
        shared_read=bool(shared_from),
        shared_from=shared_from,
    )


async def resolve_runtime_read_scope_for_user(
    repo: "RuntimeStoreRepository",
    *,
    user_scope: ProjectScope,
    now: datetime.datetime | None = None,
) -> RuntimeReadScope:
    """The read set for a request that names no project but reads by id.

    `GET /runtime/executions/{id}`, `/executions/{id}/logs` and their siblings
    identify a row by its own id, not by project, so `resolve_runtime_read_scope`
    has nothing to widen from. Without this they would 404 on exactly the rows a
    granted reader just saw in a list -- a drill-down that dead-ends one click
    after the feature appears to work, which is worse than not sharing at all.

    The widening is the union of the grants held by EVERY project the user can
    already see. That is sound because each grant was created by an operator
    holding both sides, and the user is independently entitled to the project
    that holds it; it grants no reach that naming those projects one at a time
    would not.

    An unrestricted user (`user_scope is None`) already sees every row, so there
    is nothing to widen and the scan over projects is skipped entirely.
    """
    if user_scope is None:
        return RuntimeReadScope.unscoped(None)

    reference = now or datetime.datetime.now(datetime.UTC)
    terms: dict[str, RuntimeReadTerm] = {
        # Every project the user owns outright, unrestricted.
        project_id: RuntimeReadTerm(project_id=project_id)
        for project_id in user_scope
    }
    owned = set(terms)
    shared_from: list[str] = []
    for project_id in user_scope:
        resolved = await resolve_runtime_read_scope(
            repo, user_scope=user_scope, project_id=project_id, now=reference
        )
        shared_from.extend(
            label for label in resolved.shared_from if label not in shared_from
        )
        for term in resolved.terms:
            # A project the user owns is already unrestricted above; a grant
            # must never narrow rows they can reach without it.
            if term.project_id in owned:
                continue
            existing = terms.get(term.project_id)
            terms[term.project_id] = (
                term if existing is None else _merge_term(existing, term)
            )

    ordered = [terms[project_id] for project_id in user_scope] + [
        term for key, term in terms.items() if key not in owned
    ]
    return RuntimeReadScope(
        scope=[term.project_id for term in ordered],
        terms=ordered,
        shared_read=bool(shared_from),
        shared_from=shared_from,
    )
