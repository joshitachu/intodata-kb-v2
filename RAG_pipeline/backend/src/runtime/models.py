"""Runtime status and generic tool contract — provider-agnostic, no
Talend-specific fields or operation names."""

from __future__ import annotations

from datetime import datetime, time
from enum import Enum
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field, computed_field, field_validator, model_validator

from src.db.models.runtime_data import is_ownerless_estate_key
from src.runtime.read_scope import RuntimeReadTerm


class RuntimeConnectionStatus(str, Enum):
    DISABLED = "disabled"
    CONNECTED = "connected"
    UNAVAILABLE = "unavailable"


class RuntimeStatusResponse(BaseModel):
    status: RuntimeConnectionStatus
    provider_key: str | None = None
    provider_label: str
    message: str
    checked_at: datetime


class RuntimeSyncStatus(str, Enum):
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"


class RuntimeEngineStatus(str, Enum):
    ONLINE = "online"
    OFFLINE = "offline"
    DEGRADED = "degraded"
    UNKNOWN = "unknown"
    STALE = "stale"


class RuntimeExecutableKind(str, Enum):
    """Operational semantics of a TMC executable, independent of its API shape."""

    JOB = "job"
    ROUTE = "route"
    DATA_SERVICE = "data_service"
    UNKNOWN = "unknown"


class RuntimeExecutionState(str, Enum):
    """Outcome vocabulary shared by every runtime provider.

    A run that did not succeed is not necessarily a run that failed, and
    collapsing the distinctions loses the only fact that says what to do about
    it: a MISFIRED run never started (nothing ran, so no logs exist to
    diagnose), a TIMED_OUT run was killed by its own configured limit, and a
    TERMINATED run was stopped by something outside it. Providers that only
    report success/failure simply never emit the middle three.
    """

    SUCCESS = "success"
    FAILED = "failed"
    # Scheduled run that never started (e.g. the previous run was still
    # holding the executable, or the task was paused at trigger time).
    MISFIRED = "misfired"
    # Started, then killed by its configured timeout.
    TIMED_OUT = "timed_out"
    # Stopped before completing, by an operator or the platform.
    TERMINATED = "terminated"
    RUNNING = "running"
    UNKNOWN = "unknown"


class RuntimeOutcomeProvenance(BaseModel):
    """The provider's own account of an outcome, carried alongside the state.

    `RuntimeExecutionState` is a lossy projection by design -- six TMC statuses
    already collapse into it, and a seventh will appear without warning. Keeping
    the verbatim string means a status we mapped coarsely (or not at all) can
    still be counted, queried and re-mapped after the fact, instead of being
    unrecoverable the moment it is written.

    Mixed into every run-shaped model so the three fields cannot drift apart
    between job runs, executions and plan runs.
    """

    # The provider's status string exactly as sent -- never the normalised one.
    raw_state: str | None = None
    # Why a scheduled run never started (TMC: misfiredExecutionReason, e.g.
    # PARALLEL_EXECUTION_FORBIDDEN, TASK_IS_PAUSED). The provider explains every
    # misfire; discarding it leaves a non-run with no diagnosis attached.
    misfire_reason: str | None = None
    # What triggered the run: MANUAL | SCHEDULED | PLAN. Separates "someone ran
    # this by hand" from "the schedule fired", which changes what a failure means.
    execution_type: str | None = None


class RuntimeSyncState(BaseModel):
    source_key: str
    project_id: str
    status: RuntimeSyncStatus
    last_synced_at: datetime | None = None
    failure_reason_code: str | None = None
    error_summary: str | None = None
    updated_at: datetime


class RuntimeSource(BaseModel):
    """One source row as it appears in the MIRROR inventory.

    `estate_key` is the stamp its mirrored rows carry, NOT an owning project. It
    was called `project_id` for as long as one column answered both questions;
    the name went with the conflation, so a consumer that wants "which project
    owns this" has to ask `RuntimeSourceRead.linked_project_ids`, which is the
    only field that can answer it.
    """

    estate_key: str
    source_key: str
    source_type: str
    label: str
    status: RuntimeSyncStatus
    is_demo: bool = False
    last_synced_at: datetime | None = None
    last_seen_at: datetime | None = None
    ingested_at: datetime


class RuntimeSourceCredentialField(BaseModel):
    name: str
    type: str
    label: str | None = None
    required: bool = True


class RuntimeScopeSelection(BaseModel):
    """Describes a provider's discoverable ingest scope for the source dialog.

    Providers whose estate is partitioned (TMC environments, and any future
    connector with tenants/regions/folders) declare the partition here instead
    of the UI hardcoding a per-provider branch. ``field`` names the credential
    key that stores the selected ids, so the whole flow stays provider-neutral.
    """

    field: str
    label: str
    description: str | None = None
    required: bool = True
    multiple: bool = True


class RuntimeScopeOption(BaseModel):
    """One selectable ingest scope discovered from a live source."""

    id: str
    label: str
    detail: str | None = None
    default_selected: bool = False


class RuntimeSourceTypeInfo(BaseModel):
    provider_key: str
    provider_label: str
    credential_schema: list[RuntimeSourceCredentialField]
    scope_selection: RuntimeScopeSelection | None = None
    # Non-secret fields that are safe to edit without re-supplying the secret.
    # Excludes anything the physical identity is derived from, since changing
    # those would silently repoint the source at a different account.
    editable_config_fields: list[str] = Field(default_factory=list)


class RuntimeSourceHealth(BaseModel):
    last_attempt_at: datetime | None = None
    last_success_at: datetime | None = None
    last_failure_at: datetime | None = None
    consecutive_failures: int = 0
    last_failure_sanitized: str | None = None
    total_cycles: int = 0
    total_records_ingested: int = 0


