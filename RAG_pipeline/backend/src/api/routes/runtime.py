"""Runtime endpoints.

`GET /runtime/status` reports whether a live runtime provider is configured.

The read-only logs API (`/runtime/executions`, `/runtime/failures`, and nested log
routes) exposes the held runtime-data store to the logs UI. Every endpoint derives
its project scope from the authenticated user via `get_user_project_ids()`; no query
parameter or path value may widen access. `project_id` query params are filters
*within* the caller's scope, never an authorization source. Cross-project point
lookups return 404 so they cannot reveal whether an execution exists outside scope.

All endpoints are available to any authenticated user; authorization is project-scope
based (the same contract used across the RAG backend), so there is no extra
role/permission gate.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from src.api.dependencies import CurrentUser, DBSession
from src.rbac.permissions import get_user_project_ids
from src.runtime.insights.models import (
    RuntimeAlertReadyMetadata,
    RuntimeExecutionSummaryInsight,
    RuntimeIncidentEvidence,
    RuntimeIncidentInsight,
    RuntimeSimilarIncident,
    RuntimeTimelineEvent,
)
from src.runtime.insights.repository import RuntimeInsightRepository
from src.runtime.models import (
    DetectFailuresInput,
    GetExecutionDetailsInput,
    GetExecutionLogsInput,
    ListExecutionsInput,
    RuntimeExecution,
    RuntimeExecutionState,
    RuntimeFailure,
    RuntimeLogEvent,
    RuntimeLogSeverity,
    RuntimeStatusResponse,
)
from src.runtime.status import get_runtime_status
from src.runtime.store import ProjectScope, RuntimeRecordNotFoundError, RuntimeStoreRepository

router = APIRouter(prefix="/runtime", tags=["runtime"])


# --- Response envelopes ------------------------------------------------------
# `total` is the count of rows returned in this (bounded) response, not a
# full-table pagination total — these endpoints cap results via `limit`.


class ExecutionListResponse(BaseModel):
    executions: list[RuntimeExecution]
    total: int


class ExecutionDetailResponse(BaseModel):
    execution: RuntimeExecution


class LogEventListResponse(BaseModel):
    execution_id: str
    events: list[RuntimeLogEvent]
    total: int


class FailureListResponse(BaseModel):
    failures: list[RuntimeFailure]
    total: int


# --- Scope helper ------------------------------------------------------------


def _intersect_scope(scope: ProjectScope, project_id: str | None) -> ProjectScope:
    """Narrow the authenticated scope by an optional `project_id` filter.

    The filter can only ever narrow access, never widen it:
      - unrestricted (None) + filter        -> [project_id]
      - restricted [ids] + in-scope filter  -> [project_id]
      - restricted [ids] + out-of-scope     -> []  (deny)
      - empty [] (no access)                -> []  (stays denied)
      - no filter                            -> scope unchanged
    """
    if project_id is None:
        return scope
    if scope is None:
        return [project_id]
    if project_id in scope:
        return [project_id]
    return []


@router.get("/status", response_model=RuntimeStatusResponse, summary="Get live runtime connection status")
async def get_status(_user: CurrentUser) -> RuntimeStatusResponse:
    return await get_runtime_status()


@router.get("/executions", response_model=ExecutionListResponse, summary="List runtime executions")
async def list_executions(
    user: CurrentUser,
    db: DBSession,
    project_id: str | None = None,
    job_name: str | None = None,
    state: RuntimeExecutionState | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = Query(default=50, ge=1, le=200),
) -> ExecutionListResponse:
    scope = await get_user_project_ids(user, db)
    repo = RuntimeStoreRepository(db)
    executions = await repo.list_executions(
        _intersect_scope(scope, project_id),
        ListExecutionsInput(job_name=job_name, state=state, since=since, until=until, limit=limit),
    )
    return ExecutionListResponse(executions=executions, total=len(executions))


@router.get(
    "/executions/{execution_id}",
    response_model=ExecutionDetailResponse,
    summary="Get a single runtime execution",
)
async def get_execution(
    execution_id: str,
    user: CurrentUser,
    db: DBSession,
) -> ExecutionDetailResponse:
    scope = await get_user_project_ids(user, db)
    repo = RuntimeStoreRepository(db)
    try:
        execution = await repo.get_execution_details(
            scope, GetExecutionDetailsInput(execution_id=execution_id)
        )
    except RuntimeRecordNotFoundError:
        raise HTTPException(status_code=404, detail="Execution not found")
    return ExecutionDetailResponse(execution=execution)


@router.get(
    "/executions/{execution_id}/logs",
    response_model=LogEventListResponse,
    summary="Get log events for a runtime execution",
)
async def get_execution_logs(
    execution_id: str,
    user: CurrentUser,
    db: DBSession,
    min_severity: RuntimeLogSeverity | None = None,
    limit: int = Query(default=100, ge=1, le=1000),
) -> LogEventListResponse:
    scope = await get_user_project_ids(user, db)
    repo = RuntimeStoreRepository(db)
    # Gate on a scoped existence check first: an unknown or out-of-scope id is a 404,
    # while an in-scope execution with no log lines at this severity is an empty list.
    try:
        await repo.get_execution_details(scope, GetExecutionDetailsInput(execution_id=execution_id))
    except RuntimeRecordNotFoundError:
        raise HTTPException(status_code=404, detail="Execution not found")
    try:
        events = await repo.get_execution_logs(
            scope,
            GetExecutionLogsInput(execution_id=execution_id, min_severity=min_severity, limit=limit),
        )
    except RuntimeRecordNotFoundError:
        events = []
    return LogEventListResponse(execution_id=execution_id, events=events, total=len(events))


@router.get("/failures", response_model=FailureListResponse, summary="List runtime failures")
async def list_failures(
    user: CurrentUser,
    db: DBSession,
    project_id: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = Query(default=50, ge=1, le=200),
) -> FailureListResponse:
    scope = await get_user_project_ids(user, db)
    repo = RuntimeStoreRepository(db)
    failures = await repo.detect_failures(
        _intersect_scope(scope, project_id),
        DetectFailuresInput(since=since, until=until, limit=limit),
    )
    return FailureListResponse(failures=failures, total=len(failures))


# ---------------------------------------------------------------------------
# Smart-log insight endpoints (Slice 2)
# ---------------------------------------------------------------------------


class ExecutionSummaryResponse(BaseModel):
    summary: RuntimeExecutionSummaryInsight


class TimelineResponse(BaseModel):
    execution_id: str
    events: list[RuntimeTimelineEvent]
    total: int


class IncidentListResponse(BaseModel):
    incidents: list[RuntimeIncidentInsight]
    total: int


class IncidentDetailResponse(BaseModel):
    incident: RuntimeIncidentInsight


class IncidentEvidenceResponse(BaseModel):
    incident_id: str
    evidence: list[RuntimeIncidentEvidence]
    total: int


@router.get(
    "/executions/{execution_id}/summary",
    response_model=ExecutionSummaryResponse,
    summary="Get the smart-log execution summary for a runtime execution",
)
async def get_execution_summary(
    execution_id: str,
    user: CurrentUser,
    db: DBSession,
) -> ExecutionSummaryResponse:
    scope = await get_user_project_ids(user, db)
    repo = RuntimeInsightRepository(db)
    try:
        summary = await repo.get_execution_summary(scope, execution_id)
    except RuntimeRecordNotFoundError:
        raise HTTPException(status_code=404, detail="Execution not found")
    return ExecutionSummaryResponse(summary=summary)


@router.get(
    "/executions/{execution_id}/timeline",
    response_model=TimelineResponse,
    summary="Get the enriched event timeline for a runtime execution",
)
async def get_execution_timeline(
    execution_id: str,
    user: CurrentUser,
    db: DBSession,
    min_severity: RuntimeLogSeverity | None = None,
    limit: int = Query(default=200, ge=1, le=1000),
) -> TimelineResponse:
    scope = await get_user_project_ids(user, db)
    repo = RuntimeInsightRepository(db)
    try:
        events = await repo.get_execution_timeline(
            scope,
            execution_id,
            min_severity=min_severity.value if min_severity else None,
            limit=limit,
        )
    except RuntimeRecordNotFoundError:
        raise HTTPException(status_code=404, detail="Execution not found")
    return TimelineResponse(execution_id=execution_id, events=events, total=len(events))


@router.get(
    "/incidents",
    response_model=IncidentListResponse,
    summary="List runtime incidents",
)
async def list_incidents(
    user: CurrentUser,
    db: DBSession,
    project_id: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = Query(default=50, ge=1, le=200),
) -> IncidentListResponse:
    scope = await get_user_project_ids(user, db)
    repo = RuntimeInsightRepository(db)
    incidents = await repo.list_incidents(
        _intersect_scope(scope, project_id),
        since=since,
        until=until,
        limit=limit,
    )
    return IncidentListResponse(incidents=incidents, total=len(incidents))


@router.get(
    "/incidents/{incident_id}",
    response_model=IncidentDetailResponse,
    summary="Get a single runtime incident",
)
async def get_incident(
    incident_id: str,
    user: CurrentUser,
    db: DBSession,
) -> IncidentDetailResponse:
    scope = await get_user_project_ids(user, db)
    repo = RuntimeInsightRepository(db)
    try:
        incident = await repo.get_incident(scope, incident_id)
    except RuntimeRecordNotFoundError:
        raise HTTPException(status_code=404, detail="Incident not found")
    return IncidentDetailResponse(incident=incident)


@router.get(
    "/incidents/{incident_id}/evidence",
    response_model=IncidentEvidenceResponse,
    summary="Get bounded evidence for a runtime incident",
)
async def get_incident_evidence(
    incident_id: str,
    user: CurrentUser,
    db: DBSession,
    limit: int = Query(default=20, ge=1, le=50),
) -> IncidentEvidenceResponse:
    scope = await get_user_project_ids(user, db)
    repo = RuntimeInsightRepository(db)
    try:
        evidence = await repo.get_incident_evidence(scope, incident_id, limit=limit)
    except RuntimeRecordNotFoundError:
        raise HTTPException(status_code=404, detail="Incident not found")
    return IncidentEvidenceResponse(incident_id=incident_id, evidence=evidence, total=len(evidence))


class IncidentSimilarResponse(BaseModel):
    incident_id: str
    execution_id: str
    recurrence: RuntimeAlertReadyMetadata
    similar_incidents: list[RuntimeSimilarIncident]


@router.get(
    "/incidents/{incident_id}/similar",
    response_model=IncidentSimilarResponse,
    summary="Get similar failures for a runtime incident",
)
async def get_similar_failures(
    incident_id: str,
    user: CurrentUser,
    db: DBSession,
    limit: int = Query(default=10, ge=1, le=50),
) -> IncidentSimilarResponse:
    scope = await get_user_project_ids(user, db)
    repo = RuntimeInsightRepository(db)
    try:
        result = await repo.get_similar_failures_for_incident(scope, incident_id, limit=limit)
    except RuntimeRecordNotFoundError:
        raise HTTPException(status_code=404, detail="Incident not found")
    return IncidentSimilarResponse(
        incident_id=result.incident_id,
        execution_id=result.execution_id,
        recurrence=result.recurrence,
        similar_incidents=result.similar_incidents,
    )
