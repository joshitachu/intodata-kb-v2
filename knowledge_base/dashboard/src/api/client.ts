import { type ZodType } from "zod";
import { getLoginUrl } from "../hooks/useAuth";
import {
  AssignPluginPackResponseSchema,
  DocContentSchema,
  DocListResponseSchema,
  GenerateAssessmentResponseSchema,
  GenerateRunListResponseSchema,
  InstructionPackMetadataSchema,
  LogContentSchema,
  LogFileListResponseSchema,
  ModelListResponseSchema,
  PlatformListResponseSchema,
  PluginPackFileSchema,
  PluginPackFilesSchema,
  PluginPackListSchema,
  PluginPackSchema,
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
  SubmitReviewResponseSchema,
  WebhookStatusSchema,
} from "./schemas";

function parseResponse<T>(schema: ZodType<T>, data: unknown): T {
  return schema.parse(data);
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
      ? { ...(options.headers as Record<string, string>) }
      : { "Content-Type": "application/json", ...(options.headers as Record<string, string>) },
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

  addRepo: (data: { url: string; project_id: string; platform?: string; display_name?: string; github_token?: string }) =>
    request<import("../types").Project>("/mcp/add-repo", {
      method: "POST",
      body: JSON.stringify(data),
    }, ProjectSchema),

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

  assignPluginPack: (projectId: string, pluginPackId: number | null) =>
    request<{ project_id: string; plugin_pack_id: number | null }>(
      `/mcp/projects/${projectId}/plugin`,
      {
        method: "PATCH",
        body: JSON.stringify({ plugin_pack_id: pluginPackId }),
      },
      AssignPluginPackResponseSchema
    ),

  registerPluginPack: (body: { path?: string; repo_url?: string }) =>
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
  ) =>
    request<import("../types").StatusResponse>("/mcp/generate", {
      method: "POST",
      body: JSON.stringify({
        project_id: projectId,
        resume,
        investigation_model: investigationModel ?? null,
        model: model ?? null,
        write_model: writeModel ?? null,
      }),
    }, StatusResponseSchema),
};