class RuntimeSourceCreate(BaseModel):
    """A registration request, with or without a project to attach.

    `project_id` is OPTIONAL, and that is the whole behaviour change. Supplied,
    it does exactly what it always did: it becomes the source's `estate_key`, so
    every mirrored row carries it and the resolved read set collapses to the one
    bare equality this store has always emitted for a single-project source --
    the common path stays byte-identical. Omitted, the estate key is derived
    from the source key and the source is attached to NOBODY; projects attach
    afterwards through `POST /runtime/sources/{id}/readers`.

    Registering with no project is what makes a vendor tenant connectable before
    any project exists, which is the bootstrapping order operators actually
    work in. It is NOT a lighter-weight variant of the project path: because it
    attaches nothing, it cannot hand its caller access to any project's data.
    """

    project_id: str | None = None
    provider_key: str
    label: str
    credentials: dict[str, Any] = Field(default_factory=dict)
    # A TMC sync cycle fans out one windowed run-history fetch PER EXECUTABLE
    # (a whole tenant is ~240), so a short interval never completes before the
    # next one is due: three live sources on 60s ran 169-481s behind and pinned
    # a CPU core permanently, which starved the API sharing that event loop.
    # 30 minutes is well inside how often TMC run history actually changes.
    sync_interval_seconds: int = Field(default=1800, ge=30)


class RuntimeSourceUpdate(BaseModel):
    label: str | None = None
    enabled: bool | None = None
    sync_interval_seconds: int | None = Field(default=None, ge=30)


class RuntimeSourceRotateCredentials(BaseModel):
    credentials: dict[str, Any] = Field(default_factory=dict)


class RuntimeSourceConfigUpdate(BaseModel):
    """Partial update of the non-secret settings stored alongside credentials.

    Secrets and identity-bearing fields are rejected by the route; this exists
    so ingest scope, filters, and history windows can be changed without
    forcing an operator to re-type the API token.
    """

    config: dict[str, Any] = Field(default_factory=dict)


class RuntimeSourceScopeDiscovery(BaseModel):
    """Validate unsaved credentials and list the scopes they can reach."""

    provider_key: str
    credentials: dict[str, Any] = Field(default_factory=dict)


class RuntimeSourceRead(BaseModel):
    """One registered source config, credentials excluded.

    Three fields replace the single `project_id` this model used to carry,
    because that one field was answering three questions and could only ever be
    right about one of them:

    * `estate_key` -- the stamp this source writes onto every row it mirrors.
      For a source registered against a project it IS that project's id, which
      is why nothing had to be re-stamped; for one registered against no project
      it is a derived `estate:` key that no project id can collide with.
    * `linked_project_ids` -- every project attached to this source, sorted.
      The complete answer to "who may reach this", and the ONLY field that
      answers it. An empty list is a real state, not a gap: a tenant registered
      before any project has nobody attached yet.
    * `is_ownerless` -- whether `estate_key` names a project at all.

    `is_ownerless` is computed here rather than published as a stored field so
    that no producer can forget it and no client has to re-implement the prefix
    rule. A client that parsed `estate_key` itself would be duplicating a rule
    it cannot see change; one that reads this boolean cannot get it wrong.
    """

    id: int
    estate_key: str
    linked_project_ids: list[str] = Field(default_factory=list)
    source_key: str
    source_type: str
    label: str
    status: str
    enabled: bool
    sync_interval_seconds: int
    auto_disable_threshold: int
    disabled_reason: str | None = None
    is_demo: bool = False
    last_synced_at: datetime | None = None
    last_seen_at: datetime | None = None
    ingested_at: datetime
    health: RuntimeSourceHealth | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_ownerless(self) -> bool:
        """True when this source's estate belongs to no project.

        The single interpretation of the estate-key prefix on the wire. See
        `src.db.models.runtime_data.is_ownerless_estate_key`, which is also what
        the store's SQL predicate mirrors, so the API and the query can never
        disagree about one row.
        """
        return is_ownerless_estate_key(self.estate_key)


class RuntimeWorkspaceProjectMap(BaseModel):
    id: int
    source_id: int
    container_key: str
    container_name: str | None = None
    project_id: str
    created_at: datetime
    created_by: str | None = None


class RuntimeWorkspaceProjectMapCreate(BaseModel):
    container_key: str
    container_name: str | None = None
    project_id: str


class RuntimeSourceReadGrant(BaseModel):
    """One project ATTACHED to a runtime source's estate.

    Distinct from `RuntimeWorkspaceProjectMap`, which SPLITS a source's rows
    between projects at write time. An attachment SHARES the same rows with
    another reader. Presenting them as one control is how an operator picks the
    wrong one.

    Every project that may read the source is one of these rows -- there is no
    owner with an implied one.
    """

    id: int
    source_id: int
    # The attached project.
    project_id: str
    # The stamp the source mirrors into, which is NOT necessarily a project:
    # for a source registered against one it is that project's id, and for a
    # source registered against none it is a derived `estate:` key. It was
    # published as `source_project_id` while those two were the same field.
    source_estate_key: str
    source_label: str
    # None means every mirrored environment; [] is a deliberate "nothing".
    environment_ids: list[str] | None = None
    window_days: int | None = None
    # Every row stamp this attachment exposes: the source's estate key plus any
    # project its workspace map re-points rows to. Returned so an attachment
    # can never expose a third project's data without saying so.
    exposes_project_ids: list[str] = Field(default_factory=list)
    created_at: datetime
    created_by: str | None = None


class RuntimeSourceReadGrantCreate(BaseModel):
    project_id: str
    environment_ids: list[str] | None = None
    window_days: int | None = Field(default=None, ge=1, le=365)


class RuntimeEnvironment(BaseModel):
    environment_id: str
    source_key: str
    name: str
    description: str | None = None
    last_seen_at: datetime | None = None


class RuntimeMirroredEnvironment(BaseModel):
    """An environment actually present in a project's mirror.

    The documentation read-scope picker offers these and only these. Offering
    the provider's live environment list instead would let an operator select an
    environment that was never ingested, which yields empty evidence with no
    visible cause -- the failure mode this model exists to prevent. `run_count`
    is shown so the choice is informed rather than nominal.
    """

    environment_id: str
    name: str
    run_count: int = 0
    last_run_at: datetime | None = None


