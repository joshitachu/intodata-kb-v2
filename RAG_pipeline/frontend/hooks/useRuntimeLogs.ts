"use client";

import { useQuery } from "@tanstack/react-query";
import {
  ExecutionDetailResponse,
  ExecutionDetailResponseSchema,
  ExecutionListResponse,
  ExecutionListResponseSchema,
  ExecutionSummaryResponse,
  ExecutionSummaryResponseSchema,
  FailureListResponse,
  FailureListResponseSchema,
  HealthSummaryResponse,
  HealthSummaryResponseSchema,
  IncidentDetailResponse,
  IncidentDetailResponseSchema,
  IncidentEvidenceResponse,
  IncidentEvidenceResponseSchema,
  IncidentListResponse,
  IncidentListResponseSchema,
  IncidentSimilarResponse,
  IncidentSimilarResponseSchema,
  LogEventListResponse,
  LogEventListResponseSchema,
  RuntimeEngineHealthResponse,
  RuntimeEngineHealthResponseSchema,
  RuntimeEngineListResponse,
  RuntimeEngineListResponseSchema,
  RuntimeEngineStatus,
  RuntimeArtifactListResponse,
  RuntimeArtifactListResponseSchema,
  RuntimeConnectionListResponse,
  RuntimeConnectionListResponseSchema,
  RuntimeDependencyListResponse,
  RuntimeDependencyListResponseSchema,
  RuntimeDependencySummaryResponse,
  RuntimeDependencySummaryResponseSchema,
  RuntimeExecutionState,
  RuntimeExecutableKind,
  RuntimeJobHealthResponse,
  RuntimeJobHealthResponseSchema,
  RuntimeJobListResponse,
  RuntimeJobListResponseSchema,
  RuntimeDeploymentListResponse,
  RuntimeDeploymentListResponseSchema,
  RuntimeJobRunListResponse,
  RuntimeJobRunListResponseSchema,
  RuntimeLogSeverity,
  RuntimeOverviewResponse,
  RuntimeOverviewResponseSchema,
  RuntimePlanHealthResponse,
  RuntimePlanHealthResponseSchema,
  RuntimePlanListResponse,
  RuntimePlanListResponseSchema,
  RuntimePlanRunListResponse,
  RuntimePlanRunListResponseSchema,
  RuntimeSourceListResponse,
  RuntimeSourceListResponseSchema,
  RuntimeComponentHotspotResponse,
  RuntimeComponentHotspotResponseSchema,
  RuntimeErrorSignatureResponse,
  RuntimeErrorSignatureResponseSchema,
  RuntimeJobReliabilityResponse,
  RuntimeJobReliabilityResponseSchema,
  RuntimeJobInsightResponse,
  RuntimeJobInsightResponseSchema,
  RuntimeJobThroughputResponse,
  RuntimeJobThroughputResponseSchema,
  RuntimeTrendBucket,
  RuntimeTrendResponse,
  RuntimeTrendResponseSchema,
  TimelineResponse,
  TimelineResponseSchema,
} from "@/types";

export interface ExecutionFilters {
  projectId?: string;
  jobName?: string;
  state?: RuntimeExecutionState;
  since?: string;
  until?: string;
  minDurationMs?: number;
  /** Tri-state: omitted = all runs, true = only runs with no ended_at, false = only finished. */
  unfinished?: boolean;
  limit?: number;
  offset?: number;
}

export interface FailureFilters {
  projectId?: string;
  limit?: number;
  offset?: number;
}

export interface HealthFilters {
  projectId?: string;
  sourceKey?: string;
  limit?: number;
}

export interface InventoryFilters {
  projectId?: string;
  sourceKey?: string;
  limit?: number;
  offset?: number;
}

export interface EngineFilters extends InventoryFilters {
  status?: RuntimeEngineStatus;
}

export interface JobFilters extends InventoryFilters {
  environmentId?: string;
  workspaceId?: string;
  engineId?: string;
  status?: string;
  executableKind?: RuntimeExecutableKind;
}

export interface DeploymentFilters extends InventoryFilters {
  executableKind?: Exclude<RuntimeExecutableKind, "job">;
  state?: RuntimeExecutionState;
}

