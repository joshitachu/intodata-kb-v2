"""Repository helpers for the RAG active-knowledge-revision authority."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import ActiveRagKnowledgeRevision, RagKnowledgeRevision


class KnowledgeRevisionConflictError(RuntimeError):
    pass


class KnowledgeRevisionNotFoundError(RuntimeError):
    pass


async def active_knowledge_revisions(
    db: AsyncSession, project_ids: Sequence[str] | None
) -> dict[str, str]:
    """Return the active revision map allowed by the already-authorized scope."""
    stmt = select(ActiveRagKnowledgeRevision)
    if project_ids is not None:
        if not project_ids:
            return {}
        stmt = stmt.where(ActiveRagKnowledgeRevision.project_id.in_(project_ids))
    rows = (await db.execute(stmt)).scalars().all()
    return {row.project_id: row.knowledge_revision_id for row in rows}


async def prepare_knowledge_revision(
    db: AsyncSession,
    *,
    project_id: str,
    knowledge_revision_id: str,
    base_knowledge_revision_id: str | None,
    membership_hashes: Mapping[str, str],
    deleted_logical_ids: Sequence[str],
    aliases: Mapping[str, str],
) -> RagKnowledgeRevision:
    """Persist an inactive revision and reject stale/conflicting replays."""
    active = await db.get(ActiveRagKnowledgeRevision, project_id)
    active_id = active.knowledge_revision_id if active else None
    if base_knowledge_revision_id != active_id:
        raise KnowledgeRevisionConflictError("base_knowledge_revision_id is stale")
    existing = (
        await db.execute(
            select(RagKnowledgeRevision).where(
                RagKnowledgeRevision.project_id == project_id,
                RagKnowledgeRevision.knowledge_revision_id == knowledge_revision_id,
            )
        )
    ).scalar_one_or_none()
    values = (dict(membership_hashes), list(deleted_logical_ids), dict(aliases))
    if existing is not None:
        if (
            existing.base_knowledge_revision_id != base_knowledge_revision_id
            or (existing.membership_hashes, existing.deleted_logical_ids, existing.aliases) != values
        ):
            raise KnowledgeRevisionConflictError("knowledge revision replay conflicts with stored receipt")
        return existing
    row = RagKnowledgeRevision(
        project_id=project_id,
        knowledge_revision_id=knowledge_revision_id,
        base_knowledge_revision_id=base_knowledge_revision_id,
        state="prepared",
        membership_hashes=values[0],
        deleted_logical_ids=values[1],
        aliases=values[2],
    )
    db.add(row)
    await db.flush()
    return row


async def activate_knowledge_revision(
    db: AsyncSession, *, project_id: str, knowledge_revision_id: str
) -> tuple[RagKnowledgeRevision, str | None]:
    """CAS-switch the sole visible revision after a matching preparation."""
    active = await db.get(ActiveRagKnowledgeRevision, project_id, with_for_update=True)
    active_id = active.knowledge_revision_id if active else None
    revision = (
        await db.execute(
            select(RagKnowledgeRevision)
            .where(
                RagKnowledgeRevision.project_id == project_id,
                RagKnowledgeRevision.knowledge_revision_id == knowledge_revision_id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if revision is None:
        raise KnowledgeRevisionNotFoundError("knowledge revision is not prepared")
    if revision.base_knowledge_revision_id != active_id and active_id != knowledge_revision_id:
        raise KnowledgeRevisionConflictError("active knowledge revision changed after prepare")
    if active is None:
        db.add(ActiveRagKnowledgeRevision(
            project_id=project_id, knowledge_revision_id=knowledge_revision_id
        ))
    else:
        active.knowledge_revision_id = knowledge_revision_id
    revision.state = "active"
    await db.flush()
    return revision, active_id


async def rollback_knowledge_revision(
    db: AsyncSession,
    *,
    project_id: str,
    knowledge_revision_id: str,
    expected_active_revision_id: str,
) -> tuple[RagKnowledgeRevision, str]:
    """CAS-restore a retained complete revision without creating new vectors."""

    active = await db.get(ActiveRagKnowledgeRevision, project_id, with_for_update=True)
    if active is None or active.knowledge_revision_id != expected_active_revision_id:
        found = active.knowledge_revision_id if active else None
        raise KnowledgeRevisionConflictError(
            f"active knowledge revision changed: expected {expected_active_revision_id!r}, found {found!r}"
        )
    revision = (
        await db.execute(
            select(RagKnowledgeRevision)
            .where(
                RagKnowledgeRevision.project_id == project_id,
                RagKnowledgeRevision.knowledge_revision_id == knowledge_revision_id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if revision is None:
        raise KnowledgeRevisionNotFoundError("rollback knowledge revision is not retained")
    previous = active.knowledge_revision_id
    active.knowledge_revision_id = knowledge_revision_id
    revision.state = "active"
    await db.flush()
    return revision, previous
