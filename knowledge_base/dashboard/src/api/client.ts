const BASE = "";

async function request<T>(
  path: string,
  options: RequestInit = {}
): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json", ...options.headers as Record<string, string> },
    ...options,
  });
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
    return fetch(`${BASE}/mcp/upload-zip`, { method: "POST", body: form }).then(
      (r) => {
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
};