class RuntimeDocReadScope(BaseModel):
    """What a documentation run for one project is allowed to read.

    Distinct from ingest scope: this narrows what is read OUT of the mirror,
    costs nothing at the vendor API, and can change without re-mirroring.

    Two filters, and only one of them is flat
    -----------------------------------------
    The fields below are the FLAT scope: every configured source's environment
    selection unioned, and the widest window. That is the right rule for a
    project's OWN sources -- one source may only ever narrow its own
    contribution, so an intersection would let one source silently suppress
    another's data -- and it is the wrong rule for a read grant, whose limits
    are a boundary the GRANTOR set on THEIR estate. Applying a grant's limits
    flatly either leaks the grantor's excluded rows (union) or hides the
    reader's own (intersection).

    So a grant's own limits do not appear in `environment_ids` or `window_days`
    at all: they are applied per project, on top of these fields, by
    `RuntimeStoreRepository._apply_read_terms`, and `grant_limits` is what
    records them. A consumer reading only the flat fields would believe the
    granted projects were read as widely as its own.
    """

    # None means every mirrored environment. A list is a deliberate narrowing.
    environment_ids: list[str] | None = None
    # Display names for the selected ids, resolved against the mirror. Empty
    # when the selection is unresolvable, which is reported, never guessed.
    environment_names: list[str] = Field(default_factory=list)
    window_days: int | None = None
    # True when an operator set either field. Distinguishes "defaults" from
    # "deliberately chose the defaults", which matters in the coverage record.
    configured: bool = False
    # Selected ids with nothing in the mirror -- an ingest scope that no longer
    # covers them, or an environment retired at the vendor. Surfaced rather than
    # dropped: silently reading less than requested is the worse failure.
    unavailable_environment_ids: list[str] = Field(default_factory=list)
    # True when this project read a mirror it does not own, via a read grant.
    # A shared read is not comparable to an owned one -- the runtime job
    # vocabulary belongs to the whole tenant, not to this project -- so the
    # published document has to be able to say which it was.
    shared_read: bool = False
    # Labels of the granting sources, for the coverage record. Labels, not
    # project ids: the reader may have no permission to see the owner's project.
    shared_from: list[str] = Field(default_factory=list)
    # The project ids this read was actually scoped to: this project plus every
    # project the granted sources stamp their rows with.
    read_project_ids: list[str] = Field(default_factory=list)
    # The projects in `read_project_ids` whose contribution a read grant bounded
    # more tightly than the flat fields above describe, with the bound. Only
    # NARROWED terms appear: an unrestricted grant takes nothing out, and a
    # limitation that is not true of this read would be its own defect. `since`
    # is absolute rather than a day count so it compares directly against the
    # response's own `since` -- the reader's history starts at one instant and a
    # granted project's at another, and a document that quotes one window for
    # both is wrong about half its evidence.
    grant_limits: list[RuntimeReadTerm] = Field(default_factory=list)


class RuntimeWorkspace(BaseModel):
    workspace_id: str
    source_key: str
    name: str
    environment_id: str | None = None
    environment_name: str | None = None
    owner: str | None = None
    workspace_type: str | None = None
    last_seen_at: datetime | None = None


class RuntimeEngine(BaseModel):
    engine_id: str
    source_key: str
    name: str
    status: RuntimeEngineStatus
    raw_status: str | None = None
    availability: str | None = None
    version: str | None = None
    environment_id: str | None = None
    environment_name: str | None = None
    workspace_id: str | None = None
    workspace_name: str | None = None
    last_seen_at: datetime | None = None
    source_updated_at: datetime | None = None


class RuntimeJob(BaseModel):
    job_id: str
    source_key: str
    name: str
    display_name: str | None = None
    job_type: str | None = None
    executable_kind: RuntimeExecutableKind = RuntimeExecutableKind.JOB
    status: str | None = None
    environment_id: str | None = None
    environment_name: str | None = None
    workspace_id: str | None = None
    workspace_name: str | None = None
    engine_id: str | None = None
    engine_name: str | None = None
    owner: str | None = None
    artifact_name: str | None = None
    artifact_id: str | None = None
    artifact_version: str | None = None
    metric_path: str | None = None
    last_seen_at: datetime | None = None
    source_updated_at: datetime | None = None


class RuntimeArtifact(BaseModel):
    artifact_id: str
    source_key: str
    name: str
    artifact_type: str | None = None
    version: str | None = None
    workspace_id: str | None = None
    workspace_name: str | None = None
    owner: str | None = None
    last_seen_at: datetime | None = None
    source_updated_at: datetime | None = None


class RuntimeConnection(BaseModel):
    connection_id: str
    source_key: str
    name: str
    connection_type: str | None = None
    workspace_id: str | None = None
    workspace_name: str | None = None
    owner: str | None = None
    last_seen_at: datetime | None = None
    source_updated_at: datetime | None = None


class RuntimeJobDependency(BaseModel):
    job_id: str
    job_name: str
    source_key: str
    dependency_type: str
    dependency_id: str
    dependency_name: str | None = None
    dependency_kind: str | None = None
    workspace_id: str | None = None
    workspace_name: str | None = None
    last_seen_at: datetime | None = None


class RuntimeExtendedMetadataAvailability(BaseModel):
    artifacts_supported: bool = False
    connections_supported: bool = False
    schedules_supported: bool = False
    promotions_supported: bool = False
    clusters_supported: bool = False
    unavailable: list[str] = Field(default_factory=list)


class RuntimeJobRun(RuntimeOutcomeProvenance):
    run_id: str
    job_id: str
    job_name: str
    source_key: str
    execution_id: str | None = None
    state: RuntimeExecutionState
    executable_kind: RuntimeExecutableKind = RuntimeExecutableKind.JOB
    environment_id: str | None = None
    environment_name: str | None = None
    workspace_id: str | None = None
    workspace_name: str | None = None
    engine_id: str | None = None
    engine_name: str | None = None
    started_at: datetime
    ended_at: datetime | None = None
    duration_ms: int | None = None
    completed_exchanges: int | None = None
    failed_exchanges: int | None = None
    total_exchanges: int | None = None
    last_process_time_ms: int | None = None
    mean_process_time_ms: int | None = None
    metrics_updated_at: datetime | None = None
    metric_path: str | None = None
    service_call_count: int | None = None
    one_minute_rate: float | None = None
    five_minute_rate: float | None = None
    fifteen_minute_rate: float | None = None


