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
export async function scanProject(projectId: string): Promise<void> {
  const res = await fetch("/api/source/scan", {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ project_id: projectId }),
  });
  if (!res.ok) {
    throw new Error(`/api/source/scan failed (${res.status}): ${await res.text()}`);
  }
}

/**
 * Kick off doc generation. Called after the scan complete event arrives on
 * the log stream.
 */
export async function generateDocs(projectId: string): Promise<void> {
  const res = await fetch("/api/source/generate", {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ project_id: projectId }),
  });
  if (!res.ok) {
    throw new Error(`/api/source/generate failed (${res.status}): ${await res.text()}`);
  }
}

export interface LogEvent {
  type?: string;
  level?: string;
  data?: string;
  timestamp?: string;
  // Full raw JSON payload for UIs that want to show everything
  raw: unknown;
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
      const raw = JSON.parse(msg.data);
      onEvent({
        type: typeof raw?.type === "string" ? raw.type : undefined,
        level: typeof raw?.level === "string" ? raw.level : undefined,
        data: typeof raw?.data === "string" ? raw.data : undefined,
        timestamp: typeof raw?.timestamp === "string" ? raw.timestamp : undefined,
        raw,
      });
    } catch {
      onEvent({ data: msg.data, raw: msg.data });
    }
  };
  es.onerror = (err) => {
    if (onError) onError(err);
  };
  return () => es.close();
}
