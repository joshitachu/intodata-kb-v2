/**
 * Browser-side MCP client.
 *
 * Talks to Next.js API routes which proxy to the MCP server from the server
 * side. Customer deployments do not expose /mcp/* or /logs/* publicly.
 */

/** Error carrying the upstream HTTP status so callers can branch on it. */
export class McpRequestError extends Error {
  readonly status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "McpRequestError";
    this.status = status;
  }
}

/**
 * Doc generation routing for the customer admin surface.
 *
 * Customer-facing scan/generate runs are pinned to the default ("legacy")
 * custom harness on Anthropic Sonnet for all three phases, so the outcome does
 * not depend on per-deployment MCP env overrides or on a project row that an
 * internal operator switched to another generation method. Sent as a one-off
 * request override; it does not change the project's saved settings.
 */
export const CUSTOMER_DOC_GENERATION_ROUTE = {
  doc_generation_provider: "custom",
  investigation_model: "anthropic/claude-sonnet-4-6",
  model: "anthropic/claude-sonnet-4-6",
  write_model: "anthropic/claude-sonnet-4-6",
} as const;

export interface UploadZipArgs {
  file: File;
  projectId: string;
  platform: string;
  onProgress?: (pct: number) => void;
  signal?: AbortSignal;
}

export interface UploadZipResult {
  id: string;
  platform: string;
  source_type: "zip";
  upload_receipt: UploadedZipReceipt;
}

export interface UploadedZipReceipt {
  receipt: string;
  filename: string;
  size_bytes: number;
  sha256: string;
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
  // Azure DevOps organization connection supplying the clone credential.
  azureDevOpsConnectionId?: string;
}

export interface SourceProject {
  id: string;
  display_name: string;
  repo_url?: string | null;
  platform: string;
  source_type: string;
  status: string;
  job_count?: number;
  artifact_counts?: {
    total_artifacts?: number;
    job_count?: number;
    route_count?: number;
    joblet_count?: number;
    routelet_count?: number;
  };
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
  /** True when a credential is resolvable — a stored PAT, a GitHub App installation, or an Azure DevOps connection. */
  has_github_token?: boolean;
  connection_kind?: string;
  github_account_login?: string | null;
  azure_devops_connection_id?: string | null;
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

export interface ScanStatus {
  project_id: string;
  status: string;
  last_scan_at?: string | null;
  job_count?: number;
  doc_count?: number;
  error_message?: string | null;
}

/** Read the project's authoritative scan/generate state. */
export async function getScanStatus(projectId: string): Promise<ScanStatus> {
  const res = await fetch(`/api/source/status/${encodeURIComponent(projectId)}`, {
    credentials: "include",
  });
  if (!res.ok) {
    throw new McpRequestError(
      `/api/source/status/${projectId} failed (${res.status}): ${await res.text()}`,
      res.status,
    );
  }
  return res.json();
}

/** Statuses meaning "a parsed scan result exists on disk". */
const SCAN_SETTLED_STATUSES = new Set(["ready", "scanned", "generating"]);
/** Statuses meaning "still working towards one". */
const SCAN_PENDING_STATUSES = new Set(["scanning", "registered", "pending"]);

/**
 * Block until the project has a usable scan result, or throw.
 *
 * Doc generation reads `scan_result.json` and fails hard if it is missing or
 * stale. Log text cannot gate that safely -- the marker the UI used to watch for
 * ("scan complete") can arrive from an earlier run or ahead of the file landing,
 * which produced `FileNotFoundError: No valid fresh scan_result.json` with the
 * generate attempt recorded as a failure. The project row is the real signal: it
 * only reaches `ready` after the scanner has written AND parsed that file.
 */
export async function waitForScanReady(
  projectId: string,
  options: { timeoutMs?: number; pollMs?: number; signal?: AbortSignal } = {},
): Promise<ScanStatus> {
  const timeoutMs = options.timeoutMs ?? 30 * 60 * 1000;
  const pollMs = options.pollMs ?? 3000;
  const deadline = Date.now() + timeoutMs;

  for (;;) {
    if (options.signal?.aborted) throw new DOMException("Aborted", "AbortError");
    const status = await getScanStatus(projectId);

    if (SCAN_SETTLED_STATUSES.has(status.status)) return status;
    if (status.status === "error") {
      throw new Error(status.error_message || `Scan failed for ${projectId}`);
    }
    if (!SCAN_PENDING_STATUSES.has(status.status)) {
      // An unrecognised status is not a licence to generate against a possibly
      // absent scan result -- surface it instead of guessing.
      throw new Error(`Unexpected project status '${status.status}' for ${projectId}`);
    }
    if (Date.now() > deadline) {
      throw new Error(
        `Timed out after ${Math.round(timeoutMs / 60000)} min waiting for the scan of ${projectId} to finish`,
      );
    }
    await new Promise((resolve) => setTimeout(resolve, pollMs));
  }
}

/**
 * Kick off doc generation once the scan has produced a usable result.
 *
 * Always gated: every caller in the app wants "generate the docs for this
 * project", and none of them wants to race the scanner. Pass
 * `waitForScan: false` only when the caller has already confirmed readiness.
 */
export async function generateDocsWhenScanned(
  projectId: string,
  options: {
    resume?: boolean;
    waitForScan?: boolean;
    signal?: AbortSignal;
    pollMs?: number;
    timeoutMs?: number;
  } = {},
): Promise<void> {
  if (options.waitForScan !== false) {
    await waitForScanReady(projectId, {
      signal: options.signal,
      pollMs: options.pollMs,
      timeoutMs: options.timeoutMs,
    });
  }
  await generateDocs(projectId, options.resume ?? true);
}

/**
 * Kick off doc generation immediately.
 *
 * Prefer `generateDocsWhenScanned`: calling this before the scanner has written
 * `scan_result.json` fails the run and records it as a failed generate.
 */
export async function generateDocs(projectId: string, resume = true): Promise<void> {
  const res = await fetch("/api/source/generate", {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      project_id: projectId,
      resume,
      ...CUSTOMER_DOC_GENERATION_ROUTE,
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
      azure_devops_connection_id: args.azureDevOpsConnectionId,
    }),
  });
  if (!res.ok) {
    throw new Error(`/api/source/github failed (${res.status}): ${await res.text()}`);
  }
  return res.json();
}

