"""Runtime endpoints.

`GET /runtime/status` reports whether a live runtime provider is configured.

The read-only logs API (`/runtime/executions`, `/runtime/failures`, and nested log
routes) exposes the held runtime-data store to the logs UI. Every endpoint derives
its project scope from the authenticated user via `get_user_project_ids()`; no query
parameter or path value may widen access. `project_id` query params are filters
*within* the caller's scope, never an authorization source. Cross-project point
lookups return 404 so they cannot reveal whether an execution exists outside scope.

Read grants, and the one way to apply them
------------------------------------------
Scope widens in exactly one place: a STORED read grant, resolved by the
`RuntimeRead` / `RuntimeReadById` dependencies in `src/api/dependencies.py`. The
resolved `RuntimeReadScope` carries two halves -- `scope`, widened to include the
granting project, and `terms`, the per-grant environment and window limits -- and
a handler that passes one without the other has switched the grant's own
narrowing off. So the rule in this module is mechanical and has no exceptions:

    wherever `read.scope` goes, `read_terms=read.terms` goes with it.

An endpoint whose repository call cannot carry `read_terms` is NOT widened; it
keeps `_intersect_scope` and says so at its call site. Handing a widened scope to
a lane that cannot express the narrowing is the fail-open this design exists to
prevent, and it is worse than a shared reader seeing nothing.

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

from src.api.dependencies import (
    CurrentUser,
    DBSession,
    RuntimeRead,
    RuntimeReadById,
    RuntimeSourceReader,
    RuntimeSourceWriter,
)
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
    ListRuntimeDeploymentsInput,
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
    RuntimeDeployment,
    RuntimeEngine,
    RuntimeEngineHealth,
    RuntimeEngineStatus,
    RuntimeErrorSignature,
    RuntimeEstateOverview,
    RuntimeExecution,
    RuntimeExecutionState,
    RuntimeExecutableKind,
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
from src.runtime.read_scope import (
    RuntimeReadScope,
    resolve_runtime_read_scope,
    resolve_runtime_read_scope_for_user,
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


class RuntimeDeploymentListResponse(BaseModel):
    deployments: list[RuntimeDeployment]
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

    THE UNWIDENED PATH, kept deliberately. The `RuntimeRead` dependency replaced
    this on every endpoint whose repository call can carry `read_terms`. What is
    left are the endpoints that must NOT widen, and each one says why where it
    calls this:

      * the two `job-scan-maps` WRITES -- a read grant is read permission;
      * `GET /job-scan-maps`, `GET /sources`, `GET /job-insights` and the
        incident lane -- their repository methods take no `read_terms`, so a
        widened scope would arrive with the grant's narrowing missing;
      * `GET /doc-read-scope` -- for the 404 GATE only. It resolves its own read
        set immediately afterwards, terms included, because the picker it feeds
        must describe the environments a documentation run will really reach.
        The gate stays because this endpoint answers for a NAMED project and has
        to 404 on one the user cannot see, which an empty read set cannot
        express.

    Deleting it would therefore mean widening one of those, which is the one
    thing none of them may do.
    """
    if project_id is None:
        return scope
    if scope is None:
        return [project_id]
    if project_id in scope:
        return [project_id]
    return []