class RuntimeDeployment(BaseModel):
    """Latest observed deployment lifecycle for a Route or Data Service."""

    project_id: str
    source_key: str
    deployment_id: str | None = None
    executable_id: str
    executable_name: str
    executable_kind: RuntimeExecutableKind
    state: RuntimeExecutionState | None = None
    environment_name: str | None = None
    workspace_name: str | None = None
    engine_name: str | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    duration_ms: int | None = None
    completed_exchanges: int | None = None
    failed_exchanges: int | None = None
    total_exchanges: int | None = None
    last_process_time_ms: int | None = None
    mean_process_time_ms: int | None = None
    metrics_updated_at: datetime | None = None
    metric_path: str | None = None
    service_call_count: int | None = None
    one_minute_rate: float | None = None
    five_minute_rate: float | None = None
    fifteen_minute_rate: float | None = None


class RuntimeJobHealth(BaseModel):
    """Health of one mirrored runtime executable, whatever kind it is.

    The three additive fields exist because this surface used to answer only for
    `job`-kind rows and said nothing about it. `executable_kind` states what the
    row is; `liveness` states what ONE of its rows MEANS, taken from the
    provider's own `executable_semantics()` declaration and never guessed -- an
    `execution` row is a run that started and ended, a `lifecycle` row is a
    deployment state, so `state != success` counts a failure on the first and a
    healthy deployed route on the second. `unknown` is a real answer, not a
    fallback: a provider that declares nothing for a kind gets it, because
    defaulting to `execution` is exactly what makes a live route look failed.

    `artifact_name` is the design name the provider's inventory row carries, so
    a consumer can join a runtime row to a scanned design by exact equality
    instead of pattern-matching `job_name`. It is None when the inventory row
    has no artifact (Elastic) or when there is no inventory row at all.
    """

    project_id: str
    source_key: str
    job_id: str
    job_name: str
    artifact_name: str | None = None
    executable_kind: str | None = None
    liveness: Literal["execution", "lifecycle", "unknown"] = "unknown"
    environment_name: str | None = None
    workspace_name: str | None = None
    engine_name: str | None = None
    total_runs: int
    failed_runs: int
    failure_rate: float
    current_failure_streak: int
    latest_run_at: datetime | None = None
    latest_state: RuntimeExecutionState | None = None


class RuntimeEngineHealth(BaseModel):
    project_id: str
    source_key: str
    engine_id: str
    engine_name: str
    engine_status: RuntimeEngineStatus | None = None
    environment_name: str | None = None
    workspace_name: str | None = None
    total_runs: int
    failed_runs: int
    failure_rate: float
    current_failure_streak: int
    latest_run_at: datetime | None = None
    latest_state: RuntimeExecutionState | None = None
    p50_duration_ms: int | None = None
    p95_duration_ms: int | None = None


class RuntimePlanStep(BaseModel):
    step_id: str
    name: str
    job_id: str | None = None
    job_name: str | None = None
    order: int | None = None


class RuntimePlan(BaseModel):
    plan_id: str
    source_key: str
    name: str
    display_name: str | None = None
    status: str | None = None
    environment_id: str | None = None
    environment_name: str | None = None
    workspace_id: str | None = None
    workspace_name: str | None = None
    owner: str | None = None
    step_count: int | None = None
    steps: list[RuntimePlanStep] = Field(default_factory=list)
    last_seen_at: datetime | None = None
    source_updated_at: datetime | None = None


class RuntimePlanRunStepResult(BaseModel):
    step_id: str
    job_id: str | None = None
    job_name: str | None = None
    state: RuntimeExecutionState | None = None
    execution_id: str | None = None


class RuntimePlanRun(RuntimeOutcomeProvenance):
    run_id: str
    plan_id: str
    plan_name: str
    source_key: str
    state: RuntimeExecutionState
    environment_id: str | None = None
    environment_name: str | None = None
    workspace_id: str | None = None
    workspace_name: str | None = None
    started_at: datetime
    ended_at: datetime | None = None
    duration_ms: int | None = None
    step_results: list[RuntimePlanRunStepResult] = Field(default_factory=list)


class RuntimePlanHealth(BaseModel):
    project_id: str
    source_key: str
    plan_id: str
    plan_name: str
    environment_name: str | None = None
    workspace_name: str | None = None
    total_runs: int
    failed_runs: int
    failure_rate: float
    current_failure_streak: int
    latest_run_at: datetime | None = None
    latest_finish_at: datetime | None = None
    latest_state: RuntimeExecutionState | None = None


class RuntimeEstateOverview(BaseModel):
    project_id: str | None = None
    sources_total: int
    sources_failed: int
    sources_partial: int
    sources_demo: int
    engines_total: int
    engines_online: int
    engines_degraded: int
    engines_offline: int
    engines_unknown: int
    engines_unhealthy: int = 0
    jobs_unhealthy: int = 0
    plans_unhealthy: int = 0
    sources_stale: int = 0
    environments_total: int
    workspaces_total: int
    last_synced_at: datetime | None = None


class RuntimeUnhealthySummary(BaseModel):
    """Compact runtime estate health summary for chat/runtime tool answers.

    It contains only DB-derived inventory and health facts. Raw runtime log rows
    and source-platform payloads are intentionally excluded.
    """

    overview: RuntimeEstateOverview
    sources: list[RuntimeSource] = Field(default_factory=list)
    unhealthy_engines: list[RuntimeEngineHealth] = Field(default_factory=list)
    unhealthy_jobs: list[RuntimeJobHealth] = Field(default_factory=list)
    unhealthy_plans: list[RuntimePlanHealth] = Field(default_factory=list)


class RuntimeDependencySummary(BaseModel):
    availability: RuntimeExtendedMetadataAvailability
    artifacts_total: int
    connections_total: int
    dependencies_total: int
    stale_artifacts: list[RuntimeArtifact] = Field(default_factory=list)
    unowned_artifacts: list[RuntimeArtifact] = Field(default_factory=list)
    unowned_connections: list[RuntimeConnection] = Field(default_factory=list)


