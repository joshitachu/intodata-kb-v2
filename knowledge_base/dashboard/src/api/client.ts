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
  const res = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json", ...options.headers as Record<string, string> },
    credentials: "include",
    ...options,
  });

  if (res.status === 401) {
    window.location.href = `${getLoginUrl()}?redirect=${encodeURIComponent(window.location.href)}`;
    throw new Error("Not authenticated");
  }

  if (!res.ok) {
    const detail = await res.text();
    throw new Error(`${res.status}: ${detail}`);
  }
  return res.json();
}

export const api = {
  listProjects: () => request<{ projects: import("../types").Project[]; total: number }>("/mcp/projects"),

  listPlatforms: () => request<import("../types").PlatformListResponse>("/mcp/platforms"),

  listModels: () => request<import("../types").ModelListResponse>("/mcp/models"),

  addRepo: (data: { url: string; project_id: string; platform?: string; display_name?: string }) =>
    request<import("../types").Project>("/mcp/add-repo", {
      method: "POST",
      body: JSON.stringify(data),
    }),

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

  updateProject: (projectId: string, data: { display_name?: string; platform?: string; doc_generation_provider?: string | null }) =>
    request<import("../types").Project>(`/mcp/projects/${projectId}`, {
      method: "PATCH",
      body: JSON.stringify(data),
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
    templateProfileId?: number,
    resume = true,
    model?: string | null,
    writeModel?: string | null,
  ) =>
    request<import("../types").StatusResponse>("/mcp/generate", {
      method: "POST",
      body: JSON.stringify({
        project_id: projectId,
        template_profile_id: templateProfileId ?? null,
        resume,
        model: model ?? null,
        write_model: writeModel ?? null,
      }),
    }),

  // --- Templates ---

  listTemplates: (params?: { category?: string; platform?: string; tier?: string }) => {
    const qs = new URLSearchParams();
    if (params?.category) qs.set("category", params.category);
    if (params?.platform) qs.set("platform", params.platform);
    if (params?.tier) qs.set("tier", params.tier);
    const query = qs.toString();
    return request<{ templates: import("../types").Template[]; total: number }>(
      `/mcp/templates/${query ? `?${query}` : ""}`
    );
  },

  getTemplate: (slug: string) =>
    request<import("../types").Template>(`/mcp/templates/by-slug?slug=${encodeURIComponent(slug)}`),

  createTemplate: (data: { slug: string; category: string; display_name: string; platform?: string; description?: string }) =>
    request<import("../types").Template>("/mcp/templates/", {
      method: "POST",
      body: JSON.stringify(data),
    }),

  updateTemplate: (slug: string, data: { display_name?: string; description?: string; is_active?: boolean }) =>
    request<import("../types").Template>(`/mcp/templates/by-slug?slug=${encodeURIComponent(slug)}`, {
      method: "PATCH",
      body: JSON.stringify(data),
    }),

  listVersions: (slug: string) =>
    request<{ versions: import("../types").TemplateVersion[]; total: number }>(
      `/mcp/templates/versions?slug=${encodeURIComponent(slug)}`
    ),

  getVersion: (slug: string, versionNumber: number) =>
    request<import("../types").TemplateVersion>(
      `/mcp/templates/versions/detail?slug=${encodeURIComponent(slug)}&version_number=${versionNumber}`
    ),

  createVersion: (slug: string, data: { content: string; change_summary: string; created_by?: string }) =>
    request<import("../types").TemplateVersion>(`/mcp/templates/versions?slug=${encodeURIComponent(slug)}`, {
      method: "POST",
      body: JSON.stringify(data),
    }),

  diffVersions: (slug: string, from: number, to: number) =>
    request<{ slug: string; from_version: number; to_version: number; diff: string }>(
      `/mcp/templates/diff?slug=${encodeURIComponent(slug)}&from=${from}&to=${to}`
    ),

  rollbackVersion: (slug: string, versionNumber: number) =>
    request<import("../types").TemplateVersion>(
      `/mcp/templates/rollback?slug=${encodeURIComponent(slug)}&version_number=${versionNumber}`,
      { method: "POST" }
    ),

  // --- Profiles ---

  listProfiles: (params?: { platform?: string; scope?: string }) => {
    const qs = new URLSearchParams();
    if (params?.platform) qs.set("platform", params.platform);
    if (params?.scope) qs.set("scope", params.scope);
    const query = qs.toString();
    return request<{ profiles: import("../types").TemplateProfile[]; total: number }>(
      `/mcp/profiles/${query ? `?${query}` : ""}`
    );
  },

  getProfile: (id: number) =>
    request<import("../types").ProfileDetail>(`/mcp/profiles/${id}`),

  createProfile: (data: { name: string; platform?: string; description?: string; scope?: string }) =>
    request<import("../types").TemplateProfile>("/mcp/profiles/", {
      method: "POST",
      body: JSON.stringify(data),
    }),

  updateProfile: (id: number, data: { name?: string; description?: string }) =>
    request<import("../types").TemplateProfile>(`/mcp/profiles/${id}`, {
      method: "PATCH",
      body: JSON.stringify(data),
    }),

  cloneProfile: (id: number, newName: string) =>
    request<import("../types").TemplateProfile>(`/mcp/profiles/${id}/clone`, {
      method: "POST",
      body: JSON.stringify({ new_name: newName }),
    }),

  setDefaultProfile: (id: number) =>
    request<import("../types").TemplateProfile>(`/mcp/profiles/${id}/set-default`, {
      method: "POST",
    }),

  listAssignments: (profileId: number) =>
    request<{ assignments: import("../types").ProfileAssignment[]; total: number }>(
      `/mcp/profiles/${profileId}/assignments`
    ),

  setAssignment: (profileId: number, slug: string, versionId: number) =>
    request<import("../types").ProfileAssignment>(
      `/mcp/profiles/${profileId}/assignments?slug=${encodeURIComponent(slug)}`,
      { method: "PATCH", body: JSON.stringify({ slug, version_id: versionId }) }
    ),

  deleteAssignment: (profileId: number, slug: string) =>
    request<import("../types").StatusResponse>(
      `/mcp/profiles/${profileId}/assignments?slug=${encodeURIComponent(slug)}`,
      { method: "DELETE" }
    ),
};
