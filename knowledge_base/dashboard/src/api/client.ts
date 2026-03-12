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

  generate: (projectId: string) =>
    request<import("../types").StatusResponse>("/mcp/generate", {
      method: "POST",
      body: JSON.stringify({ project_id: projectId }),
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
};
