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

`GET /runtime/doc-evidence` is the one exception to the user-scope rule: it is a
machine endpoint for the documentation generator, authenticated with the MCP
ingest key. Having no user, it pins scope to the single requested project rather
than running unrestricted.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from src.api.dependencies import CurrentUser, DBSession, RuntimeSourceReader, RuntimeSourceWriter
from src.api.routes.ingest import verify_machine_topology_ingest_api_key
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
    RuntimeDocEvidence,
    RuntimeDocReadScope,
    RuntimeMirroredEnvironment,
    DetectFailuresInput,
    GetExecutionDetailsInput,
    GetExecutionLogsInput,
    ListRuntimeArtifactsInput,
    ListRuntimeComponentHotspotsInput,
    ListRuntimeConnectionsInput,
    ListRuntimeDependenciesInput,
    ListRuntimeEnginesInput,
    ListRuntimeErrorSignaturesInput,
    ListRuntimeInventoryInput,
    ListRuntimeJobRunsInput,
    ListRuntimeJobReliabilityInput,
    ListRuntimeJobThroughputInput,
    ListRuntimeJobsInput,
    ListRuntimePlanRunsInput,
    ListRuntimePlansInput,
    ListExecutionsInput,
    RuntimeArtifact,
    RuntimeComponentHotspot,
    RuntimeConnection,
    RuntimeDependencySummary,
    RuntimeEngine,
    RuntimeEngineHealth,
    RuntimeEngineStatus,
    RuntimeErrorSignature,
    RuntimeEstateOverview,
    RuntimeExecution,
    RuntimeExecutionState,
    RuntimeFailure,
    RuntimeHealthSummary,
    RuntimeJob,
    RuntimeJobDependency,
    RuntimeJobHealth,
    RuntimeJobReliability,
    RuntimeJobOperationalInsight,
    RuntimeJobRun,
    RuntimeJobThroughput,
    RuntimeLogEvent,
    RuntimeLogSeverity,
    RuntimeOutcomeTrendPoint,
    RuntimePlan,
    RuntimePlanHealth,
    RuntimePlanRun,
    RuntimeSourceRead,
    RuntimeSyncState,
    RuntimeStatusResponse,
    RuntimeTrendBucket,
    RuntimeTrendCoverage,
    RuntimeTrendInput,
)
from src.runtime.status import get_runtime_status
from src.runtime.store import ProjectScope, RuntimeRecordNotFoundError, RuntimeStoreRepository
from src.runtime.topology.correlation import RuntimeCorrelationService
from src.runtime.topology.models import (
    RuntimeExecutionCorrelation,
    RuntimeJobScanMap,
    RuntimeJobScanMapCreate,
)
from src.runtime.topology.repository import (
    RuntimeJobScanMapValidationError,
    RuntimeTopologyRepository,
)

router = APIRouter(prefix="/runtime", tags=["runtime"])

# One documentation run asks about the units of one doc target, not a whole
# scan. The cap bounds the IN-clause and the response without constraining any
# real caller.
_DOC_EVIDENCE_MAX_UNITS = 200


# --- Response envelopes ------------------------------------------------------
# `total` is the full count matching the server-scoped filters. Offset pagination
# keeps payloads bounded while retaining compatibility with existing limit users.


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


class SyncStatusResponse(BaseModel):
    states: list[RuntimeSyncState]
    total: int


class HealthSummaryResponse(BaseModel):
    health: list[RuntimeHealthSummary]
    total: int


class RuntimeSourceListResponse(BaseModel):
    sources: list[RuntimeSourceRead]
    total: int


class RuntimeEngineListResponse(BaseModel):
    engines: list[RuntimeEngine]
    total: int


class RuntimeEngineHealthResponse(BaseModel):
    health: list[RuntimeEngineHealth]
    total: int


class RuntimeOverviewResponse(BaseModel):
    overview: RuntimeEstateOverview


class RuntimeJobListResponse(BaseModel):
    jobs: list[RuntimeJob]
    total: int


class RuntimeArtifactListResponse(BaseModel):
    artifacts: list[RuntimeArtifact]
    total: int


class RuntimeConnectionListResponse(BaseModel):
    connections: list[RuntimeConnection]
    total: int


class RuntimeDependencyListResponse(BaseModel):
    dependencies: list[RuntimeJobDependency]
    total: int


class RuntimeJobScanMapListResponse(BaseModel):
    mappings: list[RuntimeJobScanMap]
    total: int


class RuntimeJobScanMapResponse(BaseModel):
    mapping: RuntimeJobScanMap


class RuntimeDependencySummaryResponse(BaseModel):
    summary: RuntimeDependencySummary


