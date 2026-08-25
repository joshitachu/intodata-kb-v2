"use client";

/** Which external data sources each project will actually read.
 *
 * Two independent registries answer half the question each, and neither is
 * readable from a project row:
 *
 *   - **Live evidence** (Elastic today) is a workspace *connector* bound to a
 *     project. `/api/source/connectors/usage` returns connector -> projects.
 *   - **Runtime sync** (Talend Management Console today) is a *runtime source*
 *     whose credential lives in this backend's scheduler tables.
 *     `/api/runtime/sources` returns one row per source, carrying its project.
 *
 * This module inverts both into one project -> sources map so a project row can
 * state what a documentation run will reach for. The kinds are deliberately kept
 * apart rather than merged into "connected": they behave differently at
 * generation time, and a row that blurred them would imply Elastic feeds the
 * legacy pipeline.
 */

import { useQuery } from "@tanstack/react-query";

export type LiveSourceKind = "live_evidence" | "runtime_sync";

export interface ProjectLiveSource {
  key: string;
  label: string;
  kind: LiveSourceKind;
  /** Tooltip text: what this source is and when it was last proven reachable. */
  detail: string;
  /** False for a disabled or unhealthy source. The chip stays visible -- an
   *  operator needs to see that a source exists but is not being read. */
  healthy: boolean;
}

export type ConnectorStatus = "connected" | "degraded" | "not_connected" | "disabled" | "unknown";

export interface ConnectorInstance {
  id: string;
  provider_id: string;
  display_name: string;
  enabled: boolean;
  status: ConnectorStatus;
  last_checked_at: string | null;
  health_code: string | null;
}

export interface ConnectorListResponse {
  connectors: ConnectorInstance[];
}

export interface ConnectorUsageEntry {
  project_id: string;
  display_name: string;
  source_count: number;
  enabled: boolean;
}

export interface ConnectorUsageResponse {
  usage: Record<string, ConnectorUsageEntry[]>;
}

export interface RuntimeSource {
  id: number;
  project_id: string;
  source_type: string;
  label: string;
  status: string;
  enabled: boolean;
  disabled_reason?: string | null;
  last_synced_at?: string | null;
}

export interface RuntimeSourceListResponse {
  sources: RuntimeSource[];
}

export interface RuntimeSourceType {
  provider_key: string;
  provider_label?: string | null;
}

export interface RuntimeSourceTypeListResponse {
  source_types: RuntimeSourceType[];
}

export async function responseError(response: Response): Promise<string> {
  try {
    const payload = (await response.json()) as { detail?: unknown; error?: unknown; message?: unknown };
    const value = payload.detail ?? payload.error ?? payload.message;
    if (typeof value === "string" && value.trim()) return value;
  } catch {
    // The status fallback below is safe to display and never exposes credentials.
  }
  if (response.status === 403) return "Connector management permission is required.";
  return `Request failed (${response.status})`;
}

export async function fetchConnectors(): Promise<ConnectorListResponse> {
  const response = await fetch("/api/source/connectors", { credentials: "include", cache: "no-store" });
  if (!response.ok) throw new Error(await responseError(response));
  return response.json() as Promise<ConnectorListResponse>;
}

export async function fetchConnectorUsage(): Promise<ConnectorUsageResponse> {
  const response = await fetch("/api/source/connectors/usage", { credentials: "include", cache: "no-store" });
  if (!response.ok) throw new Error(await responseError(response));
  return response.json() as Promise<ConnectorUsageResponse>;
}

export async function fetchRuntimeSources(): Promise<RuntimeSourceListResponse> {
  const response = await fetch("/api/runtime/sources", { credentials: "include", cache: "no-store" });
  if (!response.ok) throw new Error(await responseError(response));
  return response.json() as Promise<RuntimeSourceListResponse>;
}

export async function fetchRuntimeSourceTypes(): Promise<RuntimeSourceTypeListResponse> {
  const response = await fetch("/api/runtime/source-types", { credentials: "include", cache: "no-store" });
  if (!response.ok) throw new Error(await responseError(response));
  return response.json() as Promise<RuntimeSourceTypeListResponse>;
}

