"""DB-backed runtime provider (held-data Model 2 skeleton, Slice 2).

Answers the four generic runtime tools from the held runtime-data store instead of
calling a live source, by delegating to the Slice 1 RuntimeStoreRepository. Reads are
project-scoped using the scope carried on RuntimeProviderContext — which originates
from the authenticated request, never from LLM tool arguments.

Two halves of one boundary
--------------------------
`context.project_scope` is the RBAC allow-list, and under a read grant it is
WIDENED to include the granting project — a grant is a permission to see another
project's rows, so widening it is the point. The per-grant environment and window
limits are NOT in it. They ride on the params model's `read_terms` (or, for
`search_logs`, on an explicit keyword), and the repository applies them as a
per-project disjunction.

That makes a widened scope arriving here without its terms a data leak, not a
missing filter: the grantor's rows come back with the grant's own narrowing
silently unapplied. Every call below therefore passes both, and `_read_terms` is
the single accessor the four keyword-argument repository methods use so no call
site invents its own way of lifting them.

Source-agnostic: no Talend-specific names. A future sync worker (or any source)
populates the store through the repository write-port; this provider only reads.
"""

from __future__ import annotations

from typing import Any, Literal, Mapping, Sequence
from zoneinfo import ZoneInfoNotFoundError

from sqlalchemy.exc import SQLAlchemyError

from src.runtime.insights.models import (
    RuntimeFailureAnalysis,
    RuntimeIncidentDetail,
    RuntimeSimilarFailures,
    RuntimeTimelineEvent,
)
from src.runtime.insights.parser import redact_credentials
from src.runtime.insights.repository import RuntimeInsightRepository
from src.runtime.models import (
    AnalyzeExecutionFailureInput,
    DetectFailuresInput,
    FindSimilarFailuresInput,
    GetExecutionDetailsInput,
    GetExecutionLogsInput,
    GetExecutionTimelineInput,
    GetRuntimeIncidentInput,
    ListExecutionsInput,
    ListRuntimeArtifactsInput,
    ListRuntimeComponentHotspotsInput,
    ListRuntimeConnectionsInput,
    ListRuntimeDependenciesInput,
    ListRuntimeEnginesInput,
    ListRuntimeErrorSignaturesInput,
    ListRuntimeHealthInput,
    ListRuntimeInventoryInput,
    ListRuntimeJobReliabilityInput,
    ListRuntimeJobScheduleProfileInput,
    ListRuntimeJobThroughputInput,
    ListRuntimeJobsInput,
    ListRuntimePlansInput,
    ListRuntimeSyncStatusInput,
    RuntimeArtifact,
    RuntimeComponentHotspot,
    RuntimeConnection,
    RuntimeDependencySummary,
    RuntimeEngine,
    RuntimeErrorSignature,
    RuntimeExecution,
    RuntimeFailure,
    RuntimeHealthSummary,
    RuntimeJob,
    RuntimeJobDependency,
    RuntimeJobReliability,
    RuntimeJobScheduleProfile,
    RuntimeJobThroughput,
    RuntimeLogEvent,
    RuntimeOutcomeTrendPoint,
    RuntimePlan,
    RuntimeSource,
    RuntimeSyncState,
    RuntimeTrendInput,
    RuntimeUnhealthySummary,
)
from src.runtime.providers.base import (
    CredentialField,
    NoScopeSelectionMixin,
    SingleTenantIdentityMixin,
)
from src.runtime.read_scope import RuntimeReadTerm
from src.runtime.providers import RuntimeProviderContext, RuntimeProviderError
from src.runtime.store import RuntimeRecordNotFoundError, RuntimeStoreRepository

_TIMELINE_SIGNAL_MAX_CHARS = 200
_LOG_SEARCH_SNIPPET_MAX_CHARS = 360
_LOG_SEARCH_MAX_RESULTS = 20
# Aggregate rollups share their input models with the dashboard, whose pagination caps
# are far larger than an LLM context can absorb. Clamping here (rather than in the
# model) keeps the tool bound at the provider boundary, where it cannot be widened by
# tool arguments, and mirrors how log search and timeline signals are already bounded.
#: The default agent-facing page size for a rollup. Public because a caller that
#: cannot see this number cannot tell a complete result from a clamped one, and
#: a clamped ranking presented as complete is the failure this cap otherwise
#: causes. `_bounded` is the only thing that applies it.
#:
#: 25 is the right default for a RANKING -- "which jobs fail most", "which move
#: the most rows" -- where the top of the list IS the answer and the tail is
#: noise. pluripharm has 158 job names and 306 error signatures; nobody wants
#: either in full.
AGGREGATE_MAX_ROWS = 25
#: Retained under the original private name for in-module readers.
_AGGREGATE_MAX_ROWS = AGGREGATE_MAX_ROWS

