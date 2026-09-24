"use client";

import { FormEvent, type ReactNode, useCallback, useEffect, useMemo, useState } from "react";
import {
  Activity,
  ArrowRight,
  Check,
  ChevronDown,
  ChevronRight,
  CircleAlert,
  Database,
  ExternalLink,
  Fingerprint,
  Gauge,
  KeyRound,
  Link2,
  Loader2,
  Plus,
  Power,
  RefreshCw,
  Search,
  ShieldCheck,
  Sparkles,
  Trash2,
  Unplug,
  Zap,
  type LucideIcon,
} from "lucide-react";
import Link from "next/link";
import Image from "next/image";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Modal } from "@/components/ui/modal";
import { SelectMenu } from "@/components/ui/select-menu";
import { useAdminProjects } from "@/hooks/useAdminDirectory";
import { usePermissions } from "@/hooks/usePermissions";
import { authedFetch } from "@/lib/authedFetch";
import { apiErrorMessage } from "@/lib/apiError";
import { cn } from "@/lib/utils";

type AuthMode = "none" | "credential_reference";
type HealthStatus = "unknown" | "healthy" | "unhealthy";

type Provider = {
  provider_key: string;
  display_name: string;
  description: string;
  auth_modes: AuthMode[];
  capability_ids: string[];
  config_schema: Record<string, string>;
};

type Connector = {
  id: number;
  name: string;
  provider_key: string;
  config: Record<string, unknown>;
  auth_mode: AuthMode;
  credential_reference_type: string | null;
  credential_present: boolean;
  enabled: boolean;
  health_status: HealthStatus;
  health_summary: string | null;
  health_checked_at: string | null;
  created_at: string;
  updated_at: string;
};

type ExistingElasticSource = { id: string; label: string; type: string };
type ExistingElastic = { available: boolean; display_name: string | null; sources: ExistingElasticSource[] };

type Binding = {
  id: number;
  connector_instance_id: number;
  capability_id: string;
  project_ids: string[];
  domains: string[];
  risk_tier: string;
  redaction_fields: string[];
  enabled: boolean;
};

type SetupState = {
  providerKey: string;
  name: string;
  authMode: AuthMode;
  credentialReference: string;
  config: Record<string, string>;
  projectIds: string[];
};

const EMPTY_SETUP: SetupState = {
  providerKey: "",
  name: "",
  authMode: "none",
  credentialReference: "",
  config: {},
  projectIds: [],
};

const FIELD_LABELS: Record<string, string> = {
  base_url: "Endpoint URL",
  allowed_hosts: "Allowed hosts",
  index: "Index",
  project_field: "Project ID field",
  search_path: "Search path",
  health_path: "Health path",
  timeout_seconds: "Timeout (seconds)",
  max_response_bytes: "Maximum response size",
  max_results: "Maximum results",
  dataset: "Dataset",
  host: "Database host",
  port: "Port",
  database: "Database",
  username: "Read-only user",
  view_name: "Approved read-only view",
  openapi_spec: "OpenAPI 3 contract (JSON)",
  operation_id: "Reviewed operation ID",
  operation_path: "Reviewed GET path",
  health_project_id: "Health-check project ID",
  query_parameter: "Search parameter",
  project_parameter: "Project parameter",
  limit_parameter: "Limit parameter",
  items_field: "Results array field",
  title_field: "Result title field",
  excerpt_field: "Result excerpt field",
  source_ref_field: "Result source field",
  result_project_field: "Result project field",
};

const FIELD_DEFAULTS: Record<string, string> = {
  dataset: "default",
  max_results: "10",
  search_path: "/{index}/_search",
  health_path: "/{index}/_search",
  timeout_seconds: "5",
  max_response_bytes: "1000000",
  project_field: "project_id.keyword",
  port: "5432",
  query_parameter: "q",
  project_parameter: "project_id",
  limit_parameter: "limit",
  items_field: "items",
  title_field: "title",
  excerpt_field: "excerpt",
  source_ref_field: "source_ref",
  result_project_field: "project_id",
};

async function errorMessage(response: Response, fallback: string) {
  const body = await response.json().catch(() => null);
  const named = (body as { error?: unknown } | null)?.error;
  if (typeof named === "string" && named.trim()) return named;
  return apiErrorMessage((body as { detail?: unknown } | null)?.detail, fallback);
}

function parseConfig(schema: Record<string, string>, values: Record<string, string>) {
  const parsed: Record<string, unknown> = {};
  for (const [key, kind] of Object.entries(schema)) {
    const raw = values[key]?.trim();
    if (!raw) continue;
    if (kind.includes("array")) parsed[key] = raw.split(",").map((part) => part.trim()).filter(Boolean);
    else if (key === "openapi_spec") {
      if (new TextEncoder().encode(raw).length > 65_536) throw new Error("OpenAPI contract exceeds 64 KiB");
      try { parsed[key] = JSON.parse(raw) as unknown; }
      catch { throw new Error("OpenAPI contract must be valid JSON"); }
    }
    else if (kind === "integer") parsed[key] = Number.parseInt(raw, 10);
    else if (kind === "number") parsed[key] = Number.parseFloat(raw);
    else parsed[key] = raw;
  }
  return parsed;
}

function relativeDate(value: string | null) {
  if (!value) return "Not checked yet";
  const timestamp = new Date(value).getTime();
  if (Number.isNaN(timestamp)) return "Unknown";
  const seconds = Math.max(0, Math.round((Date.now() - timestamp) / 1000));
  if (seconds < 60) return "Just now";
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  return `${Math.floor(seconds / 86400)}d ago`;
}

function statusTone(status: HealthStatus) {
  if (status === "healthy") return "border-emerald-400/25 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300";
  if (status === "unhealthy") return "border-rose-400/25 bg-rose-500/10 text-rose-700 dark:text-rose-300";
  return "border-amber-400/25 bg-amber-500/10 text-amber-700 dark:text-amber-300";
}