class RuntimeComponentMetric(BaseModel):
    execution_id: str
    component_key: str
    component_type: str | None = None
    source_connector_type: str | None = None
    target_connector_type: str | None = None
    rows_total: int | None = None
    rows_rejected: int | None = None
    duration_ms: int | None = None
    measured_at: datetime | None = None


class RuntimeHealthSummary(BaseModel):
    project_id: str
    source_key: str | None = None
    job_name: str
    total_executions: int
    failed_executions: int
    failure_rate: float
    current_failure_streak: int
    last_failure_at: datetime | None = None
    last_execution_at: datetime | None = None
    p50_duration_ms: int | None = None
    p95_duration_ms: int | None = None
    rows_total: int | None = None
    rows_rejected: int | None = None
    reject_ratio: float | None = None
    recurrence_count: int | None = None
    signature_hash: str | None = None


# --- Aggregate rollups (SQL-computed) ---
#
# These are derived views over run/metric facts that previously had no read path.
# Duration percentiles are discrete (nearest observed value at the ceil-ranked
# position), matching the semantics used elsewhere in this module, so a reported
# percentile is always a real measured duration rather than an interpolation.


class RuntimeTrendBucket(str, Enum):
    HOUR = "hour"
    DAY = "day"
    WEEK = "week"


class RuntimeOutcomeTrendPoint(BaseModel):
    """One time bucket of run outcomes plus duration percentiles."""

    bucket_start: datetime
    total_runs: int
    success_runs: int
    failed_runs: int
    running_runs: int
    unknown_runs: int
    # Non-success outcomes that are not failures. Default 0 so a provider that
    # cannot distinguish them reports "none observed" rather than a gap, and so
    # success + failed + these + running + unknown accounts for total_runs.
    misfired_runs: int = 0
    terminated_runs: int = 0
    timed_out_runs: int = 0
    failure_rate: float
    p50_duration_ms: int | None = None
    p95_duration_ms: int | None = None
    max_duration_ms: int | None = None


class RuntimeTrendCoverage(BaseModel):
    """Observed execution bounds; missing buckets are not known-complete windows."""

    observed_from: datetime | None = None
    observed_until: datetime | None = None
    completeness: Literal["unknown"] = "unknown"


class RuntimeJobReliability(BaseModel):
    """Per-job reliability and latency rollup over the requested window."""

    project_id: str
    source_key: str
    job_name: str
    total_runs: int
    success_runs: int
    failed_runs: int
    unfinished_runs: int
    # See RuntimeOutcomeTrendPoint: non-success outcomes that are not failures.
    misfired_runs: int = 0
    terminated_runs: int = 0
    timed_out_runs: int = 0
    failure_rate: float
    current_failure_streak: int
    first_run_at: datetime | None = None
    last_run_at: datetime | None = None
    latest_state: RuntimeExecutionState | None = None
    # The latest observed run has no end time, excluding normally-running work.
    # This is intentionally a current-state signal, unlike `unfinished_runs`,
    # which is a historical count over the requested window.
    latest_unfinished: bool = False
    p50_duration_ms: int | None = None
    p95_duration_ms: int | None = None
    max_duration_ms: int | None = None


class RuntimeJobScheduleProfile(BaseModel):
    """Per-job clock-time profile: WHEN a job runs, pooled by local time of day.

    Distinct from `RuntimeOutcomeTrendPoint`, which buckets chronologically along
    the timeline ("22:00 on 3 September"). This pools every run in the window onto
    a single 24-hour clock across many days, so "this job starts at 23:31 every
    night" becomes a fact rather than 90 separate observations.

    Talend job schedules live in the management console, not in the scanned
    design, so the observed start times ARE the schedule evidence -- there is no
    cron expression to read anywhere in the codebase.

    All clock times are local to `timezone` and DST-correct: a run at 23:31 CET
    and one at 23:31 CEST both report 23:31, not an hour apart.
    """

    project_id: str
    source_key: str
    job_name: str
    # IANA zone the clock times below are expressed in. Echoed back because
    # "02:15" means nothing without it.
    timezone: str
    # The requested time-of-day window, echoed back. start > end means the window
    # wrapped midnight (22 -> 7 is the nightly batch window); start == end means
    # the full 24 hours anchored at that hour.
    window_start_hour: int
    window_end_hour: int
    total_runs: int
    # Distinct occurrences of the window the job ran in, anchored at
    # `window_start_hour` -- so a 22:00-07:00 window counts NIGHTS, not calendar
    # dates. Counting calendar dates would split one night across two and report
    # a nightly job as running twice as often as it does.
    distinct_days: int
    # Discrete median start: an actually observed start time, never an
    # interpolation (see the percentile note above). Computed on a
    # window-relative axis so a window spanning midnight does not put the median
    # of {23:50, 00:10} at midday.
    median_start_time: time
    # Earliest/latest measured along the same window-relative axis, so for a
    # 22:00-07:00 window the earliest is 22:05 rather than 00:02.
    earliest_start_time: time
    latest_start_time: time
    p50_duration_ms: int | None = None
    # Runs in the window whose state is anything other than success. Deliberately
    # broader than `failed_runs` elsewhere: for a schedule question "the nightly
    # load did not complete" matters whether it failed, misfired or timed out.
    non_success_runs: int = 0


class RuntimeDocEvidenceFailure(BaseModel):
    """One redacted failure headline, with how often that failure recurred.

    Distinct failure MODES, not individual incidents: a job that fails the same
    way 700 times has one story to tell, and listing near-identical lines would
    crowd out the rarer failures that are usually the interesting ones.
    """

    failed_at: datetime
    error_summary: str
    occurrences: int = 1