class RuntimeJobRunListResponse(BaseModel):
    runs: list[RuntimeJobRun]
    total: int


class RuntimeJobHealthResponse(BaseModel):
    health: list[RuntimeJobHealth]
    total: int


class RuntimePlanListResponse(BaseModel):
    plans: list[RuntimePlan]
    total: int


class RuntimePlanRunListResponse(BaseModel):
    runs: list[RuntimePlanRun]
    total: int


class RuntimePlanHealthResponse(BaseModel):
    health: list[RuntimePlanHealth]
    total: int


class RuntimeTrendResponse(BaseModel):
    bucket: RuntimeTrendBucket
    points: list[RuntimeOutcomeTrendPoint]
    total: int
    coverage: RuntimeTrendCoverage


class RuntimeJobReliabilityResponse(BaseModel):
    jobs: list[RuntimeJobReliability]
    total: int


class RuntimeJobOperationalInsightResponse(BaseModel):
    insights: list[RuntimeJobOperationalInsight]
    total: int


class RuntimeComponentHotspotResponse(BaseModel):
    components: list[RuntimeComponentHotspot]
    total: int


class RuntimeJobThroughputResponse(BaseModel):
    jobs: list[RuntimeJobThroughput]
    total: int


class RuntimeErrorSignatureResponse(BaseModel):
    signatures: list[RuntimeErrorSignature]
    total: int


class RuntimeDocEvidenceResponse(BaseModel):
    project_id: str
    window_days: int
    since: datetime
    units: list[RuntimeDocEvidence]
    # Units the caller asked about that have no runtime data. Returned
    # explicitly so a document can state "no runtime data" rather than let a
    # writer infer from silence that a job never runs.
    unmatched_units: list[str]
    # The other direction: runtime jobs in this project that matched none of
    # the requested units. Names only, capped, no execution detail. Zero
    # matches plus a populated orphan list means the project binding is wrong,
    # not that the estate is idle -- which is the difference between a document
    # that says "no runtime data" and one that quietly publishes 1-of-10
    # coverage as if it were the whole picture.
    #
    # BATCHED CALLERS MUST MERGE BY INTERSECTION, NOT UNION: the field is
    # relative to the units in one request, so a unit sent in a later batch
    # makes its job look orphaned in an earlier one. See
    # `RuntimeStoreRepository.list_doc_evidence` for the derivation.
    orphan_runtime_jobs: list[str] = Field(default_factory=list)
    # The read scope this response was produced under. Always populated, even
    # when nothing was configured, because a consumer cannot otherwise tell a
    # project with a quiet estate from one that was narrowed to a single
    # environment -- and the published document has to say which it was.
    read_scope: RuntimeDocReadScope = Field(default_factory=RuntimeDocReadScope)


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


@router.get(
    "/doc-evidence",
    response_model=RuntimeDocEvidenceResponse,
    dependencies=[Depends(verify_machine_topology_ingest_api_key)],
    summary="Runtime execution profiles for scanned units, for documentation evidence",
)
async def get_runtime_doc_evidence(
    db: DBSession,
    project_id: str,
    units: Annotated[list[str], Query(min_length=1, max_length=_DOC_EVIDENCE_MAX_UNITS)],
    window_days: int = Query(default=90, ge=1, le=365),
    max_failures_per_unit: int = Query(default=5, ge=0, le=20),
) -> RuntimeDocEvidenceResponse:
    """Serve bounded runtime aggregates to the documentation generator.

    This is a MACHINE endpoint: the doc-generation service authenticates with
    the MCP ingest key, not a user session, so there is no user project scope to
    derive.  Scope is therefore pinned to the requested `project_id` -- the
    narrowest possible -- rather than left unrestricted.

    Scope widens ONLY through stored read grants
    --------------------------------------------
    `resolve_runtime_read_projects` adds the projects whose runtime rows this
    project has been granted, so one vendor tenant, mirrored once, can be read
    by every scanned project it backs instead of being registered (and polled)
    once per project.  The signature is deliberately unchanged: no caller may
    pass a project set in.  With no user behind this key there is no second
    gate, so a request-supplied read set would be an authorization bypass with
    nothing behind it.  `read_scope.shared_read` reports whether the widening
    actually applied, and `read_scope.read_project_ids` says to what.

    Aggregates only.  No log lines and no per-run timestamps cross this
    boundary, and the one free-text field is redacted, because the response is
    destined for an LLM prompt.

    Cadence: `gap_p25/p50/p75_seconds` + `gap_sample_size` describe the
    inter-run gaps of the unit's DOMINANT environment, named by
    `environments[0]`.  They exist so a consumer can classify an observed
    interval from the MEDIAN gap; `runs_per_day` is a mean over the window and
    must not be used for that (werkorder's `Maximo2RHM_Workorders`: mean gap
    2204 s, median 1801 s -- the mean invents a schedule).  All four are null
    when the dominant environment has fewer than two runs in the window, and a
    reader that quotes an interval must qualify it with the environment
    whenever `len(environments) > 1`.

    `units` is capped at `_DOC_EVIDENCE_MAX_UNITS`; a larger scan is expected to
    batch client-side.  `orphan_runtime_jobs` is therefore per-batch and merges
    by INTERSECTION -- see the repository docstring.  Under a shared read the
    orphan list also spans the granted tenant, which is not a mis-binding
    signal; `read_scope.shared_read` is how a consumer tells the two apart.
    """
    repo = RuntimeStoreRepository(db)
    read_projects = await repo.resolve_runtime_read_projects(project_id)
    # The caller's `window_days` is the DEFAULT, not the ceiling: a per-project
    # setting is the operator's deliberate choice about how much history
    # describes this estate, and the generator has no basis to overrule it.
    read_scope = await repo.resolve_doc_read_scope(
        read_projects, project_id, default_window_days=window_days
    )
    effective_window = read_scope.window_days or window_days
    since = datetime.now(UTC) - timedelta(days=effective_window)
    units_, unmatched, orphans = await repo.list_doc_evidence(
        read_projects,
        project_id=project_id,
        unit_names=units,
        since=since,
        max_failures_per_unit=max_failures_per_unit,
        environment_ids=read_scope.environment_ids,
        project_ids=read_projects,
    )
    return RuntimeDocEvidenceResponse(
        project_id=project_id,
        window_days=effective_window,
        since=since,
        units=units_,
        unmatched_units=unmatched,
        orphan_runtime_jobs=orphans,
        read_scope=read_scope,
    )


