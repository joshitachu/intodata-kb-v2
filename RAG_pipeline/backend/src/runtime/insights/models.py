"""Pydantic API models for the runtime insight read layer.

These are the shapes returned by the RuntimeInsightRepository and the insight
REST endpoints. They join derived insight rows with raw runtime store rows to
give callers a complete picture without needing multiple round-trips.
"""

from __future__ import annotations

import datetime

from pydantic import BaseModel


class RuntimeExecutionSummaryInsight(BaseModel):
    """Execution summary combining derived insight fields with raw execution metadata."""

    project_id: str
    execution_id: str
    job_name: str
    # Aggregated counts from parser output
    warning_count: int
    error_count: int
    # Timestamps of first/last notable events
    first_warning_at: datetime.datetime | None
    first_error_at: datetime.datetime | None
    final_error_at: datetime.datetime | None
    # Primary diagnostic signals
    failed_component: str | None
    primary_error_code: str | None
    primary_exception_class: str | None
    summary_text: str | None
    # The runtime's own diagnostic sentence, credential-redacted. Quoted verbatim
    # rather than reconstructed from signals, so the UI can show what the log
    # actually said. None when no causal line could be resolved.
    causal_message: str | None = None
    evidence_refs: list[int] | None
    # Joined from RuntimeExecutionRow
    state: str | None
    started_at: datetime.datetime | None
    ended_at: datetime.datetime | None
    duration_ms: int | None
    project_name: str | None
    # Incident link (if an incident was derived for this execution)
    incident_id: str | None
    has_incident: bool
    estate_context: "RuntimeFailureEstateContext"


class RuntimeFailureEstateContext(BaseModel):
    """Sanitized runtime-estate context for a failure or execution.

    All fields are optional because adapters can provide partial inventory. The
    frontend should render explicit unknown/not-reported values instead of
    treating absence as an error.
    """

    source_key: str | None = None
    source_label: str | None = None
    source_type: str | None = None
    source_status: str | None = None
    source_is_demo: bool | None = None
    source_last_synced_at: datetime.datetime | None = None
    source_last_seen_at: datetime.datetime | None = None
    environment_id: str | None = None
    environment_name: str | None = None
    workspace_id: str | None = None
    workspace_name: str | None = None
    engine_id: str | None = None
    engine_name: str | None = None
    engine_status: str | None = None
    engine_version: str | None = None
    engine_last_seen_at: datetime.datetime | None = None
    job_id: str | None = None
    job_name: str | None = None
    job_display_name: str | None = None
    job_run_id: str | None = None
    plan_id: str | None = None
    plan_name: str | None = None
    plan_display_name: str | None = None
    plan_run_id: str | None = None


class RuntimeIncidentInsight(BaseModel):
    """Incident record with joined execution metadata."""

    project_id: str
    incident_id: str
    execution_id: str
    signature_hash: str | None
    symptom_summary: str | None
    root_cause_candidate: str | None
    # The runtime's own diagnostic sentence, credential-redacted. Distinct from
    # root_cause_candidate, which is a rendered catalog template: this is what the
    # log actually said. None when no causal line could be resolved.
    causal_message: str | None = None
    confidence: str | None
    # "deterministic" (default) or "llm_fallback" (Slice 4). Lets callers tell
    # whether the root cause came from the rule engine or the LLM fallback.
    cause_origin: str | None
    pattern_id: str | None
    first_bad_at: datetime.datetime | None
    last_healthy_at: datetime.datetime | None
    next_checks: list[str]
    # Joined from RuntimeExecutionRow
    job_name: str | None
    state: str | None
    estate_context: RuntimeFailureEstateContext


class RuntimeIncidentEvidence(BaseModel):
    """Bounded evidence excerpt with joined log event timestamp and severity."""

    log_event_id: int
    evidence_role: str | None
    excerpt: str | None
    # Joined from RuntimeLogEventRow
    timestamp: datetime.datetime
    severity: str


class RuntimeFailureAnalysis(BaseModel):
    """Bounded failure-analysis object returned by the analyze_execution_failure tool.

    Combines execution summary, incident, and bounded evidence into one object so
    the model can answer failure-cause questions without multiple round-trips.
    Evidence excerpts are already bounded at 300 chars by the insight builder;
    this list is additionally capped at a small limit before being returned to the LLM.
    """

    execution_id: str
    job_name: str | None
    state: str | None
    started_at: datetime.datetime | None
    ended_at: datetime.datetime | None
    duration_ms: int | None
    warning_count: int
    error_count: int
    failed_component: str | None
    primary_error_code: str | None
    primary_exception_class: str | None
    summary_text: str | None
    has_incident: bool
    incident_id: str | None
    symptom_summary: str | None
    root_cause_candidate: str | None
    confidence: str | None
    cause_origin: str | None
    next_checks: list[str]
    evidence: list[RuntimeIncidentEvidence]
    estate_context: RuntimeFailureEstateContext


class RuntimeIncidentDetail(BaseModel):
    """Incident with bounded evidence for the get_runtime_incident tool."""

    incident: RuntimeIncidentInsight
    evidence: list[RuntimeIncidentEvidence]


class RuntimeTimelineEvent(BaseModel):
    """One timestamped event in an execution timeline.

    User-facing timeline signal. It intentionally omits raw log message text.
    Enriched fields (from the insight layer) are None when insight rows have not
    been built or the log line had no strong signals.
    """

    log_event_id: int
    timestamp: datetime.datetime
    severity: str
    signal: str
    # From RuntimeEnrichedEventRow (nullable — left-join semantics)
    component_name: str | None
    error_code: str | None
    exception_class: str | None
    retry_attempt: int | None
    retry_max: int | None
    confidence: str | None
    message_family: str | None


class RuntimeSimilarIncident(BaseModel):
    """Compact reference to a prior incident with the same error signature."""

    incident_id: str
    execution_id: str
    job_name: str | None
    state: str | None
    first_bad_at: datetime.datetime | None
    symptom_summary: str | None
    root_cause_candidate: str | None
    confidence: str | None


class RuntimeAlertReadyMetadata(BaseModel):
    """Alerting-ready recurrence metadata derived from the error signature aggregate.

    No alert is delivered in this slice — these fields are stable enough for a
    future alert/email slice to consume directly.
    """

    signature_hash: str | None
    signature_summary: str | None
    occurrence_count: int
    similar_count: int
    first_seen_at: datetime.datetime | None
    last_seen_at: datetime.datetime | None
    is_recurring: bool
    alert_fingerprint: str
    dedupe_key: str
    alert_title: str
    alert_reason: str


class RuntimeSimilarFailures(BaseModel):
    """Similar-failure result for one incident: recurrence metadata + bounded prior list."""

    incident_id: str
    execution_id: str
    recurrence: RuntimeAlertReadyMetadata
    similar_incidents: list[RuntimeSimilarIncident]
