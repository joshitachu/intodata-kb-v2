"use client";

import { FormEvent, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { AlertCircle, CheckCircle2, Database, PlugZap } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Input } from "@/components/ui/input";

type ConnectorStatus = "connected" | "degraded" | "not_connected" | "disabled" | "unknown";

interface ConnectorInstance {
  id: string;
  provider_id: string;
  display_name: string;
  enabled: boolean;
  status: ConnectorStatus;
  last_checked_at: string | null;
  health_code: string | null;
}

interface ConnectorListResponse {
  connectors: ConnectorInstance[];
}

interface ConnectorUsageEntry {
  project_id: string;
  display_name: string;
  source_count: number;
  enabled: boolean;
}

interface ConnectorUsageResponse {
  usage: Record<string, ConnectorUsageEntry[]>;
}

interface ConnectorTestResponse {
  status: string;
  message?: string | null;
}

async function responseError(response: Response): Promise<string> {
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

async function fetchConnectors(): Promise<ConnectorListResponse> {
  const response = await fetch("/api/source/connectors", {
    credentials: "include",
    cache: "no-store",
  });
  if (!response.ok) throw new Error(await responseError(response));
  return response.json() as Promise<ConnectorListResponse>;
}

async function fetchConnectorUsage(): Promise<ConnectorUsageResponse> {
  const response = await fetch("/api/source/connectors/usage", {
    credentials: "include",
    cache: "no-store",
  });
  if (!response.ok) throw new Error(await responseError(response));
  return response.json() as Promise<ConnectorUsageResponse>;
}

function usageEntries(data: ConnectorUsageResponse | undefined, connectorId: string): ConnectorUsageEntry[] | null {
  const entries = data?.usage?.[connectorId];
  if (entries === undefined) return data?.usage ? [] : null;
  return Array.isArray(entries) ? entries : [];
}

function usageLabel(entries: ConnectorUsageEntry[]): string {
  const active = entries.filter((entry) => entry.enabled);
  if (active.length === 0) return "Not used by any project yet";
  const names = active.map((entry) => entry.display_name);
  const shown = names.slice(0, 3).join(", ");
  return active.length > 3
    ? `Used by ${shown} +${active.length - 3} more`
    : `Used by ${shown}`;
}

function statusVariant(status: ConnectorStatus): "success" | "warning" | "muted" | "destructive" {
  if (status === "connected") return "success";
  if (status === "degraded") return "warning";
  if (status === "disabled") return "muted";
  return "destructive";
}

function statusLabel(status: ConnectorStatus): string {
  return status.replaceAll("_", " ");
}

export function SimpleConnectorPanel() {
  const connectorsQuery = useQuery({
    queryKey: ["workspace-connectors"],
    queryFn: fetchConnectors,
    retry: false,
  });
  // Best-effort rollup: a caller who cannot read every project still sees the
  // connections themselves, just with a narrower usage line.
  const usageQuery = useQuery({
    queryKey: ["connector-project-usage"],
    queryFn: fetchConnectorUsage,
    retry: false,
  });
  const [formOpen, setFormOpen] = useState(false);
  const [displayName, setDisplayName] = useState("Elastic Stack");
  const [endpoint, setEndpoint] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [testingId, setTestingId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const connectors = connectorsQuery.data?.connectors ?? [];

  async function testConnector(connector: ConnectorInstance): Promise<ConnectorTestResponse> {
    setTestingId(connector.id);
    try {
      const response = await fetch(`/api/source/connectors/${encodeURIComponent(connector.id)}/test`, {
        method: "POST",
        credentials: "include",
      });
      if (!response.ok) throw new Error(await responseError(response));
      return (await response.json()) as ConnectorTestResponse;
    } finally {
      setTestingId(null);
    }
  }

  async function handleTest(connector: ConnectorInstance) {
    setError(null);
    setNotice(null);
    try {
      const result = await testConnector(connector);
      setNotice(result.message || `${connector.display_name} is reachable.`);
      await connectorsQuery.refetch();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Connection test failed.");
      await connectorsQuery.refetch();
    }
  }

  async function handleConnect(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setNotice(null);

    const normalizedEndpoint = endpoint.trim().replace(/\/+$/, "");
    if (!displayName.trim() || !normalizedEndpoint || !apiKey) {
      setError("Display name, Elasticsearch HTTPS endpoint, and API key are required.");
      return;
    }
    if (!normalizedEndpoint.startsWith("https://")) {
      setError("Elasticsearch endpoint must use HTTPS.");
      return;
    }

    setBusy(true);
    let saved: ConnectorInstance | null = null;
    try {
      const response = await fetch("/api/source/connectors", {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          provider_id: "elastic_stack",
          display_name: displayName.trim(),
          safe_config: {
            endpoint: normalizedEndpoint,
            auth_type: "api_key",
            verify_tls: true,
            kibana_reuse_elasticsearch_credentials: true,
          },
          credential_type: "api_key",
          credential: apiKey,
        }),
      });
      if (!response.ok) throw new Error(await responseError(response));
      saved = (await response.json()) as ConnectorInstance;
      setApiKey("");

      const result = await testConnector(saved);
      setNotice(result.message || `${saved.display_name} connected successfully.`);
      setEndpoint("");
      setDisplayName("Elastic Stack");
      setFormOpen(false);
    } catch (caught) {
      const message = caught instanceof Error ? caught.message : "Connection failed.";
      if (saved) {
        setFormOpen(false);
        setError(`Connection was saved, but its live test failed: ${message}`);
      } else {
        setError(message);
      }
    } finally {
      setBusy(false);
      await connectorsQuery.refetch();
    }
  }

  return (
    <section aria-labelledby="live-connections-heading">
      <Card>
        <CardHeader className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
          {/* items-start: without it the icon tile inherits `align-items: stretch`
              and grows to the full height of the description block. */}
          <div className="flex min-w-0 items-start gap-3">
            <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary">
              <PlugZap className="h-4 w-4" aria-hidden />
            </span>
            <div className="min-w-0">
              <h2 id="live-connections-heading" className="text-base font-semibold">Live data connections</h2>
              <p className="mt-1 max-w-2xl text-sm leading-relaxed text-muted-foreground">
                Connect Elastic for runtime evidence. Source discovery, project access, capabilities,
                and model testing remain managed in the Nexus operator dashboard.
              </p>
            </div>
          </div>
          <Button
            className="shrink-0"
            size="sm"
            variant={formOpen ? "secondary" : "primary"}
            onClick={() => {
              setFormOpen((current) => !current);
              setError(null);
              setNotice(null);
            }}
          >
            {formOpen ? "Cancel" : "Connect Elastic"}
          </Button>
        </CardHeader>

        <CardBody className="space-y-4">
          {connectorsQuery.isLoading && (
            <p className="text-sm text-muted-foreground">Loading connections…</p>
          )}
          {connectorsQuery.isError && (
            <div className="flex items-start gap-2 rounded-md border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive">
              <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
              {connectorsQuery.error instanceof Error ? connectorsQuery.error.message : "Unable to load connections."}
            </div>
          )}

          {!connectorsQuery.isLoading && !connectorsQuery.isError && connectors.length === 0 && !formOpen && (
            <div className="rounded-lg border border-dashed border-border px-4 py-5 text-sm text-muted-foreground">
              No live data connection has been added yet.
            </div>
          )}

          {connectors.length > 0 && (
            <div className="divide-y divide-border overflow-hidden rounded-lg border border-border">
              {connectors.map((connector) => (
                <div key={connector.id} className="flex flex-col gap-3 px-4 py-3 sm:flex-row sm:items-center sm:justify-between">
                  <div className="flex min-w-0 items-center gap-3">
                    <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-border bg-muted/40 text-muted-foreground">
                      <Database className="h-4 w-4" aria-hidden />
                    </span>
                    <div className="min-w-0">
                      <p className="truncate text-sm font-medium">{connector.display_name}</p>
                      <p className="mt-0.5 text-xs text-muted-foreground">
                        Elastic Stack{connector.last_checked_at ? ` · tested ${new Date(connector.last_checked_at).toLocaleString()}` : ""}
                      </p>
                      {(() => {
                        const entries = usageEntries(usageQuery.data, connector.id);
                        return entries === null ? null : (
                          <p className="mt-0.5 truncate text-xs text-muted-foreground">
                            {usageLabel(entries)}
                          </p>
                        );
                      })()}
                    </div>
                  </div>
                  <div className="flex shrink-0 items-center gap-2">
                    <Badge variant={statusVariant(connector.status)} className="capitalize">
                      {statusLabel(connector.status)}
                    </Badge>
                    <Button
                      size="sm"
                      variant="secondary"
                      isLoading={testingId === connector.id}
                      onClick={() => void handleTest(connector)}
                    >
                      Test
                    </Button>
                  </div>
                </div>
              ))}
            </div>
          )}

          {formOpen && (
            <form className="space-y-4 rounded-lg border border-border bg-muted/20 p-4" onSubmit={handleConnect}>
              <div className="grid gap-4 sm:grid-cols-2">
                <label className="space-y-1.5 text-sm font-medium">
                  Display name
                  <Input
                    value={displayName}
                    onChange={(event) => setDisplayName(event.target.value)}
                    autoComplete="off"
                  />
                </label>
                <label className="space-y-1.5 text-sm font-medium">
                  Elasticsearch HTTPS endpoint
                  <Input
                    type="url"
                    value={endpoint}
                    onChange={(event) => setEndpoint(event.target.value)}
                    placeholder="https://cluster.example.com"
                    autoComplete="url"
                  />
                </label>
              </div>
              <label className="block space-y-1.5 text-sm font-medium">
                API key
                <Input
                  type="password"
                  value={apiKey}
                  onChange={(event) => setApiKey(event.target.value)}
                  placeholder="Write-only; never returned to this browser"
                  autoComplete="off"
                />
              </label>
              <div className="flex flex-col gap-3 border-t border-border pt-4 sm:flex-row sm:items-center sm:justify-between">
                <p className="text-xs text-muted-foreground">
                  API key authentication · TLS certificate verification enabled
                </p>
                <Button type="submit" size="sm" isLoading={busy}>Connect &amp; test</Button>
              </div>
            </form>
          )}

          {notice && (
            <div role="status" className="flex items-start gap-2 rounded-md border border-emerald-200 bg-emerald-50 p-3 text-sm text-emerald-700">
              <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
              {notice}
            </div>
          )}
          {error && (
            <div role="alert" className="flex items-start gap-2 rounded-md border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive">
              <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
              {error}
            </div>
          )}
        </CardBody>
      </Card>
    </section>
  );
}
