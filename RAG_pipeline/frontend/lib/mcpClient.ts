/**
 * Browser-side MCP client.
 *
 * Talks to Next.js API routes which proxy to the MCP server from the server
 * side. Customer deployments do not expose /mcp/* or /logs/* publicly.
 */

export interface UploadZipArgs {
  file: File;
  projectId: string;
  platform: string;
  onProgress?: (pct: number) => void;
  signal?: AbortSignal;
}

export interface UploadZipResult {
  project_id: string;
  display_name: string | null;
  local_path: string | null;
}

export interface GithubSourceArgs {
  repoUrl: string;
  projectId: string;
  platform: string;
  displayName?: string;
  // PAT connection (fallback). Omit for GitHub App connections.
  githubToken?: string;
  // GitHub App connection (preferred). Set after installing the App.
  installationId?: number;
}

export interface SourceProject {
  id: string;
  display_name: string;
  repo_url?: string | null;
  platform: string;
  source_type: string;
  status: string;
  job_count?: number;
  doc_count?: number;
  error_message?: string | null;
  last_scan_at?: string | null;
  last_generate_at?: string | null;
  last_rag_push_at?: string | null;
  auto_reingest_enabled?: boolean;
  auto_reingest_last_checked_at?: string | null;
  auto_reingest_error?: string | null;
  last_ingested_commit_sha?: string | null;
  git_update_summary?: Record<string, unknown> | null;
  has_github_token?: boolean;
  webhook_status?: string | null;
}

/**
 * Upload a ZIP to MCP. Uses XMLHttpRequest so we can surface upload progress
 * (fetch does not expose upload progress in browsers).
 */
export function uploadZip({
  file,
  projectId,
  platform,
  onProgress,
  signal,
}: UploadZipArgs): Promise<UploadZipResult> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    const form = new FormData();
    form.append("file", file);
    form.append("project_id", projectId);
    form.append("platform", platform);

    xhr.open("POST", "/api/source/upload-zip");
    xhr.withCredentials = true;

    if (onProgress) {
      xhr.upload.onprogress = (e) => {
        if (e.lengthComputable) {
          onProgress(Math.round((e.loaded / e.total) * 100));
        }
      };
    }

    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        try {
          resolve(JSON.parse(xhr.responseText));
        } catch {
          reject(new Error("Invalid response from /api/source/upload-zip"));
        }
      } else {
        reject(new Error(`Upload failed (${xhr.status}): ${xhr.responseText}`));
      }
    };
    xhr.onerror = () => reject(new Error("Network error during upload"));
    xhr.onabort = () => reject(new DOMException("Aborted", "AbortError"));

    if (signal) {
      signal.addEventListener("abort", () => xhr.abort(), { once: true });
    }

    xhr.send(form);
  });
}

/**
 * Explicitly trigger an `intodoc scan` for a project. Required after
 * `/api/source/upload-zip` — the upload endpoint only unpacks the ZIP and registers
 * the project, it does not auto-kick the scan.
 */
export async function scanProject(projectId: string, force = true): Promise<void> {
  const res = await fetch("/api/source/scan", {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ project_id: projectId, force }),
  });
  if (!res.ok) {
    throw new Error(`/api/source/scan failed (${res.status}): ${await res.text()}`);
  }
}

/**
 * Kick off doc generation. Called after the scan complete event arrives on
 * the log stream.
 */
export async function generateDocs(projectId: string, resume = true): Promise<void> {
  const res = await fetch("/api/source/generate", {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      project_id: projectId,
      resume,
      template_profile_id: null,
      model: null,
      write_model: null,
    }),
  });
  if (!res.ok) {
    throw new Error(`/api/source/generate failed (${res.status}): ${await res.text()}`);
  }
}

export async function pushProjectToRag(projectId: string): Promise<void> {
  const res = await fetch("/api/source/push-to-rag", {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ project_id: projectId }),
  });
  if (!res.ok) {
    throw new Error(`/api/source/push-to-rag failed (${res.status}): ${await res.text()}`);
  }
}

export async function resetProjectStatus(projectId: string): Promise<void> {
  const res = await fetch("/api/source/reset-status", {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ project_id: projectId }),
  });
  if (!res.ok) {
    throw new Error(`/api/source/reset-status failed (${res.status}): ${await res.text()}`);
  }
}

export async function deleteSourceProject(projectId: string): Promise<void> {
  const res = await fetch(`/api/source/projects/${encodeURIComponent(projectId)}`, {
    method: "DELETE",
    credentials: "include",
  });
  if (!res.ok) {
    throw new Error(`Delete source failed (${res.status}): ${await res.text()}`);
  }
}

export async function addGithubSource(args: GithubSourceArgs): Promise<SourceProject> {
  const res = await fetch("/api/source/github", {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      repo_url: args.repoUrl,
      project_id: args.projectId,
      platform: args.platform,
      display_name: args.displayName,
      github_token: args.githubToken,
      installation_id: args.installationId,
    }),
  });
  if (!res.ok) {
    throw new Error(`/api/source/github failed (${res.status}): ${await res.text()}`);
  }
  return res.json();
}