export interface ArtifactFilters extends InventoryFilters {
  workspaceId?: string;
  artifactType?: string;
}

export interface ConnectionFilters extends InventoryFilters {
  workspaceId?: string;
  connectionType?: string;
}

export interface DependencyFilters extends InventoryFilters {
  dependencyType?: "artifact" | "connection";
  dependencyId?: string;
  jobId?: string;
}

export interface JobRunFilters extends InventoryFilters {
  jobId?: string;
  engineId?: string;
  state?: RuntimeExecutionState;
  since?: string;
  until?: string;
}

export interface PlanFilters extends InventoryFilters {
  environmentId?: string;
  workspaceId?: string;
  status?: string;
}

export interface PlanRunFilters extends InventoryFilters {
  planId?: string;
  state?: RuntimeExecutionState;
  since?: string;
  until?: string;
}

export interface LogFilters {
  minSeverity?: RuntimeLogSeverity;
  limit?: number;
  offset?: number;
}

/** Shared window/scope filters for the SQL-computed aggregate rollups. */
export interface AggregateFilters {
  projectId?: string;
  sourceKey?: string;
  since?: string;
  until?: string;
  limit?: number;
  offset?: number;
}

export interface TrendFilters extends Omit<AggregateFilters, "offset"> {
  jobName?: string;
  bucket?: RuntimeTrendBucket;
}

export interface JobReliabilityFilters extends AggregateFilters {
  jobName?: string;
  attentionOnly?: boolean;
}

export interface ComponentHotspotFilters extends AggregateFilters {
  componentKey?: string;
  componentType?: string;
}

export interface JobThroughputFilters extends AggregateFilters {
  jobName?: string;
}

export interface ErrorSignatureFilters extends Omit<AggregateFilters, "sourceKey"> {
  errorCode?: string;
  exceptionClass?: string;
  componentName?: string;
}

/** Build a query string from defined, non-empty filter values. */
function buildQuery(
  params: Record<string, string | number | boolean | undefined>,
): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== "") {
      search.set(key, String(value));
    }
  }
  const qs = search.toString();
  return qs ? `?${qs}` : "";
}

const STALE_MS = 15 * 1000;

async function fetchExecutions(
  filters: ExecutionFilters,
): Promise<ExecutionListResponse> {
  const query = buildQuery({
    project_id: filters.projectId,
    job_name: filters.jobName,
    state: filters.state,
    since: filters.since,
    until: filters.until,
    min_duration_ms: filters.minDurationMs,
    unfinished: filters.unfinished,
    limit: filters.limit,
    offset: filters.offset,
  });
  const res = await fetch(`/api/runtime/executions${query}`, { cache: "no-store" });
  if (!res.ok) {
    throw new Error("Failed to fetch executions");
  }
  return ExecutionListResponseSchema.parse(await res.json());
}

export function useRuntimeExecutions(filters: ExecutionFilters = {}) {
  return useQuery({
    queryKey: ["runtime", "executions", filters],
    queryFn: () => fetchExecutions(filters),
    staleTime: STALE_MS,
  });
}

async function fetchExecution(
  executionId: string,
): Promise<ExecutionDetailResponse> {
  const res = await fetch(
    `/api/runtime/executions/${encodeURIComponent(executionId)}`,
    { cache: "no-store" },
  );
  if (!res.ok) {
    throw new Error("Failed to fetch execution");
  }
  return ExecutionDetailResponseSchema.parse(await res.json());
}

export function useRuntimeExecution(executionId: string | null, enabled = true) {
  return useQuery({
    queryKey: ["runtime", "execution", executionId],
    queryFn: () => fetchExecution(executionId as string),
    enabled: enabled && !!executionId,
    staleTime: STALE_MS,
  });
}

async function fetchExecutionLogs(
  executionId: string,
  filters: LogFilters,
): Promise<LogEventListResponse> {
  const query = buildQuery({
    min_severity: filters.minSeverity,
    limit: filters.limit,
    offset: filters.offset,
  });
  const res = await fetch(
    `/api/runtime/executions/${encodeURIComponent(executionId)}/logs${query}`,
    { cache: "no-store" },
  );
  if (!res.ok) {
    throw new Error("Failed to fetch execution logs");
  }
  return LogEventListResponseSchema.parse(await res.json());
}