class RuntimeDocReadScopeResponse(BaseModel):
    """What a project's documentation runs read, and what they could read."""

    project_id: str
    scope: RuntimeDocReadScope
    # Every environment in the mirror, busiest first. The picker offers these;
    # an environment absent here cannot be chosen, which is what keeps a
    # selection from silently resolving to no evidence.
    available_environments: list[RuntimeMirroredEnvironment] = Field(default_factory=list)


@router.get(
    "/doc-read-scope",
    response_model=RuntimeDocReadScopeResponse,
    summary="Documentation read scope for a project, with the environments available to it",
)
async def get_runtime_doc_read_scope(
    project_id: str,
    user: RuntimeSourceReader,
    db: DBSession,
    default_window_days: int = Query(default=90, ge=1, le=365),
) -> RuntimeDocReadScopeResponse:
    """Read-side companion to `PATCH /runtime/sources/{id}/config`.

    A USER endpoint, unlike `/doc-evidence`: the operator choosing a scope is a
    real human with project permissions, so this runs under their scope and
    404s on a project they cannot see.

    Access to `project_id` is checked FIRST, against the user's own scope; only
    then is the read set resolved from stored grants.  A grant may name a
    granting project this user cannot otherwise see -- that is the point of a
    grant, and it was created by someone holding both sides -- so the picker
    offers the shared tenant's environments.  Without that the reader's
    operator could not configure a scope for the data their documentation runs
    will actually read.
    """
    scope = await get_user_project_ids(user, db)
    if _intersect_scope(scope, project_id) == []:
        raise HTTPException(status_code=404, detail="Project not found")
    repo = RuntimeStoreRepository(db)
    read_projects = await repo.resolve_runtime_read_projects(project_id)
    return RuntimeDocReadScopeResponse(
        project_id=project_id,
        scope=await repo.resolve_doc_read_scope(
            read_projects, project_id, default_window_days=default_window_days
        ),
        available_environments=await repo.list_mirrored_environments(
            read_projects, project_id, project_ids=read_projects
        ),
    )


@router.get("/job-scan-maps", response_model=RuntimeJobScanMapListResponse)
async def list_runtime_job_scan_maps(
    project_id: str,
    user: RuntimeSourceReader,
    db: DBSession,
) -> RuntimeJobScanMapListResponse:
    scope = await get_user_project_ids(user, db)
    if _intersect_scope(scope, project_id) == []:
        raise HTTPException(status_code=404, detail="Project not found")
    mappings = await RuntimeTopologyRepository(db).list_job_scan_maps(project_id)
    return RuntimeJobScanMapListResponse(mappings=mappings, total=len(mappings))