class RuntimeDocIncidentInsight(BaseModel):
    """One recurring, evidence-backed incident safe for documentation prompts.

    Low-confidence and generic placeholder incidents are deliberately excluded
    by the repository.  This model is for diagnoses the runtime logs actually
    support, not for the fact that an execution failed.
    """

    pattern_id: str
    confidence: Literal["high", "medium"]
    occurrences: int
    most_recent_at: datetime
    failed_component: str | None = None
    termination_component: str | None = None
    error_code: str | None = None
    exception_class: str | None = None
    causal_message: str | None = None
    root_cause_candidate: str | None = None
    cause_origin: str | None = None
    next_checks: list[str] = Field(default_factory=list)


class RuntimeJobOperationalInsight(BaseModel):
    """Bounded, provider-neutral operational story for one runtime job.

    This intentionally contains aggregates and one dominant diagnosis only. Raw
    logs and per-execution records belong to drill-down endpoints, not this card.
    """

    project_id: str
    source_key: str
    job_name: str
    total_runs: int
    success_runs: int
    failed_runs: int
    unfinished_runs: int
    failure_rate: float
    first_run_at: datetime | None = None
    last_run_at: datetime | None = None
    failures_with_logs: int = 0
    diagnosed_failures: int = 0
    capture_ceiling_reached: int = 0
    evidence_completeness: Literal[
        "no_failures", "complete", "partial", "logs_only", "missing"
    ]
    pattern_id: str | None = None
    confidence: Literal["high", "medium"] | None = None
    occurrences: int = 0
    most_recent_at: datetime | None = None
    failed_component: str | None = None
    termination_component: str | None = None
    causal_message: str | None = None
    root_cause_candidate: str | None = None
    cause_origin: str | None = None
    next_checks: list[str] = Field(default_factory=list)


class RuntimeDocLogQuality(BaseModel):
    """How much of a unit's failed-execution population logs can explain."""

    failed_executions: int = 0
    executions_with_logs: int = 0
    diagnosed_executions: int = 0
    capture_ceiling_reached: int = 0


class RuntimeDocEvidence(BaseModel):
    """Runtime execution profile for ONE scanned unit, safe for LLM context.

    Aggregates only.  Raw log lines never appear here: a document needs to say
    how often a job runs and how it fails, not to replay its output, and the
    narrower payload keeps the redaction surface to `error_summary` alone.
    """

    unit_name: str
    job_name: str
    source_key: str
    resolved_by: Literal["scan_map", "exact_name"]
    environments: list[str] = Field(default_factory=list)
    total_runs: int
    success_runs: int
    failed_runs: int
    unfinished_runs: int
    failure_rate: float
    current_failure_streak: int
    first_run_at: datetime | None = None
    last_run_at: datetime | None = None
    runs_per_day: float | None = None
    # Latency over SUCCESSFUL, completed runs only. Hung and abandoned
    # executions keep a duration and would otherwise dominate the percentiles
    # (verified: a 13-day p95 in werkorder), which is not what "how long does
    # this job take" means to a reader.
    p50_duration_ms: int | None = None
    p95_duration_ms: int | None = None
    max_duration_ms: int | None = None
    # Inter-run gap quantiles, in seconds, over successive run STARTS.  These
    # exist so a consumer can classify an OBSERVED CADENCE ("runs every 15
    # minutes") from the median gap and the spread, which `runs_per_day` cannot
    # support: it is a mean over the whole window, and a mean gap is dragged by
    # outages and backfills.  Verified in werkorder --
    # `Maximo2RHM_Workorders` has a mean gap of 2204 s against a median of
    # 1801 s, so the mean would publish a 37-minute schedule nobody configured.
    #
    # SINGLE-ENVIRONMENT BY CONSTRUCTION: the quantiles describe the job's
    # dominant (highest-volume) environment, which is `environments[0]`.  A
    # name can span DEV/TEST/PROD (`runtime_jobs` is unique on
    # `(project_id, source_key, job_id)`, not on name), and gaps pooled across
    # environments blend an hourly PROD schedule with ad-hoc DEV runs -- an
    # inflated spread that reads as "no schedule" for exactly the jobs whose
    # schedule is worth publishing.  A reader that reports the interval MUST
    # qualify it with `environments[0]` when `len(environments) > 1`.
    #
    # None whenever the dominant environment has fewer than two runs in the
    # window: one run is a sighting, not a cadence.
    gap_p25_seconds: int | None = None
    gap_p50_seconds: int | None = None
    gap_p75_seconds: int | None = None
    # Number of GAPS the quantiles were computed from (runs - 1), not the run
    # count -- a cadence rule with an `n >= 20` floor is counting intervals.
    gap_sample_size: int | None = None
    plans: list[str] = Field(default_factory=list)
    recent_failures: list[RuntimeDocEvidenceFailure] = Field(default_factory=list)
    log_quality: RuntimeDocLogQuality = Field(default_factory=RuntimeDocLogQuality)
    incident_insights: list[RuntimeDocIncidentInsight] = Field(default_factory=list)


class RuntimeComponentHotspot(BaseModel):
    """Per-component throughput and latency rollup across executions."""

    project_id: str
    source_key: str
    component_key: str
    component_type: str | None = None
    source_connector_type: str | None = None
    target_connector_type: str | None = None
    executions: int
    rows_total: int | None = None
    rows_rejected: int | None = None
    reject_ratio: float | None = None
    avg_duration_ms: int | None = None
    p95_duration_ms: int | None = None
    max_duration_ms: int | None = None
    last_measured_at: datetime | None = None


class RuntimeJobThroughput(BaseModel):
    """Rows moved per job, joined from run facts to component metrics."""

    project_id: str
    source_key: str
    job_name: str
    executions_with_metrics: int
    component_count: int
    rows_total: int | None = None
    rows_rejected: int | None = None
    reject_ratio: float | None = None
    last_measured_at: datetime | None = None


class RuntimeErrorSignature(BaseModel):
    """A recurring fault fingerprint with its observed occurrence window."""

    project_id: str
    signature_hash: str
    error_code: str | None = None
    exception_class: str | None = None
    component_name: str | None = None
    summary_text: str | None = None
    # A representative diagnostic sentence from the runtime itself,
    # credential-redacted. summary_text is only "<code> / <exception> / <component>",
    # so without this the signature carries no human-readable fault description.
    causal_message: str | None = None
    occurrence_count: int
    first_seen_at: datetime | None = None
    last_seen_at: datetime | None = None


