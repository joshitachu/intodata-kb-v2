const BASE = "";

function getLoginUrl(): string {
  if (import.meta.env.VITE_LOGIN_URL) {
    return import.meta.env.VITE_LOGIN_URL;
  }
  return `${window.location.protocol}//${window.location.hostname}/login`;
}

async function request<T>(
  path: string,
  options: RequestInit = {}
): Promise<T> {
  // Don't set Content-Type for FormData — browser sets it with the correct boundary
  const isFormData = options.body instanceof FormData;
  const res = await fetch(`${BASE}${path}`, {
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
  return res.json();
}

export const api = {
  listProjects: () => request<{ projects: import("../types").Project[]; total: number }>("/mcp/projects"),

  listPlatforms: () => request<import("../types").PlatformListResponse>("/mcp/platforms"),

  listModels: () => request<import("../types").ModelListResponse>("/mcp/models"),

  addRepo: (data: { url: string; project_id: string; platform?: string; display_name?: string; github_token?: string }) =>
    request<import("../types").Project>("/mcp/add-repo", {
      method: "POST",
      body: JSON.stringify(data),
    }),

  updateGithubToken: (projectId: string, github_token: string) =>
    request<import("../types").Project>(`/mcp/projects/${projectId}/github-token`, {
      method: "PATCH",
      body: JSON.stringify({ github_token }),
    }),

  registerWebhook: (projectId: string) =>
    request<import("../types").Project>(`/mcp/projects/${projectId}/github-webhook/register`, {
      method: "POST",
    }),

  deleteWebhook: (projectId: string) =>
    request<import("../types").StatusResponse>(`/mcp/projects/${projectId}/github-webhook`, {
      method: "DELETE",
    }),

  getWebhookStatus: (projectId: string) =>
    request<{ project_id: string; webhook_status: string; webhook_id: number | null }>(
      `/mcp/projects/${projectId}/github-webhook/status`
    ),

  uploadZip: (file: File, projectId: string, platform: string = "talend") => {
    const form = new FormData();
    form.append("file", file);
    form.append("project_id", projectId);
    form.append("platform", platform);
    return fetch(`${BASE}/mcp/upload-zip`, {
      method: "POST",
      body: form,
      credentials: "include",
    }).then(
      (r) => {
        if (r.status === 401) {
          window.location.href = `${getLoginUrl()}?redirect=${encodeURIComponent(window.location.href)}`;
          throw new Error("Not authenticated");
        }
        if (!r.ok) throw new Error(`Upload failed: ${r.status}`);
        return r.json() as Promise<import("../types").Project>;
      }
    );
  },

  scan: (projectId: string, force = false) =>
    request<import("../types").StatusResponse>("/mcp/scan", {
      method: "POST",
      body: JSON.stringify({ project_id: projectId, force }),
    }),

  pushToRag: (projectId: string) =>
    request<import("../types").StatusResponse>("/mcp/push-to-rag", {
      method: "POST",
      body: JSON.stringify({ project_id: projectId }),
    }),

  resetStatus: (projectId: string) =>
    request<import("../types").StatusResponse>("/mcp/reset-status", {
      method: "POST",
      body: JSON.stringify({ project_id: projectId }),
    }),

  repairLatestGitUpdate: (projectId: string) =>
    request<import("../types").StatusResponse>(`/mcp/projects/${projectId}/git-updates/repair-latest`, {
      method: "POST",
    }),

  deleteProject: (projectId: string) =>
    request<{ status: string; message: string }>(`/mcp/projects/${projectId}`, {
      method: "DELETE",
    }),

  getStatus: (projectId: string) =>
    request<import("../types").ScanStatusResponse>(`/mcp/status/${projectId}`),

  listGenerateRuns: (projectId: string) =>
    request<{ runs: import("../types").GenerateRun[]; total: number }>(
      `/mcp/generate-runs/${projectId}`
    ),

  listLogFiles: (projectId: string) =>
    request<{ files: import("../types").LogFile[]; total: number }>(
      `/mcp/logs/${projectId}`
    ),

  getLogContent: (projectId: string, filename: string) =>
    request<{ filename: string; content: string }>(
      `/mcp/logs/${projectId}/${filename}`
    ),

  updateProject: (projectId: string, data: { display_name?: string; platform?: string; doc_generation_provider?: string | null; instruction_pack_dir?: string | null }) =>
    request<import("../types").Project>(`/mcp/projects/${projectId}`, {
      method: "PATCH",
      body: JSON.stringify(data),
    }),

  listPluginPacks: () =>
    request<import("../types").PluginPack[]>("/mcp/plugins"),

  assignPluginPack: (projectId: string, pluginPackId: number | null) =>
    request<{ project_id: string; plugin_pack_id: number | null }>(
      `/mcp/projects/${projectId}/plugin`,
      {
        method: "PATCH",
        body: JSON.stringify({ plugin_pack_id: pluginPackId }),
      }
    ),

  registerPluginPack: (body: { path?: string; repo_url?: string }) =>
    request<import("../types").PluginPack>("/mcp/plugins", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  uploadPluginPackZip: (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return request<import("../types").PluginPack>("/mcp/plugins/upload", {
      method: "POST",
      body: form,
    });
  },

  pullPluginPack: (packId: number) =>
    request<import("../types").PluginPack>(`/mcp/plugins/${packId}`, {
      method: "PATCH",
      body: JSON.stringify({ pull: true }),
    }),

  deletePluginPack: (packId: number) =>
    request<void>(`/mcp/plugins/${packId}`, { method: "DELETE" }),

  getPluginPackFiles: (packId: number) =>
    request<string[]>(`/mcp/plugins/${packId}/files`),

  getPluginPackFile: (packId: number, filePath: string) =>
    request<{ path: string; content: string }>(`/mcp/plugins/${packId}/files/${filePath}`),

  savePluginPackFile: (packId: number, filePath: string, content: string) =>
    request<{ path: string; content: string }>(`/mcp/plugins/${packId}/files/${filePath}`, {
      method: "PUT",
      body: JSON.stringify({ content }),
    }),

  listScanRuns: (projectId: string) =>
    request<{ runs: import("../types").ScanRun[]; total: number }>(
      `/mcp/scan-runs/${projectId}`
    ),

  listDocs: (projectId: string) =>
    request<{ project_id: string; docs: import("../types").DocFile[]; total: number }>(
      `/mcp/projects/${projectId}/docs`
    ),

  getDocContent: (projectId: string, filename: string) =>
    request<{ filename: string; content: string }>(
      `/mcp/projects/${projectId}/docs/${filename}/content`
    ),

  getProjectUsage: (projectId: string) =>
    request<import("../types").ProjectUsage>(
      `/mcp/projects/${projectId}/usage`
    ),

  compareGenerateRuns: (projectId: string, a: number, b: number) =>
    request<import("../types").RunComparison>(
      `/mcp/generate-runs/${projectId}/compare?a=${a}&b=${b}`
    ),

  getQualityHistory: (projectId: string) =>
    request<import("../types").QualityHistoryResponse>(
      `/mcp/projects/${projectId}/quality-history`
    ),

  getQuestionnaire: (projectId: string, runId: number) =>
    request<import("../types").QuestionnaireResponse>(
      `/mcp/projects/${projectId}/questionnaire?run_id=${runId}`
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
    }),

  getReviews: (projectId: string, runId?: number | null) =>
    request<import("../types").ReviewAggregateResponse>(
      `/mcp/projects/${projectId}/reviews${runId != null ? `?run_id=${runId}` : ""}`
    ),

  getInstructionPack: () =>
    request<import("../types").InstructionPackMetadata>("/mcp/instruction-pack"),

  generateAssessment: (projectId: string) =>
    request<{ project_id: string; status: string; html_path: string | null; error: string | null }>(
      `/mcp/projects/${projectId}/generate-assessment`,
      { method: "POST" }
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
    }),
};
