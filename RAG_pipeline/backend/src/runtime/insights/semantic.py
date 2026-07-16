"""Insight-owned hook that projects persisted derived rows into optional semantic recall."""

from __future__ import annotations

import logging
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.runtime_insights import (
    RuntimeErrorSignatureRow,
    RuntimeExecutionSummaryRow,
    RuntimeIncidentRow,
)
from src.runtime.semantic.projection import (
    RuntimeSemanticDocument,
    error_signature_document,
    execution_summary_document,
    incident_document,
)

logger = logging.getLogger(__name__)


class RuntimeSemanticIndexer(Protocol):
    async def upsert(self, documents: list[RuntimeSemanticDocument]) -> bool: ...


async def index_runtime_insights_for_execution(
    session: AsyncSession,
    *,
    project_id: str,
    execution_id: str,
    signature_hash: str | None,
    indexer: RuntimeSemanticIndexer,
) -> bool:
    """Index only persisted curated insight fields for one execution.

    This is intentionally invoked after the deterministic insight rows have been
    flushed. A false result is non-fatal: the SQL insight write remains authoritative.
    """
    documents: list[RuntimeSemanticDocument] = []
    summary = await _one(
        session,
        select(RuntimeExecutionSummaryRow).where(
            RuntimeExecutionSummaryRow.project_id == project_id,
            RuntimeExecutionSummaryRow.execution_id == execution_id,
        ),
    )
    if summary is not None:
        documents.append(execution_summary_document(summary))
    incident = await _one(
        session,
        select(RuntimeIncidentRow).where(
            RuntimeIncidentRow.project_id == project_id,
            RuntimeIncidentRow.execution_id == execution_id,
        ),
    )
    if incident is not None:
        documents.append(incident_document(incident))
    if signature_hash:
        signature = await _one(
            session,
            select(RuntimeErrorSignatureRow).where(
                RuntimeErrorSignatureRow.project_id == project_id,
                RuntimeErrorSignatureRow.signature_hash == signature_hash,
            ),
        )
        if signature is not None:
            documents.append(error_signature_document(signature))
    try:
        return await indexer.upsert(documents)
    except Exception:
        logger.warning("Runtime semantic hook failed; retaining SQL insights", exc_info=True)
        return False


async def _one(session: AsyncSession, statement):
    return (await session.execute(statement)).scalars().first()
