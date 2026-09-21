"use client";

import { FormEvent, type ReactNode, useCallback, useEffect, useMemo, useState } from "react";
import {
  Activity,
  ArrowRight,
  Check,
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
  search_path: "Search path",
  health_path: "Health path",
  timeout_seconds: "Timeout (seconds)",
  max_response_bytes: "Maximum response size",
  max_results: "Maximum results",
  dataset: "Dataset",
};

const FIELD_DEFAULTS: Record<string, string> = {
  dataset: "default",
  max_results: "10",
  search_path: "/{index}/_search",
  health_path: "/_cluster/health",
  timeout_seconds: "5",
  max_response_bytes: "1000000",
};

async function errorMessage(response: Response, fallback: string) {
  const body = await response.json().catch(() => null);
  return apiErrorMessage((body as { detail?: unknown } | null)?.detail, fallback);
}

function parseConfig(schema: Record<string, string>, values: Record<string, string>) {
  const parsed: Record<string, unknown> = {};
  for (const [key, kind] of Object.entries(schema)) {
    const raw = values[key]?.trim();
    if (!raw) continue;
    if (kind.includes("array")) parsed[key] = raw.split(",").map((part) => part.trim()).filter(Boolean);
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
  const [bindings, setBindings] = useState<Record<number, Binding[]>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [setupOpen, setSetupOpen] = useState(false);
  const [setup, setSetup] = useState<SetupState>(EMPTY_SETUP);
  const [saving, setSaving] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<Connector | null>(null);

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
                <p className="mt-1 max-w-sm text-sm text-muted-foreground">Add the deterministic fixture for a safe end-to-end test, or connect an allowlisted search index.</p>
                <Button className="mt-5" onClick={() => setSetupOpen(true)}><Plus className="h-4 w-4" /> Configure first connector</Button>
              </div>
            ) : connectors.map((connector) => {
              const provider = providers.find((item) => item.provider_key === connector.provider_key);
              const connectorBindings = bindings[connector.id] ?? [];
              const projectIds = [...new Set(connectorBindings.flatMap((binding) => binding.project_ids))];
              const isBusy = busyId === connector.id;
              return (
                <article key={connector.id} className="group overflow-hidden rounded-2xl border border-border/80 bg-card/85 shadow-elevation-sm backdrop-blur transition hover:border-primary/25 hover:shadow-elevation-md">
                  <div className="flex flex-col gap-5 p-5 sm:p-6">
                    <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
                      <div className="flex min-w-0 items-start gap-4">
                        <div className="relative shrink-0 rounded-xl border border-primary/15 bg-gradient-to-br from-primary/15 to-cyan-500/5 p-3 text-primary">
                          {connector.provider_key === "http_elastic" ? <Search className="h-5 w-5" /> : <Database className="h-5 w-5" />}
                          <span className={cn("absolute -right-1 -top-1 h-2.5 w-2.5 rounded-full border-2 border-card", connector.health_status === "healthy" ? "bg-emerald-500" : connector.health_status === "unhealthy" ? "bg-rose-500" : "bg-amber-400")} />
                        </div>
                        <div className="min-w-0">
                          <div className="flex flex-wrap items-center gap-2"><h3 className="truncate font-semibold">{connector.name}</h3><span className={cn("rounded-full border px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide", statusTone(connector.health_status))}>{connector.health_status}</span></div>
                          <p className="mt-1 text-sm text-muted-foreground">{provider?.description ?? connector.provider_key}</p>
                        </div>
                      </div>
                      <div className="flex shrink-0 items-center gap-1">
                        <Button variant="ghost" size="sm" disabled={isBusy} onClick={() => void perform(connector, () => authedFetch(`/api/admin/connectors/${connector.id}`, { method: "PATCH", headers: { "content-type": "application/json" }, body: JSON.stringify({ enabled: !connector.enabled }) }), "Could not update connector")}>
                          <Power className="h-3.5 w-3.5" /> {connector.enabled ? "Disable" : "Enable"}
                        </Button>
                        <Button variant="ghost" size="sm" className="text-destructive hover:text-destructive" onClick={() => setDeleteTarget(connector)}><Trash2 className="h-3.5 w-3.5" /></Button>
                      </div>
                    </div>
                    <div className="grid gap-3 sm:grid-cols-3">
                      <InfoTile icon={ShieldCheck} label="Health" value={connector.health_summary ?? "Awaiting first probe"} detail={relativeDate(connector.health_checked_at)} />
                      <InfoTile icon={Link2} label="Project scope" value={projectIds.length ? projectIds.map((id) => projects.find((project) => project.id === id)?.display_name ?? id).join(", ") : "Not bound"} detail={`${connectorBindings.length} capability binding${connectorBindings.length === 1 ? "" : "s"}`} />
                      <InfoTile icon={KeyRound} label="Credentials" value={connector.credential_present ? "Vault reference attached" : "No credential reference"} detail={connector.auth_mode === "none" ? "No authentication" : connector.credential_reference_type ?? "Opaque reference"} />
                    </div>
                  </div>
                  <div className="flex flex-wrap items-center justify-between gap-3 border-t border-border/60 bg-muted/15 px-5 py-3 sm:px-6">
                    <div className="flex flex-wrap gap-1.5">{connectorBindings.map((binding) => <span key={binding.id} className="rounded-md border border-border bg-background/70 px-2 py-1 font-mono text-[10px] text-muted-foreground">{binding.capability_id}</span>)}</div>
                    <Button variant="secondary" size="sm" isLoading={isBusy} onClick={() => void perform(connector, () => authedFetch(`/api/admin/connectors/${connector.id}/health`, { method: "POST" }), "Health check failed")}><Gauge className="h-3.5 w-3.5" /> Test connection</Button>
                  </div>
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
              <p className="mt-2 text-sm font-medium">Databases & custom MCP</p><p className="mt-1 text-xs leading-5 text-muted-foreground">Not available yet. This screen only exposes providers the backend can safely execute today.</p>
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
              {Object.entries(selectedProvider.config_schema).map(([key, kind]) => <Field key={key} label={FIELD_LABELS[key] ?? key.replaceAll("_", " ")} hint={kind.includes("array") ? "Comma-separated hostnames" : undefined}><Input type={kind === "integer" || kind === "number" ? "number" : kind.includes("URL") ? "url" : "text"} value={setup.config[key] ?? ""} onChange={(event) => setSetup((current) => ({ ...current, config: { ...current.config, [key]: event.target.value } }))} required={["base_url", "allowed_hosts", "index", "dataset"].includes(key)} /></Field>)}
            </div>
            {selectedProvider.auth_modes.length > 1 && <Field label="Authentication"><SelectMenu value={setup.authMode} onChange={(value) => setSetup((current) => ({ ...current, authMode: value as AuthMode }))} options={selectedProvider.auth_modes.map((mode) => ({ value: mode, label: mode === "none" ? "No authentication" : "Credential reference" }))} ariaLabel="Authentication mode" variant="field" size="md" className="w-full max-w-none" /></Field>}
            {setup.authMode === "credential_reference" && <Field label="Credential reference" hint="An opaque vault reference only; never paste a secret here."><Input value={setup.credentialReference} onChange={(event) => setSetup((current) => ({ ...current, credentialReference: event.target.value }))} placeholder="vault://team/search-reader" required /></Field>}
            <Field label="Project access" hint="Labs receives this connector only inside selected projects.">
              <div className="max-h-44 space-y-1 overflow-y-auto rounded-xl border border-border p-2">{projects.map((project) => { const checked = setup.projectIds.includes(project.id); return <label key={project.id} className={cn("flex cursor-pointer items-center gap-3 rounded-lg px-3 py-2.5 text-sm transition", checked ? "bg-primary/8 text-foreground" : "hover:bg-muted/60 text-muted-foreground")}><input type="checkbox" className="sr-only" checked={checked} onChange={() => setSetup((current) => ({ ...current, projectIds: checked ? current.projectIds.filter((id) => id !== project.id) : [...current.projectIds, project.id] }))} /><span className={cn("flex h-5 w-5 items-center justify-center rounded-md border", checked ? "border-primary bg-primary text-primary-foreground" : "border-border")}><Check className={cn("h-3.5 w-3.5", !checked && "opacity-0")} /></span><span className="min-w-0 flex-1"><span className="block truncate font-medium">{project.display_name}</span><span className="block truncate text-[11px] opacity-70">{project.id}</span></span></label>; })}</div>
            </Field>
          </>}
        </form>
      </Modal>

      <Modal open={Boolean(deleteTarget)} title="Remove connector?" description="This removes its project bindings too. Labs will stop receiving these capabilities immediately." onClose={() => setDeleteTarget(null)} size="sm" footer={<><Button variant="secondary" onClick={() => setDeleteTarget(null)}>Cancel</Button><Button variant="destructive" isLoading={busyId === deleteTarget?.id} onClick={() => { const target = deleteTarget; if (!target) return; setDeleteTarget(null); void perform(target, () => authedFetch(`/api/admin/connectors/${target.id}`, { method: "DELETE" }), "Could not remove connector"); }}>Remove connector</Button></>}><p className="text-sm text-muted-foreground">You are removing <span className="font-medium text-foreground">{deleteTarget?.name}</span>. This cannot be undone.</p></Modal>
    </main>
  );
}

function Field({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return <label className="block"><span className="mb-1.5 flex items-center justify-between gap-3 text-xs font-medium text-foreground"><span>{label}</span>{hint && <span className="font-normal text-muted-foreground">{hint}</span>}</span>{children}</label>;
}

function InfoTile({ icon: Icon, label, value, detail }: { icon: LucideIcon; label: string; value: string; detail: string }) {
  return <div className="min-w-0 rounded-xl border border-border/70 bg-muted/20 p-3.5"><div className="flex items-center gap-2 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground"><Icon className="h-3.5 w-3.5" /> {label}</div><div className="mt-2 truncate text-sm font-medium" title={value}>{value}</div><div className="mt-1 truncate text-[11px] text-muted-foreground">{detail}</div></div>;
}