export function useRuntimeExecutionLogs(
  executionId: string | null,
  filters: LogFilters = {},
  enabled = true,
) {
  return useQuery({
    queryKey: ["runtime", "logs", executionId, filters],
    queryFn: () => fetchExecutionLogs(executionId as string, filters),
    enabled: enabled && !!executionId,
    staleTime: STALE_MS,
  });
}

async function fetchFailures(filters: FailureFilters): Promise<FailureListResponse> {
  const query = buildQuery({
    project_id: filters.projectId,
    limit: filters.limit,
    offset: filters.offset,
  });
  const res = await fetch(`/api/runtime/failures${query}`, { cache: "no-store" });
  if (!res.ok) {
    throw new Error("Failed to fetch failures");
  }
  return FailureListResponseSchema.parse(await res.json());
}

export function useRuntimeFailures(filters: FailureFilters = {}) {
  return useQuery({
    queryKey: ["runtime", "failures", filters],
    queryFn: () => fetchFailures(filters),
    staleTime: STALE_MS,
  });
}

async function fetchHealth(filters: HealthFilters): Promise<HealthSummaryResponse> {
  const query = buildQuery({
    project_id: filters.projectId,
    source_key: filters.sourceKey,
    limit: filters.limit,
  });
  const res = await fetch(`/api/runtime/health${query}`, { cache: "no-store" });
  if (!res.ok) {
    throw new Error("Failed to fetch runtime health");
  }
  return HealthSummaryResponseSchema.parse(await res.json());
}

export function useRuntimeHealth(filters: HealthFilters = {}) {
  return useQuery({
    queryKey: ["runtime", "health", filters],
    queryFn: () => fetchHealth(filters),
    staleTime: STALE_MS,
  });
}

async function fetchSources(filters: InventoryFilters): Promise<RuntimeSourceListResponse> {
  const query = buildQuery({
    project_id: filters.projectId,
    source_key: filters.sourceKey,
    limit: filters.limit,
  });
  const res = await fetch(`/api/runtime/sources${query}`, { cache: "no-store" });
  if (!res.ok) {
    throw new Error("Failed to fetch runtime sources");
  }
  return RuntimeSourceListResponseSchema.parse(await res.json());
}

export function useRuntimeSources(filters: InventoryFilters = {}) {
  return useQuery({
    queryKey: ["runtime", "sources", filters],
    queryFn: () => fetchSources(filters),
    staleTime: STALE_MS,
  });
}

async function fetchEngines(filters: EngineFilters): Promise<RuntimeEngineListResponse> {
  const query = buildQuery({
    project_id: filters.projectId,
    source_key: filters.sourceKey,
    status: filters.status,
    limit: filters.limit,
    offset: filters.offset,
  });
  const res = await fetch(`/api/runtime/engines${query}`, { cache: "no-store" });
  if (!res.ok) {
    throw new Error("Failed to fetch runtime engines");
  }
  return RuntimeEngineListResponseSchema.parse(await res.json());
}

export function useRuntimeEngines(filters: EngineFilters = {}) {
  return useQuery({
    queryKey: ["runtime", "engines", filters],
    queryFn: () => fetchEngines(filters),
    staleTime: STALE_MS,
  });
}

async function fetchEngineHealth(
  filters: EngineFilters,
): Promise<RuntimeEngineHealthResponse> {
  const query = buildQuery({
    project_id: filters.projectId,
    source_key: filters.sourceKey,
    status: filters.status,
    limit: filters.limit,
  });
  const res = await fetch(`/api/runtime/engine-health${query}`, { cache: "no-store" });
  if (!res.ok) {
    throw new Error("Failed to fetch runtime engine health");
  }
  return RuntimeEngineHealthResponseSchema.parse(await res.json());
}

export function useRuntimeEngineHealth(filters: EngineFilters = {}) {
  return useQuery({
    queryKey: ["runtime", "engine-health", filters],
    queryFn: () => fetchEngineHealth(filters),
    staleTime: STALE_MS,
  });
}

