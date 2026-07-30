import { type ZodType } from "zod";
import { getLoginUrl } from "../hooks/useAuth";
import {
  AgenticExperimentListSchema,
  AgenticExperimentCapabilitiesSchema,
  AgenticExperimentSchema,
  AgenticExperimentTaskListSchema,
  AgenticExperimentTaskSchema,
  AgenticRecipeSnapshotSchema,
  AgenticReconcileResultSchema,
  AgenticRetentionResultSchema,
  AgenticSourceSnapshotSchema,
  AssignPluginPackResponseSchema,
  DocContentSchema,
  DocListResponseSchema,
  GenerateAssessmentResponseSchema,
  InstanceSettingsResponseSchema,
  GenerateRunListResponseSchema,
  InstructionPackMetadataSchema,
  LogContentSchema,
  LogFileListResponseSchema,
  ModelListResponseSchema,
  PlatformListResponseSchema,
  PluginPackBranchDiscoverySchema,
  PluginPackFileSchema,
  PluginPackFilesSchema,
  PluginPackListSchema,
  PluginPackSchema,
  NexusPackWorkspaceSchema,
  ProjectListResponseSchema,
  ProjectSchema,
  ProjectUsageSchema,
  QualityHistoryResponseSchema,
  QuestionnaireResponseSchema,
  ReviewAggregateResponseSchema,
  RunComparisonSchema,
  ScanRunListResponseSchema,
  ScanStatusResponseSchema,
  StatusResponseSchema,
  StarterPackSchema,
  SubmitReviewResponseSchema,
  WebhookStatusSchema,
} from "./schemas";

export function openCodeRuntimeModelId(
  providerId: string | undefined,
  modelId: string | undefined,
): string | undefined {
  return providerId && modelId?.startsWith(`${providerId}/`)
    ? modelId.slice(providerId.length + 1)
    : modelId;
}

function parseResponse<T>(schema: ZodType<T>, data: unknown): T {
  return schema.parse(data);
}

// The backend's CSRFMiddleware uses the double-submit cookie pattern: the
// non-httpOnly `csrf_token` cookie set at login must be echoed back in the
// X-CSRF-Token header on every state-changing request.
export function getCsrfToken(): string | undefined {
  const raw = document.cookie.match(/(?:^|;\s*)csrf_token=([^;]*)/)?.[1];
  return raw ? decodeURIComponent(raw) : undefined;
}

export function csrfHeaders(): Record<string, string> {
  const token = getCsrfToken();
  return token ? { "X-CSRF-Token": token } : {};
}

async function request<T>(
  path: string,
  options: RequestInit = {},
  schema?: ZodType<T>
): Promise<T> {
  // Don't set Content-Type for FormData — browser sets it with the correct boundary
  const isFormData = options.body instanceof FormData;
  const res = await fetch(path, {
    ...options,
    headers: isFormData
      ? { ...csrfHeaders(), ...(options.headers as Record<string, string>) }
      : { "Content-Type": "application/json", ...csrfHeaders(), ...(options.headers as Record<string, string>) },
    credentials: "include",
  });

  if (res.status === 401) {
    window.location.href = `${getLoginUrl()}?redirect=${encodeURIComponent(window.location.href)}`;
    throw new Error("Not authenticated");
  }

  if (!res.ok) {
    const detail = await res.text();
    throw new Error(`${res.status}: ${detail}`);
  }
  if (res.status === 204) return undefined as T;
  const data = await res.json();
  return schema ? parseResponse(schema, data) : (data as T);
}