@router.post("/job-scan-maps", response_model=RuntimeJobScanMapResponse)
async def upsert_runtime_job_scan_map(
    body: RuntimeJobScanMapCreate,
    user: RuntimeSourceWriter,
    db: DBSession,
) -> RuntimeJobScanMapResponse:
    scope = await get_user_project_ids(user, db)
    if _intersect_scope(scope, body.project_id) == []:
        raise HTTPException(status_code=404, detail="Project not found")
    try:
        mapping = await RuntimeTopologyRepository(db).upsert_job_scan_map(
            body, created_by=str(user.id)
        )
    except RuntimeJobScanMapValidationError as exc:
        raise HTTPException(status_code=409, detail="Static job mapping cannot be validated") from exc
    await db.commit()
    return RuntimeJobScanMapResponse(mapping=mapping)


@router.delete("/job-scan-maps/{project_id}/{source_key}/{runtime_job_id}", status_code=204)
async def delete_runtime_job_scan_map(
    project_id: str,
    source_key: str,
    runtime_job_id: str,
    user: RuntimeSourceWriter,
    db: DBSession,
) -> None:
    scope = await get_user_project_ids(user, db)
    if _intersect_scope(scope, project_id) == []:
        raise HTTPException(status_code=404, detail="Project not found")
    deleted = await RuntimeTopologyRepository(db).delete_job_scan_map(
        project_id, source_key, runtime_job_id
    )
    if not deleted:
        raise HTTPException(status_code=404, detail="Runtime job mapping not found")
    await db.commit()


@router.get("/executions", response_model=ExecutionListResponse, summary="List runtime executions")
async def list_executions(
    user: CurrentUser,
    db: DBSession,
    project_id: str | None = None,
    job_name: str | None = None,
    state: RuntimeExecutionState | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    min_duration_ms: int | None = Query(default=None, ge=0),
    unfinished: bool | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> ExecutionListResponse:
    scope = await get_user_project_ids(user, db)
    repo = RuntimeStoreRepository(db)
    params = ListExecutionsInput(
        job_name=job_name,
        state=state,
        since=since,
        until=until,
        min_duration_ms=min_duration_ms,
        unfinished=unfinished,
        limit=limit,
        offset=offset,
    )
    executions = await repo.list_executions(
        _intersect_scope(scope, project_id),
        params,
    )
    return ExecutionListResponse(executions=executions, total=await repo.count_executions(_intersect_scope(scope, project_id), params))


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
    "/executions/{execution_id}/correlation",
    response_model=RuntimeExecutionCorrelation,
    summary="Correlate a runtime execution with explicit static topology evidence",
)
async def get_execution_correlation(
    execution_id: str,
    user: CurrentUser,
    db: DBSession,
) -> RuntimeExecutionCorrelation:
    try:
        return await RuntimeCorrelationService(db).correlate_execution(
            await get_user_project_ids(user, db), execution_id
        )
    except RuntimeRecordNotFoundError:
        raise HTTPException(status_code=404, detail="Execution not found")


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
    offset: int = Query(default=0, ge=0),
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
        params = GetExecutionLogsInput(execution_id=execution_id, min_severity=min_severity, limit=limit, offset=offset)
        events = await repo.get_execution_logs(
            scope,
            params,
        )
    except RuntimeRecordNotFoundError:
        events = []
    return LogEventListResponse(execution_id=execution_id, events=events, total=await repo.count_execution_logs(scope, GetExecutionLogsInput(execution_id=execution_id, min_severity=min_severity)))