/**
 * Register an Azure DevOps repository. Shares the /add-repo path with GitHub —
 * the only difference is which connection resolves the clone credential.
 */
export async function addAzureDevOpsSource(
  args: Omit<GithubSourceArgs, "githubToken" | "installationId"> & {
    azureDevOpsConnectionId: string;
  },
): Promise<SourceProject> {
  return addGithubSource(args);
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
    throw new McpRequestError(
      `/api/source/github/installations failed (${res.status}): ${await res.text()}`,
      res.status,
    );
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
    // 404 = the installation is gone on GitHub (the server has pruned it);
    // 502 = GitHub unreachable, worth retrying.
    throw new McpRequestError(
      `/api/source/github/installations/${installationId}/repos failed (${res.status}): ${await res.text()}`,
      res.status,
    );
  }
  const data = await res.json();
  return { account_login: data.account_login ?? null, repositories: data.repositories ?? [] };
}

// --- Azure DevOps ------------------------------------------------------------

export interface AzureDevOpsConnection {
  id: string;
  organization_url: string;
  organization_name: string;
  account_name?: string | null;
  last_verified_at?: string | null;
}

export interface AzureDevOpsRepo {
  id: string;
  name: string;
  project: string;
  clone_url?: string | null;
  web_url?: string | null;
  default_branch?: string | null;
  is_disabled?: boolean;
  size_bytes?: number;
}

async function azureDevOpsRequest(path: string, init?: RequestInit): Promise<unknown> {
  const res = await fetch(path, { credentials: "include", ...init });
  if (!res.ok) {
    // The upstream message is user-facing on purpose ("token expired", "needs
    // Code: Read"), so surface it rather than a generic failure.
    const body = await res.text();
    let detail = body;
    try {
      const parsed = JSON.parse(body) as { detail?: string; error?: string };
      detail = parsed.detail ?? parsed.error ?? body;
    } catch {
      // Non-JSON body; use it verbatim.
    }
    throw new McpRequestError(detail || `${path} failed (${res.status})`, res.status);
  }
  return res.json();
}

/** Connect an Azure DevOps organization with a PAT. The PAT is not stored client-side. */
export async function connectAzureDevOps(
  organizationUrl: string,
  pat: string,
): Promise<AzureDevOpsConnection> {
  return (await azureDevOpsRequest("/api/source/azure-devops/connections", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ organization_url: organizationUrl, pat }),
  })) as AzureDevOpsConnection;
}

export async function listAzureDevOpsConnections(): Promise<AzureDevOpsConnection[]> {
  const data = (await azureDevOpsRequest("/api/source/azure-devops/connections")) as {
    connections?: AzureDevOpsConnection[];
  };
  return data.connections ?? [];
}

/** Re-verify a stored PAT; Azure DevOps tokens expire without notice. */
export async function testAzureDevOpsConnection(
  connectionId: string,
): Promise<AzureDevOpsConnection> {
  return (await azureDevOpsRequest(
    `/api/source/azure-devops/connections/${encodeURIComponent(connectionId)}/test`,
    { method: "POST" },
  )) as AzureDevOpsConnection;
}

export async function listAzureDevOpsRepos(
  connectionId: string,
): Promise<{ organization_name: string; repositories: AzureDevOpsRepo[] }> {
  const data = (await azureDevOpsRequest(
    `/api/source/azure-devops/connections/${encodeURIComponent(connectionId)}/repos`,
  )) as { organization_name?: string; repositories?: AzureDevOpsRepo[] };
  return {
    organization_name: data.organization_name ?? "",
    repositories: data.repositories ?? [],
  };
}

export async function deleteAzureDevOpsConnection(connectionId: string): Promise<void> {
  await azureDevOpsRequest(
    `/api/source/azure-devops/connections/${encodeURIComponent(connectionId)}`,
    { method: "DELETE" },
  );
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