#: Rollups whose answer is an INVENTORY rather than a ranking, with the page
#: size each one needs to answer in a single call.
#:
#: The distinction is whether truncation preserves meaning. A ranking clipped at
#: 25 is still a correct ranking. "Which jobs run between 22:00 and 07:00"
#: clipped at 25 is a wrong answer to the question asked -- and measured, it is
#: worse than that: an agent that cannot fit the window in one call slices it
#: into nine hourly calls instead, which cost 9 model steps, 9 re-prefills of a
#: growing context, and 172 rows for 67 distinct jobs, then reported the same
#: job under four headings with four different "medians". One call returning 67
#: rows is smaller, faster and correct.
#: Keyed by the PROVIDER method name -- the public name of the rollup -- so the
#: harness layer that has to disclose "this may be truncated" can look up the
#: same number this module enforces, instead of hardcoding a second copy that
#: drifts.
_AGGREGATE_ROW_CAPS: dict[str, int] = {
    # 67 jobs in pluripharm's overnight window; 100 leaves room for a larger
    # estate without inviting a whole-year, whole-estate read.
    "list_runtime_job_schedule_profile": 100,
}
_TREND_MAX_POINTS = 60
_LOG_SEARCH_FIELDS = (
    "execution_id",
    "project_id",
    "job_name",
    "source_key",
    "timestamp",
    "severity",
    "snippet",
    "message",
    "occurrence_count",
)