async function fetchOverview(filters: InventoryFilters): Promise<RuntimeOverviewResponse> {
  const query = buildQuery({
    project_id: filters.projectId,
    source_key: filters.sourceKey,
  });
  const res = await fetch(`/api/runtime/overview${query}`, { cache: "no-store" });
  if (!res.ok) {
    throw new Error("Failed to fetch runtime overview");
  }
  return RuntimeOverviewResponseSchema.parse(await res.json());
}

export function useRuntimeOverview(filters: InventoryFilters = {}) {
  return useQuery({
    queryKey: ["runtime", "overview", filters],
    queryFn: () => fetchOverview(filters),
    staleTime: STALE_MS,
  });
}

async function fetchJobs(filters: JobFilters): Promise<RuntimeJobListResponse> {
  const query = buildQuery({
    project_id: filters.projectId,
    source_key: filters.sourceKey,
    environment_id: filters.environmentId,
    workspace_id: filters.workspaceId,
    engine_id: filters.engineId,
    status: filters.status,
    executable_kind: filters.executableKind,
    limit: filters.limit,
    offset: filters.offset,
  });
  const res = await fetch(`/api/runtime/jobs${query}`, { cache: "no-store" });
  if (!res.ok) {
    throw new Error("Failed to fetch runtime jobs");
  }
  return RuntimeJobListResponseSchema.parse(await res.json());
}

export function useRuntimeJobs(filters: JobFilters = {}) {
  return useQuery({
    queryKey: ["runtime", "jobs", filters],
    queryFn: () => fetchJobs(filters),
    staleTime: STALE_MS,
  });
}

async function fetchDeployments(
  filters: DeploymentFilters,
): Promise<RuntimeDeploymentListResponse> {
  const query = buildQuery({
    project_id: filters.projectId,
    source_key: filters.sourceKey,
    executable_kind: filters.executableKind,
    state: filters.state,
    limit: filters.limit,
    offset: filters.offset,
  });
  const res = await fetch(`/api/runtime/deployments${query}`, { cache: "no-store" });
  if (!res.ok) throw new Error("Failed to fetch runtime deployments");
  return RuntimeDeploymentListResponseSchema.parse(await res.json());
}

export function useRuntimeDeployments(filters: DeploymentFilters = {}) {
  return useQuery({
    queryKey: ["runtime", "deployments", filters],
    queryFn: () => fetchDeployments(filters),
    staleTime: STALE_MS,
  });
}

async function fetchArtifacts(
  filters: ArtifactFilters,
): Promise<RuntimeArtifactListResponse> {
  const query = buildQuery({
    project_id: filters.projectId,
    source_key: filters.sourceKey,
    workspace_id: filters.workspaceId,
    artifact_type: filters.artifactType,
    limit: filters.limit,
  });
  const res = await fetch(`/api/runtime/artifacts${query}`, { cache: "no-store" });
  if (!res.ok) {
    throw new Error("Failed to fetch runtime artifacts");
  }
  return RuntimeArtifactListResponseSchema.parse(await res.json());
}

export function useRuntimeArtifacts(filters: ArtifactFilters = {}) {
  return useQuery({
    queryKey: ["runtime", "artifacts", filters],
    queryFn: () => fetchArtifacts(filters),
    staleTime: STALE_MS,
  });
}

async function fetchConnections(
  filters: ConnectionFilters,
): Promise<RuntimeConnectionListResponse> {
  const query = buildQuery({
    project_id: filters.projectId,
    source_key: filters.sourceKey,
    workspace_id: filters.workspaceId,
    connection_type: filters.connectionType,
    limit: filters.limit,
  });
  const res = await fetch(`/api/runtime/connections${query}`, { cache: "no-store" });
  if (!res.ok) {
    throw new Error("Failed to fetch runtime connections");
  }
  return RuntimeConnectionListResponseSchema.parse(await res.json());
}

export function useRuntimeConnections(filters: ConnectionFilters = {}) {
  return useQuery({
    queryKey: ["runtime", "connections", filters],
    queryFn: () => fetchConnections(filters),
    staleTime: STALE_MS,
  });
}