export const api = {
  listProjects: () =>
    request<{ projects: import("../types").Project[]; total: number }>(
      "/mcp/projects",
      {},
      ProjectListResponseSchema
    ),

  listPlatforms: () =>
    request<import("../types").PlatformListResponse>(
      "/mcp/platforms",
      {},
      PlatformListResponseSchema
    ),

  listModels: () =>
    request<import("../types").ModelListResponse>(
      "/mcp/models",
      {},
      ModelListResponseSchema
    ),

  getInstanceSettings: () =>
    request<{ settings: Record<string, string> }>(
      "/mcp/admin/instance-settings",
      {},
      InstanceSettingsResponseSchema
    ),

  updateInstanceSettings: (settings: Record<string, string | null>) =>
    request<{ settings: Record<string, string> }>(
      "/mcp/admin/instance-settings",
      {
        method: "PUT",
        body: JSON.stringify({ settings }),
      },
      InstanceSettingsResponseSchema
    ),

  addRepo: (data: { url: string; project_id: string; platform?: string; display_name?: string; github_token?: string; installation_id?: number }) =>
    request<import("../types").Project>("/mcp/add-repo", {
      method: "POST",
      body: JSON.stringify(data),
    }, ProjectSchema),

  // GitHub App connect: get the install URL to redirect the operator to.
  getGithubAppInstallUrl: (projectId?: string) =>
    request<{ install_url: string }>(
      `/mcp/github/app/connect${projectId ? `?project_id=${encodeURIComponent(projectId)}` : ""}`,
    ),

  // List repos an installation can access (for the repo-picker).
  listGithubAppRepos: (installationId: number) =>
    request<{
      installation_id: number;
      account_login: string | null;
      repositories: { full_name: string | null; clone_url: string | null; private: boolean | null; default_branch: string | null }[];
    }>(`/mcp/github/app/installations/${installationId}/repos`),

  updateGithubToken: (projectId: string, github_token: string) =>
    request<import("../types").Project>(`/mcp/projects/${projectId}/github-token`, {
      method: "PATCH",
      body: JSON.stringify({ github_token }),
    }, ProjectSchema),

  registerWebhook: (projectId: string) =>
    request<import("../types").Project>(`/mcp/projects/${projectId}/github-webhook/register`, {
      method: "POST",
    }, ProjectSchema),

  deleteWebhook: (projectId: string) =>
    request<import("../types").StatusResponse>(
      `/mcp/projects/${projectId}/github-webhook`,
      { method: "DELETE" },
      StatusResponseSchema
    ),

  getWebhookStatus: (projectId: string) =>
    request<{ project_id: string; webhook_status: string; webhook_id: number | null }>(
      `/mcp/projects/${projectId}/github-webhook/status`,
      {},
      WebhookStatusSchema
    ),

  uploadZip: (file: File, projectId: string, platform: string = "talend") => {
    const form = new FormData();
    form.append("file", file);
    form.append("project_id", projectId);
    form.append("platform", platform);
    return request<import("../types").Project>("/mcp/upload-zip", { method: "POST", body: form }, ProjectSchema);
  },

  scan: (projectId: string, force = false) =>
    request<import("../types").StatusResponse>("/mcp/scan", {
      method: "POST",
      body: JSON.stringify({ project_id: projectId, force }),
    }, StatusResponseSchema),

  pushToRag: (projectId: string) =>
    request<import("../types").StatusResponse>("/mcp/push-to-rag", {
      method: "POST",
      body: JSON.stringify({ project_id: projectId }),
    }, StatusResponseSchema),

  resetStatus: (projectId: string) =>
    request<import("../types").StatusResponse>("/mcp/reset-status", {
      method: "POST",
      body: JSON.stringify({ project_id: projectId }),
    }, StatusResponseSchema),

  repairLatestGitUpdate: (projectId: string) =>
    request<import("../types").StatusResponse>(
      `/mcp/projects/${projectId}/git-updates/repair-latest`,
      { method: "POST" },
      StatusResponseSchema
    ),

  deleteProject: (projectId: string) =>
    request<import("../types").StatusResponse>(`/mcp/projects/${projectId}`, {
      method: "DELETE",
    }, StatusResponseSchema),

  getStatus: (projectId: string) =>
    request<import("../types").ScanStatusResponse>(
      `/mcp/status/${projectId}`,
      {},
      ScanStatusResponseSchema
    ),

  listGenerateRuns: (projectId: string) =>
    request<{ runs: import("../types").GenerateRun[]; total: number }>(
      `/mcp/generate-runs/${projectId}`,
      {},
      GenerateRunListResponseSchema
    ),

  listLogFiles: (projectId: string) =>
    request<{ files: import("../types").LogFile[]; total: number }>(
      `/mcp/logs/${projectId}`,
      {},
      LogFileListResponseSchema
    ),

  getLogContent: (projectId: string, filename: string) =>
    request<{ filename: string; content: string }>(
      `/mcp/logs/${projectId}/${filename}`,
      {},
      LogContentSchema
    ),

  updateProject: (projectId: string, data: { display_name?: string; platform?: string; doc_generation_provider?: string | null; instruction_pack_dir?: string | null }) =>
    request<import("../types").Project>(`/mcp/projects/${projectId}`, {
      method: "PATCH",
      body: JSON.stringify(data),
    }, ProjectSchema),

  listPluginPacks: () =>
    request<import("../types").PluginPack[]>("/mcp/plugins", {}, PluginPackListSchema),

  getStarterPack: () =>
    request<import("../types").StarterPack>("/mcp/plugins/starter", {}, StarterPackSchema),

  discoverPluginPackBranches: (repoUrl?: string | null) =>
    request<import("../types").PluginPackBranchDiscovery>(
      "/mcp/plugins/discover",
      {
        method: "POST",
        body: JSON.stringify({ repo_url: repoUrl ?? undefined }),
      },
      PluginPackBranchDiscoverySchema
    ),

  getPackWorkspace: (projectId: string) =>
    request<import("../types").NexusPackWorkspace>(
      `/nexus-api/harness/packs/projects/${encodeURIComponent(projectId)}/workspace`,
      {},
      NexusPackWorkspaceSchema,
    ),

  updatePackWorkspace: (
    projectId: string,
    organizationPackId: number | null,
    privateVersionIds: number[],
  ) =>
    request<import("../types").NexusPackWorkspace>(
      `/nexus-api/harness/packs/projects/${encodeURIComponent(projectId)}/workspace`,
      {
        method: "PUT",
        body: JSON.stringify({
          organization_pack_id: organizationPackId,
          private_version_ids: privateVersionIds,
        }),
      },
      NexusPackWorkspaceSchema,
    ),

  assignPluginPack: (projectId: string, pluginPackId: number | null) =>
    request<{ project_id: string; plugin_pack_id: number | null }>(
      `/mcp/projects/${projectId}/plugin`,
      {
        method: "PATCH",
        body: JSON.stringify({ plugin_pack_id: pluginPackId }),
      },
      AssignPluginPackResponseSchema
    ),

  registerPluginPack: (body: { path?: string; repo_url?: string; repo_branch?: string }) =>
    request<import("../types").PluginPack>("/mcp/plugins", {
      method: "POST",
      body: JSON.stringify(body),
    }, PluginPackSchema),

  uploadPluginPackZip: (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return request<import("../types").PluginPack>("/mcp/plugins/upload", {
      method: "POST",
      body: form,
    }, PluginPackSchema);
  },

  pullPluginPack: (packId: number) =>
    request<import("../types").PluginPack>(`/mcp/plugins/${packId}`, {
      method: "PATCH",
      body: JSON.stringify({ pull: true }),
    }, PluginPackSchema),

  deletePluginPack: (packId: number) =>
    request<void>(`/mcp/plugins/${packId}`, { method: "DELETE" }),

  getPluginPackFiles: (packId: number) =>
    request<string[]>(`/mcp/plugins/${packId}/files`, {}, PluginPackFilesSchema),

  getPluginPackBranchFiles: (repoUrl: string, repoBranch: string) =>
    request<string[]>("/mcp/plugins/branch-files", {
      method: "POST",
      body: JSON.stringify({ repo_url: repoUrl, repo_branch: repoBranch }),
    }, PluginPackFilesSchema),

  getPluginPackBranchFile: (repoUrl: string, repoBranch: string, filePath: string) =>
    request<import("../types").PluginPackFileContent>("/mcp/plugins/branch-file", {
      method: "POST",
      body: JSON.stringify({ repo_url: repoUrl, repo_branch: repoBranch, file_path: filePath }),
    }, PluginPackFileSchema),

  getPluginPackFile: (packId: number, filePath: string) =>
    request<{ path: string; content: string }>(
      `/mcp/plugins/${packId}/files/${filePath}`,
      {},
      PluginPackFileSchema
    ),

  savePluginPackFile: (packId: number, filePath: string, content: string) =>
    request<{ path: string; content: string }>(`/mcp/plugins/${packId}/files/${filePath}`, {
      method: "PUT",
      body: JSON.stringify({ content }),
    }, PluginPackFileSchema),

  listScanRuns: (projectId: string) =>
    request<{ runs: import("../types").ScanRun[]; total: number }>(
      `/mcp/scan-runs/${projectId}`,
      {},
      ScanRunListResponseSchema
    ),

  listDocs: (projectId: string, source: import("../types").DocSource = "published") =>
    request<{ project_id: string; docs: import("../types").DocFile[]; total: number }>(
      `/mcp/projects/${projectId}/docs?source=${source}`,
      {},
      DocListResponseSchema
    ),

  getDocContent: (projectId: string, filename: string, source: import("../types").DocSource = "published") =>
    request<{ filename: string; content: string }>(
      `/mcp/projects/${projectId}/docs/${filename}/content?source=${source}`,
      {},
      DocContentSchema
    ),

  getProjectUsage: (projectId: string) =>
    request<import("../types").ProjectUsage>(
      `/mcp/projects/${projectId}/usage`,
      {},
      ProjectUsageSchema
    ),

  compareGenerateRuns: (projectId: string, a: number, b: number) =>
    request<import("../types").RunComparison>(
      `/mcp/generate-runs/${projectId}/compare?a=${a}&b=${b}`,
      {},
      RunComparisonSchema
    ),

  getQualityHistory: (projectId: string) =>
    request<import("../types").QualityHistoryResponse>(
      `/mcp/projects/${projectId}/quality-history`,
      {},
      QualityHistoryResponseSchema
    ),

  getQuestionnaire: (projectId: string, runId: number) =>
    request<import("../types").QuestionnaireResponse>(
      `/mcp/projects/${projectId}/questionnaire?run_id=${runId}`,
      {},
      QuestionnaireResponseSchema
    ),

  submitReview: (
    projectId: string,
    data: {
      run_id: number;
      reviewer: string;
      overall: number;
      questions: import("../types").ReviewQuestion[];
      feedback: string;
      reviewed_at: string;
    },
  ) =>
    request<{ status: string; path: string }>(`/mcp/projects/${projectId}/reviews`, {
      method: "POST",
      body: JSON.stringify(data),
    }, SubmitReviewResponseSchema),

  getReviews: (projectId: string, runId?: number | null) =>
    request<import("../types").ReviewAggregateResponse>(
      `/mcp/projects/${projectId}/reviews${runId != null ? `?run_id=${runId}` : ""}`,
      {},
      ReviewAggregateResponseSchema
    ),

  getInstructionPack: () =>
    request<import("../types").InstructionPackMetadata>(
      "/mcp/instruction-pack",
      {},
      InstructionPackMetadataSchema
    ),

  generateAssessment: (projectId: string) =>
    request<{ project_id: string; status: string; html_path: string | null; error: string | null }>(
      `/mcp/projects/${projectId}/generate-assessment`,
      { method: "POST" },
      GenerateAssessmentResponseSchema
    ),

  generate: (
    projectId: string,
    resume = true,
    investigationModel?: string | null,
    model?: string | null,
    writeModel?: string | null,
    organizationPackId: number | null = null,
    privateVersionIds: number[] = [],
  ) =>
    request<import("../types").StatusResponse>(
      `/nexus-api/harness/packs/projects/${encodeURIComponent(projectId)}/generate`,
      {
        method: "POST",
        body: JSON.stringify({
          project_id: projectId,
          resume,
          investigation_model: investigationModel ?? null,
          model: model ?? null,
          write_model: writeModel ?? null,
          organization_pack_id: organizationPackId,
          private_version_ids: privateVersionIds,
        }),
      },
      StatusResponseSchema,
    ),

  generateOpenCodeCandidate: (
    projectId: string,
    candidateMode: "single_agent" | "orchestrated" | "grounded" = "single_agent",
    providerId?: string,
    modelId?: string,
  ) => {
    // Discovery exposes canonical routes as ``provider/model`` while the
    // OpenCode HTTP API accepts providerID and the provider-local modelID as
    // separate fields.
    const runtimeModelId = openCodeRuntimeModelId(providerId, modelId);
    return request<import("../types").AgenticExperiment>(
      "/mcp/agentic-experiments/quick-run",
      {
        method: "POST",
        body: JSON.stringify({
          project_id: projectId,
          recipe_id: "alitalend-legacy-equivalence",
          recipe_version: "1.0.0",
          provider_id: providerId,
          model_id: runtimeModelId,
          candidate_mode: candidateMode,
          acknowledge_non_publishing: true,
          idempotency_key: `generate-ui:${projectId}:${globalThis.crypto.randomUUID()}`,
        }),
      },
      AgenticExperimentSchema,
    );
  },

  listAgenticExperiments: (projectId: string) =>
    request<import("../types").AgenticExperiment[]>(
      `/mcp/agentic-experiments?project_id=${encodeURIComponent(projectId)}`,
      {},
      AgenticExperimentListSchema,
    ),

  getAgenticExperimentCapabilities: () =>
    request<import("../types").AgenticExperimentCapabilities>(
      "/mcp/agentic-experiments/capabilities",
      {},
      AgenticExperimentCapabilitiesSchema,
    ),

  getAgenticExperiment: (experimentId: string) =>
    request<import("../types").AgenticExperiment>(
      `/mcp/agentic-experiments/${encodeURIComponent(experimentId)}`,
      {},
      AgenticExperimentSchema,
    ),

  createAgenticExperiment: (body: {
    project_id: string;
    baseline_run_id?: number | null;
    recipe_id?: string | null;
    recipe_version?: string | null;
    runtime_id?: string;
    provider_id?: string | null;
    model_id?: string | null;
    scope?: Record<string, unknown> | null;
    acknowledge_non_publishing: true;
    idempotency_key: string;
  }) =>
    request<import("../types").AgenticExperiment>(
      "/mcp/agentic-experiments",
      { method: "POST", body: JSON.stringify(body) },
      AgenticExperimentSchema,
    ),

  freezeAgenticSourceSnapshot: (
    experimentId: string,
    sources: import("../types").AgenticSourceInput[] = [{
      id: "current-intodoc-scan",
      source_kind: "intodoc_scan",
      source_role: "implementation",
      capabilities: ["searchable_text", "structured_records"],
    }],
  ) =>
    request<import("../types").AgenticSourceSnapshot>(
      `/mcp/agentic-experiments/${encodeURIComponent(experimentId)}/source-snapshot`,
      {
        method: "POST",
        body: JSON.stringify({ sources }),
      },
      AgenticSourceSnapshotSchema,
    ),

  compileAgenticRecipe: (
    experimentId: string,
    recipe: { recipe_id: string; recipe_version: string; model_capabilities: string[]; scope?: Record<string, unknown> | null },
  ) =>
    request<import("../types").AgenticRecipeSnapshot>(
      `/mcp/agentic-experiments/${encodeURIComponent(experimentId)}/recipe-snapshot`,
      { method: "POST", body: JSON.stringify(recipe) },
      AgenticRecipeSnapshotSchema,
    ),

  listAgenticExperimentTasks: (experimentId: string) =>
    request<import("../types").AgenticExperimentTaskList>(
      `/mcp/agentic-experiments/${encodeURIComponent(experimentId)}/tasks`,
      {},
      AgenticExperimentTaskListSchema,
    ),

  retryAgenticExperimentTask: (experimentId: string, taskRunId: string) =>
    request<import("../types").AgenticExperimentTask>(
      `/mcp/agentic-experiments/${encodeURIComponent(experimentId)}/tasks/${encodeURIComponent(taskRunId)}/retry`,
      { method: "POST", body: JSON.stringify({ acknowledge_explicit_retry: true }) },
      AgenticExperimentTaskSchema,
    ),

  reconcileAgenticExperiment: (experimentId: string) =>
    request<import("../types").AgenticReconcileResult>(
      `/mcp/agentic-experiments/${encodeURIComponent(experimentId)}/reconcile`,
      {
        method: "POST",
        body: JSON.stringify({ acknowledge_no_dispatch: true }),
      },
      AgenticReconcileResultSchema,
    ),

  quarantineAgenticExperiment: (experimentId: string) =>
    request<import("../types").AgenticRetentionResult>(
      `/mcp/agentic-experiments/${encodeURIComponent(experimentId)}/quarantine`,
      {
        method: "POST",
        body: JSON.stringify({ acknowledge_recoverable_move: true }),
      },
      AgenticRetentionResultSchema,
    ),

  restoreAgenticExperiment: (experimentId: string) =>
    request<import("../types").AgenticRetentionResult>(
      `/mcp/agentic-experiments/${encodeURIComponent(experimentId)}/restore`,
      {
        method: "POST",
        body: JSON.stringify({ acknowledge_recoverable_move: true }),
      },
      AgenticRetentionResultSchema,
    ),
};
