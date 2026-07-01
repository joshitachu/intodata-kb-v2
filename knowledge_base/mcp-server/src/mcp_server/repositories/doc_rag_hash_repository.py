"""Repository for per-doc RAG content-hash rows (Slice 2 — vector-store push skip).

Encapsulates all SQL for the project_doc_rag_hashes table.  Callers own the
transaction boundary; this repository does not commit or rollback.

Pattern: Repository (CLAUDE.md rule 10) — single class, async session injected
by the caller, no framework base class required.
"""

from __future__ import annotations

import datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from mcp_server.db.models import ProjectDocRagHash


class DocRagHashRepository:
    """Persist and query SHA-256 content hashes for pushed RAG documents."""

    def __init__(self, session: AsyncSession) -> None:
        self._db = session

    async def get_hash(self, project_id: str, doc_filename: str) -> str | None:
        """Return the stored SHA-256 hex digest for the doc, or None if absent."""
        result = await self._db.execute(
            select(ProjectDocRagHash).where(
                ProjectDocRagHash.project_id == project_id,
                ProjectDocRagHash.doc_filename == doc_filename,
            )
        )
        row = result.scalars().one_or_none()
        return row.content_hash if row is not None else None

    async def upsert_hash(
        self, project_id: str, doc_filename: str, content_hash: str
    ) -> None:
        """Insert or update the hash row for a successfully pushed document.

        Uses ORM merge() for dialect-agnostic upsert (SELECT + INSERT or UPDATE
        by composite PK).  pushed_at is set explicitly so it is refreshed on
        every successful push regardless of INSERT vs UPDATE path.
        """
        row = ProjectDocRagHash(
            project_id=project_id,
            doc_filename=doc_filename,
            content_hash=content_hash,
            pushed_at=datetime.datetime.now(tz=datetime.timezone.utc),
        )
        await self._db.merge(row)

    async def delete_hash(self, project_id: str, doc_filename: str) -> None:
        """Remove the hash row for a retired document.

        Called when a doc is deleted from output so a later re-add does not
        produce a false skip.
        """
        await self._db.execute(
            delete(ProjectDocRagHash).where(
                ProjectDocRagHash.project_id == project_id,
                ProjectDocRagHash.doc_filename == doc_filename,
            )
        )

    async def delete_all_for_project(self, project_id: str) -> None:
        """Remove all hash rows for a project.

        Used when the internal RAG layer is wiped wholesale so the next
        push_docs call re-establishes baselines from scratch.
        """
        await self._db.execute(
            delete(ProjectDocRagHash).where(
                ProjectDocRagHash.project_id == project_id,
            )
        )
