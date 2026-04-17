"""Admin endpoints for Hindsight memory management.

Provides mental model creation, pattern reflection, and read endpoints
for the admin Memory Dashboard (Phase 5).
"""
from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field
from sqlalchemy import select

from src.api.dependencies import AuditViewer, DBSession
from src.config import settings
from src.db.models import User
from src.db.models.user import ProjectAssignment
from src.memory.hindsight_client import (
    QUALITY_BANK,
    CORRECTIONS_BANK,
    _get_client,
    _project_bank,
    create_project_mental_model,
    get_bank_stats,
    list_bank_memories,
    reflect_project_patterns,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/memory", tags=["memory"])


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class MentalModelRequest(BaseModel):
    project_id: str = Field(..., min_length=1)
    name: str = Field(..., min_length=1, max_length=200)
    source_query: str = Field(..., min_length=1)
    tags: list[str] | None = None


class ReflectPatternsRequest(BaseModel):
    project_id: str = Field(..., min_length=1)
    query: str | None = None


# ---------------------------------------------------------------------------
# Existing POST endpoints
# ---------------------------------------------------------------------------

@router.post("/mental-models")
async def create_mental_model(body: MentalModelRequest, _user: AuditViewer) -> dict:
    """Create a mental model (pre-computed answer) for a project bank.

    Requires view_audit_logs permission.
    """
    result = await create_project_mental_model(
        project_id=body.project_id,
        name=body.name,
        source_query=body.source_query,
        tags=body.tags,
    )
    return result


@router.post("/reflect-patterns")
async def reflect_patterns(body: ReflectPatternsRequest, _user: AuditViewer) -> dict:
    """Trigger pattern reflection for a project bank.

    Uses Hindsight's agentic reflect to synthesize patterns across project memory.
    Requires view_audit_logs permission.
    """
    result = await reflect_project_patterns(
        project_id=body.project_id,
        query=body.query,
    )
    return {"project_id": body.project_id, "reflection": result}


# ---------------------------------------------------------------------------
# Phase 5: GET endpoints for Memory Dashboard
# ---------------------------------------------------------------------------

async def _build_candidate_banks(db: DBSession) -> list[dict]:
    """Build list of candidate bank IDs with type and label metadata."""
    candidates: list[dict] = []

    # System banks
    candidates.append({"bank_id": QUALITY_BANK, "type": "system", "label": "RAG Quality"})
    candidates.append({"bank_id": CORRECTIONS_BANK, "type": "system", "label": "RAG Corrections"})

    # User banks
    result = await db.execute(select(User.id, User.username).where(User.is_active == True))  # noqa: E712
    for row in result.all():
        candidates.append({
            "bank_id": f"user-{row.id}",
            "type": "user",
            "label": row.username,
        })

    # Project banks
    if settings.hindsight_project_banks_enabled:
        result = await db.execute(
            select(ProjectAssignment.project_id).distinct()
        )
        for row in result.all():
            pid = row[0]
            candidates.append({
                "bank_id": _project_bank(pid),
                "type": "project",
                "label": pid,
            })

    return candidates


@router.get("/banks")
async def list_banks(db: DBSession, _user: AuditViewer) -> dict:
    """List all memory banks with stats and abstraction funnel totals.

    Powers the Insight Pipeline tab of the Memory Dashboard.
    """
    candidates = await _build_candidate_banks(db)

    # Fetch stats for all banks concurrently
    stats_list = await asyncio.gather(
        *[get_bank_stats(c["bank_id"]) for c in candidates]
    )

    banks = []
    for candidate, stats in zip(candidates, stats_list):
        banks.append({
            **candidate,
            "exists": stats["exists"],
            "total_nodes": stats["total_nodes"],
            "nodes_by_fact_type": stats["nodes_by_fact_type"],
            "total_observations": stats["total_observations"],
        })

    # Abstraction funnel totals (OpenBrain p.9)
    funnel = {
        "raw_memories": sum(b["total_nodes"] for b in banks),
        "observations": sum(b["total_observations"] for b in banks),
        "mental_models": sum(
            b["nodes_by_fact_type"].get("mental_model", 0) for b in banks
        ),
    }

    return {
        "banks": banks,
        "hindsight_enabled": settings.hindsight_enabled,
        "funnel": funnel,
    }


@router.get("/banks/{bank_id}/memories")
async def get_bank_memories(
    bank_id: str,
    _user: AuditViewer,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    type: str | None = Query(default=None),
) -> dict:
    """List memories in a specific bank with pagination."""
    return await list_bank_memories(
        bank_id=bank_id,
        limit=limit,
        offset=offset,
        memory_type=type,
    )


@router.get("/banks/{bank_id}/reflect")
async def reflect_bank(
    bank_id: str,
    _user: AuditViewer,
    query: str | None = Query(default=None),
) -> dict:
    """Run pattern reflection on a memory bank."""
    if bank_id.startswith("project-"):
        project_id = bank_id.removeprefix("project-")
        reflection = await reflect_project_patterns(project_id, query)
    else:
        # Direct reflect for non-project banks
        try:
            client = _get_client()
            if client is None:
                return {"bank_id": bank_id, "reflection": "Hindsight client not available"}
            reflect_query = query or f"What patterns and insights exist in bank {bank_id}?"
            response = await client.areflect(bank_id=bank_id, query=reflect_query)
            reflection = response.text if hasattr(response, "text") else str(response)
        except Exception as e:
            logger.warning("Reflect failed for bank %s: %s", bank_id, e)
            reflection = f"Reflect failed: {e}"

    return {"bank_id": bank_id, "reflection": reflection}


@router.get("/recent-captures")
async def recent_captures(
    db: DBSession,
    _user: AuditViewer,
    limit: int = Query(default=20, ge=1, le=100),
) -> dict:
    """Fetch recent memories across all banks (OpenBrain transparency layer).

    Merges memories from multiple banks, sorted by timestamp descending.
    """
    candidates = await _build_candidate_banks(db)

    # Fetch stats to find existing banks
    stats_list = await asyncio.gather(
        *[get_bank_stats(c["bank_id"]) for c in candidates]
    )

    existing = [
        (c, s) for c, s in zip(candidates, stats_list)
        if s["exists"] and s["total_nodes"] > 0
    ][:10]  # cap at 10 banks

    if not existing:
        return {"captures": [], "total_banks": 0}

    # Fetch recent memories from each bank concurrently
    mem_results = await asyncio.gather(
        *[list_bank_memories(c["bank_id"], limit=5) for c, _ in existing]
    )

    captures = []
    for (candidate, _stats), mem_result in zip(existing, mem_results):
        for mem in mem_result.get("memories", []):
            captures.append({
                **mem,
                "bank_id": candidate["bank_id"],
                "bank_type": candidate["type"],
                "bank_label": candidate["label"],
            })

    # Sort by timestamp descending, then truncate
    captures.sort(key=lambda m: m.get("timestamp", ""), reverse=True)
    captures = captures[:limit]

    return {"captures": captures, "total_banks": len(existing)}