# --- Normalized execution/log models (EP-004 slice 2) ---


class RuntimeLogSeverity(str, Enum):
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class RuntimeExecution(RuntimeOutcomeProvenance):
    execution_id: str
    job_name: str
    project_name: str | None = None
    state: RuntimeExecutionState
    executable_kind: RuntimeExecutableKind = RuntimeExecutableKind.JOB
    started_at: datetime
    ended_at: datetime | None = None
    duration_ms: int | None = None


class RuntimeFailure(BaseModel):
    execution_id: str
    job_name: str
    failed_at: datetime
    error_summary: str
    error_detail: str | None = None


class RuntimeLogEvent(BaseModel):
    execution_id: str
    timestamp: datetime
    severity: RuntimeLogSeverity
    message: str
    # Position is part of a line's identity: repeated messages are valid log data.
    line_ordinal: int = Field(default=0, ge=0)
    # TMC Route/Data Service exports are OSGi Runtime scoped and can contain
    # lines from sibling deployments. Keep the evidence, but do not attribute
    # it to one Route or use it to synthesize an incident.
    scope: Literal["execution", "engine"] = "execution"
    attribution_safe: bool = True


# --- Generic tool input models ---


class RuntimeReadTermsMixin(BaseModel):
    """The resolved read set for one request, attached to its filter params.

    Server-resolved only: `resolve_runtime_read_scope` is the sole producer, and
    nothing derived from request input may populate it. Empty means "no read set
    was resolved", and every consumer then falls back to the single `project_id`
    predicate the lane used before read grants existed.

    A MIXIN rather than a base class that also declares `project_id`: the inputs
    that carry one disagree about whether it is required, and the execution
    family carries none at all (it is scoped by `execution_id` and the caller's
    RBAC scope). A shared base would have forced them to agree and changed the
    contract of models that are reachable from an LLM tool call.
    """

    read_terms: list[RuntimeReadTerm] = Field(default_factory=list)


class ListExecutionsInput(RuntimeReadTermsMixin):
    project_name: str | None = None
    job_name: str | None = None
    state: RuntimeExecutionState | None = None
    # The generic Runs surface is batch semantics. Long-running Route/Data
    # Service lifecycles are exposed by the deployments endpoint instead.
    executable_kind: RuntimeExecutableKind = RuntimeExecutableKind.JOB
    since: datetime | None = None
    until: datetime | None = None
    # Duration/completion filters surface implausibly long and never-completed runs,
    # which a state filter alone cannot find: a run stuck for days is still reported
    # by the source as "success" or "unknown".
    min_duration_ms: int | None = Field(default=None, ge=0)
    unfinished: bool | None = None
    limit: int = Field(default=20, ge=1, le=200)
    offset: int = Field(default=0, ge=0)


class GetExecutionDetailsInput(RuntimeReadTermsMixin):
    execution_id: str


class DetectFailuresInput(RuntimeReadTermsMixin):
    since: datetime | None = None
    until: datetime | None = None
    limit: int = Field(default=20, ge=1, le=200)
    offset: int = Field(default=0, ge=0)


class GetExecutionLogsInput(RuntimeReadTermsMixin):
    execution_id: str
    limit: int = Field(default=100, ge=1, le=10_000)
    min_severity: RuntimeLogSeverity | None = None
    offset: int = Field(default=0, ge=0)


class AnalyzeExecutionFailureInput(BaseModel):
    execution_id: str


class GetExecutionTimelineInput(BaseModel):
    execution_id: str
    min_severity: RuntimeLogSeverity | None = None
    limit: int = Field(default=50, ge=1, le=200)


class GetRuntimeIncidentInput(BaseModel):
    incident_id: str
    include_evidence: bool = True
    evidence_limit: int = Field(default=5, ge=1, le=20)


class FindSimilarFailuresInput(BaseModel):
    incident_id: str | None = None
    execution_id: str | None = None
    limit: int = Field(default=10, ge=1, le=50)

    @model_validator(mode="after")
    def check_one_provided(self) -> "FindSimilarFailuresInput":
        if self.incident_id is None and self.execution_id is None:
            raise ValueError("exactly one of incident_id or execution_id is required")
        if self.incident_id is not None and self.execution_id is not None:
            raise ValueError("provide exactly one of incident_id or execution_id, not both")
        return self


class ListRuntimeSyncStatusInput(RuntimeReadTermsMixin):
    source_key: str | None = None
    project_id: str | None = None
    limit: int = Field(default=20, ge=1, le=100)


class ListRuntimeHealthInput(RuntimeReadTermsMixin):
    source_key: str | None = None
    project_id: str | None = None
    limit: int = Field(default=10, ge=1, le=100)


class ListRuntimeInventoryInput(RuntimeReadTermsMixin):
    source_key: str | None = None
    project_id: str | None = None
    limit: int = Field(default=100, ge=1, le=500)
    offset: int = Field(default=0, ge=0)


class RuntimeTrendInput(ListRuntimeInventoryInput):
    """Time-bucketed outcome trend over run facts."""

    bucket: RuntimeTrendBucket = RuntimeTrendBucket.DAY
    job_name: str | None = None
    since: datetime | None = None
    until: datetime | None = None
    # A trend is a bounded series, not a page of rows: the cap is the number of
    # buckets returned (most recent first).
    limit: int = Field(default=90, ge=1, le=400)


class ListRuntimeJobReliabilityInput(ListRuntimeInventoryInput):
    job_name: str | None = None
    since: datetime | None = None
    until: datetime | None = None
    # A resolved SHARED read set, which takes precedence over `project_id` when
    # set. Only the documentation lane sets it -- the operations console reads
    # one project at a time, and widening its rollups would mean a page headed
    # with one project's name showing another's jobs.
    project_ids: list[str] | None = None
    # Raised from the inherited 500 for shared reads only: the documentation
    # lane needs the WHOLE runtime vocabulary of every project in the read set
    # (truncating it turns a mirrored job into "no runtime data" with nothing
    # to show that it was cut), and it scales that ceiling with the set size.
    # The user-facing endpoint keeps its own `Query(le=500)`, so this does not
    # widen anything a caller can ask for.
    limit: int = Field(default=100, ge=1, le=5000)
    # Restrict the rollup to runs observed in these environment ids. None means
    # every mirrored environment, which is the historical behaviour and stays
    # the default -- an empty list would be a different claim ("no environment")
    # and is rejected by the caller rather than silently treated as "all".
    environment_ids: list[str] | None = None
    # Only jobs whose latest observed run still needs intervention: it failed,
    # or it has no end time and is not still running.
    attention_only: bool = False


