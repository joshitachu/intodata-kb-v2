"""Query-time entity matching against the per-project alias table.

The `entity_aliases` table (Slice 3, fed from `_facts/entity_index.csv` and
`usage_index.csv`) knows every named thing in a project: jobs, components,
tables, config keys, files. Embeddings are structurally weak at exact-name
lookup, so the retrieval ladder uses this matcher as the deterministic path:
detect which known entities a query names, then fetch their chunks directly.

Matching is deliberately simple and deterministic: casefolded substring
matching on word boundaries, longest alias first. No fuzzy matching — a false
entity match would steer retrieval to the wrong unit.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import EntityAlias

logger = logging.getLogger(__name__)

# Aliases shorter than this match too promiscuously (e.g. "id", "out").
MIN_ALIAS_LENGTH = 4


@dataclass(frozen=True)
class EntityMatch:
    """One alias-table entity detected in a user query."""

    alias: str
    canonical_name: str
    entity_type: str
    project_id: str
    unit_name: str | None
    source_path: str | None
    source_artifact: str | None
    confidence: float


@dataclass(frozen=True)
class EntityMatchResult:
    """Matches plus whether the project has alias rows at all.

    ``alias_rows_present=False`` means the project was never alias-synced
    (legacy) — callers should fall back to the heuristic identifier detector.
    ``alias_rows_present=True`` with empty matches means the query names no
    known entity — callers must NOT descend on heuristic guesses.
    """

    matches: list[EntityMatch]
    alias_rows_present: bool


def _normalize(text: str) -> str:
    return " ".join(text.split()).casefold()


def _contains_on_boundary(haystack: str, needle: str) -> bool:
    """Casefolded boundary-aware containment.

    Identifier characters (word chars, ``.``, ``|``) on either side of the
    candidate mean the query names a longer identifier, not this alias.
    """
    return re.search(
        rf"(?<![\w.|]){re.escape(needle)}(?![\w.|])",
        haystack,
    ) is not None


async def match_query_entities(
    db: AsyncSession,
    query: str,
    project_ids: list[str] | None,
    limit: int = 25,
) -> EntityMatchResult:
    """Detect alias-table entities named in the query, longest match first.

    Scoped to ``project_ids`` — entities from projects the caller did not
    select are never matched. Returns at most ``limit`` matches, deduped by
    (project_id, unit_name, canonical_name, alias_normalized), preferring
    higher confidence rows.
    """
    normalized_query = _normalize(query)
    if not normalized_query:
        return EntityMatchResult(matches=[], alias_rows_present=False)

    stmt = select(EntityAlias)
    if project_ids is not None:
        stmt = stmt.where(EntityAlias.project_id.in_(project_ids))
    rows = (await db.execute(stmt)).scalars().all()
    if not rows:
        return EntityMatchResult(matches=[], alias_rows_present=False)

    best: dict[tuple, EntityAlias] = {}
    for row in rows:
        needle = _normalize(row.alias_normalized or row.alias)
        if len(needle) < MIN_ALIAS_LENGTH:
            continue
        if not _contains_on_boundary(normalized_query, needle):
            continue
        key = (row.project_id, row.unit_name or "", row.canonical_name, needle)
        existing = best.get(key)
        if existing is None or row.confidence > existing.confidence:
            best[key] = row

    matches = sorted(
        best.values(),
        key=lambda r: (-len(r.alias_normalized or r.alias), -r.confidence, r.canonical_name),
    )[:limit]

    if matches:
        logger.info(
            "Entity matcher: %d alias matches for query %r: %s",
            len(matches), query[:80],
            [m.canonical_name for m in matches[:5]],
        )
    return EntityMatchResult(
        matches=[
            EntityMatch(
                alias=row.alias,
                canonical_name=row.canonical_name,
                entity_type=row.entity_type,
                project_id=row.project_id,
                unit_name=row.unit_name,
                source_path=row.source_path,
                source_artifact=row.source_artifact,
                confidence=row.confidence,
            )
            for row in matches
        ],
        alias_rows_present=True,
    )


def matched_unit_names(matches: list[EntityMatch]) -> list[str]:
    """Distinct unit names implicated by the matches, in match order.

    Both the unit a matched entity belongs to and entities that ARE units
    (canonical_name == unit_name) end up here; rung 2 fetches their chunks
    via the `unit` payload index.
    """
    seen: dict[str, None] = {}
    for m in matches:
        if m.unit_name:
            seen.setdefault(m.unit_name, None)
        if m.entity_type in ("unit", "job") and m.canonical_name:
            seen.setdefault(m.canonical_name, None)
    return list(seen)
