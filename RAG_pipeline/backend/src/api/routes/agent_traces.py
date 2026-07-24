"""Protected operator API for safe Agent Run traces."""

from __future__ import annotations

import datetime
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query

from src.api.dependencies import AuditViewer, DBSession
from src.harness.agent_trace import AgentTraceDetail, AgentTraceList, AgentTraceService

router = APIRouter(prefix="/admin/agent-traces", tags=["admin", "agent-traces"])


@router.get("", response_model=AgentTraceList)
async def list_agent_traces(
    db: DBSession,
    _admin: AuditViewer,
    status: str | None = None,
    cohort: str | None = None,
    tool: str | None = None,
    conversation_id: int | None = None,
    owner_user_id: int | None = None,
    started_after: datetime.datetime | None = None,
    started_before: datetime.datetime | None = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> AgentTraceList:
    return await AgentTraceService(db).list_traces(
        status=status, cohort=cohort, tool=tool,
        conversation_id=conversation_id, owner_user_id=owner_user_id,
        started_after=started_after, started_before=started_before,
        offset=offset, limit=limit,
    )


@router.get("/{run_id}", response_model=AgentTraceDetail)
async def get_agent_trace(
    run_id: str,
    db: DBSession,
    _admin: AuditViewer,
) -> AgentTraceDetail:
    trace = await AgentTraceService(db).get_trace(run_id)
    if trace is None:
        raise HTTPException(status_code=404, detail="Agent trace not found")
    return trace
