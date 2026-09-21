"""Reading what non-evidence material a project actually holds.

The agent has always been able to *search* a project's documents and has never
been able to *see* what it holds. Those are not the same thing: similarity
retrieval answers "is there a passage like this", not "does this project have a
policy at all". A constraint the agent never thought to search for is a
constraint that does not apply, which is the failure
:mod:`src.harness.guidance.material` exists to prevent.

This module is the read side of that: one query per turn that returns the
project's non-evidence documents as :class:`ProjectMaterial`, ready for the
renderer to turn into the manifest and for the workspace writer to mount.

Three properties matter more than the SQL:

* **The access boundary is enforced here, not assumed.** The manifest names
  documents. Naming one the caller may not read is a disclosure, even without
  its content -- a filename is frequently the most sensitive thing about a
  client document. See ``_caller_may_read`` for exactly how much of
  :func:`src.retrieval.search._build_search_filter` can be mirrored through this
  signature, and what the caller is therefore required to pass.
* **Never raises.** A manifest is an enhancement; an answer is the product.
  Every failure path degrades to an empty :class:`ProjectMaterial`, which the
  renderer omits entirely, which costs the turn nothing it had before.
* **The cache key means what the compiler's means.** An unchanged key is a
  promise that the rendered prefix is unchanged, so it digests every row that
  took part in the decision -- including the ones the entry cap pushed into
  ``overflow_count``, because that count renders too.

The text itself is deliberately not part of this. See
:func:`load_material_texts`.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Sequence
from typing import Final

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.document import Document
from src.db.models.document_text import DocumentTextRow
from src.document_roles import DOCUMENT_ROLES
from src.harness.guidance.material import (
    MANIFEST_MAX_ENTRIES,
    MaterialEntry,
    MaterialKind,
    ProjectMaterial,
    material_kind,
    should_materialize,
)
from src.harness.guidance.renderer import MATERIAL_KIND_PRIORITY

logger = logging.getLogger(__name__)

__all__ = ["compile_project_material", "load_material_texts"]


#: Roles that can appear in a manifest, derived rather than listed.
#:
#: Evidence is excluded at the SQL level, not filtered afterwards: the source
#: corpus is the bulk of ``documents`` (hundreds of rows per project), it is
#: searched by design, and pulling it across the wire every turn to discard it
#: would be the whole cost of the feature spent on nothing. Deriving the list
#: from the role->kind map means a role added later is classified once, in
#: :mod:`src.harness.guidance.material`, and appears here automatically.
_MANIFEST_ROLES: Final[tuple[str, ...]] = tuple(
    role for role in DOCUMENT_ROLES if material_kind(role) is not MaterialKind.EVIDENCE
)

#: Generated RAG support-layer artifacts, which are not project material.
#:
#: These are written by the pipeline into ``{project}/_internal/{layer}/...``
#: and are already excluded from the user-facing document list for the same
#: reason: nobody uploaded them, so describing one to the agent as background to
#: read would be describing the system's own output back to it.
_INTERNAL_ARTIFACT_PATTERN: Final = "%/_internal/%"

#: The group name that bypasses scoping, mirroring
#: :func:`src.retrieval.search._build_search_filter`. Named here rather than
#: imported because that function keeps it as a literal too; if it ever becomes
#: a shared constant, both should move together.
_ADMIN_GROUP: Final = "admin"


def _caller_may_read(project_id: str, user_groups: Sequence[str]) -> bool:
    """Whether ``user_groups`` grants access to ``project_id``.

    This is the Postgres-side equivalent of the ``allowed_groups`` condition in
    :func:`src.retrieval.search._build_search_filter`. Over in Qdrant a chunk
    carries ``allowed_groups``, which ingestion sets to ``[project_id]`` for
    every project document (``src/ingestion/pipeline.py`` and
    ``src/api/routes/ingest.py``), and the search filter admits a point when
    that list intersects ``user_groups + project_ids`` -- or unconditionally for
    the ``admin`` group. There is no ``allowed_groups`` column on ``documents``,
    so the equivalent test is whether the project id itself is in the caller's
    group set.

    That makes one demand of the caller, and it is load-bearing: ``user_groups``
    must be the *union* the search filter composes internally -- the caller's
    role names plus the project ids they are assigned -- not role names alone.
    The signature takes a single sequence, so this is stated rather than
    enforceable, and it fails closed: a caller that passes role names only gets
    an empty manifest and a warning in the log, never another project's
    filenames.
    """

    if _ADMIN_GROUP in user_groups:
        return True
    return project_id in user_groups


def _cache_key(
    contributions: list[tuple[int, str, int, bool]],
    *,
    project_id: str | None,
) -> str:
    """Digest of every document that fed this compile, plus the scope it was for.

    Same contract as :func:`src.harness.guidance.compiler._cache_key`: the
    render is pure, so an unchanged key must mean an unchanged prompt prefix.

    ``(document_id, role, char_count, materialized)`` is the full set of inputs
    the manifest line is built from -- role picks the wording, ``materialized``
    picks between a path and a search instruction, and ``char_count`` is what
    decides materialization in the first place. Rows beyond the entry cap are
    included even though they do not render individually: they move
    ``overflow_count``, which does.
    """

    payload = {
        "project_id": project_id,
        "documents": sorted(
            [document_id, role, char_count, materialized]
            for document_id, role, char_count, materialized in contributions
        ),
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


async def compile_project_material(
    db: AsyncSession, *, project_id: str | None, user_groups: Sequence[str]
) -> ProjectMaterial:
    """Compile the non-evidence material one caller may see in one project.

    ``user_groups`` must be the caller's full authorization group set -- role
    names plus assigned project ids, the same union
    ``_build_search_filter`` composes. See :func:`_caller_may_read`.

    Returns an empty :class:`ProjectMaterial` rather than raising, for any
    reason: no project bound, no access, or a database failure. The manifest is
    an addition to the prompt; losing it costs tuning, and failing the turn over
    it would cost the answer. An empty ``cache_key`` marks exactly this case --
    "nothing was compiled" -- and is deliberately distinguishable from the real
    digest a successful compile over zero documents produces.
    """

    if project_id is None:
        # No project binding, so nothing can be attributed to one. The renderer
        # drops its project section on the same condition.
        return ProjectMaterial()

    if not _caller_may_read(project_id, user_groups):
        # Fail closed, and loudly: this is also what an under-populated
        # ``user_groups`` looks like, and a silently empty manifest would be
        # indistinguishable from a project that simply holds no material.
        logger.warning(
            "Project material suppressed for project=%s: caller groups do not grant access",
            project_id,
        )
        return ProjectMaterial(project_id=project_id)

    statement = (
        select(
            Document.id,
            Document.filename,
            Document.document_role,
            DocumentTextRow.document_id,
            DocumentTextRow.char_count,
        )
        .outerjoin(DocumentTextRow, DocumentTextRow.document_id == Document.id)
        .where(
            Document.project_id == project_id,
            Document.deleted_at.is_(None),
            Document.document_role.in_(_MANIFEST_ROLES),
            Document.filename.not_like(_INTERNAL_ARTIFACT_PATTERN),
        )
        .order_by(Document.id)
    )

    try:
        result = await db.execute(statement)
        rows = result.all()
    except SQLAlchemyError:
        # Deliberate fail-soft, and the reason it is safe: nothing in this
        # module is part of the safety floor. The invariants are code-owned
        # constants in the renderer and still render in full.
        logger.error(
            "Project material compile failed for project=%s; continuing without a manifest",
            project_id,
            exc_info=True,
        )
        return ProjectMaterial(project_id=project_id)

    contributions: list[tuple[int, str, int, bool]] = []
    entries: list[MaterialEntry] = []
    for document_id, filename, role, text_document_id, char_count in rows:
        stored_chars = int(char_count) if char_count is not None else 0
        # A stored text is necessary for mounting and no longer sufficient for
        # it. ``document_texts`` used to be written under exactly this rule, so
        # "a row exists" and "small enough and non-evidence" were the same fact
        # and the join answered both. Ingestion now stores the text of EVERY
        # document -- the completeness questions are asked about the source
        # corpus, which the old rule excluded -- so without re-applying
        # ``should_materialize`` here a 300-page reference would be written into
        # the workspace as a file the agent is told to read.
        materialized = text_document_id is not None and should_materialize(
            role, stored_chars
        )
        contributions.append((document_id, role, stored_chars, materialized))
        entries.append(
            MaterialEntry(
                document_id=document_id,
                filename=filename,
                role=role,
                kind=material_kind(role),
                char_count=stored_chars,
                materialized=materialized,
            )
        )

    # Constraints first, then methods, then references, then document id. The
    # order is not cosmetic: it is what decides which entries survive the cap
    # below and the renderer's token budget above it. A reference surviving at
    # the expense of a constraint would be the manifest dropping the one kind of
    # document that must never be missed.
    entries.sort(key=lambda entry: (MATERIAL_KIND_PRIORITY[entry.kind], entry.document_id))

    kept = entries[:MANIFEST_MAX_ENTRIES]
    overflow = len(entries) - len(kept)

    return ProjectMaterial(
        project_id=project_id,
        entries=tuple(kept),
        overflow_count=overflow,
        cache_key=_cache_key(contributions, project_id=project_id),
    )


async def load_material_texts(db: AsyncSession, document_ids: Sequence[int]) -> dict[int, str]:
    """Load the stored text of specific documents, for writing them to disk.

    Kept separate from :func:`compile_project_material`, and the text kept off
    :class:`MaterialEntry`, because the two have opposite cost profiles. The
    manifest is re-rendered on every turn and needs metadata only; the text is
    needed once, when the workspace files are written. Carrying tens of
    thousands of characters through a model that is rebuilt every turn would be
    pure waste for the turns that write nothing.

    ``document_ids`` must come from a :class:`ProjectMaterial` compiled for this
    caller: that compile is the access boundary, and this function does not
    repeat it -- it has no project or group argument with which to. Callers that
    obtain ids any other way are responsible for having authorized them.

    The id list is capped at ``MANIFEST_MAX_ENTRIES``: nothing outside the
    manifest is mountable, so a longer list is already a caller bug, and
    bounding it keeps a bad call from turning into an unbounded read. The cap
    takes the lowest ids so the truncation is at least deterministic.

    Missing ids are simply absent from the result, and so is everything on the
    failure path. One unwritten file is a state the manifest already has to
    tolerate -- a non-materialized entry tells the agent to search instead.
    """

    if not document_ids:
        # No query at all for the common "nothing to mount" case.
        return {}

    wanted = sorted(set(document_ids))
    if len(wanted) > MANIFEST_MAX_ENTRIES:
        logger.warning(
            "load_material_texts asked for %d documents; capping at %d",
            len(wanted),
            MANIFEST_MAX_ENTRIES,
        )
        wanted = wanted[:MANIFEST_MAX_ENTRIES]

    try:
        result = await db.execute(
            select(DocumentTextRow.document_id, DocumentTextRow.text).where(
                DocumentTextRow.document_id.in_(wanted)
            )
        )
        return {document_id: text for document_id, text in result.all()}
    except SQLAlchemyError:
        # Same fail-soft trade as the compile, for the same reason: an unwritten
        # reference file costs the agent one search; a raised exception here
        # would cost the user the answer.
        logger.error(
            "Loading material texts failed for %d document(s); continuing without them",
            len(wanted),
            exc_info=True,
        )
        return {}