// Start a GitHub App install: returns the URL to redirect the browser to.
export async function getGithubAppInstallUrl(projectId?: string): Promise<string> {
  const qs = projectId ? `?project_id=${encodeURIComponent(projectId)}` : "";
  const res = await fetch(`/api/source/github/connect${qs}`, { credentials: "include" });
  if (!res.ok) {
    throw new Error(`/api/source/github/connect failed (${res.status}): ${await res.text()}`);
  }
  const data = await res.json();
  return data.install_url as string;
}

export interface GithubAppInstallation {
  installation_id: number;
  account_login: string | null;
}

export async function listGithubAppInstallations(): Promise<GithubAppInstallation[]> {
  const res = await fetch("/api/source/github/installations", { credentials: "include" });
  if (!res.ok) {
    throw new Error(`/api/source/github/installations failed (${res.status}): ${await res.text()}`);
  }
  const data = await res.json();
  return data.installations ?? [];
}

export interface GithubAppRepo {
  full_name: string | null;
  clone_url: string | null;
  private: boolean | null;
  default_branch: string | null;
}

// List repositories an installation can access (for the repo-picker).
export async function listGithubAppRepos(
  installationId: number,
): Promise<{ account_login: string | null; repositories: GithubAppRepo[] }> {
  const res = await fetch(`/api/source/github/installations/${installationId}/repos`, {
    credentials: "include",
  });
  if (!res.ok) {
    throw new Error(`/api/source/github/installations failed (${res.status}): ${await res.text()}`);
  }
  const data = await res.json();
  return { account_login: data.account_login ?? null, repositories: data.repositories ?? [] };
}

export async function updateGithubToken(
  projectId: string,
  githubToken: string,
): Promise<SourceProject> {
  const res = await fetch(`/api/source/github/${encodeURIComponent(projectId)}/token`, {
    method: "PATCH",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ github_token: githubToken }),
  });
  if (!res.ok) {
    throw new Error(`GitHub token update failed (${res.status}): ${await res.text()}`);
  }
  return res.json();
}

export async function registerGithubWebhook(projectId: string): Promise<SourceProject> {
  const res = await fetch(`/api/source/github/${encodeURIComponent(projectId)}/webhook`, {
    method: "POST",
    credentials: "include",
  });
  if (!res.ok) {
    throw new Error(`Webhook registration failed (${res.status}): ${await res.text()}`);
  }
  return res.json();
}

export async function deleteGithubWebhook(projectId: string): Promise<void> {
  const res = await fetch(`/api/source/github/${encodeURIComponent(projectId)}/webhook`, {
    method: "DELETE",
    credentials: "include",
  });
  if (!res.ok) {
    throw new Error(`Webhook deletion failed (${res.status}): ${await res.text()}`);
  }
}

export async function checkGithubWebhookStatus(
  projectId: string,
): Promise<{ project_id: string; webhook_status: string; webhook_id: number | null }> {
  const res = await fetch(
    `/api/source/github/${encodeURIComponent(projectId)}/webhook/status`,
    { credentials: "include" },
  );
  if (!res.ok) {
    throw new Error(`Webhook status check failed (${res.status}): ${await res.text()}`);
  }
  return res.json();
}

export interface LogEvent {
  type?: string;
  level?: string;
  data?: string;
  message?: string;
  timestamp?: string;
  logFile?: string;
  // Full raw JSON payload for UIs that want to show everything
  raw: unknown;
}

export interface SourceLogHistory {
  project_id: string;
  filename: string | null;
  lines: string[];
}

export function normalizeLogEvent(raw: unknown): LogEvent {
  if (raw && typeof raw === "object") {
    const value = raw as Record<string, unknown>;
    const data =
      typeof value.data === "string"
        ? value.data
        : typeof value.message === "string"
          ? value.message
          : undefined;
    return {
      type: typeof value.type === "string" ? value.type : undefined,
      level: typeof value.level === "string" ? value.level : undefined,
      data,
      message: typeof value.message === "string" ? value.message : data,
      timestamp: typeof value.timestamp === "string" ? value.timestamp : undefined,
      logFile: typeof value.log_file === "string" ? value.log_file : undefined,
      raw,
    };
  }

  const message = typeof raw === "string" ? raw : undefined;
  return { data: message, message, raw };
}

/**
 * Subscribe to the per-project SSE log stream. Returns an unsubscribe fn.
 * The onEvent callback receives parsed events as they arrive.
 */
export function subscribeToLogs(
  projectId: string,
  onEvent: (e: LogEvent) => void,
  onError?: (err: Event) => void,
): () => void {
  const url = `/api/source/logs?project_id=${encodeURIComponent(projectId)}`;
  const es = new EventSource(url, { withCredentials: true });
  es.onmessage = (msg) => {
    try {
      onEvent(normalizeLogEvent(JSON.parse(msg.data)));
    } catch {
      onEvent(normalizeLogEvent(msg.data));
    }
  };
  es.onerror = (err) => {
    if (onError) onError(err);
  };
  return () => es.close();
}

export async function fetchSourceLogHistory(projectId: string): Promise<SourceLogHistory> {
  const res = await fetch(`/api/source/log-history?project_id=${encodeURIComponent(projectId)}`, {
    credentials: "include",
  });
  if (!res.ok) {
    throw new Error(`/api/source/log-history failed (${res.status}): ${await res.text()}`);
  }
  return res.json();
}