async function fetchDependencies(
  filters: DependencyFilters,
): Promise<RuntimeDependencyListResponse> {
  const query = buildQuery({
    project_id: filters.projectId,
    source_key: filters.sourceKey,
    dependency_type: filters.dependencyType,
    dependency_id: filters.dependencyId,
    job_id: filters.jobId,
    limit: filters.limit,
  });
  const res = await fetch(`/api/runtime/dependencies${query}`, { cache: "no-store" });
  if (!res.ok) {
    throw new Error("Failed to fetch runtime dependencies");
  }
  return RuntimeDependencyListResponseSchema.parse(await res.json());
}

export function useRuntimeDependencies(filters: DependencyFilters = {}) {
  return useQuery({
    queryKey: ["runtime", "dependencies", filters],
    queryFn: () => fetchDependencies(filters),
    staleTime: STALE_MS,
  });
}

async function fetchDependencySummary(
  filters: InventoryFilters,
): Promise<RuntimeDependencySummaryResponse> {
  const query = buildQuery({
    project_id: filters.projectId,
    source_key: filters.sourceKey,
    limit: filters.limit,
  });
  const res = await fetch(`/api/runtime/dependency-summary${query}`, { cache: "no-store" });
  if (!res.ok) {
    throw new Error("Failed to fetch runtime dependency summary");
  }
  return RuntimeDependencySummaryResponseSchema.parse(await res.json());
}

export function useRuntimeDependencySummary(filters: InventoryFilters = {}) {
  return useQuery({
    queryKey: ["runtime", "dependency-summary", filters],
    queryFn: () => fetchDependencySummary(filters),
    staleTime: STALE_MS,
  });
}

async function fetchJobRuns(filters: JobRunFilters): Promise<RuntimeJobRunListResponse> {
  const query = buildQuery({
    project_id: filters.projectId,
    source_key: filters.sourceKey,
    job_id: filters.jobId,
    engine_id: filters.engineId,
    state: filters.state,
    since: filters.since,
    until: filters.until,
    limit: filters.limit,
  });
  const res = await fetch(`/api/runtime/job-runs${query}`, { cache: "no-store" });
  if (!res.ok) {
    throw new Error("Failed to fetch runtime job runs");
  }
  return RuntimeJobRunListResponseSchema.parse(await res.json());
}

export function useRuntimeJobRuns(filters: JobRunFilters = {}) {
  return useQuery({
    queryKey: ["runtime", "job-runs", filters],
    queryFn: () => fetchJobRuns(filters),
    staleTime: STALE_MS,
  });
}

async function fetchJobHealth(filters: JobFilters): Promise<RuntimeJobHealthResponse> {
  const query = buildQuery({
    project_id: filters.projectId,
    source_key: filters.sourceKey,
    environment_id: filters.environmentId,
    workspace_id: filters.workspaceId,
    engine_id: filters.engineId,
    limit: filters.limit,
  });
  const res = await fetch(`/api/runtime/job-health${query}`, { cache: "no-store" });
  if (!res.ok) {
    throw new Error("Failed to fetch runtime job health");
  }
  return RuntimeJobHealthResponseSchema.parse(await res.json());
}

export function useRuntimeJobHealth(filters: JobFilters = {}) {
  return useQuery({
    queryKey: ["runtime", "job-health", filters],
    queryFn: () => fetchJobHealth(filters),
    staleTime: STALE_MS,
  });
}

async function fetchPlans(filters: PlanFilters): Promise<RuntimePlanListResponse> {
  const query = buildQuery({
    project_id: filters.projectId,
    source_key: filters.sourceKey,
    environment_id: filters.environmentId,
    workspace_id: filters.workspaceId,
    status: filters.status,
    limit: filters.limit,
    offset: filters.offset,
  });
  const res = await fetch(`/api/runtime/plans${query}`, { cache: "no-store" });
  if (!res.ok) {
    throw new Error("Failed to fetch runtime plans");
  }
  return RuntimePlanListResponseSchema.parse(await res.json());
}

export function useRuntimePlans(filters: PlanFilters = {}) {
  return useQuery({
    queryKey: ["runtime", "plans", filters],
    queryFn: () => fetchPlans(filters),
    staleTime: STALE_MS,
  });
}