/** Name a runtime source by its provider's own label, falling back to the raw
 *  key. A provider this build has never heard of must still render as itself. */
export function runtimeProviderLabel(
  types: RuntimeSourceTypeListResponse | undefined,
  sourceType: string,
): string {
  const match = types?.source_types?.find((type) => type.provider_key === sourceType);
  const label = match?.provider_label?.trim();
  return label || sourceType;
}

export const LIVE_SOURCE_KIND_LABEL: Record<LiveSourceKind, string> = {
  live_evidence: "Live evidence, queried during OpenCode runs",
  runtime_sync: "Runtime history, mirrored on a schedule and read by every run",
};

function formatTimestamp(value: string | null | undefined): string | null {
  if (!value) return null;
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? null : parsed.toLocaleString();
}

/** project_id -> the external sources a documentation run for it will read.
 *
 * Every query is best-effort and `retry: false`: a caller who cannot read the
 * connector catalogue still gets the runtime half, and the project list renders
 * with fewer chips rather than failing. Absence of a chip therefore means "not
 * visible to you or not bound", never "definitely nothing".
 */
export function useProjectLiveSources(): {
  byProject: Map<string, ProjectLiveSource[]>;
  isLoading: boolean;
} {
  // Query keys are shared with SimpleConnectorPanel so the two surfaces issue
  // one request each, not two, and can never disagree about what is connected.
  const connectorsQuery = useQuery({ queryKey: ["workspace-connectors"], queryFn: fetchConnectors, retry: false });
  const usageQuery = useQuery({ queryKey: ["connector-project-usage"], queryFn: fetchConnectorUsage, retry: false });
  const runtimeQuery = useQuery({ queryKey: ["runtime-sources"], queryFn: fetchRuntimeSources, retry: false });
  const typesQuery = useQuery({ queryKey: ["runtime-source-types"], queryFn: fetchRuntimeSourceTypes, retry: false });

  const byProject = new Map<string, ProjectLiveSource[]>();
  const add = (projectId: string, entry: ProjectLiveSource) => {
    const existing = byProject.get(projectId);
    if (existing) existing.push(entry);
    else byProject.set(projectId, [entry]);
  };

  for (const connector of connectorsQuery.data?.connectors ?? []) {
    const tested = formatTimestamp(connector.last_checked_at);
    for (const entry of usageQuery.data?.usage?.[connector.id] ?? []) {
      // `entry.enabled` is the binding; `connector.enabled` is the connection.
      // Both must hold for a run to read it.
      const healthy = entry.enabled && connector.enabled && connector.status === "connected";
      add(entry.project_id, {
        key: `connector:${connector.id}`,
        label: connector.display_name,
        kind: "live_evidence",
        detail: [
          LIVE_SOURCE_KIND_LABEL.live_evidence,
          !entry.enabled ? "Binding disabled for this project" : null,
          !connector.enabled ? "Connection disabled" : null,
          connector.enabled && connector.status !== "connected" ? `Status: ${connector.status.replaceAll("_", " ")}` : null,
          tested ? `Last tested ${tested}` : null,
        ]
          .filter(Boolean)
          .join(" · "),
        healthy,
      });
    }
  }

  for (const source of runtimeQuery.data?.sources ?? []) {
    const synced = formatTimestamp(source.last_synced_at);
    add(source.project_id, {
      key: `runtime:${source.id}`,
      label: runtimeProviderLabel(typesQuery.data, source.source_type),
      kind: "runtime_sync",
      detail: [
        LIVE_SOURCE_KIND_LABEL.runtime_sync,
        source.label ? `Source: ${source.label}` : null,
        !source.enabled ? source.disabled_reason?.trim() || "Disabled" : null,
        synced ? `Last synced ${synced}` : "Not synced yet",
      ]
        .filter(Boolean)
        .join(" · "),
      healthy: source.enabled && source.status === "success",
    });
  }

  return {
    byProject,
    isLoading:
      connectorsQuery.isLoading || usageQuery.isLoading || runtimeQuery.isLoading || typesQuery.isLoading,
  };
}