export default function ConnectorControlPlane() {
  const { isOwner } = usePermissions();
  const projectsQuery = useAdminProjects(isOwner);
  const projects = useMemo(() => projectsQuery.data ?? [], [projectsQuery.data]);
  const [providers, setProviders] = useState<Provider[]>([]);
  const [connectors, setConnectors] = useState<Connector[]>([]);
  const [existingElastic, setExistingElastic] = useState<ExistingElastic>({ available: false, display_name: null, sources: [] });
  const [elasticProjectId, setElasticProjectId] = useState("");
  const [usingExistingElastic, setUsingExistingElastic] = useState(false);
  const [bindings, setBindings] = useState<Record<number, Binding[]>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [setupOpen, setSetupOpen] = useState(false);
  const [setup, setSetup] = useState<SetupState>(EMPTY_SETUP);
  const [saving, setSaving] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<Connector | null>(null);
  const [accessFor, setAccessFor] = useState<Connector | null>(null);
  const [accessProjects, setAccessProjects] = useState<string[]>([]);
  const [resourcesFor, setResourcesFor] = useState<Connector | null>(null);
  const [resources, setResources] = useState<Array<{ id: string; label: string; kind: string }>>([]);
  const [resourcesLoading, setResourcesLoading] = useState(false);
  const [previewFor, setPreviewFor] = useState<Connector | null>(null);
  const [previewProjectId, setPreviewProjectId] = useState("");
  const [previewQuery, setPreviewQuery] = useState("");
  const [previewItems, setPreviewItems] = useState<Array<{ title: string; excerpt: string; source_ref: string }>>([]);
  const [previewTruncated, setPreviewTruncated] = useState(false);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewComplete, setPreviewComplete] = useState(false);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [expandedSourceProjects, setExpandedSourceProjects] = useState<Record<string, boolean>>({});
  const [expandedConnectorCards, setExpandedConnectorCards] = useState<Record<number, boolean>>({});

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [providerResponse, connectorResponse] = await Promise.all([
        authedFetch("/api/admin/connectors/providers", { cache: "no-store" }),
        authedFetch("/api/admin/connectors", { cache: "no-store" }),
      ]);
      if (!providerResponse.ok) throw new Error(await errorMessage(providerResponse, "Could not load connector types"));
      if (!connectorResponse.ok) throw new Error(await errorMessage(connectorResponse, "Could not load connectors"));
      const providerData = (await providerResponse.json()) as { providers?: Provider[] };
      const connectorData = (await connectorResponse.json()) as { connectors?: Connector[] };
      const existingResponse = await authedFetch("/api/admin/connectors/existing-elastic", { cache: "no-store" });
      if (existingResponse.ok) {
        setExistingElastic((await existingResponse.json()) as ExistingElastic);
      } else {
        setExistingElastic({ available: false, display_name: null, sources: [] });
      }
      const nextConnectors = connectorData.connectors ?? [];
      setProviders(providerData.providers ?? []);
      setConnectors(nextConnectors);
      const entries = await Promise.all(
        nextConnectors.map(async (connector) => {
          const response = await authedFetch(`/api/admin/connectors/${connector.id}/bindings`, { cache: "no-store" });
          if (!response.ok) return [connector.id, []] as const;
          const data = (await response.json()) as { bindings?: Binding[] };
          return [connector.id, data.bindings ?? []] as const;
        }),
      );
      setBindings(Object.fromEntries(entries));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Connector request failed");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { if (isOwner) void refresh(); else setLoading(false); }, [isOwner, refresh]);

  const healthy = connectors.filter((connector) => connector.health_status === "healthy").length;
  const boundProjects = new Set(Object.values(bindings).flat().flatMap((binding) => binding.project_ids)).size;
  const activeCapabilities = Object.values(bindings).flat().filter((binding) => binding.enabled).length;

  function chooseProvider(providerKey: string) {
    const provider = providers.find((item) => item.provider_key === providerKey);
    const config = Object.fromEntries(
      Object.keys(provider?.config_schema ?? {}).map((key) => [key, FIELD_DEFAULTS[key] ?? ""]),
    );
    setSetup({
      ...EMPTY_SETUP,
      providerKey,
      name: provider?.display_name ?? "",
      authMode: provider?.auth_modes[0] ?? "none",
      config,
    });
  }

  function closeSetup() {
    if (saving) return;
    setSetupOpen(false);
    // Credential references are deliberately ephemeral form state.
    setSetup(EMPTY_SETUP);
  }

  async function createConnector(event: FormEvent) {
    event.preventDefault();
    const provider = providers.find((item) => item.provider_key === setup.providerKey);
    if (!provider || setup.projectIds.length === 0) return;
    setSaving(true);
    setError(null);
    let created: Connector | null = null;
    try {
      const createResponse = await authedFetch("/api/admin/connectors", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          name: setup.name,
          provider_key: setup.providerKey,
          config: parseConfig(provider.config_schema, setup.config),
          auth_mode: setup.authMode,
          credential_reference: setup.authMode === "credential_reference" ? setup.credentialReference : null,
          enabled: true,
        }),
      });
      if (!createResponse.ok) throw new Error(await errorMessage(createResponse, "Could not create connector"));
      created = (await createResponse.json()) as Connector;
      for (const capabilityId of provider.capability_ids) {
        const bindResponse = await authedFetch(`/api/admin/connectors/${created.id}/bindings`, {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ capability_id: capabilityId, project_ids: setup.projectIds, enabled: true }),
        });
        if (!bindResponse.ok) throw new Error(await errorMessage(bindResponse, "Connector created, but project binding failed"));
      }
      const healthResponse = await authedFetch(`/api/admin/connectors/${created.id}/health`, { method: "POST" });
      if (!healthResponse.ok) throw new Error(await errorMessage(healthResponse, "Connector created, but health check failed"));
      setSetupOpen(false);
      setSetup(EMPTY_SETUP);
      await refresh();
    } catch (reason) {
      const message = reason instanceof Error ? reason.message : "Connector setup failed";
      setError(created ? `Connector saved, but setup is incomplete: ${message}` : message);
      if (created) {
        setSetupOpen(false);
        setSetup(EMPTY_SETUP);
        await refresh();
      }
    } finally {
      setSaving(false);
    }
  }

  async function perform(connector: Connector, action: () => Promise<Response>, fallback: string) {
    setBusyId(connector.id);
    setError(null);
    try {
      const response = await action();
      if (!response.ok) throw new Error(await errorMessage(response, fallback));
      await refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : fallback);
    } finally {
      setBusyId(null);
    }
  }

  function editAccess(connector: Connector) {
    setAccessFor(connector);
    setAccessProjects([...new Set((bindings[connector.id] ?? []).flatMap((binding) => binding.project_ids))]);
  }

  async function saveAccess() {
    const connector = accessFor;
    if (!connector || accessProjects.length === 0) return;
    const provider = providers.find((item) => item.provider_key === connector.provider_key);
    if (!provider) return;
    setBusyId(connector.id);
    setError(null);
    try {
      const existing = bindings[connector.id] ?? [];
      for (const capabilityId of provider.capability_ids) {
        const binding = existing.find((item) => item.capability_id === capabilityId);
        const response = await authedFetch(
          binding
            ? `/api/admin/connectors/${connector.id}/bindings/${binding.id}`
            : `/api/admin/connectors/${connector.id}/bindings`,
          {
            method: binding ? "PATCH" : "POST",
            headers: { "content-type": "application/json" },
            body: JSON.stringify(binding
              ? { project_ids: accessProjects }
              : { capability_id: capabilityId, project_ids: accessProjects, enabled: true }),
          },
        );
        if (!response.ok) throw new Error(await errorMessage(response, "Could not update project access"));
      }
      setAccessFor(null);
      await refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Project access update failed");
      await refresh();
    } finally {
      setBusyId(null);
    }
  }

  async function inspectResources(connector: Connector) {
    setResourcesFor(connector);
    setResources([]);
    setResourcesLoading(true);
    setError(null);
    try {
      const response = await authedFetch(`/api/admin/connectors/${connector.id}/resources`, { cache: "no-store" });
      if (!response.ok) throw new Error(await errorMessage(response, "Could not discover resources"));
      const payload = (await response.json()) as { resources?: Array<{ id: string; label: string; kind: string }> };
      setResources(payload.resources ?? []);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Resource discovery failed");
      setResourcesFor(null);
    } finally {
      setResourcesLoading(false);
    }
  }

  async function useIndex(index: string) {
    const connector = resourcesFor;
    if (!connector) return;
    await perform(
      connector,
      () => authedFetch(`/api/admin/connectors/${connector.id}`, {
        method: "PATCH",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ config: { ...connector.config, index } }),
      }),
      "Could not select index",
    );
    setResourcesFor(null);
  }

  function openPreview(connector: Connector) {
    const projectId = (bindings[connector.id] ?? []).flatMap((binding) => binding.project_ids)[0] ?? "";
    setPreviewFor(connector);
    setPreviewProjectId(projectId);
    setPreviewQuery(connector.provider_key === "mcp_elastic" ? "error OR failed OR exception" : "");
    setPreviewItems([]);
    setPreviewError(null);
    setPreviewComplete(false);
  }

  async function runPreview(event: FormEvent) {
    event.preventDefault();
    const connector = previewFor;
    const capabilityId = connector && (bindings[connector.id] ?? [])[0]?.capability_id;
    if (!connector || !capabilityId || !previewProjectId || !previewQuery.trim()) return;
    setPreviewLoading(true);
    setPreviewError(null);
    try {
      const response = await authedFetch(`/api/admin/connectors/${connector.id}/preview`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          capability_id: capabilityId,
          project_id: previewProjectId,
          query: previewQuery.trim(),
          limit: 5,
        }),
      });
      if (!response.ok) throw new Error(await errorMessage(response, "Preview failed"));
      const payload = (await response.json()) as { items?: Array<{ title: string; excerpt: string; source_ref: string }>; truncated?: boolean };
      setPreviewItems(payload.items ?? []);
      setPreviewTruncated(payload.truncated ?? false);
      setPreviewComplete(true);
    } catch (reason) {
      setPreviewError(reason instanceof Error ? reason.message : "Preview failed");
      setPreviewItems([]);
    } finally {
      setPreviewLoading(false);
    }
  }

  async function enableExistingElastic() {
    if (!elasticProjectId) return;
    setUsingExistingElastic(true);
    setError(null);
    try {
      const response = await authedFetch("/api/admin/connectors/existing-elastic/use", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ project_id: elasticProjectId }),
      });
      if (!response.ok) throw new Error(await errorMessage(response, "Could not enable the existing Elastic connection"));
      await refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not enable the existing Elastic connection");
    } finally {
      setUsingExistingElastic(false);
    }
  }

  const selectedProvider = providers.find((provider) => provider.provider_key === setup.providerKey);

  if (!isOwner) {
    return <main className="flex min-h-[60vh] items-center justify-center p-6"><div className="max-w-md rounded-2xl border border-border bg-card p-8 text-center shadow-elevation-md"><ShieldCheck className="mx-auto h-8 w-8 text-muted-foreground" /><h1 className="mt-4 text-lg font-semibold">Owner access required</h1><p className="mt-2 text-sm text-muted-foreground">Agent connectors can expose external evidence to projects, so only the deployment owner can manage them.</p></div></main>;
  }

  return (
    <main className="relative min-h-full overflow-hidden bg-[radial-gradient(circle_at_15%_0%,hsl(var(--primary)/0.12),transparent_28%),radial-gradient(circle_at_88%_18%,hsl(190_90%_50%/0.08),transparent_24%)]">
      <div className="pointer-events-none absolute inset-0 opacity-[0.035] [background-image:linear-gradient(hsl(var(--foreground))_1px,transparent_1px),linear-gradient(90deg,hsl(var(--foreground))_1px,transparent_1px)] [background-size:40px_40px]" />
      <div className="relative mx-auto w-full max-w-7xl space-y-6 p-4 sm:p-6 lg:p-8">
        <header className="overflow-hidden rounded-2xl border border-primary/15 bg-card/80 shadow-elevation-lg backdrop-blur-xl">
          <div className="relative p-6 sm:p-8">
            <div className="absolute right-[-5rem] top-[-8rem] h-64 w-64 rounded-full bg-primary/10 blur-3xl" />
            <div className="relative flex flex-col gap-6 lg:flex-row lg:items-end lg:justify-between">
              <div className="max-w-2xl">
                <div className="mb-4 inline-flex items-center gap-2 rounded-full border border-primary/20 bg-primary/5 px-3 py-1 text-[11px] font-semibold uppercase tracking-[0.18em] text-primary">
                  <Sparkles className="h-3.5 w-3.5" /> Agent capability fabric
                </div>
                <h1 className="text-2xl font-semibold tracking-tight sm:text-3xl">Connector control plane</h1>
                <p className="mt-2 max-w-xl text-sm leading-6 text-muted-foreground">
                  Bring live evidence into Labs. Every connector is health-checked, read-only, and explicitly scoped to the projects that may use it.
                </p>
              </div>
              <div className="flex flex-wrap gap-2">
                <Button variant="secondary" onClick={() => void refresh()} isLoading={loading}>
                  <RefreshCw className="h-4 w-4" /> Refresh
                </Button>
                <Button onClick={() => setSetupOpen(true)}>
                  <Plus className="h-4 w-4" /> Add connector
                </Button>
              </div>
            </div>
          </div>
          <div className="grid border-t border-border/60 bg-muted/20 sm:grid-cols-3">
            {([
              ["Healthy links", `${healthy}/${connectors.length}`, Activity],
              ["Project scopes", String(boundProjects), Fingerprint],
              ["Agent capabilities", String(activeCapabilities), Zap],
            ] satisfies Array<[string, string, LucideIcon]>).map(([label, value, Icon], index) => (
              <div key={String(label)} className={cn("flex items-center gap-3 px-6 py-4", index > 0 && "border-t sm:border-l sm:border-t-0")}>
                <div className="rounded-lg border border-primary/15 bg-primary/5 p-2 text-primary"><Icon className="h-4 w-4" /></div>
                <div><div className="text-xl font-semibold tabular-nums">{value}</div><div className="text-xs text-muted-foreground">{label}</div></div>
              </div>
            ))}
          </div>
        </header>

        {error && (
          <div className="flex items-start gap-3 rounded-xl border border-destructive/25 bg-destructive/5 p-4 text-sm text-destructive">
            <CircleAlert className="mt-0.5 h-4 w-4 shrink-0" /><span>{error}</span>
          </div>
        )}

        {existingElastic.available && (
          <section className="overflow-hidden rounded-2xl border border-cyan-500/20 bg-gradient-to-r from-cyan-500/[0.08] via-card to-primary/[0.06] shadow-elevation-sm">
            <div className="flex flex-col gap-4 p-5 sm:flex-row sm:items-center sm:justify-between sm:p-6">
              <div className="flex min-w-0 items-start gap-3">
                <div className="rounded-xl border border-cyan-500/20 bg-cyan-500/10 p-2.5 text-cyan-700 dark:text-cyan-300"><Database className="h-5 w-5" /></div>
                <div className="min-w-0">
                  <h2 className="text-sm font-semibold">Elastic is already connected</h2>
                  <p className="mt-1 max-w-xl text-xs leading-5 text-muted-foreground">Use {existingElastic.display_name ?? "your Nexus Elastic connection"} in Labs. No URL or key to enter. We’ll add only a couple of recommended log sources to the project you choose.</p>
                </div>
              </div>
              <div className="flex flex-col gap-2 sm:min-w-64 sm:flex-row">
                <SelectMenu
                  value={elasticProjectId}
                  onChange={setElasticProjectId}
                  options={projects.map((project) => ({ value: project.id, label: project.display_name }))}
                  placeholder="Choose a project"
                  ariaLabel="Project to enable Elastic for"
                  variant="field"
                  size="md"
                  className="w-full max-w-none"
                  disabled={usingExistingElastic}
                />
                <Button onClick={() => void enableExistingElastic()} disabled={!elasticProjectId} isLoading={usingExistingElastic}>
                  <Zap className="h-4 w-4" /> Enable for Labs
                </Button>
              </div>
            </div>
          </section>
        )}

        <section className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_18rem]">
          <div className="space-y-4">
            <div className="flex items-center justify-between">
              <div><h2 className="text-sm font-semibold">Live connectors</h2><p className="text-xs text-muted-foreground">Only healthy, bound capabilities are offered to the agent.</p></div>
              {!loading && <span className="text-xs text-muted-foreground">{connectors.length} configured</span>}
            </div>

            {loading && connectors.length === 0 ? (
              <div className="flex min-h-64 items-center justify-center rounded-2xl border border-border bg-card/70"><Loader2 className="h-5 w-5 animate-spin text-primary" /></div>
            ) : connectors.length === 0 ? (
              <div className="flex min-h-72 flex-col items-center justify-center rounded-2xl border border-dashed border-primary/25 bg-card/60 p-8 text-center">
                <div className="relative mb-5"><div className="absolute inset-0 rounded-full bg-primary/25 blur-xl" /><div className="relative rounded-2xl border border-primary/20 bg-primary/10 p-4 text-primary"><Unplug className="h-7 w-7" /></div></div>
                <h3 className="font-semibold">Your capability fabric is empty</h3>
                <p className="mt-1 max-w-sm text-sm text-muted-foreground">Try the fixture end to end, or connect a project-scoped search index, approved database view, or reviewed API.</p>
                <Button className="mt-5" onClick={() => setSetupOpen(true)}><Plus className="h-4 w-4" /> Configure first connector</Button>
              </div>
            ) : connectors.map((connector) => {
              const provider = providers.find((item) => item.provider_key === connector.provider_key);
              const connectorBindings = bindings[connector.id] ?? [];
              const projectIds = [...new Set(connectorBindings.flatMap((binding) => binding.project_ids))];
              const sourceIds = Array.isArray(connector.config.source_ids)
                ? connector.config.source_ids.filter((id): id is string => typeof id === "string")
                : [];
              const sourceLabels = new Map(existingElastic.sources.map((source) => [source.id, source.label]));
              const timeWindowDays = typeof connector.config.time_window_days === "number"
                ? connector.config.time_window_days
                : 7;
              const isBusy = busyId === connector.id;
              const cardExpanded = Boolean(expandedConnectorCards[connector.id]);
              const detailsId = `connector-details-${connector.id}`;
              return (
                <article key={connector.id} className="group overflow-hidden rounded-2xl border border-border/80 bg-card/85 shadow-elevation-sm backdrop-blur transition hover:border-primary/25 hover:shadow-elevation-md">
                  <div className={cn("flex flex-col gap-5 p-4 sm:px-5", cardExpanded ? "sm:py-6" : "sm:py-4")}>
                    <div className="flex items-center gap-3">
                      <button
                        type="button"
                        aria-expanded={cardExpanded}
                        aria-controls={detailsId}
                        onClick={() => setExpandedConnectorCards((current) => ({ ...current, [connector.id]: !current[connector.id] }))}
                        className="flex min-w-0 flex-1 items-center gap-4 rounded-xl text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/40"
                      >
                        <div className={cn("relative shrink-0 rounded-xl p-2.5", connector.provider_key === "mcp_elastic" ? "border border-border bg-white" : "border border-primary/15 bg-gradient-to-br from-primary/15 to-cyan-500/5 text-primary")}>
                          {connector.provider_key === "mcp_elastic"
                            ? <Image src="/elastic-logo.png" alt="Elastic" width={40} height={40} className="h-10 w-10 object-contain" />
                            : connector.provider_key === "http_elastic" ? <Search className="h-5 w-5" /> : <Database className="h-5 w-5" />}
                          <span className={cn("absolute -right-1 -top-1 h-2.5 w-2.5 rounded-full border-2 border-card", connector.health_status === "healthy" ? "bg-emerald-500" : connector.health_status === "unhealthy" ? "bg-rose-500" : "bg-amber-400")} />
                        </div>
                        <div className="min-w-0">
                          <div className="flex flex-wrap items-center gap-2"><h3 className="truncate font-semibold">{connector.name}</h3><span className={cn("rounded-full border px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide", statusTone(connector.health_status))}>{connector.health_status}</span></div>
                          {cardExpanded && <p className="mt-1 text-sm text-muted-foreground">{provider?.description ?? connector.provider_key}</p>}
                        </div>
                        {cardExpanded ? <ChevronDown className="ml-auto h-4 w-4 shrink-0 text-muted-foreground" /> : <ChevronRight className="ml-auto h-4 w-4 shrink-0 text-muted-foreground" />}
                      </button>
                      {cardExpanded && <div className="flex shrink-0 items-center gap-1">
                        <Button variant="ghost" size="sm" disabled={isBusy} onClick={() => void perform(connector, () => authedFetch(`/api/admin/connectors/${connector.id}`, { method: "PATCH", headers: { "content-type": "application/json" }, body: JSON.stringify({ enabled: !connector.enabled }) }), "Could not update connector")}>
                          <Power className="h-3.5 w-3.5" /> {connector.enabled ? "Disable" : "Enable"}
                        </Button>
                        <Button variant="ghost" size="sm" className="text-destructive hover:text-destructive" onClick={() => setDeleteTarget(connector)}><Trash2 className="h-3.5 w-3.5" /></Button>
                      </div>}
                    </div>
                    {cardExpanded && <div id={detailsId} className="flex flex-col gap-5">
                      <div className="grid gap-3 sm:grid-cols-3">
                        <InfoTile icon={ShieldCheck} label="Health" value={connector.health_summary ?? "Awaiting first probe"} detail={relativeDate(connector.health_checked_at)} />
                        <InfoTile icon={Link2} label="Project scope" value={projectIds.length ? projectIds.map((id) => projects.find((project) => project.id === id)?.display_name ?? id).join(", ") : "Not bound"} detail={`${connectorBindings.length} capability binding${connectorBindings.length === 1 ? "" : "s"}`} />
                        <InfoTile icon={KeyRound} label="Credentials" value={connector.credential_present ? "Server secret reference attached" : "No credential reference"} detail={connector.auth_mode === "none" ? "No authentication" : connector.credential_reference_type ?? "Opaque reference"} />
                      </div>
                      {connector.provider_key === "mcp_elastic" && (
                      <div className="rounded-xl border border-cyan-500/20 bg-cyan-500/[0.04] p-4">
                        <div className="flex flex-wrap items-center justify-between gap-2">
                          <div>
                            <p className="text-xs font-semibold">Elastic sources available in each project</p>
                            <p className="mt-1 text-[11px] text-muted-foreground">A test search checks the last {timeWindowDays} days.</p>
                          </div>
                          <span className="rounded-md border border-border bg-background/70 px-2 py-1 text-[10px] text-muted-foreground">{sourceIds.length} source{sourceIds.length === 1 ? "" : "s"}</span>
                        </div>
                        <div className="mt-3 divide-y divide-border/60">
                          {projectIds.map((projectId) => {
                            const projectKey = `${connector.id}:${projectId}`;
                            const expanded = Boolean(expandedSourceProjects[projectKey]);
                            const detailId = `elastic-sources-${connector.id}-${projectId.replace(/[^a-zA-Z0-9_-]/g, "-")}`;
                            return (
                              <div key={projectId} className="py-1.5 first:pt-0 last:pb-0">
                                <button
                                  type="button"
                                  aria-expanded={expanded}
                                  aria-controls={detailId}
                                  onClick={() => setExpandedSourceProjects((current) => ({ ...current, [projectKey]: !current[projectKey] }))}
                                  className="flex w-full items-center gap-2 rounded-lg px-2 py-2 text-left transition-colors hover:bg-background/70 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/40"
                                >
                                  {expanded ? <ChevronDown className="h-4 w-4 shrink-0 text-muted-foreground" /> : <ChevronRight className="h-4 w-4 shrink-0 text-muted-foreground" />}
                                  <span className="min-w-0 flex-1 truncate text-xs font-medium">{projects.find((project) => project.id === projectId)?.display_name ?? projectId}</span>
                                  <span className="shrink-0 text-[10px] text-muted-foreground">{sourceIds.length} source{sourceIds.length === 1 ? "" : "s"} · {timeWindowDays} days</span>
                                </button>
                                {expanded && (
                                  <div id={detailId} className="flex flex-wrap gap-1.5 px-8 pb-2 pt-1">
                                    {sourceIds.length ? sourceIds.map((sourceId) => (
                                      <span key={`${projectId}-${sourceId}`} className="rounded-md border border-cyan-500/20 bg-background/70 px-2 py-1 text-[10px] text-muted-foreground" title={sourceId}>
                                        {sourceLabels.get(sourceId) ?? "Source name unavailable"}
                                      </span>
                                    )) : <span className="text-[10px] text-muted-foreground">No sources selected</span>}
                                  </div>
                                )}
                              </div>
                            );
                          })}
                          {!existingElastic.sources.length && sourceIds.length > 0 && (
                            <p className="pt-2 text-[10px] text-muted-foreground">Elastic source names are temporarily unavailable; refresh to try again.</p>
                          )}
                        </div>
                      </div>
                      )}
                    </div>}
                  </div>
                  {cardExpanded && <div className="flex flex-wrap items-center justify-between gap-3 border-t border-border/60 bg-muted/15 px-5 py-3 sm:px-6">
                    <div className="flex flex-wrap gap-1.5">{connectorBindings.map((binding) => <span key={binding.id} className="rounded-md border border-border bg-background/70 px-2 py-1 font-mono text-[10px] text-muted-foreground">{binding.capability_id}</span>)}</div>
                    <div className="flex flex-wrap gap-2">
                      <Button variant="ghost" size="sm" onClick={() => editAccess(connector)}><Fingerprint className="h-3.5 w-3.5" /> Project access</Button>
                      {connector.provider_key === "http_elastic" && <Button variant="ghost" size="sm" onClick={() => void inspectResources(connector)}><Search className="h-3.5 w-3.5" /> Browse indexes</Button>}
                      <Button variant="ghost" size="sm" disabled={connector.health_status !== "healthy" || connectorBindings.length === 0} onClick={() => openPreview(connector)}><Sparkles className="h-3.5 w-3.5" /> {connector.provider_key === "mcp_elastic" ? "Test search" : "Preview evidence"}</Button>
                      <Button variant="secondary" size="sm" isLoading={isBusy} onClick={() => void perform(connector, () => authedFetch(`/api/admin/connectors/${connector.id}/health`, { method: "POST" }), "Health check failed")}><Gauge className="h-3.5 w-3.5" /> Test connection</Button>
                    </div>
                  </div>}
                </article>
              );
            })}
          </div>

          <aside className="space-y-4">
            <div className="rounded-2xl border border-border bg-card/80 p-5 shadow-elevation-sm">
              <div className="mb-4 flex items-center gap-2 text-sm font-semibold"><ShieldCheck className="h-4 w-4 text-emerald-500" /> How admission works</div>
              <ol className="space-y-4">
                {["Register a trusted connector", "Pass its live health probe", "Bind capabilities to projects", "Labs receives only eligible tools"].map((text, index) => (
                  <li key={text} className="flex gap-3 text-xs text-muted-foreground"><span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full border border-primary/20 bg-primary/5 text-[10px] font-semibold text-primary">{index + 1}</span><span className="pt-0.5">{text}</span></li>
                ))}
              </ol>
            </div>
            <Link href="/admin/runtime" className="group block rounded-2xl border border-border bg-card/80 p-5 shadow-elevation-sm transition hover:border-primary/25">
              <div className="flex items-center justify-between"><div className="rounded-lg bg-cyan-500/10 p-2 text-cyan-600 dark:text-cyan-300"><Activity className="h-4 w-4" /></div><ExternalLink className="h-3.5 w-3.5 text-muted-foreground transition group-hover:text-primary" /></div>
              <h3 className="mt-4 text-sm font-semibold">Runtime ingestion</h3>
              <p className="mt-1 text-xs leading-5 text-muted-foreground">TMC and Elasticsearch ingestion remain in Runtime Sources. Agent connectors are query-time evidence links.</p>
            </Link>
            <div className="rounded-2xl border border-dashed border-border bg-muted/20 p-5">
              <div className="text-[10px] font-semibold uppercase tracking-[0.16em] text-muted-foreground">On the horizon</div>
              <p className="mt-2 text-sm font-medium">Custom MCP & approved actions</p><p className="mt-1 text-xs leading-5 text-muted-foreground">Coming after per-tool scope and approval controls. Database and reviewed API searches are available above.</p>
            </div>
          </aside>
        </section>
      </div>

      <Modal open={setupOpen} title="Add an agent connector" description="Configure, scope, and verify a live evidence source in one flow." onClose={closeSetup} size="lg" footer={<><Button variant="secondary" onClick={closeSetup} disabled={saving}>Cancel</Button><Button type="submit" form="connector-setup" isLoading={saving} disabled={!selectedProvider || !setup.name.trim() || setup.projectIds.length === 0}>Connect & verify <ArrowRight className="h-4 w-4" /></Button></>}>
        <form id="connector-setup" className="space-y-5" onSubmit={(event) => void createConnector(event)}>
          <Field label="Connector type" hint="Only installed, trusted providers appear here.">
            <SelectMenu value={setup.providerKey} onChange={chooseProvider} options={providers.map((provider) => ({ value: provider.provider_key, label: provider.display_name, hint: provider.description }))} placeholder="Choose a provider" ariaLabel="Connector type" variant="field" size="md" className="w-full max-w-none" />
          </Field>
          {selectedProvider && <>
            <div className="rounded-xl border border-primary/15 bg-primary/5 p-4"><div className="flex items-center gap-2 text-sm font-medium text-primary"><Sparkles className="h-4 w-4" /> {selectedProvider.display_name}</div><p className="mt-1 text-xs leading-5 text-muted-foreground">{selectedProvider.description}</p></div>
            <Field label="Display name"><Input value={setup.name} onChange={(event) => setSetup((current) => ({ ...current, name: event.target.value }))} required /></Field>
            <div className="grid gap-4 sm:grid-cols-2">
              {Object.entries(selectedProvider.config_schema).map(([key, kind]) => <Field key={key} label={FIELD_LABELS[key] ?? key.replaceAll("_", " ")} hint={kind.includes("array") ? "Comma-separated hostnames" : key === "view_name" ? "Must expose project_id, searchable_text, title, excerpt and source_ref" : key === "openapi_spec" ? "Paste an owner-reviewed OpenAPI 3 JSON document (64 KiB max). Only the selected GET operation runs." : undefined}>{key === "openapi_spec" ? <textarea className="min-h-36 w-full rounded-lg border border-border bg-background px-3 py-2 font-mono text-xs text-foreground focus:outline-none focus:ring-2 focus:ring-primary/30" value={setup.config[key] ?? ""} onChange={(event) => setSetup((current) => ({ ...current, config: { ...current.config, [key]: event.target.value } }))} required /> : <Input type={kind === "integer" || kind === "number" ? "number" : kind.includes("URL") ? "url" : "text"} value={setup.config[key] ?? ""} onChange={(event) => setSetup((current) => ({ ...current, config: { ...current.config, [key]: event.target.value } }))} required={["base_url", "allowed_hosts", "index", "dataset", "host", "database", "username", "view_name", "operation_id", "operation_path", "health_project_id"].includes(key)} />}</Field>)}
            </div>
            {selectedProvider.auth_modes.length > 1 && <Field label="Authentication"><SelectMenu value={setup.authMode} onChange={(value) => setSetup((current) => ({ ...current, authMode: value as AuthMode }))} options={selectedProvider.auth_modes.map((mode) => ({ value: mode, label: mode === "none" ? "No authentication" : "Credential reference" }))} ariaLabel="Authentication mode" variant="field" size="md" className="w-full max-w-none" /></Field>}
            {setup.authMode === "credential_reference" && <Field label="Credential reference" hint="Use a server-provisioned env:NEXUS_CONNECTOR_* reference. Never paste a secret here."><Input value={setup.credentialReference} onChange={(event) => setSetup((current) => ({ ...current, credentialReference: event.target.value }))} placeholder="env:NEXUS_CONNECTOR_SEARCH_READER" required /></Field>}
            <Field label="Project access" hint="Labs receives this connector only inside selected projects.">
              <div className="max-h-44 space-y-1 overflow-y-auto rounded-xl border border-border p-2">{projects.map((project) => { const checked = setup.projectIds.includes(project.id); return <label key={project.id} className={cn("flex cursor-pointer items-center gap-3 rounded-lg px-3 py-2.5 text-sm transition", checked ? "bg-primary/8 text-foreground" : "hover:bg-muted/60 text-muted-foreground")}><input type="checkbox" className="sr-only" checked={checked} onChange={() => setSetup((current) => ({ ...current, projectIds: checked ? current.projectIds.filter((id) => id !== project.id) : [...current.projectIds, project.id] }))} /><span className={cn("flex h-5 w-5 items-center justify-center rounded-md border", checked ? "border-primary bg-primary text-primary-foreground" : "border-border")}><Check className={cn("h-3.5 w-3.5", !checked && "opacity-0")} /></span><span className="min-w-0 flex-1"><span className="block truncate font-medium">{project.display_name}</span><span className="block truncate text-[11px] opacity-70">{project.id}</span></span></label>; })}</div>
            </Field>
          </>}
        </form>
      </Modal>

      <Modal open={Boolean(deleteTarget)} title="Remove connector?" description="This removes its project bindings too. Labs will stop receiving these capabilities immediately." onClose={() => setDeleteTarget(null)} size="sm" footer={<><Button variant="secondary" onClick={() => setDeleteTarget(null)}>Cancel</Button><Button variant="destructive" isLoading={busyId === deleteTarget?.id} onClick={() => { const target = deleteTarget; if (!target) return; setDeleteTarget(null); void perform(target, () => authedFetch(`/api/admin/connectors/${target.id}`, { method: "DELETE" }), "Could not remove connector"); }}>Remove connector</Button></>}><p className="text-sm text-muted-foreground">You are removing <span className="font-medium text-foreground">{deleteTarget?.name}</span>. This cannot be undone.</p></Modal>

      <Modal open={Boolean(accessFor)} title="Project access" description="Choose the projects that may offer this connector's capabilities to Labs." onClose={() => setAccessFor(null)} size="lg" footer={<><Button variant="secondary" onClick={() => setAccessFor(null)}>Cancel</Button><Button disabled={accessProjects.length === 0} isLoading={busyId === accessFor?.id} onClick={() => void saveAccess()}>Save access</Button></>}>
        <div className="space-y-1">{projects.map((project) => {
          const checked = accessProjects.includes(project.id);
          return <label key={project.id} className={cn("flex cursor-pointer items-center gap-3 rounded-lg px-3 py-2.5 text-sm", checked ? "bg-primary/10" : "hover:bg-muted/60")}><input type="checkbox" className="sr-only" checked={checked} onChange={() => setAccessProjects((current) => checked ? current.filter((id) => id !== project.id) : [...current, project.id])} /><span className={cn("flex h-5 w-5 items-center justify-center rounded-md border", checked ? "border-primary bg-primary text-primary-foreground" : "border-border")}><Check className={cn("h-3.5 w-3.5", !checked && "opacity-0")} /></span><span>{project.display_name}</span></label>;
        })}</div>
      </Modal>

      <Modal open={Boolean(resourcesFor)} title="Available indexes" description="Discovered from the live endpoint. Selecting one updates this connector and requires a fresh health check." onClose={() => setResourcesFor(null)} size="lg">
        {resourcesLoading ? <div className="flex justify-center py-8"><Loader2 className="h-5 w-5 animate-spin text-primary" /></div> : resources.length === 0 ? <p className="py-6 text-center text-sm text-muted-foreground">No indexes were returned.</p> : <div className="space-y-2">{resources.map((resource) => <div key={resource.id} className="flex items-center justify-between gap-3 rounded-xl border border-border p-3"><div className="min-w-0"><div className="truncate text-sm font-medium">{resource.label}</div><div className="text-xs text-muted-foreground">{resource.kind}</div></div><Button size="sm" variant="secondary" disabled={resourcesFor?.config.index === resource.id} onClick={() => void useIndex(resource.id)}>{resourcesFor?.config.index === resource.id ? "Selected" : "Use index"}</Button></div>)}</div>}
      </Modal>

      <Modal open={Boolean(previewFor)} title={previewFor?.provider_key === "mcp_elastic" ? "Test Elastic search" : "Preview live evidence"} description="See the bounded, project-scoped results Labs would receive." onClose={() => setPreviewFor(null)} size="lg">
        <form className="space-y-4" onSubmit={(event) => void runPreview(event)}>
          <Field label="Project"><SelectMenu value={previewProjectId} onChange={setPreviewProjectId} options={[...new Set((bindings[previewFor?.id ?? -1] ?? []).flatMap((binding) => binding.project_ids))].map((id) => ({ value: id, label: projects.find((project) => project.id === id)?.display_name ?? id }))} ariaLabel="Preview project" variant="field" size="md" className="w-full max-w-none" /></Field>
          {previewFor?.provider_key === "mcp_elastic" && (() => {
            const ids = Array.isArray(previewFor.config.source_ids)
              ? previewFor.config.source_ids.filter((id): id is string => typeof id === "string")
              : [];
            const windowDays = typeof previewFor.config.time_window_days === "number" ? previewFor.config.time_window_days : 7;
            const names = ids.map((id) => existingElastic.sources.find((source) => source.id === id)?.label).filter((label): label is string => Boolean(label));
            return <div className="rounded-lg border border-cyan-500/20 bg-cyan-500/[0.04] p-3 text-xs"><span className="font-medium">Search scope:</span> {names.length ? names.join(", ") : "Configured Elastic sources"} · last {windowDays} days</div>;
          })()}
          <Field label="Search query"><Input value={previewQuery} onChange={(event) => setPreviewQuery(event.target.value)} placeholder="Search for a real record" required /></Field>
          <Button type="submit" isLoading={previewLoading} disabled={!previewProjectId || !previewQuery.trim()}><Search className="h-4 w-4" /> {previewFor?.provider_key === "mcp_elastic" ? "Run test search" : "Run preview"}</Button>
        </form>
        {previewError && <p className="mt-4 rounded-lg border border-destructive/25 bg-destructive/5 p-3 text-sm text-destructive">{previewError}</p>}
        {previewItems.length > 0 && <div className="mt-5 space-y-2">{previewItems.map((item, index) => <div key={`${item.source_ref}-${index}`} className="rounded-xl border border-border bg-muted/20 p-3"><div className="text-sm font-medium">{item.title}</div><p className="mt-1 line-clamp-4 whitespace-pre-wrap text-xs leading-5 text-muted-foreground">{item.excerpt}</p><div className="mt-2 truncate font-mono text-[10px] text-muted-foreground">{item.source_ref}</div></div>)}{previewTruncated && <p className="text-xs text-muted-foreground">More results exist. Labs receives a bounded set per call.</p>}</div>}
        {previewComplete && !previewError && previewQuery && previewItems.length === 0 && <p className="mt-5 text-sm text-muted-foreground">No matching records were returned from the selected sources in this time window.</p>}
      </Modal>
    </main>
  );
}

function Field({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return <label className="block"><span className="mb-1.5 flex items-center justify-between gap-3 text-xs font-medium text-foreground"><span>{label}</span>{hint && <span className="font-normal text-muted-foreground">{hint}</span>}</span>{children}</label>;
}

function InfoTile({ icon: Icon, label, value, detail }: { icon: LucideIcon; label: string; value: string; detail: string }) {
  return <div className="min-w-0 rounded-xl border border-border/70 bg-muted/20 p-3.5"><div className="flex items-center gap-2 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground"><Icon className="h-3.5 w-3.5" /> {label}</div><div className="mt-2 truncate text-sm font-medium" title={value}>{value}</div><div className="mt-1 truncate text-[11px] text-muted-foreground">{detail}</div></div>;
}