async function fetchPlanRuns(filters: PlanRunFilters): Promise<RuntimePlanRunListResponse> {
  const query = buildQuery({
    project_id: filters.projectId,
    source_key: filters.sourceKey,
    plan_id: filters.planId,
    state: filters.state,
    since: filters.since,
    until: filters.until,
    limit: filters.limit,
  });
  const res = await fetch(`/api/runtime/plan-runs${query}`, { cache: "no-store" });
  if (!res.ok) {
    throw new Error("Failed to fetch runtime plan runs");
  }
  return RuntimePlanRunListResponseSchema.parse(await res.json());
}

export function useRuntimePlanRuns(filters: PlanRunFilters = {}) {
  return useQuery({
    queryKey: ["runtime", "plan-runs", filters],
    queryFn: () => fetchPlanRuns(filters),
    staleTime: STALE_MS,
  });
}

async function fetchPlanHealth(filters: PlanFilters): Promise<RuntimePlanHealthResponse> {
  const query = buildQuery({
    project_id: filters.projectId,
    source_key: filters.sourceKey,
    environment_id: filters.environmentId,
    workspace_id: filters.workspaceId,
    limit: filters.limit,
  });
  const res = await fetch(`/api/runtime/plan-health${query}`, { cache: "no-store" });
  if (!res.ok) {
    throw new Error("Failed to fetch runtime plan health");
  }
  return RuntimePlanHealthResponseSchema.parse(await res.json());
}

export function useRuntimePlanHealth(filters: PlanFilters = {}) {
  return useQuery({
    queryKey: ["runtime", "plan-health", filters],
    queryFn: () => fetchPlanHealth(filters),
    staleTime: STALE_MS,
  });
}

// --- Smart-log insight hooks (EP-006 slice 4) ---

export interface IncidentFilters {
  projectId?: string;
  since?: string;
  until?: string;
  limit?: number;
  offset?: number;
}

export interface TimelineFilters {
  minSeverity?: RuntimeLogSeverity;
  limit?: number;
}

export interface EvidenceFilters {
  limit?: number;
}

async function fetchExecutionSummary(
  executionId: string,
): Promise<ExecutionSummaryResponse> {
  const res = await fetch(
    `/api/runtime/executions/${encodeURIComponent(executionId)}/summary`,
    { cache: "no-store" },
  );
  if (!res.ok) {
    throw new Error(`Failed to fetch execution summary: ${res.status}`);
  }
  return ExecutionSummaryResponseSchema.parse(await res.json());
}

export function useRuntimeExecutionSummary(
  executionId: string | null,
  enabled = true,
) {
  return useQuery({
    queryKey: ["runtime", "summary", executionId],
    queryFn: () => fetchExecutionSummary(executionId as string),
    enabled: enabled && !!executionId,
    staleTime: STALE_MS,
  });
}

async function fetchExecutionTimeline(
  executionId: string,
  filters: TimelineFilters,
): Promise<TimelineResponse> {
  const query = buildQuery({
    min_severity: filters.minSeverity,
    limit: filters.limit,
  });
  const res = await fetch(
    `/api/runtime/executions/${encodeURIComponent(executionId)}/timeline${query}`,
    { cache: "no-store" },
  );
  if (!res.ok) {
    throw new Error(`Failed to fetch execution timeline: ${res.status}`);
  }
  return TimelineResponseSchema.parse(await res.json());
}

export function useRuntimeExecutionTimeline(
  executionId: string | null,
  filters: TimelineFilters = {},
  enabled = true,
) {
  return useQuery({
    queryKey: ["runtime", "timeline", executionId, filters],
    queryFn: () => fetchExecutionTimeline(executionId as string, filters),
    enabled: enabled && !!executionId,
    staleTime: STALE_MS,
  });
}

async function fetchIncidents(filters: IncidentFilters): Promise<IncidentListResponse> {
  const query = buildQuery({
    project_id: filters.projectId,
    since: filters.since,
    until: filters.until,
    limit: filters.limit,
    offset: filters.offset,
  });
  const res = await fetch(`/api/runtime/incidents${query}`, { cache: "no-store" });
  if (!res.ok) {
    throw new Error("Failed to fetch incidents");
  }
  return IncidentListResponseSchema.parse(await res.json());
}

export function useRuntimeIncidents(filters: IncidentFilters = {}) {
  return useQuery({
    queryKey: ["runtime", "incidents", filters],
    queryFn: () => fetchIncidents(filters),
    staleTime: STALE_MS,
  });
}