class StoredRuntimeProvider(NoScopeSelectionMixin, SingleTenantIdentityMixin):
    """Reads held runtime data via the repository, scoped per request."""

    def provider_key(self) -> str:
        return "stored"

    def provider_label(self) -> str:
        return "Stored Runtime Data"

    def credential_schema(self) -> list[CredentialField]:
        return []

    def container_key_field(self) -> str:
        return "workspace_id"

    def static_ref_strategy(self) -> None:
        """Nothing to declare: this provider reads the mirror, it does not fill it.

        The rows it serves were written by whichever provider actually ingested
        them, and that provider's own strategy is the one that binds them. A
        second declaration here would resolve the same rows twice under a
        provenance that names the reader rather than the ingester.
        """
        return None

    def executable_semantics(self) -> Mapping[str, Literal["execution", "lifecycle"]]:
        """Empty for the same reason: it produces no runtime rows of its own."""
        return {}

    def physical_source_identity(self, credentials: Mapping[str, Any]) -> str:
        return "stored:central-runtime-store"

    def source_type_aliases(self) -> set[str]:
        return {self.provider_key()}

    def identity_credential_fields(self) -> set[str]:
        return set()

    async def validate_credentials(self, credentials: Mapping[str, Any]) -> tuple[bool, str | None]:
        return True, None

    async def sync(self, *, credentials: Mapping[str, Any], project_id: str):
        raise RuntimeProviderError("stored runtime provider is read-only and cannot sync a live source")

    @staticmethod
    def _store_repository(context: RuntimeProviderContext | None) -> RuntimeStoreRepository:
        if context is None or context.db is None:
            raise RuntimeProviderError("stored runtime provider requires a database session")
        return RuntimeStoreRepository(context.db)

    @staticmethod
    def _insight_repository(context: RuntimeProviderContext | None) -> RuntimeInsightRepository:
        if context is None or context.db is None:
            raise RuntimeProviderError("stored runtime provider requires a database session")
        return RuntimeInsightRepository(context.db)

    @staticmethod
    def _scope(context: RuntimeProviderContext | None):
        # context is non-None here (checked by _store_repository/_insight_repository)
        return context.project_scope if context is not None else None

    @staticmethod
    def _read_terms(params: Any) -> Sequence[RuntimeReadTerm]:
        """The resolved read set carried on a filter-params model.

        Most reads here hand the params model straight to the repository, which
        reads `read_terms` off it through `_ProjectFilterParams`. Four do not --
        `list_sync_states`, `list_health_summaries`, `list_sources` and
        `search_log_events` take their filters as keywords -- and those four have
        to lift it out explicitly. This accessor is why they all lift it the same
        way instead of four call sites each deciding.

        `getattr` with a default rather than an attribute access: the inputs that
        reach these methods carry `RuntimeReadTermsMixin` today, but the provider
        contract types them as the model, not as the mixin, and an
        `AttributeError` from a read path is a worse failure than falling back to
        the single-`project_id` predicate the lane used before grants existed.
        An empty read set is the documented "nothing was resolved" case, not a
        denial -- the repository treats it as such.
        """

        terms = getattr(params, "read_terms", ())
        return terms or ()

    async def list_executions(
        self, params: ListExecutionsInput, context: RuntimeProviderContext | None = None
    ) -> list[RuntimeExecution]:
        repo = self._store_repository(context)
        return await repo.list_executions(self._scope(context), params)

    async def get_execution_details(
        self, params: GetExecutionDetailsInput, context: RuntimeProviderContext | None = None
    ) -> RuntimeExecution:
        repo = self._store_repository(context)
        try:
            return await repo.get_execution_details(self._scope(context), params)
        except RuntimeRecordNotFoundError as e:
            raise RuntimeProviderError(str(e)) from e

    async def detect_failures(
        self, params: DetectFailuresInput, context: RuntimeProviderContext | None = None
    ) -> list[RuntimeFailure]:
        repo = self._store_repository(context)
        return await repo.detect_failures(self._scope(context), params)

    async def get_execution_logs(
        self, params: GetExecutionLogsInput, context: RuntimeProviderContext | None = None
    ) -> list[RuntimeLogEvent]:
        repo = self._store_repository(context)
        try:
            return await repo.get_execution_logs(self._scope(context), params)
        except RuntimeRecordNotFoundError as e:
            raise RuntimeProviderError(str(e)) from e

    async def search_logs(
        self,
        params: Any,
        context: RuntimeProviderContext | None = None,
        *,
        read_terms: Sequence[RuntimeReadTerm] = (),
    ) -> list[dict[str, Any]]:
        """Search held runtime logs and expose only bounded safe search observations.

        The repository owns matching, ordering, and request scope. This adapter is
        deliberately conservative at the provider boundary: no database row ids,
        raw payloads, or unbounded messages can reach a runtime tool result.

        ``read_terms`` is the resolved read set, and it is an explicit KEYWORD
        rather than a field on ``params``. ``params`` is ``SearchLogsInput``, the
        model the runtime tool registry validates LLM-supplied arguments against;
        a ``read_terms`` field on it would put the read set inside a schema the
        model can write to, and a model-supplied read set is an authorization
        bypass. The caller resolves it server-side and passes it here instead.

        Passing a WIDENED ``context.project_scope`` without the matching terms
        returns the grantor's rows with the grant's environment and window
        narrowing not applied. Every caller must pass both; the default of ``()``
        exists only for the callers that never widen (`src/runtime/client.py`,
        which runs one project's tool call under that project's own scope).
        """
        repo = self._store_repository(context)
        try:
            rows = await repo.search_log_events(
                self._scope(context),
                query=params.question,
                project_id=params.project_id,
                execution_id=params.execution_id,
                since=params.since,
                until=params.until,
                limit=params.limit,
                snippet_chars=_LOG_SEARCH_SNIPPET_MAX_CHARS,
                read_terms=read_terms,
            )
        except RuntimeRecordNotFoundError as e:
            raise RuntimeProviderError(str(e)) from e
        except AttributeError as e:
            raise RuntimeProviderError("Stored log search is not available") from e
        return [_safe_log_search_row(row) for row in list(rows)[:_LOG_SEARCH_MAX_RESULTS]]

    async def analyze_execution_failure(
        self, params: AnalyzeExecutionFailureInput, context: RuntimeProviderContext | None = None
    ) -> RuntimeFailureAnalysis:
        repo = self._insight_repository(context)
        scope = self._scope(context)
        try:
            summary = await repo.get_execution_summary(scope, params.execution_id)
        except RuntimeRecordNotFoundError as e:
            raise RuntimeProviderError(str(e)) from e

        incident = None
        evidence = []
        if summary.has_incident and summary.incident_id:
            try:
                incident = await repo.get_incident(scope, summary.incident_id)
                evidence = await repo.get_incident_evidence(scope, summary.incident_id, limit=5)
            except RuntimeRecordNotFoundError:
                pass  # insight rows may be partially built; non-fatal

        return RuntimeFailureAnalysis(
            execution_id=summary.execution_id,
            job_name=summary.job_name,
            state=summary.state,
            started_at=summary.started_at,
            ended_at=summary.ended_at,
            duration_ms=summary.duration_ms,
            warning_count=summary.warning_count,
            error_count=summary.error_count,
            failed_component=summary.failed_component,
            primary_error_code=summary.primary_error_code,
            primary_exception_class=summary.primary_exception_class,
            summary_text=summary.summary_text,
            has_incident=summary.has_incident,
            incident_id=summary.incident_id,
            symptom_summary=incident.symptom_summary if incident else None,
            root_cause_candidate=incident.root_cause_candidate if incident else None,
            termination_component=incident.termination_component if incident else None,
            confidence=incident.confidence if incident else None,
            cause_origin=incident.cause_origin if incident else None,
            next_checks=incident.next_checks if incident else [],
            evidence=evidence,
            estate_context=summary.estate_context,
        )

    async def get_execution_timeline(
        self, params: GetExecutionTimelineInput, context: RuntimeProviderContext | None = None
    ) -> list[RuntimeTimelineEvent]:
        repo = self._insight_repository(context)
        scope = self._scope(context)
        try:
            events = await repo.get_execution_timeline(
                scope,
                params.execution_id,
                min_severity=params.min_severity.value if params.min_severity else None,
                limit=params.limit,
            )
        except RuntimeRecordNotFoundError as e:
            raise RuntimeProviderError(str(e)) from e
        # Cap signal length so timeline results cannot flood LLM context.
        return [
            event.model_copy(update={"signal": event.signal[:_TIMELINE_SIGNAL_MAX_CHARS]})
            if len(event.signal) > _TIMELINE_SIGNAL_MAX_CHARS
            else event
            for event in events
        ]

    async def get_runtime_incident(
        self, params: GetRuntimeIncidentInput, context: RuntimeProviderContext | None = None
    ) -> RuntimeIncidentDetail:
        repo = self._insight_repository(context)
        scope = self._scope(context)
        try:
            incident = await repo.get_incident(scope, params.incident_id)
        except RuntimeRecordNotFoundError as e:
            raise RuntimeProviderError(str(e)) from e
        evidence = []
        if params.include_evidence:
            try:
                evidence = await repo.get_incident_evidence(scope, params.incident_id, limit=params.evidence_limit)
            except RuntimeRecordNotFoundError:
                pass
        return RuntimeIncidentDetail(incident=incident, evidence=evidence)

    async def find_similar_failures(
        self, params: FindSimilarFailuresInput, context: RuntimeProviderContext | None = None
    ) -> RuntimeSimilarFailures:
        repo = self._insight_repository(context)
        scope = self._scope(context)
        incident_id = params.incident_id
        if incident_id is None and params.execution_id is not None:
            try:
                summary = await repo.get_execution_summary(scope, params.execution_id)
            except RuntimeRecordNotFoundError as e:
                raise RuntimeProviderError(str(e)) from e
            if not summary.has_incident or summary.incident_id is None:
                raise RuntimeProviderError(
                    f"No incident was derived for execution {params.execution_id}"
                )
            incident_id = summary.incident_id
        try:
            return await repo.get_similar_failures_for_incident(scope, incident_id, limit=params.limit)
        except RuntimeRecordNotFoundError as e:
            raise RuntimeProviderError(str(e)) from e

    async def list_runtime_sync_status(
        self, params: ListRuntimeSyncStatusInput, context: RuntimeProviderContext | None = None
    ) -> list[RuntimeSyncState]:
        repo = self._store_repository(context)
        return await repo.list_sync_states(
            self._scope(context),
            source_key=params.source_key,
            project_id=params.project_id,
            limit=params.limit,
            read_terms=self._read_terms(params),
        )

    async def list_runtime_health(
        self, params: ListRuntimeHealthInput, context: RuntimeProviderContext | None = None
    ) -> list[RuntimeHealthSummary]:
        repo = self._store_repository(context)
        return await repo.list_health_summaries(
            self._scope(context),
            source_key=params.source_key,
            project_id=params.project_id,
            limit=params.limit,
            read_terms=self._read_terms(params),
        )

    async def list_runtime_sources(
        self, params: ListRuntimeInventoryInput, context: RuntimeProviderContext | None = None
    ) -> list[RuntimeSource]:
        repo = self._store_repository(context)
        return await repo.list_sources(
            self._scope(context),
            source_key=params.source_key,
            project_id=params.project_id,
            limit=params.limit,
            read_terms=self._read_terms(params),
        )

    async def list_runtime_engines(
        self, params: ListRuntimeEnginesInput, context: RuntimeProviderContext | None = None
    ) -> list[RuntimeEngine]:
        repo = self._store_repository(context)
        return await repo.list_engines(self._scope(context), params)

    async def list_runtime_jobs(
        self, params: ListRuntimeJobsInput, context: RuntimeProviderContext | None = None
    ) -> list[RuntimeJob]:
        repo = self._store_repository(context)
        return await repo.list_jobs(self._scope(context), params)

    async def list_runtime_artifacts(
        self, params: ListRuntimeArtifactsInput, context: RuntimeProviderContext | None = None
    ) -> list[RuntimeArtifact]:
        repo = self._store_repository(context)
        return await repo.list_artifacts(self._scope(context), params)

    async def list_runtime_connections(
        self, params: ListRuntimeConnectionsInput, context: RuntimeProviderContext | None = None
    ) -> list[RuntimeConnection]:
        repo = self._store_repository(context)
        return await repo.list_connections(self._scope(context), params)

    async def list_runtime_dependencies(
        self, params: ListRuntimeDependenciesInput, context: RuntimeProviderContext | None = None
    ) -> list[RuntimeJobDependency]:
        repo = self._store_repository(context)
        return await repo.list_job_dependencies(self._scope(context), params)

    async def get_runtime_dependency_summary(
        self, params: ListRuntimeInventoryInput, context: RuntimeProviderContext | None = None
    ) -> RuntimeDependencySummary:
        repo = self._store_repository(context)
        return await repo.get_dependency_summary(self._scope(context), params)

    async def list_runtime_plans(
        self, params: ListRuntimePlansInput, context: RuntimeProviderContext | None = None
    ) -> list[RuntimePlan]:
        repo = self._store_repository(context)
        return await repo.list_plans(self._scope(context), params)

    async def get_runtime_outcome_trend(
        self, params: RuntimeTrendInput, context: RuntimeProviderContext | None = None
    ) -> list[RuntimeOutcomeTrendPoint]:
        repo = self._store_repository(context)
        bounded = params.model_copy(
            update={"limit": min(params.limit, _TREND_MAX_POINTS)}
        )
        return await repo.get_outcome_trend(self._scope(context), bounded)

    async def list_runtime_job_reliability(
        self,
        params: ListRuntimeJobReliabilityInput,
        context: RuntimeProviderContext | None = None,
    ) -> list[RuntimeJobReliability]:
        repo = self._store_repository(context)
        return await repo.list_job_reliability(self._scope(context), _bounded(params, "list_runtime_job_reliability"))

    async def list_runtime_job_schedule_profile(
        self,
        params: ListRuntimeJobScheduleProfileInput,
        context: RuntimeProviderContext | None = None,
    ) -> list[RuntimeJobScheduleProfile]:
        """Per-job local clock-time profile over a (optionally overnight) window.

        Unlike the neighbouring rollups this one converts timestamps into a named
        timezone, so a bad zone or an unsatisfiable offset table must not escape
        as a stdlib or SQLAlchemy exception: a runtime tool result has exactly one
        error type. The surrounding methods do NOT yet wrap `SQLAlchemyError`;
        lifting that into a shared wrapper for every read here is worth doing, but
        it changes the failure contract of twenty methods and belongs in its own
        change rather than riding along with this one.
        """
        repo = self._store_repository(context)
        try:
            return await repo.list_job_schedule_profile(self._scope(context), _bounded(params, "list_runtime_job_schedule_profile"))
        except (ZoneInfoNotFoundError, ValueError) as e:
            raise RuntimeProviderError(f"Unusable timezone {params.timezone!r}: {e}") from e
        except SQLAlchemyError as e:
            raise RuntimeProviderError(f"Schedule profile query failed: {e}") from e

    async def list_runtime_component_hotspots(
        self,
        params: ListRuntimeComponentHotspotsInput,
        context: RuntimeProviderContext | None = None,
    ) -> list[RuntimeComponentHotspot]:
        repo = self._store_repository(context)
        return await repo.list_component_hotspots(self._scope(context), _bounded(params, "list_runtime_component_hotspots"))

    async def list_runtime_job_throughput(
        self,
        params: ListRuntimeJobThroughputInput,
        context: RuntimeProviderContext | None = None,
    ) -> list[RuntimeJobThroughput]:
        repo = self._store_repository(context)
        return await repo.list_job_throughput(self._scope(context), _bounded(params, "list_runtime_job_throughput"))

    async def list_runtime_error_signatures(
        self,
        params: ListRuntimeErrorSignaturesInput,
        context: RuntimeProviderContext | None = None,
    ) -> list[RuntimeErrorSignature]:
        repo = self._store_repository(context)
        return await repo.list_error_signatures(self._scope(context), _bounded(params, "list_runtime_error_signatures"))

    async def list_runtime_unhealthy(
        self, params: ListRuntimeInventoryInput, context: RuntimeProviderContext | None = None
    ) -> RuntimeUnhealthySummary:
        """The one read that fans out into five, and therefore the one that can
        half-apply a grant.

        The four sub-inputs below are BUILT HERE rather than received, so the
        resolved read set on `params` has to be copied onto each of them by hand.
        Miss one and that sub-read runs under the widened `scope` with no grant
        limits -- a summary whose engine list obeys the grant and whose job list
        does not, which is worse than either alone because nothing in the result
        distinguishes them.
        """
        repo = self._store_repository(context)
        scope = self._scope(context)
        read_terms = self._read_terms(params)
        overview = await repo.get_estate_overview(scope, params)
        sources = await repo.list_sources(
            scope,
            source_key=params.source_key,
            project_id=params.project_id,
            limit=params.limit,
            read_terms=read_terms,
        )
        # `attention_only` is the repository's own predicate over run states.
        # Filtering here instead -- on the health model's `failed_runs`, which
        # counts FAILED and nothing else by design -- would drop every misfired
        # and timed-out run from the surface whose entire job is to surface them.
        engine_health = await repo.list_engine_health(
            scope,
            ListRuntimeEnginesInput(
                source_key=params.source_key,
                project_id=params.project_id,
                limit=params.limit,
                read_terms=list(read_terms),
            ),
            attention_only=True,
        )
        job_health = await repo.list_job_health(
            scope,
            ListRuntimeJobsInput(
                source_key=params.source_key,
                project_id=params.project_id,
                limit=params.limit,
                read_terms=list(read_terms),
            ),
            attention_only=True,
        )
        plan_health = await repo.list_plan_health(
            scope,
            ListRuntimePlansInput(
                source_key=params.source_key,
                project_id=params.project_id,
                limit=params.limit,
                read_terms=list(read_terms),
            ),
            attention_only=True,
        )
        return RuntimeUnhealthySummary(
            overview=overview,
            sources=sources,
            unhealthy_engines=engine_health[: params.limit],
            unhealthy_jobs=job_health[: params.limit],
            unhealthy_plans=plan_health[: params.limit],
        )