@router.get("/failures", response_model=FailureListResponse, summary="List runtime failures")
async def list_failures(
    user: CurrentUser,
    db: DBSession,
    project_id: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> FailureListResponse:
    scope = await get_user_project_ids(user, db)
    repo = RuntimeStoreRepository(db)
    params = DetectFailuresInput(since=since, until=until, limit=limit, offset=offset)
    failures = await repo.detect_failures(
        _intersect_scope(scope, project_id),
        params,
    )
    return FailureListResponse(failures=failures, total=await repo.count_failures(_intersect_scope(scope, project_id), params))


@router.get(
    "/trend",
    response_model=RuntimeTrendResponse,
    summary="Get bucketed run-outcome and duration trend",
)
async def get_runtime_trend(
    user: CurrentUser,
    db: DBSession,
    project_id: str | None = None,
    source_key: str | None = None,
    job_name: str | None = None,
    bucket: RuntimeTrendBucket = RuntimeTrendBucket.DAY,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = Query(default=90, ge=1, le=400),
) -> RuntimeTrendResponse:
    scope = await get_user_project_ids(user, db)
    repo = RuntimeStoreRepository(db)
    scoped_projects = _intersect_scope(scope, project_id)
    params = RuntimeTrendInput(
        project_id=project_id,
        source_key=source_key,
        job_name=job_name,
        bucket=bucket,
        since=since,
        until=until,
        limit=limit,
    )
    points = await repo.get_outcome_trend(scoped_projects, params)
    coverage = await repo.get_outcome_trend_coverage(scoped_projects, params)
    return RuntimeTrendResponse(
        bucket=bucket,
        points=points,
        total=len(points),
        coverage=coverage,
    )


@router.get(
    "/job-reliability",
    response_model=RuntimeJobReliabilityResponse,
    summary="List per-job reliability and latency rollups",
)
async def list_runtime_job_reliability(
    user: CurrentUser,
    db: DBSession,
    project_id: str | None = None,
    source_key: str | None = None,
    job_name: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    attention_only: bool = Query(
        default=False,
        description="Only jobs whose latest observed run still needs attention.",
    ),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> RuntimeJobReliabilityResponse:
    scope = await get_user_project_ids(user, db)
    repo = RuntimeStoreRepository(db)
    selected_scope = _intersect_scope(scope, project_id)
    params = ListRuntimeJobReliabilityInput(
        project_id=project_id,
        source_key=source_key,
        job_name=job_name,
        since=since,
        until=until,
        attention_only=attention_only,
        limit=limit,
        offset=offset,
    )
    return RuntimeJobReliabilityResponse(
        jobs=await repo.list_job_reliability(selected_scope, params),
        total=await repo.count_job_reliability(selected_scope, params),
    )


@router.get(
    "/job-insights",
    response_model=RuntimeJobOperationalInsightResponse,
    summary="List bounded per-job operational insights",
)
async def list_runtime_job_insights(
    user: CurrentUser,
    db: DBSession,
    project_id: str,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = Query(default=100, ge=1, le=500),
) -> RuntimeJobOperationalInsightResponse:
    """One causal story per job for operator and documentation surfaces.

    The response contains aggregate coverage plus the dominant high/medium
    deterministic diagnosis. It never returns raw log rows or stack traces.
    """
    scope = _intersect_scope(await get_user_project_ids(user, db), project_id)
    repo = RuntimeStoreRepository(db)
    count_params = ListRuntimeJobReliabilityInput(
        project_id=project_id,
        since=since,
        until=until,
        limit=limit,
    )
    return RuntimeJobOperationalInsightResponse(
        insights=await repo.list_job_operational_insights(
            scope,
            project_id=project_id,
            since=since,
            until=until,
            limit=limit,
        ),
        total=await repo.count_job_reliability(scope, count_params),
    )


@router.get(
    "/component-hotspots",
    response_model=RuntimeComponentHotspotResponse,
    summary="List per-component throughput and latency hotspots",
)
async def list_runtime_component_hotspots(
    user: CurrentUser,
    db: DBSession,
    project_id: str | None = None,
    source_key: str | None = None,
    component_key: str | None = None,
    component_type: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> RuntimeComponentHotspotResponse:
    scope = await get_user_project_ids(user, db)
    repo = RuntimeStoreRepository(db)
    selected_scope = _intersect_scope(scope, project_id)
    params = ListRuntimeComponentHotspotsInput(
        project_id=project_id,
        source_key=source_key,
        component_key=component_key,
        component_type=component_type,
        since=since,
        until=until,
        limit=limit,
        offset=offset,
    )
    return RuntimeComponentHotspotResponse(
        components=await repo.list_component_hotspots(selected_scope, params),
        total=await repo.count_component_hotspots(selected_scope, params),
    )


@router.get(
    "/job-throughput",
    response_model=RuntimeJobThroughputResponse,
    summary="List rows moved per job from component metrics",
)
async def list_runtime_job_throughput(
    user: CurrentUser,
    db: DBSession,
    project_id: str | None = None,
    source_key: str | None = None,
    job_name: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> RuntimeJobThroughputResponse:
    scope = await get_user_project_ids(user, db)
    repo = RuntimeStoreRepository(db)
    selected_scope = _intersect_scope(scope, project_id)
    params = ListRuntimeJobThroughputInput(
        project_id=project_id,
        source_key=source_key,
        job_name=job_name,
        since=since,
        until=until,
        limit=limit,
        offset=offset,
    )
    return RuntimeJobThroughputResponse(
        jobs=await repo.list_job_throughput(selected_scope, params),
        total=await repo.count_job_throughput(selected_scope, params),
    )


@router.get(
    "/error-signatures",
    response_model=RuntimeErrorSignatureResponse,
    summary="List recurring runtime error signatures",
)
async def list_runtime_error_signatures(
    user: CurrentUser,
    db: DBSession,
    project_id: str | None = None,
    error_code: str | None = None,
    exception_class: str | None = None,
    component_name: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> RuntimeErrorSignatureResponse:
    scope = await get_user_project_ids(user, db)
    repo = RuntimeStoreRepository(db)
    selected_scope = _intersect_scope(scope, project_id)
    params = ListRuntimeErrorSignaturesInput(
        project_id=project_id,
        error_code=error_code,
        exception_class=exception_class,
        component_name=component_name,
        since=since,
        until=until,
        limit=limit,
        offset=offset,
    )
    return RuntimeErrorSignatureResponse(
        signatures=await repo.list_error_signatures(selected_scope, params),
        total=await repo.count_error_signatures(selected_scope, params),
    )


@router.get("/sync-status", response_model=SyncStatusResponse, summary="List runtime sync status")
async def list_sync_status(
    user: CurrentUser,
    db: DBSession,
    project_id: str | None = None,
    source_key: str | None = None,
    limit: int = Query(default=100, ge=1, le=200),
) -> SyncStatusResponse:
    scope = await get_user_project_ids(user, db)
    repo = RuntimeStoreRepository(db)
    states = await repo.list_sync_states(
        _intersect_scope(scope, project_id),
        source_key=source_key,
        project_id=project_id,
        limit=limit,
    )
    return SyncStatusResponse(states=states, total=len(states))


@router.get("/health", response_model=HealthSummaryResponse, summary="List runtime health rollups")
async def list_runtime_health(
    user: CurrentUser,
    db: DBSession,
    project_id: str | None = None,
    source_key: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
) -> HealthSummaryResponse:
    scope = await get_user_project_ids(user, db)
    repo = RuntimeStoreRepository(db)
    health = await repo.list_health_summaries(
        _intersect_scope(scope, project_id),
        project_id=project_id,
        source_key=source_key,
        limit=limit,
    )
    return HealthSummaryResponse(health=health, total=len(health))


@router.get("/sources", response_model=RuntimeSourceListResponse, summary="List runtime sources")
async def list_runtime_sources(
    user: CurrentUser,
    db: DBSession,
    project_id: str | None = None,
    source_key: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
) -> RuntimeSourceListResponse:
    scope = await get_user_project_ids(user, db)
    repo = RuntimeStoreRepository(db)
    sources = await repo.list_source_configs(
        _intersect_scope(scope, project_id),
        project_id=project_id,
        limit=limit,
    )
    if source_key is not None:
        sources = [source for source in sources if source.source_key == source_key]
    return RuntimeSourceListResponse(sources=sources, total=len(sources))


@router.get("/engines", response_model=RuntimeEngineListResponse, summary="List runtime engines")
async def list_runtime_engines(
    user: CurrentUser,
    db: DBSession,
    project_id: str | None = None,
    source_key: str | None = None,
    status: RuntimeEngineStatus | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> RuntimeEngineListResponse:
    scope = await get_user_project_ids(user, db)
    repo = RuntimeStoreRepository(db)
    selected_scope = _intersect_scope(scope, project_id)
    params = ListRuntimeEnginesInput(
        project_id=project_id,
        source_key=source_key,
        status=status,
        limit=limit,
        offset=offset,
    )
    engines = await repo.list_engines(selected_scope, params)
    return RuntimeEngineListResponse(
        engines=engines,
        total=await repo.count_engines(selected_scope, params),
    )


@router.get(
    "/engine-health",
    response_model=RuntimeEngineHealthResponse,
    summary="List runtime engine health",
)
async def list_runtime_engine_health(
    user: CurrentUser,
    db: DBSession,
    project_id: str | None = None,
    source_key: str | None = None,
    status: RuntimeEngineStatus | None = None,
    limit: int = Query(default=100, ge=1, le=500),
) -> RuntimeEngineHealthResponse:
    scope = await get_user_project_ids(user, db)
    repo = RuntimeStoreRepository(db)
    health = await repo.list_engine_health(
        _intersect_scope(scope, project_id),
        ListRuntimeEnginesInput(
            project_id=project_id,
            source_key=source_key,
            status=status,
            limit=limit,
        ),
    )
    return RuntimeEngineHealthResponse(health=health, total=len(health))


@router.get("/overview", response_model=RuntimeOverviewResponse, summary="Get runtime estate overview")
async def get_runtime_overview(
    user: CurrentUser,
    db: DBSession,
    project_id: str | None = None,
    source_key: str | None = None,
) -> RuntimeOverviewResponse:
    scope = await get_user_project_ids(user, db)
    repo = RuntimeStoreRepository(db)
    overview = await repo.get_estate_overview(
        _intersect_scope(scope, project_id),
        ListRuntimeInventoryInput(project_id=project_id, source_key=source_key),
    )
    return RuntimeOverviewResponse(overview=overview)


@router.get("/jobs", response_model=RuntimeJobListResponse, summary="List runtime jobs")
async def list_runtime_jobs(
    user: CurrentUser,
    db: DBSession,
    project_id: str | None = None,
    source_key: str | None = None,
    environment_id: str | None = None,
    workspace_id: str | None = None,
    engine_id: str | None = None,
    status: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> RuntimeJobListResponse:
    scope = await get_user_project_ids(user, db)
    repo = RuntimeStoreRepository(db)
    selected_scope = _intersect_scope(scope, project_id)
    params = ListRuntimeJobsInput(
        project_id=project_id,
        source_key=source_key,
        environment_id=environment_id,
        workspace_id=workspace_id,
        engine_id=engine_id,
        status=status,
        limit=limit,
        offset=offset,
    )
    jobs = await repo.list_jobs(selected_scope, params)
    return RuntimeJobListResponse(
        jobs=jobs,
        total=await repo.count_jobs(selected_scope, params),
    )


@router.get("/artifacts", response_model=RuntimeArtifactListResponse, summary="List runtime artifacts")
async def list_runtime_artifacts(
    user: CurrentUser,
    db: DBSession,
    project_id: str | None = None,
    source_key: str | None = None,
    workspace_id: str | None = None,
    artifact_type: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> RuntimeArtifactListResponse:
    scope = await get_user_project_ids(user, db)
    repo = RuntimeStoreRepository(db)
    selected_scope = _intersect_scope(scope, project_id)
    params = ListRuntimeArtifactsInput(
        project_id=project_id,
        source_key=source_key,
        workspace_id=workspace_id,
        artifact_type=artifact_type,
        limit=limit,
        offset=offset,
    )
    return RuntimeArtifactListResponse(
        artifacts=await repo.list_artifacts(selected_scope, params),
        total=await repo.count_artifacts(selected_scope, params),
    )


@router.get("/connections", response_model=RuntimeConnectionListResponse, summary="List runtime connections")
async def list_runtime_connections(
    user: CurrentUser,
    db: DBSession,
    project_id: str | None = None,
    source_key: str | None = None,
    workspace_id: str | None = None,
    connection_type: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> RuntimeConnectionListResponse:
    scope = await get_user_project_ids(user, db)
    repo = RuntimeStoreRepository(db)
    selected_scope = _intersect_scope(scope, project_id)
    params = ListRuntimeConnectionsInput(
        project_id=project_id,
        source_key=source_key,
        workspace_id=workspace_id,
        connection_type=connection_type,
        limit=limit,
        offset=offset,
    )
    return RuntimeConnectionListResponse(
        connections=await repo.list_connections(selected_scope, params),
        total=await repo.count_connections(selected_scope, params),
    )


@router.get("/dependencies", response_model=RuntimeDependencyListResponse, summary="List runtime dependencies")
async def list_runtime_dependencies(
    user: CurrentUser,
    db: DBSession,
    project_id: str | None = None,
    source_key: str | None = None,
    dependency_type: str | None = None,
    dependency_id: str | None = None,
    job_id: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> RuntimeDependencyListResponse:
    scope = await get_user_project_ids(user, db)
    repo = RuntimeStoreRepository(db)
    selected_scope = _intersect_scope(scope, project_id)
    params = ListRuntimeDependenciesInput(
        project_id=project_id,
        source_key=source_key,
        dependency_type=dependency_type,
        dependency_id=dependency_id,
        job_id=job_id,
        limit=limit,
        offset=offset,
    )
    return RuntimeDependencyListResponse(
        dependencies=await repo.list_job_dependencies(selected_scope, params),
        total=await repo.count_job_dependencies(selected_scope, params),
    )


@router.get(
    "/dependency-summary",
    response_model=RuntimeDependencySummaryResponse,
    summary="Get runtime dependency metadata summary",
)
async def get_runtime_dependency_summary(
    user: CurrentUser,
    db: DBSession,
    project_id: str | None = None,
    source_key: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
) -> RuntimeDependencySummaryResponse:
    scope = await get_user_project_ids(user, db)
    repo = RuntimeStoreRepository(db)
    summary = await repo.get_dependency_summary(
        _intersect_scope(scope, project_id),
        ListRuntimeInventoryInput(project_id=project_id, source_key=source_key, limit=limit),
    )
    return RuntimeDependencySummaryResponse(summary=summary)


@router.get("/job-runs", response_model=RuntimeJobRunListResponse, summary="List runtime job runs")
async def list_runtime_job_runs(
    user: CurrentUser,
    db: DBSession,
    project_id: str | None = None,
    source_key: str | None = None,
    job_id: str | None = None,
    engine_id: str | None = None,
    state: RuntimeExecutionState | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> RuntimeJobRunListResponse:
    scope = await get_user_project_ids(user, db)
    repo = RuntimeStoreRepository(db)
    selected_scope = _intersect_scope(scope, project_id)
    params = ListRuntimeJobRunsInput(
        project_id=project_id,
        source_key=source_key,
        job_id=job_id,
        engine_id=engine_id,
        state=state,
        since=since,
        until=until,
        limit=limit,
        offset=offset,
    )
    return RuntimeJobRunListResponse(
        runs=await repo.list_job_runs(selected_scope, params),
        total=await repo.count_job_runs(selected_scope, params),
    )


@router.get("/job-health", response_model=RuntimeJobHealthResponse, summary="List runtime job health")
async def list_runtime_job_health(
    user: CurrentUser,
    db: DBSession,
    project_id: str | None = None,
    source_key: str | None = None,
    environment_id: str | None = None,
    workspace_id: str | None = None,
    engine_id: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
) -> RuntimeJobHealthResponse:
    scope = await get_user_project_ids(user, db)
    repo = RuntimeStoreRepository(db)
    health = await repo.list_job_health(
        _intersect_scope(scope, project_id),
        ListRuntimeJobsInput(
            project_id=project_id,
            source_key=source_key,
            environment_id=environment_id,
            workspace_id=workspace_id,
            engine_id=engine_id,
            limit=limit,
        ),
    )
    return RuntimeJobHealthResponse(health=health, total=len(health))


@router.get("/plans", response_model=RuntimePlanListResponse, summary="List runtime plans")
async def list_runtime_plans(
    user: CurrentUser,
    db: DBSession,
    project_id: str | None = None,
    source_key: str | None = None,
    environment_id: str | None = None,
    workspace_id: str | None = None,
    status: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> RuntimePlanListResponse:
    scope = await get_user_project_ids(user, db)
    repo = RuntimeStoreRepository(db)
    selected_scope = _intersect_scope(scope, project_id)
    params = ListRuntimePlansInput(
        project_id=project_id,
        source_key=source_key,
        environment_id=environment_id,
        workspace_id=workspace_id,
        status=status,
        limit=limit,
        offset=offset,
    )
    plans = await repo.list_plans(selected_scope, params)
    return RuntimePlanListResponse(
        plans=plans,
        total=await repo.count_plans(selected_scope, params),
    )


@router.get("/plan-runs", response_model=RuntimePlanRunListResponse, summary="List runtime plan runs")
async def list_runtime_plan_runs(
    user: CurrentUser,
    db: DBSession,
    project_id: str | None = None,
    source_key: str | None = None,
    plan_id: str | None = None,
    state: RuntimeExecutionState | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> RuntimePlanRunListResponse:
    scope = await get_user_project_ids(user, db)
    repo = RuntimeStoreRepository(db)
    selected_scope = _intersect_scope(scope, project_id)
    params = ListRuntimePlanRunsInput(
        project_id=project_id,
        source_key=source_key,
        plan_id=plan_id,
        state=state,
        since=since,
        until=until,
        limit=limit,
        offset=offset,
    )
    return RuntimePlanRunListResponse(
        runs=await repo.list_plan_runs(selected_scope, params),
        total=await repo.count_plan_runs(selected_scope, params),
    )


@router.get("/plan-health", response_model=RuntimePlanHealthResponse, summary="List runtime plan health")
async def list_runtime_plan_health(
    user: CurrentUser,
    db: DBSession,
    project_id: str | None = None,
    source_key: str | None = None,
    environment_id: str | None = None,
    workspace_id: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
) -> RuntimePlanHealthResponse:
    scope = await get_user_project_ids(user, db)
    repo = RuntimeStoreRepository(db)
    health = await repo.list_plan_health(
        _intersect_scope(scope, project_id),
        ListRuntimePlansInput(
            project_id=project_id,
            source_key=source_key,
            environment_id=environment_id,
            workspace_id=workspace_id,
            limit=limit,
        ),
    )
    return RuntimePlanHealthResponse(health=health, total=len(health))


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
    offset: int = Query(default=0, ge=0),
) -> IncidentListResponse:
    scope = await get_user_project_ids(user, db)
    repo = RuntimeInsightRepository(db)
    selected_scope = _intersect_scope(scope, project_id)
    incidents = await repo.list_incidents(
        selected_scope,
        since=since,
        until=until,
        limit=limit,
        offset=offset,
    )
    return IncidentListResponse(
        incidents=incidents,
        total=await repo.count_incidents(selected_scope, since=since, until=until),
    )


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