async function fetchIncident(incidentId: string): Promise<IncidentDetailResponse> {
  const res = await fetch(
    `/api/runtime/incidents/${encodeURIComponent(incidentId)}`,
    { cache: "no-store" },
  );
  if (!res.ok) {
    throw new Error(`Failed to fetch incident: ${res.status}`);
  }
  return IncidentDetailResponseSchema.parse(await res.json());
}

export function useRuntimeIncident(incidentId: string | null, enabled = true) {
  return useQuery({
    queryKey: ["runtime", "incident", incidentId],
    queryFn: () => fetchIncident(incidentId as string),
    enabled: enabled && !!incidentId,
    staleTime: STALE_MS,
  });
}

async function fetchIncidentEvidence(
  incidentId: string,
  filters: EvidenceFilters,
): Promise<IncidentEvidenceResponse> {
  const query = buildQuery({ limit: filters.limit });
  const res = await fetch(
    `/api/runtime/incidents/${encodeURIComponent(incidentId)}/evidence${query}`,
    { cache: "no-store" },
  );
  if (!res.ok) {
    throw new Error(`Failed to fetch incident evidence: ${res.status}`);
  }
  return IncidentEvidenceResponseSchema.parse(await res.json());
}

export function useRuntimeIncidentEvidence(
  incidentId: string | null,
  filters: EvidenceFilters = {},
  enabled = true,
) {
  return useQuery({
    queryKey: ["runtime", "evidence", incidentId, filters],
    queryFn: () => fetchIncidentEvidence(incidentId as string, filters),
    enabled: enabled && !!incidentId,
    staleTime: STALE_MS,
  });
}

// --- Similar failures (EP-006 slice 5) ---

export interface SimilarFailuresFilters {
  limit?: number;
}

async function fetchSimilarFailures(
  incidentId: string,
  filters: SimilarFailuresFilters,
): Promise<IncidentSimilarResponse> {
  const query = buildQuery({ limit: filters.limit });
  const res = await fetch(
    `/api/runtime/incidents/${encodeURIComponent(incidentId)}/similar${query}`,
    { cache: "no-store" },
  );
  if (!res.ok) {
    throw new Error(`Failed to fetch similar failures: ${res.status}`);
  }
  return IncidentSimilarResponseSchema.parse(await res.json());
}

export function useRuntimeSimilarFailures(
  incidentId: string | null,
  filters: SimilarFailuresFilters = {},
  enabled = true,
) {
  return useQuery({
    queryKey: ["runtime", "similar", incidentId, filters],
    queryFn: () => fetchSimilarFailures(incidentId as string, filters),
    enabled: enabled && !!incidentId,
    staleTime: STALE_MS,
  });
}

// --- Aggregate rollups ---
// Each takes `enabled` so a route-scoped view only fires the queries it renders.

async function fetchTrend(filters: TrendFilters): Promise<RuntimeTrendResponse> {
  const query = buildQuery({
    project_id: filters.projectId,
    source_key: filters.sourceKey,
    job_name: filters.jobName,
    bucket: filters.bucket,
    since: filters.since,
    until: filters.until,
    limit: filters.limit,
  });
  const res = await fetch(`/api/runtime/trend${query}`, { cache: "no-store" });
  if (!res.ok) {
    throw new Error("Failed to fetch runtime trend");
  }
  return RuntimeTrendResponseSchema.parse(await res.json());
}

export function useRuntimeTrend(filters: TrendFilters = {}, enabled = true) {
  return useQuery({
    queryKey: ["runtime", "trend", filters],
    queryFn: () => fetchTrend(filters),
    enabled,
    staleTime: STALE_MS,
  });
}

async function fetchJobReliability(
  filters: JobReliabilityFilters,
): Promise<RuntimeJobReliabilityResponse> {
  const query = buildQuery({
    project_id: filters.projectId,
    source_key: filters.sourceKey,
    job_name: filters.jobName,
    since: filters.since,
    until: filters.until,
    limit: filters.limit,
    offset: filters.offset,
    attention_only: filters.attentionOnly,
  });
  const res = await fetch(`/api/runtime/job-reliability${query}`, { cache: "no-store" });
  if (!res.ok) {
    throw new Error("Failed to fetch job reliability");
  }
  return RuntimeJobReliabilityResponseSchema.parse(await res.json());
}