def aggregate_row_cap(provider_method: str) -> int:
    """The page size one rollup may return, by its public provider-method name.

    The single source of the number. `_bounded` enforces it here; the harness
    reads it to tell a caller whether a result is complete. Two copies of a cap
    is how a result gets clamped at one number and disclosed as another.
    """

    return _AGGREGATE_ROW_CAPS.get(provider_method, AGGREGATE_MAX_ROWS)


def _bounded(params: Any, provider_method: str) -> Any:
    """Clamp a rollup input's page size to what that rollup is allowed to return."""
    return params.model_copy(
        update={"limit": min(params.limit, aggregate_row_cap(provider_method))}
    )


def _safe_log_search_row(row: Any) -> dict[str, Any]:
    """Project a repository hit to the public runtime-tool contract."""
    if hasattr(row, "model_dump"):
        raw = row.model_dump(mode="json")
    elif isinstance(row, Mapping):
        raw = dict(row)
    else:
        raw = {}
    result = {field: raw[field] for field in _LOG_SEARCH_FIELDS if raw.get(field) is not None}
    text = result.pop("snippet", None) or result.pop("message", None) or ""
    result["snippet"] = redact_credentials(str(text))[:_LOG_SEARCH_SNIPPET_MAX_CHARS]
    return result


provider = StoredRuntimeProvider()