async def _execution_read_scope(
    repo: RuntimeStoreRepository, read: RuntimeReadScope, execution_id: str
) -> ProjectScope:
    """The narrowest scope under which `execution_id` is readable, or 404.

    The bridge to a lane that takes a plain `ProjectScope` and cannot carry
    `read_terms`. Handing such a lane the widened `read.scope` would be the
    fail-open this module exists to prevent -- worse than a plain widening,
    because an execution id is not unique across projects: the gate could match
    the reader's OWN row while the terms-blind query behind it picked the
    grantor's row with the same id from an environment the grant excluded.

    So the scope is resolved to rows that are readable OUTRIGHT, in at most one
    query plus one per narrowed grant:

    * terms that restrict nothing are tried together, and on a hit the whole
      unrestricted set is returned -- every project in it is fully readable, so
      no later query inside that scope can reach a row the grant did not allow;
    * each narrowed term is then tried alone, carrying its own environment and
      window, and a hit returns that project and nothing else;
    * no terms at all means no read set was resolved (an unrestricted user, or a
      fully denied one), so `read.scope` is already exactly the old boundary.

    Raises `RuntimeRecordNotFoundError`, the same signal the repository raises,
    so a caller's existing 404 handler covers both without a second branch.
    """
    if not read.terms:
        return read.scope

    unrestricted = [term for term in read.terms if term.unrestricted]
    if unrestricted:
        projects = [term.project_id for term in unrestricted]
        try:
            await repo.get_execution_details(
                projects,
                GetExecutionDetailsInput(execution_id=execution_id, read_terms=unrestricted),
            )
            return projects
        except RuntimeRecordNotFoundError:
            pass

    for term in read.terms:
        if term.unrestricted:
            continue
        try:
            await repo.get_execution_details(
                [term.project_id],
                GetExecutionDetailsInput(execution_id=execution_id, read_terms=[term]),
            )
            return [term.project_id]
        except RuntimeRecordNotFoundError:
            continue
    raise RuntimeRecordNotFoundError(f"Unknown execution_id: {execution_id}")


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
    `resolve_runtime_read_scope` adds the projects whose runtime rows this
    project has been granted, so one vendor tenant, mirrored once, can be read
    by every scanned project it backs instead of being registered (and polled)
    once per project.  The signature is deliberately unchanged: no caller may
    pass a project set in.  With no user behind this key there is no second
    gate, so a request-supplied read set would be an authorization bypass with
    nothing behind it.  `user_scope=None` says exactly that -- there is no user
    scope to check the named project against -- and widening still comes only
    from stored grant rows.  `read_scope.shared_read` reports whether the
    widening actually applied, and `read_scope.read_project_ids` says to what.

    Two resolutions, on purpose
    ---------------------------
    `resolve_doc_read_scope` is the FLAT documentation scope this response
    reports and applies to the whole read: every configured source's environment
    selection unioned, and the widest window.  `resolve_runtime_read_scope` is
    the PER-PROJECT terms, which bound each granting project's contribution on
    top of it -- a grant's environment and window are limits the GRANTOR set on
    THEIR estate, and the flat scope cannot express them without either leaking
    the grantor's excluded rows or suppressing the reader's own.  Both are
    anchored at the same `now`, so a grant's window floor and this response's
    own `since` compare against one instant rather than two.

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
    now = datetime.now(UTC)
    read = await resolve_runtime_read_scope(
        repo, user_scope=None, project_id=project_id, now=now
    )
    read_projects = read.project_ids or [project_id]
    # The caller's `window_days` is the DEFAULT, not the ceiling: a per-project
    # setting is the operator's deliberate choice about how much history
    # describes this estate, and the generator has no basis to overrule it.
    read_scope = await repo.resolve_doc_read_scope(
        read_projects, project_id, default_window_days=window_days, read_terms=read.terms
    )
    effective_window = read_scope.window_days or window_days
    since = now - timedelta(days=effective_window)
    units_, unmatched, orphans = await repo.list_doc_evidence(
        read_projects,
        project_id=project_id,
        unit_names=units,
        since=since,
        max_failures_per_unit=max_failures_per_unit,
        environment_ids=read_scope.environment_ids,
        project_ids=read_projects,
        # Never one without the other: `read_projects` names the granting
        # project, and the terms are what that grant actually allows of it.
        read_terms=read.terms,
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

    It offers the environments that grant actually REACHES, which is why the
    resolved terms are threaded into both calls.  This endpoint exists so an
    operator's selection lands on data; an environment a grant excludes would be
    a choice that silently resolves to no evidence, and listing it would also
    tell a reader granted PROD what else the grantor's estate runs.
    """
    scope = await get_user_project_ids(user, db)
    if _intersect_scope(scope, project_id) == []:
        raise HTTPException(status_code=404, detail="Project not found")
    repo = RuntimeStoreRepository(db)
    # Same resolver as `/doc-evidence`, under the USER's scope: the check above
    # already 404s a project outside it, and resolving here keeps the scope this
    # picker describes identical to the one a documentation run will read.
    read = await resolve_runtime_read_scope(repo, user_scope=scope, project_id=project_id)
    read_projects = read.project_ids or [project_id]
    return RuntimeDocReadScopeResponse(
        project_id=project_id,
        scope=await repo.resolve_doc_read_scope(
            read_projects,
            project_id,
            default_window_days=default_window_days,
            read_terms=read.terms,
        ),
        available_environments=await repo.list_mirrored_environments(
            read_projects, project_id, project_ids=read_projects, read_terms=read.terms
        ),
    )


@router.get("/job-scan-maps", response_model=RuntimeJobScanMapListResponse)
async def list_runtime_job_scan_maps(
    project_id: str,
    user: RuntimeSourceReader,
    db: DBSession,
) -> RuntimeJobScanMapListResponse:
    """Static-to-runtime job mappings for one project.

    NOT widened by read grants. `RuntimeTopologyRepository.list_job_scan_maps`
    takes a single `project_id` and has no `read_terms` parameter, so there is no
    way to express a grant's environment or window here; a mapping is also a
    statement about the READER's scan, not about the mirrored estate, so a
    granted tenant has nothing to contribute to it.
    """
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
    """Create or replace one static-to-runtime job mapping.

    A WRITE, and therefore deliberately on the raw user scope. A read grant says
    "this project may READ my mirrored rows"; it is not consent to write into the
    granting project, and `RuntimeRead` here would silently turn one into the
    other. The same applies to the DELETE below, and to any write added beside
    them: writes intersect, never widen.
    """
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
    # A WRITE: raw user scope only, never `RuntimeRead`. See the upsert above.
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
    read: RuntimeRead,
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
    """Page of runtime executions, newest first.

    `ListExecutionsInput` carries no `project_id` of its own -- this surface was
    always filtered by the caller's scope alone -- which is exactly why the terms
    matter here. `read.scope` may now include a granting project, and without
    `read_terms` that widening would arrive with the grant's environment and
    window stripped off. The SAME params object feeds the page and the count, so
    the two can never be computed from different predicates.
    """
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
        read_terms=read.terms,
    )
    executions = await repo.list_executions(read.scope, params)
    return ExecutionListResponse(
        executions=executions, total=await repo.count_executions(read.scope, params)
    )


@router.get(
    "/executions/{execution_id}",
    response_model=ExecutionDetailResponse,
    summary="Get a single runtime execution",
)
async def get_execution(
    execution_id: str,
    read: RuntimeReadById,
    db: DBSession,
) -> ExecutionDetailResponse:
    """One execution by id -- the drill-down from a row in the list above.

    `RuntimeReadById`, not `RuntimeRead`: there is no `project_id` here to widen
    from, and without the by-id resolver a granted reader would 404 on the very
    row the list just handed them.

    An execution id is not a secret -- the same id exists in the granting
    project's mirror -- so `read_terms` is the only thing keeping a reader
    granted PROD from naming an ACC execution and reading it.
    """
    repo = RuntimeStoreRepository(db)
    try:
        execution = await repo.get_execution_details(
            read.scope,
            GetExecutionDetailsInput(execution_id=execution_id, read_terms=read.terms),
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
    """Correlate one execution with the static topology it was scanned from.

    Resolved in the handler rather than through `RuntimeReadById` because
    `RuntimeCorrelationService` takes a plain `ProjectScope` and cannot carry
    `read_terms`: the scope it receives must FIRST be narrowed to the project of
    the execution the terms actually admit (`_execution_read_scope`). Doing half
    of that in a dependency would leave a widened scope sitting in the handler
    looking ready to use, which is precisely the mistake this module is arranged
    to make impossible.

    Everything the service reads after that point is keyed by the resolved
    execution's own project, so narrowing here costs nothing and the drill-down
    reaches a granted reader's rows the same way the list and the detail do.
    """
    repo = RuntimeStoreRepository(db)
    read = await resolve_runtime_read_scope_for_user(
        repo, user_scope=await get_user_project_ids(user, db)
    )
    try:
        scope = await _execution_read_scope(repo, read, execution_id)
        return await RuntimeCorrelationService(db).correlate_execution(scope, execution_id)
    except RuntimeRecordNotFoundError:
        raise HTTPException(status_code=404, detail="Execution not found")


@router.get(
    "/executions/{execution_id}/logs",
    response_model=LogEventListResponse,
    summary="Get log events for a runtime execution",
)
async def get_execution_logs(
    execution_id: str,
    read: RuntimeReadById,
    db: DBSession,
    min_severity: RuntimeLogSeverity | None = None,
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> LogEventListResponse:
    """Log lines of one execution -- the second half of the drill-down.

    The read set is applied to all THREE reads below (the existence gate, the
    page and the count) and with the same terms, so a granted reader cannot see
    a total it is not allowed to page to, and a reader granted PROD cannot name
    an ACC execution and read its lines.
    """
    repo = RuntimeStoreRepository(db)
    # Gate on a scoped existence check first: an unknown or out-of-scope id is a 404,
    # while an in-scope execution with no log lines at this severity is an empty list.
    try:
        await repo.get_execution_details(
            read.scope,
            GetExecutionDetailsInput(execution_id=execution_id, read_terms=read.terms),
        )
    except RuntimeRecordNotFoundError:
        raise HTTPException(status_code=404, detail="Execution not found")
    try:
        params = GetExecutionLogsInput(
            execution_id=execution_id,
            min_severity=min_severity,
            limit=limit,
            offset=offset,
            read_terms=read.terms,
        )
        events = await repo.get_execution_logs(
            read.scope,
            params,
        )
    except RuntimeRecordNotFoundError:
        events = []
    return LogEventListResponse(
        execution_id=execution_id,
        events=events,
        total=await repo.count_execution_logs(
            read.scope,
            GetExecutionLogsInput(
                execution_id=execution_id,
                min_severity=min_severity,
                read_terms=read.terms,
            ),
        ),
    )


@router.get("/failures", response_model=FailureListResponse, summary="List runtime failures")
async def list_failures(
    read: RuntimeRead,
    db: DBSession,
    project_id: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> FailureListResponse:
    """Failures across the read set, newest first.

    Like `/executions`, `DetectFailuresInput` carries no `project_id`, so the
    terms are the only thing narrowing a granting project's contribution.
    """
    repo = RuntimeStoreRepository(db)
    params = DetectFailuresInput(
        since=since, until=until, limit=limit, offset=offset, read_terms=read.terms
    )
    failures = await repo.detect_failures(read.scope, params)
    return FailureListResponse(
        failures=failures, total=await repo.count_failures(read.scope, params)
    )


@router.get(
    "/trend",
    response_model=RuntimeTrendResponse,
    summary="Get bucketed run-outcome and duration trend",
)
async def get_runtime_trend(
    read: RuntimeRead,
    db: DBSession,
    project_id: str | None = None,
    source_key: str | None = None,
    job_name: str | None = None,
    bucket: RuntimeTrendBucket = RuntimeTrendBucket.DAY,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = Query(default=90, ge=1, le=400),
) -> RuntimeTrendResponse:
    """Bucketed run outcomes, and the coverage the series was drawn from.

    Both calls take the same params, so the coverage statement describes the
    population the points were counted over -- including, under a grant, the
    window that grant imposes on the grantor's half of it.
    """
    repo = RuntimeStoreRepository(db)
    params = RuntimeTrendInput(
        project_id=project_id,
        source_key=source_key,
        job_name=job_name,
        bucket=bucket,
        since=since,
        until=until,
        limit=limit,
        read_terms=read.terms,
    )
    points = await repo.get_outcome_trend(read.scope, params)
    coverage = await repo.get_outcome_trend_coverage(read.scope, params)
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
    read: RuntimeRead,
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
    repo = RuntimeStoreRepository(db)
    params = ListRuntimeJobReliabilityInput(
        project_id=project_id,
        source_key=source_key,
        job_name=job_name,
        since=since,
        until=until,
        attention_only=attention_only,
        limit=limit,
        offset=offset,
        read_terms=read.terms,
    )
    return RuntimeJobReliabilityResponse(
        jobs=await repo.list_job_reliability(read.scope, params),
        total=await repo.count_job_reliability(read.scope, params),
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

    NOT widened by read grants. `list_job_operational_insights` builds its own
    `ListRuntimeJobReliabilityInput` internally and takes no `read_terms`, so
    terms passed here would reach the `total` and not the page -- a count nobody
    can page to -- while a widened scope alone would reach the grantor's rows
    with the grant's narrowing missing. Widening belongs in that repository
    method, not in a second resolution here.
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
    read: RuntimeRead,
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
    repo = RuntimeStoreRepository(db)
    params = ListRuntimeComponentHotspotsInput(
        project_id=project_id,
        source_key=source_key,
        component_key=component_key,
        component_type=component_type,
        since=since,
        until=until,
        limit=limit,
        offset=offset,
        read_terms=read.terms,
    )
    return RuntimeComponentHotspotResponse(
        components=await repo.list_component_hotspots(read.scope, params),
        total=await repo.count_component_hotspots(read.scope, params),
    )


@router.get(
    "/job-throughput",
    response_model=RuntimeJobThroughputResponse,
    summary="List rows moved per job from component metrics",
)
async def list_runtime_job_throughput(
    read: RuntimeRead,
    db: DBSession,
    project_id: str | None = None,
    source_key: str | None = None,
    job_name: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> RuntimeJobThroughputResponse:
    repo = RuntimeStoreRepository(db)
    params = ListRuntimeJobThroughputInput(
        project_id=project_id,
        source_key=source_key,
        job_name=job_name,
        since=since,
        until=until,
        limit=limit,
        offset=offset,
        read_terms=read.terms,
    )
    return RuntimeJobThroughputResponse(
        jobs=await repo.list_job_throughput(read.scope, params),
        total=await repo.count_job_throughput(read.scope, params),
    )


@router.get(
    "/error-signatures",
    response_model=RuntimeErrorSignatureResponse,
    summary="List recurring runtime error signatures",
)
async def list_runtime_error_signatures(
    read: RuntimeRead,
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
    repo = RuntimeStoreRepository(db)
    params = ListRuntimeErrorSignaturesInput(
        project_id=project_id,
        error_code=error_code,
        exception_class=exception_class,
        component_name=component_name,
        since=since,
        until=until,
        limit=limit,
        offset=offset,
        read_terms=read.terms,
    )
    return RuntimeErrorSignatureResponse(
        signatures=await repo.list_error_signatures(read.scope, params),
        total=await repo.count_error_signatures(read.scope, params),
    )


@router.get("/sync-status", response_model=SyncStatusResponse, summary="List runtime sync status")
async def list_sync_status(
    read: RuntimeRead,
    db: DBSession,
    project_id: str | None = None,
    source_key: str | None = None,
    limit: int = Query(default=100, ge=1, le=200),
) -> SyncStatusResponse:
    """Freshness of every mirror in the read set.

    `read_terms` is a keyword here rather than a params field, but the rule is
    the same one: it travels with `read.scope` and takes precedence over
    `project_id`. A sync-state row is per (source, project) with no environment
    and no execution id, so an environment-narrowed grant contributes none of the
    grantor's rows -- fail-closed, as the repository documents.
    """
    repo = RuntimeStoreRepository(db)
    states = await repo.list_sync_states(
        read.scope,
        source_key=source_key,
        project_id=project_id,
        limit=limit,
        read_terms=read.terms,
    )
    return SyncStatusResponse(states=states, total=len(states))


@router.get("/health", response_model=HealthSummaryResponse, summary="List runtime health rollups")
async def list_runtime_health(
    read: RuntimeRead,
    db: DBSession,
    project_id: str | None = None,
    source_key: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
) -> HealthSummaryResponse:
    repo = RuntimeStoreRepository(db)
    health = await repo.list_health_summaries(
        read.scope,
        project_id=project_id,
        source_key=source_key,
        limit=limit,
        read_terms=read.terms,
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
    """Registered source CONFIGURATIONS for this project.

    NOT widened by read grants, and deliberately so. `list_source_configs` is the
    registration surface -- who holds the credential, when it last polled, what
    it is called -- not the mirror inventory, and `RuntimeStoreRepository` draws
    that line explicitly: `list_sources` takes `read_terms` and this one does
    not. A grant says "read my mirrored rows"; it does not hand the reader the
    grantor's connector setup, and a widened scope here would do exactly that
    with no narrowing available to bound it.
    """
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
    read: RuntimeRead,
    db: DBSession,
    project_id: str | None = None,
    source_key: str | None = None,
    status: RuntimeEngineStatus | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> RuntimeEngineListResponse:
    repo = RuntimeStoreRepository(db)
    params = ListRuntimeEnginesInput(
        project_id=project_id,
        source_key=source_key,
        status=status,
        limit=limit,
        offset=offset,
        read_terms=read.terms,
    )
    engines = await repo.list_engines(read.scope, params)
    return RuntimeEngineListResponse(
        engines=engines,
        total=await repo.count_engines(read.scope, params),
    )


@router.get(
    "/engine-health",
    response_model=RuntimeEngineHealthResponse,
    summary="List runtime engine health",
)
async def list_runtime_engine_health(
    read: RuntimeRead,
    db: DBSession,
    project_id: str | None = None,
    source_key: str | None = None,
    status: RuntimeEngineStatus | None = None,
    limit: int = Query(default=100, ge=1, le=500),
) -> RuntimeEngineHealthResponse:
    repo = RuntimeStoreRepository(db)
    health = await repo.list_engine_health(
        read.scope,
        ListRuntimeEnginesInput(
            project_id=project_id,
            source_key=source_key,
            status=status,
            limit=limit,
            read_terms=read.terms,
        ),
    )
    return RuntimeEngineHealthResponse(health=health, total=len(health))


@router.get("/overview", response_model=RuntimeOverviewResponse, summary="Get runtime estate overview")
async def get_runtime_overview(
    read: RuntimeRead,
    db: DBSession,
    project_id: str | None = None,
    source_key: str | None = None,
) -> RuntimeOverviewResponse:
    repo = RuntimeStoreRepository(db)
    overview = await repo.get_estate_overview(
        read.scope,
        ListRuntimeInventoryInput(
            project_id=project_id, source_key=source_key, read_terms=read.terms
        ),
    )
    return RuntimeOverviewResponse(overview=overview)


@router.get("/jobs", response_model=RuntimeJobListResponse, summary="List runtime jobs")
async def list_runtime_jobs(
    read: RuntimeRead,
    db: DBSession,
    project_id: str | None = None,
    source_key: str | None = None,
    environment_id: str | None = None,
    workspace_id: str | None = None,
    engine_id: str | None = None,
    status: str | None = None,
    executable_kind: RuntimeExecutableKind | None = RuntimeExecutableKind.JOB,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> RuntimeJobListResponse:
    """Job inventory across the read set.

    Inventory, so a grant's WINDOW deliberately does not apply here: a window
    bounds how much history a reader may see, not whether a job exists. The
    grant's environment does apply, because `runtime_jobs` carries one.
    """
    repo = RuntimeStoreRepository(db)
    params = ListRuntimeJobsInput(
        project_id=project_id,
        source_key=source_key,
        environment_id=environment_id,
        workspace_id=workspace_id,
        engine_id=engine_id,
        status=status,
        executable_kind=executable_kind,
        limit=limit,
        offset=offset,
        read_terms=read.terms,
    )
    jobs = await repo.list_jobs(read.scope, params)
    return RuntimeJobListResponse(
        jobs=jobs,
        total=await repo.count_jobs(read.scope, params),
    )


@router.get(
    "/deployments",
    response_model=RuntimeDeploymentListResponse,
    summary="List latest Route and Data Service deployments",
)
async def list_runtime_deployments(
    read: RuntimeRead,
    db: DBSession,
    project_id: str | None = None,
    source_key: str | None = None,
    executable_kind: RuntimeExecutableKind | None = None,
    state: RuntimeExecutionState | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> RuntimeDeploymentListResponse:
    if executable_kind == RuntimeExecutableKind.JOB:
        raise HTTPException(status_code=422, detail="Deployments only support routes and data services")
    repo = RuntimeStoreRepository(db)
    params = ListRuntimeDeploymentsInput(
        project_id=project_id,
        source_key=source_key,
        executable_kind=executable_kind,
        state=state,
        limit=limit,
        offset=offset,
        read_terms=read.terms,
    )
    return RuntimeDeploymentListResponse(
        deployments=await repo.list_deployments(read.scope, params),
        total=await repo.count_deployments(read.scope, params),
    )


@router.get("/artifacts", response_model=RuntimeArtifactListResponse, summary="List runtime artifacts")
async def list_runtime_artifacts(
    read: RuntimeRead,
    db: DBSession,
    project_id: str | None = None,
    source_key: str | None = None,
    workspace_id: str | None = None,
    artifact_type: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> RuntimeArtifactListResponse:
    """Artifact inventory across the read set.

    `runtime_artifacts` carries neither an environment nor an execution id, so an
    environment-narrowed grant contributes NOTHING here rather than everything.
    That fail-closed rule lives in `_apply_read_terms`; the alternative would
    hand a reader granted one environment the grantor's complete artifact
    inventory.
    """
    repo = RuntimeStoreRepository(db)
    params = ListRuntimeArtifactsInput(
        project_id=project_id,
        source_key=source_key,
        workspace_id=workspace_id,
        artifact_type=artifact_type,
        limit=limit,
        offset=offset,
        read_terms=read.terms,
    )
    return RuntimeArtifactListResponse(
        artifacts=await repo.list_artifacts(read.scope, params),
        total=await repo.count_artifacts(read.scope, params),
    )


@router.get("/connections", response_model=RuntimeConnectionListResponse, summary="List runtime connections")
async def list_runtime_connections(
    read: RuntimeRead,
    db: DBSession,
    project_id: str | None = None,
    source_key: str | None = None,
    workspace_id: str | None = None,
    connection_type: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> RuntimeConnectionListResponse:
    repo = RuntimeStoreRepository(db)
    params = ListRuntimeConnectionsInput(
        project_id=project_id,
        source_key=source_key,
        workspace_id=workspace_id,
        connection_type=connection_type,
        limit=limit,
        offset=offset,
        read_terms=read.terms,
    )
    return RuntimeConnectionListResponse(
        connections=await repo.list_connections(read.scope, params),
        total=await repo.count_connections(read.scope, params),
    )


@router.get("/dependencies", response_model=RuntimeDependencyListResponse, summary="List runtime dependencies")
async def list_runtime_dependencies(
    read: RuntimeRead,
    db: DBSession,
    project_id: str | None = None,
    source_key: str | None = None,
    dependency_type: str | None = None,
    dependency_id: str | None = None,
    job_id: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> RuntimeDependencyListResponse:
    repo = RuntimeStoreRepository(db)
    params = ListRuntimeDependenciesInput(
        project_id=project_id,
        source_key=source_key,
        dependency_type=dependency_type,
        dependency_id=dependency_id,
        job_id=job_id,
        limit=limit,
        offset=offset,
        read_terms=read.terms,
    )
    return RuntimeDependencyListResponse(
        dependencies=await repo.list_job_dependencies(read.scope, params),
        total=await repo.count_job_dependencies(read.scope, params),
    )


@router.get(
    "/dependency-summary",
    response_model=RuntimeDependencySummaryResponse,
    summary="Get runtime dependency metadata summary",
)
async def get_runtime_dependency_summary(
    read: RuntimeRead,
    db: DBSession,
    project_id: str | None = None,
    source_key: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
) -> RuntimeDependencySummaryResponse:
    repo = RuntimeStoreRepository(db)
    summary = await repo.get_dependency_summary(
        read.scope,
        ListRuntimeInventoryInput(
            project_id=project_id,
            source_key=source_key,
            limit=limit,
            read_terms=read.terms,
        ),
    )
    return RuntimeDependencySummaryResponse(summary=summary)


@router.get("/job-runs", response_model=RuntimeJobRunListResponse, summary="List runtime job runs")
async def list_runtime_job_runs(
    read: RuntimeRead,
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
    repo = RuntimeStoreRepository(db)
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
        read_terms=read.terms,
    )
    return RuntimeJobRunListResponse(
        runs=await repo.list_job_runs(read.scope, params),
        total=await repo.count_job_runs(read.scope, params),
    )


@router.get("/job-health", response_model=RuntimeJobHealthResponse, summary="List runtime job health")
async def list_runtime_job_health(
    read: RuntimeRead,
    db: DBSession,
    project_id: str | None = None,
    source_key: str | None = None,
    environment_id: str | None = None,
    workspace_id: str | None = None,
    engine_id: str | None = None,
    executable_kind: RuntimeExecutableKind | None = None,
    limit: int = Query(default=100, ge=1, le=500),
) -> RuntimeJobHealthResponse:
    """Health per mirrored executable. Omitting `executable_kind` returns EVERY kind.

    It used to return only `job`-kind rows and say nothing about it, so every
    route and data service was structurally absent from the surface -- on one
    measured estate that is 50 of 145 stream-bound deployed designs and 14 of 28
    business streams with nothing to render. Each row now carries its
    `executable_kind` and the `liveness` its provider declares for that kind, so
    a caller can tell a run that ended from a deployment that is simply up.

    `executable_kind` is passed EXPLICITLY rather than left to
    `ListRuntimeJobsInput`, whose default stays `job`: `/runtime/jobs`,
    `get_estate_overview`, `list_health_summaries` and the `list_runtime_jobs`
    chat tool all lean on that default, and widening it would silently change
    four surfaces to fix one.
    """
    repo = RuntimeStoreRepository(db)
    health = await repo.list_job_health(
        read.scope,
        ListRuntimeJobsInput(
            project_id=project_id,
            source_key=source_key,
            environment_id=environment_id,
            workspace_id=workspace_id,
            engine_id=engine_id,
            executable_kind=executable_kind,
            limit=limit,
            read_terms=read.terms,
        ),
    )
    return RuntimeJobHealthResponse(health=health, total=len(health))


@router.get("/plans", response_model=RuntimePlanListResponse, summary="List runtime plans")
async def list_runtime_plans(
    read: RuntimeRead,
    db: DBSession,
    project_id: str | None = None,
    source_key: str | None = None,
    environment_id: str | None = None,
    workspace_id: str | None = None,
    status: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> RuntimePlanListResponse:
    repo = RuntimeStoreRepository(db)
    params = ListRuntimePlansInput(
        project_id=project_id,
        source_key=source_key,
        environment_id=environment_id,
        workspace_id=workspace_id,
        status=status,
        limit=limit,
        offset=offset,
        read_terms=read.terms,
    )
    plans = await repo.list_plans(read.scope, params)
    return RuntimePlanListResponse(
        plans=plans,
        total=await repo.count_plans(read.scope, params),
    )


@router.get("/plan-runs", response_model=RuntimePlanRunListResponse, summary="List runtime plan runs")
async def list_runtime_plan_runs(
    read: RuntimeRead,
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
    repo = RuntimeStoreRepository(db)
    params = ListRuntimePlanRunsInput(
        project_id=project_id,
        source_key=source_key,
        plan_id=plan_id,
        state=state,
        since=since,
        until=until,
        limit=limit,
        offset=offset,
        read_terms=read.terms,
    )
    return RuntimePlanRunListResponse(
        runs=await repo.list_plan_runs(read.scope, params),
        total=await repo.count_plan_runs(read.scope, params),
    )


@router.get("/plan-health", response_model=RuntimePlanHealthResponse, summary="List runtime plan health")
async def list_runtime_plan_health(
    read: RuntimeRead,
    db: DBSession,
    project_id: str | None = None,
    source_key: str | None = None,
    environment_id: str | None = None,
    workspace_id: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
) -> RuntimePlanHealthResponse:
    repo = RuntimeStoreRepository(db)
    health = await repo.list_plan_health(
        read.scope,
        ListRuntimePlansInput(
            project_id=project_id,
            source_key=source_key,
            environment_id=environment_id,
            workspace_id=workspace_id,
            limit=limit,
            read_terms=read.terms,
        ),
    )
    return RuntimePlanHealthResponse(health=health, total=len(health))


# ---------------------------------------------------------------------------
# Smart-log insight endpoints (Slice 2)
#
# NONE of these are widened by read grants, and the reason is one property of
# the lane rather than six separate decisions: `RuntimeInsightRepository` filters
# on `ProjectScope` alone and has no `read_terms` parameter anywhere, and its
# tables (`runtime_execution_summaries`, `runtime_enriched_events`,
# `runtime_incidents`) carry no environment column to narrow by. Handing it the
# widened `read.scope` would therefore return the grantor's rows with the grant's
# environment and window silently dropped -- the exact fail-open the rest of this
# module is arranged to prevent.
#
# So they stay on the raw user scope and `_intersect_scope`. A granted reader
# sees no incident from the shared tenant, which is a visible, reportable gap;
# the alternative is not. Closing it means threading `read_terms` through
# `RuntimeInsightRepository` the way `RuntimeStoreRepository` already has been --
# one change in that repository, not a second resolution here.
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