class ListRuntimeJobScheduleProfileInput(ListRuntimeInventoryInput):
    """Pool runs by local time of day to answer "when does this job run?".

    The default timezone is `Europe/Amsterdam`, the operating zone of the
    deployments this store serves. UTC would be defensible but useless: the
    question is always asked in the operator's wall-clock time ("what runs
    between 22:00 and 07:00"), and an answer an hour or two off in summer is
    worse than no answer. Any IANA zone name is accepted and the resolved zone is
    echoed on every result row.
    """

    timezone: str = "Europe/Amsterdam"
    job_name: str | None = None
    since: datetime | None = None
    until: datetime | None = None
    # Local time-of-day window, in whole hours, half-open [start, end).
    # `start_hour > end_hour` WRAPS midnight -- 22/7 is the nightly batch window
    # and is the case this input exists for. `start_hour == end_hour` is the full
    # 24 hours anchored at that hour. Both must be given together: one alone has
    # no defensible reading.
    start_hour: int | None = Field(default=None, ge=0, le=23)
    end_hour: int | None = Field(default=None, ge=0, le=23)
    # See ListRuntimeJobReliabilityInput: restrict to runs observed in these
    # environments (the "PROD only" reading), and the resolved SHARED read set
    # for the documentation lane, which takes precedence over `project_id`.
    environment_ids: list[str] | None = None
    project_ids: list[str] | None = None

    @field_validator("timezone")
    @classmethod
    def _validate_timezone(cls, value: str) -> str:
        """Reject an unknown zone here, where it is a validation error.

        This input is reachable from an LLM tool call, so an unresolvable zone
        must fail as a tool-argument error rather than as an exception inside the
        repository's SQL construction.
        """
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as e:
            raise ValueError(f"Unknown IANA timezone: {value!r}") from e
        return value

    @model_validator(mode="after")
    def _validate_window(self) -> "ListRuntimeJobScheduleProfileInput":
        if (self.start_hour is None) != (self.end_hour is None):
            raise ValueError("start_hour and end_hour must be given together")
        return self


class ListRuntimeComponentHotspotsInput(ListRuntimeInventoryInput):
    component_key: str | None = None
    component_type: str | None = None
    since: datetime | None = None
    until: datetime | None = None


class ListRuntimeJobThroughputInput(ListRuntimeInventoryInput):
    job_name: str | None = None
    since: datetime | None = None
    until: datetime | None = None


class ListRuntimeErrorSignaturesInput(RuntimeReadTermsMixin):
    """Error signatures are keyed by (project, signature) only.

    Deliberately does not inherit the inventory input: signature rows carry no
    source_key, and silently ignoring such a filter would return rows the caller
    believes were excluded.
    """

    project_id: str | None = None
    error_code: str | None = None
    exception_class: str | None = None
    component_name: str | None = None
    since: datetime | None = None
    until: datetime | None = None
    limit: int = Field(default=100, ge=1, le=500)
    offset: int = Field(default=0, ge=0)


class ListRuntimeEnginesInput(ListRuntimeInventoryInput):
    status: RuntimeEngineStatus | None = None


class ListRuntimeJobsInput(ListRuntimeInventoryInput):
    environment_id: str | None = None
    workspace_id: str | None = None
    engine_id: str | None = None
    status: str | None = None
    executable_kind: RuntimeExecutableKind | None = RuntimeExecutableKind.JOB


class ListRuntimeDeploymentsInput(ListRuntimeInventoryInput):
    executable_kind: RuntimeExecutableKind | None = None
    state: RuntimeExecutionState | None = None


class ListRuntimeJobRunsInput(ListRuntimeInventoryInput):
    job_id: str | None = None
    engine_id: str | None = None
    state: RuntimeExecutionState | None = None
    since: datetime | None = None
    until: datetime | None = None


class ListRuntimePlansInput(ListRuntimeInventoryInput):
    environment_id: str | None = None
    workspace_id: str | None = None
    status: str | None = None


class ListRuntimePlanRunsInput(ListRuntimeInventoryInput):
    plan_id: str | None = None
    state: RuntimeExecutionState | None = None
    since: datetime | None = None
    until: datetime | None = None


class ListRuntimeArtifactsInput(ListRuntimeInventoryInput):
    workspace_id: str | None = None
    artifact_type: str | None = None


class ListRuntimeConnectionsInput(ListRuntimeInventoryInput):
    workspace_id: str | None = None
    connection_type: str | None = None


class ListRuntimeDependenciesInput(ListRuntimeInventoryInput):
    dependency_type: str | None = Field(default=None, pattern="^(artifact|connection)$")
    dependency_id: str | None = None
    job_id: str | None = None


# --- Generic tool envelope models ---


class RuntimeToolDefinition(BaseModel):
    name: str
    description: str
    input_schema: dict[str, Any]


class RuntimeToolCall(BaseModel):
    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class RuntimeToolErrorCode(str, Enum):
    DISABLED = "disabled"
    UNAVAILABLE = "unavailable"
    UNKNOWN_TOOL = "unknown_tool"
    VALIDATION_ERROR = "validation_error"
    PROVIDER_ERROR = "provider_error"
    TIMEOUT = "timeout"


class RuntimeToolError(BaseModel):
    code: RuntimeToolErrorCode
    message: str


class RuntimeToolResult(BaseModel):
    tool_name: str
    success: bool
    data: Any | None = None
    error: RuntimeToolError | None = None
    latency_ms: int
    truncated: bool = False
