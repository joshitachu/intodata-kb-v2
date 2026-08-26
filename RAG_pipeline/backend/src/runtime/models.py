"""Runtime status and generic tool contract — provider-agnostic, no
Talend-specific fields or operation names."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


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


class RuntimeExecutionState(str, Enum):
    SUCCESS = "success"
    FAILED = "failed"
    RUNNING = "running"
    UNKNOWN = "unknown"


class RuntimeSyncState(BaseModel):
    source_key: str
    project_id: str
    status: RuntimeSyncStatus
    last_synced_at: datetime | None = None
    failure_reason_code: str | None = None
    error_summary: str | None = None
    updated_at: datetime


class RuntimeSource(BaseModel):
    project_id: str
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
    project_id: str
    provider_key: str
    label: str
    credentials: dict[str, Any] = Field(default_factory=dict)
    sync_interval_seconds: int = Field(default=60, ge=30)


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
    id: int
    project_id: str
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
    """One project's permission to read another project's runtime mirror.

    Distinct from `RuntimeWorkspaceProjectMap`, which SPLITS a source's rows
    between projects at write time. A grant SHARES the same rows with a second
    reader. Presenting them as one control is how an operator picks the wrong
    one.
    """

    id: int
    source_id: int
    # The reader. The source's own project is the grantor and never appears.
    project_id: str
    source_project_id: str
    source_label: str
    # None means every mirrored environment; [] is a deliberate "nothing".
    environment_ids: list[str] | None = None
    window_days: int | None = None
    # Every project whose rows this grant exposes: the source's own project
    # plus any project its workspace map re-points rows to. Returned so a grant
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
    status: str | None = None
    environment_id: str | None = None
    environment_name: str | None = None
    workspace_id: str | None = None
    workspace_name: str | None = None
    engine_id: str | None = None
    engine_name: str | None = None
    owner: str | None = None
    artifact_name: str | None = None
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


class RuntimeJobRun(BaseModel):
    run_id: str
    job_id: str
    job_name: str
    source_key: str
    execution_id: str | None = None
    state: RuntimeExecutionState
    environment_id: str | None = None
    environment_name: str | None = None
    workspace_id: str | None = None
    workspace_name: str | None = None
    engine_id: str | None = None
    engine_name: str | None = None
    started_at: datetime
    ended_at: datetime | None = None
    duration_ms: int | None = None


class RuntimeJobHealth(BaseModel):
    project_id: str
    source_key: str
    job_id: str
    job_name: str
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


class RuntimePlanRun(BaseModel):
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


class RuntimeExecution(BaseModel):
    execution_id: str
    job_name: str
    project_name: str | None = None
    state: RuntimeExecutionState
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


# --- Generic tool input models ---


class ListExecutionsInput(BaseModel):
    project_name: str | None = None
    job_name: str | None = None
    state: RuntimeExecutionState | None = None
    since: datetime | None = None
    until: datetime | None = None
    # Duration/completion filters surface implausibly long and never-completed runs,
    # which a state filter alone cannot find: a run stuck for days is still reported
    # by the source as "success" or "unknown".
    min_duration_ms: int | None = Field(default=None, ge=0)
    unfinished: bool | None = None
    limit: int = Field(default=20, ge=1, le=200)
    offset: int = Field(default=0, ge=0)


class GetExecutionDetailsInput(BaseModel):
    execution_id: str


class DetectFailuresInput(BaseModel):
    since: datetime | None = None
    until: datetime | None = None
    limit: int = Field(default=20, ge=1, le=200)
    offset: int = Field(default=0, ge=0)


class GetExecutionLogsInput(BaseModel):
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


class ListRuntimeSyncStatusInput(BaseModel):
    source_key: str | None = None
    project_id: str | None = None
    limit: int = Field(default=20, ge=1, le=100)


class ListRuntimeHealthInput(BaseModel):
    source_key: str | None = None
    project_id: str | None = None
    limit: int = Field(default=10, ge=1, le=100)


class ListRuntimeInventoryInput(BaseModel):
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


class ListRuntimeComponentHotspotsInput(ListRuntimeInventoryInput):
    component_key: str | None = None
    component_type: str | None = None
    since: datetime | None = None
    until: datetime | None = None


class ListRuntimeJobThroughputInput(ListRuntimeInventoryInput):
    job_name: str | None = None
    since: datetime | None = None
    until: datetime | None = None


class ListRuntimeErrorSignaturesInput(BaseModel):
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
