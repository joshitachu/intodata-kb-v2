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

  deleteProject: (projectId: string) =>
    request<{ status: string; message: string }>(`/mcp/projects/${projectId}`, {
      method: "DELETE",
    }),

  getStatus: (projectId: string) =>
    request<{
      project_id: string;
      status: string;
      last_scan_at: string | null;
      cache_age_minutes: number | null;
      job_count: number;
      doc_count: number;
      error_message: string | null;
    }>(`/mcp/status/${projectId}`),

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

  updateProject: (projectId: string, data: { display_name?: string; platform?: string }) =>
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

  generateAssessment: (projectId: string) =>
    request<{ project_id: string; status: string; html_path: string | null; error: string | null }>(
      `/mcp/projects/${projectId}/generate-assessment`,
      { method: "POST" }
    ),

  generate: (projectId: string, templateProfileId?: number) =>
    request<import("../types").StatusResponse>("/mcp/generate", {
      method: "POST",
      body: JSON.stringify({
        project_id: projectId,
        template_profile_id: templateProfileId ?? null,
      }),
    }),

  // --- Templates ---

  listTemplates: (params?: { category?: string; platform?: string }) => {
    const qs = new URLSearchParams();
    if (params?.category) qs.set("category", params.category);
    if (params?.platform) qs.set("platform", params.platform);
    const query = qs.toString();
    return request<{ templates: import("../types").Template[]; total: number }>(
      `/mcp/templates/${query ? `?${query}` : ""}`
    );
  },

  getTemplate: (slug: string) =>
    request<import("../types").Template>(`/mcp/templates/${slug}`),

  createTemplate: (data: { slug: string; category: string; display_name: string; platform?: string; description?: string }) =>
    request<import("../types").Template>("/mcp/templates/", {
      method: "POST",
      body: JSON.stringify(data),
    }),

  updateTemplate: (slug: string, data: { display_name?: string; description?: string; is_active?: boolean }) =>
    request<import("../types").Template>(`/mcp/templates/${slug}`, {
      method: "PATCH",
      body: JSON.stringify(data),
    }),

  listVersions: (slug: string) =>
    request<{ versions: import("../types").TemplateVersion[]; total: number }>(
      `/mcp/templates/${slug}/versions`
    ),

  getVersion: (slug: string, versionNumber: number) =>
    request<import("../types").TemplateVersion>(
      `/mcp/templates/${slug}/versions/${versionNumber}`
    ),

  createVersion: (slug: string, data: { content: string; change_summary: string; created_by?: string }) =>
    request<import("../types").TemplateVersion>(`/mcp/templates/${slug}/versions`, {
      method: "POST",
      body: JSON.stringify(data),
    }),

  diffVersions: (slug: string, from: number, to: number) =>
    request<{ slug: string; from_version: number; to_version: number; diff: string }>(
      `/mcp/templates/${slug}/diff?from=${from}&to=${to}`
    ),

  rollbackVersion: (slug: string, versionNumber: number) =>
    request<import("../types").TemplateVersion>(
      `/mcp/templates/${slug}/rollback/${versionNumber}`,
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
      `/mcp/profiles/${profileId}/assignments/${slug}`,
      { method: "PATCH", body: JSON.stringify({ slug, version_id: versionId }) }
    ),

  deleteAssignment: (profileId: number, slug: string) =>
    request<import("../types").StatusResponse>(
      `/mcp/profiles/${profileId}/assignments/${slug}`,
      { method: "DELETE" }
    ),
};