export function useRuntimeJobReliability(
  filters: JobReliabilityFilters = {},
  enabled = true,
) {
  return useQuery({
    queryKey: ["runtime", "job-reliability", filters],
    queryFn: () => fetchJobReliability(filters),
    enabled,
    staleTime: STALE_MS,
  });
}

async function fetchJobInsights(
  filters: AggregateFilters,
): Promise<RuntimeJobInsightResponse> {
  const query = buildQuery({
    project_id: filters.projectId,
    source_key: filters.sourceKey,
    since: filters.since,
    until: filters.until,
    limit: filters.limit,
    offset: filters.offset,
  });
  const res = await fetch(`/api/runtime/job-insights${query}`, { cache: "no-store" });
  if (!res.ok) {
    throw new Error("Failed to fetch runtime job insights");
  }
  return RuntimeJobInsightResponseSchema.parse(await res.json());
}

export function useRuntimeJobInsights(
  filters: AggregateFilters = {},
  enabled = true,
) {
  return useQuery({
    queryKey: ["runtime", "job-insights", filters],
    queryFn: () => fetchJobInsights(filters),
    enabled,
    staleTime: STALE_MS,
  });
}

async function fetchComponentHotspots(
  filters: ComponentHotspotFilters,
): Promise<RuntimeComponentHotspotResponse> {
  const query = buildQuery({
    project_id: filters.projectId,
    source_key: filters.sourceKey,
    component_key: filters.componentKey,
    component_type: filters.componentType,
    since: filters.since,
    until: filters.until,
    limit: filters.limit,
    offset: filters.offset,
  });
  const res = await fetch(`/api/runtime/component-hotspots${query}`, { cache: "no-store" });
  if (!res.ok) {
    throw new Error("Failed to fetch component hotspots");
  }
  return RuntimeComponentHotspotResponseSchema.parse(await res.json());
}

export function useRuntimeComponentHotspots(
  filters: ComponentHotspotFilters = {},
  enabled = true,
) {
  return useQuery({
    queryKey: ["runtime", "component-hotspots", filters],
    queryFn: () => fetchComponentHotspots(filters),
    enabled,
    staleTime: STALE_MS,
  });
}

async function fetchJobThroughput(
  filters: JobThroughputFilters,
): Promise<RuntimeJobThroughputResponse> {
  const query = buildQuery({
    project_id: filters.projectId,
    source_key: filters.sourceKey,
    job_name: filters.jobName,
    since: filters.since,
    until: filters.until,
    limit: filters.limit,
    offset: filters.offset,
  });
  const res = await fetch(`/api/runtime/job-throughput${query}`, { cache: "no-store" });
  if (!res.ok) {
    throw new Error("Failed to fetch job throughput");
  }
  return RuntimeJobThroughputResponseSchema.parse(await res.json());
}

export function useRuntimeJobThroughput(
  filters: JobThroughputFilters = {},
  enabled = true,
) {
  return useQuery({
    queryKey: ["runtime", "job-throughput", filters],
    queryFn: () => fetchJobThroughput(filters),
    enabled,
    staleTime: STALE_MS,
  });
}

async function fetchErrorSignatures(
  filters: ErrorSignatureFilters,
): Promise<RuntimeErrorSignatureResponse> {
  const query = buildQuery({
    project_id: filters.projectId,
    error_code: filters.errorCode,
    exception_class: filters.exceptionClass,
    component_name: filters.componentName,
    since: filters.since,
    until: filters.until,
    limit: filters.limit,
    offset: filters.offset,
  });
  const res = await fetch(`/api/runtime/error-signatures${query}`, { cache: "no-store" });
  if (!res.ok) {
    throw new Error("Failed to fetch error signatures");
  }
  return RuntimeErrorSignatureResponseSchema.parse(await res.json());
}

export function useRuntimeErrorSignatures(
  filters: ErrorSignatureFilters = {},
  enabled = true,
) {
  return useQuery({
    queryKey: ["runtime", "error-signatures", filters],
    queryFn: () => fetchErrorSignatures(filters),
    enabled,
    staleTime: STALE_MS,
  });
}
