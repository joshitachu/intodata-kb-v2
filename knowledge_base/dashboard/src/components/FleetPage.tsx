import { useState } from "react";
import {
  describeMcError,
  type ClientSnapshot,
  type FleetClient,
  type FleetProject,
  type SnapshotEntry,
} from "../api/fleet";
import { useFleet, useTriggerGenerate, useTriggerScan } from "../hooks/useFleet";
import { useFleetClients } from "../hooks/useFleetClients";
import { safeInstanceHref } from "../lib/fleetClient";
import { timeAgo } from "../lib/timeago";
import FleetAddClientDialog, { type AddClientPrefill } from "./FleetAddClientDialog";
import FleetDetailPanel from "./FleetDetailPanel";
import FleetLicenceBadge from "./FleetLicenceBadge";

// ── Helpers ───────────────────────────────────────────────────────────────────

function clientStatus(snapshot: ClientSnapshot): "online" | "stale" | "offline" {
  const diff = Date.now() - new Date(snapshot.sent_at).getTime();
  if (diff < 5 * 60 * 1000) return "online";
  if (diff < 15 * 60 * 1000) return "stale";
  return "offline";
}

function StatusDot({ status }: { status: "online" | "stale" | "offline" }) {
  const colors = {
    online: "bg-emerald-400",
    stale: "bg-amber-400",
    offline: "bg-red-500",
  };
  return (
    <span
      className={`inline-block w-2 h-2 rounded-full ${colors[status]}`}
      title={status}
    />
  );
}

function Stat({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-[10px] uppercase tracking-widest text-slate-500 font-mono">{label}</span>
      <span className="text-sm font-medium text-slate-200">{value}</span>
    </div>
  );
}

// ── Project row inside a client card ─────────────────────────────────────────

function ProjectRow({
  project,
  clientSlug,
}: {
  project: FleetProject;
  clientSlug: string;
}) {
  const scan = useTriggerScan();
  const generate = useTriggerGenerate();
  const isBusy =
    scan.isPending ||
    generate.isPending ||
    ["scanning", "generating"].includes(project.status);

  const statusColor: Record<string, string> = {
    ready: "text-emerald-400",
    scanning: "text-sky-400",
    generating: "text-violet-400",
    error: "text-red-400",
    pending: "text-slate-500",
  };

  return (
    <div className="flex items-center justify-between py-2 border-b border-white/[0.04] last:border-0 gap-4">
      <div className="flex-1 min-w-0">
        <p className="text-xs font-mono text-slate-300 truncate">{project.display_name}</p>
        <p className="text-[10px] text-slate-600 uppercase tracking-wide">
          {project.platform} &middot; {project.doc_count ?? 0} docs &middot; {project.job_count ?? 0} jobs
        </p>
        {project.error_summary && (
          <p className="text-[10px] text-red-400 mt-0.5 truncate">{project.error_summary}</p>
        )}
      </div>

      <span className={`text-[10px] font-mono uppercase ${statusColor[project.status] ?? "text-slate-500"}`}>
        {project.status}
      </span>

      <div className="flex items-center gap-1.5 shrink-0">
        <button
          disabled={isBusy}
          onClick={() => scan.mutate({ clientSlug, projectId: project.id })}
          className="px-2 py-1 text-[10px] font-mono rounded border border-white/[0.1] text-slate-400 hover:text-slate-200 hover:border-white/[0.2] disabled:opacity-40 disabled:cursor-not-allowed transition-all"
        >
          {scan.isPending ? "..." : "Scan"}
        </button>
        <button
          disabled={isBusy}
          onClick={() => generate.mutate({ clientSlug, projectId: project.id })}
          className="px-2 py-1 text-[10px] font-mono rounded border border-indigo-500/30 text-indigo-400 hover:text-indigo-300 hover:border-indigo-400/50 disabled:opacity-40 disabled:cursor-not-allowed transition-all"
        >
          {generate.isPending ? "..." : "Generate"}
        </button>
      </div>
    </div>
  );
}

// ── Client card ───────────────────────────────────────────────────────────────

/**
 * The instance link for a card. A registered client links to its pinned
 * registry URL only; the heartbeat's `mcp_url` is client-controlled and is
 * used only for an unregistered instance, and only when it is https (or http
 * on localhost). Anything else is shown as plain text.
 */
function InstanceLink({ client, snapshot }: { client: FleetClient | null; snapshot: ClientSnapshot }) {
  const url = client ? client.mcp_url : (snapshot.mcp_url ?? null);
  if (!url) return null;
  const href = safeInstanceHref(url);
  if (!href) {
    return (
      <span
        data-testid="instance-url-text"
        title={client ? "Registered instance URL" : "Reported by the instance (unverified)"}
        className="max-w-[12rem] truncate text-[10px] font-mono text-slate-500"
      >
        {url}
      </span>
    );
  }
  return (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      onClick={(e) => e.stopPropagation()}
      title={client ? "Registered instance URL" : "Reported by the instance (unverified)"}
      className="px-2 py-1 text-[10px] font-mono rounded border border-white/[0.1] text-slate-500 hover:text-slate-300 hover:border-white/[0.2] transition-all"
    >
      Open &rarr;
    </a>
  );
}

function ClientCard({
  slug,
  snapshot,
  client,
  notRegistered,
  onSelect,
  onRegister,
}: {
  slug: string;
  snapshot: ClientSnapshot;
  client: FleetClient | null;
  /** True only when the registry was read and has no entry for this slug. */
  notRegistered: boolean;
  onSelect: () => void;
  onRegister: () => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const st = clientStatus(snapshot);
  const { totals, projects } = snapshot;

  const errorProjects = projects.filter((p) => p.status === "error" || p.error_summary);
  const hasErrors = errorProjects.length > 0;

  return (
    <div className="bg-surface-card border border-white/[0.06] rounded-xl p-5 space-y-4 hover:border-white/[0.1] transition-all cursor-pointer" onClick={onSelect}>
      {/* Header */}
      <div className="flex items-start justify-between gap-4">
        <div className="flex items-center gap-2.5">
          <StatusDot status={st} />
          <div>
            <h3 className="text-sm font-semibold text-slate-100 leading-tight">{slug}</h3>
            <p className="text-[10px] text-slate-500 font-mono mt-0.5">
              {snapshot.environment} &middot; last seen {timeAgo(snapshot.sent_at)}
            </p>
            {notRegistered && (
              <p data-testid="not-registered-hint" className="text-[10px] text-slate-500 mt-0.5">
                Not registered -- add it to push licences.{" "}
                <button
                  type="button"
                  onClick={(e) => {
                    e.stopPropagation();
                    onRegister();
                  }}
                  className="text-indigo-400 hover:text-indigo-300 underline"
                >
                  Register
                </button>
              </p>
            )}
          </div>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <FleetLicenceBadge licence={snapshot.licence} />
          {hasErrors && (
            <span className="px-1.5 py-0.5 text-[10px] font-mono rounded bg-red-500/10 text-red-400 border border-red-500/20">
              {errorProjects.length} error{errorProjects.length > 1 ? "s" : ""}
            </span>
          )}
          <InstanceLink client={client} snapshot={snapshot} />
        </div>
      </div>

      {/* KPIs */}
      <div className="grid grid-cols-4 gap-3 pt-1">
        <Stat label="Projects" value={totals.project_count} />
        <Stat label="Docs" value={totals.doc_count} />
        <Stat label="Cost" value={`$${totals.cost_usd.toFixed(2)}`} />
        <Stat
          label="Quality"
          value={totals.latest_run_average_quality != null ? totals.latest_run_average_quality.toFixed(2) : "—"}
        />
      </div>

      <div className="grid grid-cols-4 gap-3">
        <Stat label="Jobs" value={totals.job_count} />
        <Stat label="Runs" value={totals.generate_run_count} />
        <Stat label="Input tok" value={(totals.input_tokens / 1_000_000).toFixed(1) + "M"} />
        <Stat label="Output tok" value={(totals.output_tokens / 1_000_000).toFixed(1) + "M"} />
      </div>

      {/* Service health */}
      <div className="flex items-center gap-3 pt-1 border-t border-white/[0.04]">
        {Object.entries(snapshot.health).map(([svc, s]) => (
          <span key={svc} className="flex items-center gap-1.5 text-[10px] font-mono text-slate-500">
            <span className={`w-1.5 h-1.5 rounded-full ${s === "ok" ? "bg-emerald-400" : "bg-red-500"}`} />
            {svc}
          </span>
        ))}
      </div>

      {/* Project list (collapsible) */}
      {projects.length > 0 && (
        <div className="border-t border-white/[0.04] pt-3" onClick={(e) => e.stopPropagation()}>
          <button
            onClick={() => setExpanded((v) => !v)}
            className="text-[10px] font-mono uppercase tracking-widest text-slate-500 hover:text-slate-300 transition-colors"
          >
            [{expanded ? "hide" : "show"} {projects.length} project{projects.length > 1 ? "s" : ""}]
          </button>
          {expanded && (
            <div className="mt-2">
              {projects.map((p) => (
                <ProjectRow key={p.id} project={p} clientSlug={slug} />
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ── Heartbeat that failed to parse ────────────────────────────────────────────

function InvalidSnapshotCard({
  slug,
  error,
  client,
  onSelect,
}: {
  slug: string;
  error: string;
  client: FleetClient | null;
  onSelect: () => void;
}) {
  return (
    <div
      data-testid="invalid-snapshot-card"
      className="bg-surface-card border border-red-500/20 rounded-xl p-5 space-y-3 hover:border-red-500/30 transition-all cursor-pointer"
      onClick={onSelect}
    >
      <div className="flex items-start justify-between gap-4">
        <div className="flex items-center gap-2.5">
          <span className="inline-block w-2 h-2 rounded-full bg-red-500" title="invalid heartbeat" />
          <div>
            <h3 className="text-sm font-semibold text-slate-100 leading-tight">{slug}</h3>
            <p className="text-[10px] text-red-400 font-mono mt-0.5">Invalid heartbeat data</p>
          </div>
        </div>
        <FleetLicenceBadge licence={null} />
      </div>
      <p className="text-[10px] text-slate-500 font-mono break-all">{error}</p>
      {client && <p className="text-xs text-slate-500 font-mono truncate">{client.mcp_url ?? "No instance URL"}</p>}
    </div>
  );
}

// ── Registered client without a heartbeat ─────────────────────────────────────

function WaitingClientCard({ client, onSelect }: { client: FleetClient; onSelect: () => void }) {
  return (
    <div
      data-testid="waiting-client-card"
      className="bg-surface-card border border-dashed border-white/[0.1] rounded-xl p-5 space-y-3 hover:border-white/[0.15] transition-all cursor-pointer"
      onClick={onSelect}
    >
      <div className="flex items-start justify-between gap-4">
        <div className="flex items-center gap-2.5">
          <span className="inline-block w-2 h-2 rounded-full bg-slate-600" title="no heartbeat" />
          <div>
            <h3 className="text-sm font-semibold text-slate-100 leading-tight">{client.slug}</h3>
            <p className="text-[10px] text-slate-500 font-mono mt-0.5">
              {client.last_heartbeat_at
                ? `No current snapshot · last heartbeat ${timeAgo(client.last_heartbeat_at)}`
                : "Waiting for first heartbeat"}
            </p>
          </div>
        </div>
        <FleetLicenceBadge licence={null} />
      </div>
      <p className="text-xs text-slate-500 font-mono truncate">{client.mcp_url ?? "No instance URL"}</p>
      {!client.has_control_key && (
        <p className="text-[10px] text-amber-400">No control key -- licence push disabled</p>
      )}
    </div>
  );
}

// ── Fleet summary bar ─────────────────────────────────────────────────────────

function FleetSummary({ snapshots, instanceCount }: { snapshots: ClientSnapshot[]; instanceCount: number }) {
  const clients = snapshots;
  const online = clients.filter((c) => clientStatus(c) === "online").length;
  const totalCost = clients.reduce((s, c) => s + c.totals.cost_usd, 0);
  const totalDocs = clients.reduce((s, c) => s + c.totals.doc_count, 0);
  const totalProjects = clients.reduce((s, c) => s + c.totals.project_count, 0);

  return (
    <div className="grid grid-cols-4 gap-4 p-4 bg-surface-card border border-white/[0.06] rounded-xl">
      <Stat label="Instances" value={`${online} / ${instanceCount} online`} />
      <Stat label="Total projects" value={totalProjects} />
      <Stat label="Total docs" value={totalDocs} />
      <Stat label="Total cost" value={`$${totalCost.toFixed(2)}`} />
    </div>
  );
}

// ── Page ──────────────────────────────────────────────────────────────────────

interface FleetRow {
  slug: string;
  snapshot: ClientSnapshot | null;
  /** Why this slug's heartbeat failed to parse; null when it parsed or there is none. */
  invalidSnapshot: string | null;
  client: FleetClient | null;
}

/** Registry clients and heartbeat clients, joined by slug and sorted. */
function fleetRows(snapshots: Record<string, SnapshotEntry>, clients: FleetClient[]): FleetRow[] {
  const bySlug = new Map<string, FleetRow>();
  for (const [slug, entry] of Object.entries(snapshots)) {
    bySlug.set(slug, {
      slug,
      snapshot: entry.status === "valid" ? entry.snapshot : null,
      invalidSnapshot: entry.status === "invalid" ? entry.error : null,
      client: null,
    });
  }
  for (const client of clients) {
    const row = bySlug.get(client.slug);
    if (row) row.client = client;
    else bySlug.set(client.slug, { slug: client.slug, snapshot: null, invalidSnapshot: null, client });
  }
  return [...bySlug.values()].sort((a, b) => a.slug.localeCompare(b.slug));
}

export default function FleetPage({ currentUsername = null }: { currentUsername?: string | null }) {
  const { data: snapshots, isLoading, error, dataUpdatedAt } = useFleet();
  const registry = useFleetClients();
  const [selectedSlug, setSelectedSlug] = useState<string | null>(null);
  const [addPrefill, setAddPrefill] = useState<AddClientPrefill | null>(null);

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-48">
        <p className="text-slate-500 text-sm font-mono">Connecting to Mission Control...</p>
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex items-center justify-center h-48">
        <p className="text-red-400 text-sm font-mono">
          Mission Control unreachable: {(error as Error).message}
        </p>
      </div>
    );
  }

  // A registry Mission Control could not read may be missing clients, so it
  // can neither claim "not registered" nor accept changes.
  const registryUnavailable = registry.data?.registry_unavailable === true;
  const registryKnown = registry.data != null && !registryUnavailable;
  const rows = fleetRows(snapshots ?? {}, registry.data?.clients ?? []);
  const validSnapshots = rows.flatMap((r) => (r.snapshot ? [r.snapshot] : []));
  const selected = selectedSlug ? rows.find((r) => r.slug === selectedSlug) : undefined;

  return (
    <div className="space-y-6 relative">
      {selected && (
        <FleetDetailPanel
          slug={selected.slug}
          snapshot={selected.snapshot}
          invalidSnapshot={selected.invalidSnapshot}
          client={selected.client}
          registryError={registry.error}
          registryUnavailable={registryUnavailable}
          onClose={() => setSelectedSlug(null)}
          currentUsername={currentUsername}
        />
      )}
      {addPrefill && <FleetAddClientDialog prefill={addPrefill} onClose={() => setAddPrefill(null)} />}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-base font-semibold text-slate-100">Fleet</h2>
          <p className="text-xs text-slate-500 mt-0.5">
            {rows.length} instance{rows.length !== 1 ? "s" : ""} &middot; refreshes every 30s
            {dataUpdatedAt ? ` · last updated ${timeAgo(new Date(dataUpdatedAt).toISOString())}` : ""}
          </p>
        </div>
        <button
          type="button"
          onClick={() => setAddPrefill({})}
          disabled={!registryKnown}
          title={
            registryKnown
              ? undefined
              : registryUnavailable
                ? "The Fleet registry file could not be read; changes are disabled"
                : "The client registry is not available on this Mission Control"
          }
          className="px-3 py-1.5 text-xs rounded border border-indigo-500/40 text-indigo-300 hover:bg-indigo-500/10 disabled:opacity-40 disabled:cursor-not-allowed"
        >
          Add client
        </button>
      </div>

      {registryUnavailable && (
        <p role="alert" data-testid="registry-unavailable-banner" className="text-xs text-amber-400">
          The Fleet registry file could not be read; registered clients may be missing and changes are disabled.
        </p>
      )}

      {registry.error && (
        <p role="alert" className="text-xs text-amber-400">
          Client registry unavailable ({describeMcError(registry.error)}) -- showing reporting instances only.
        </p>
      )}

      {rows.length === 0 ? (
        <div className="flex items-center justify-center h-40 border border-dashed border-white/[0.1] rounded-xl">
          <p className="text-slate-500 text-sm">No instances reporting yet. Add a client to connect one.</p>
        </div>
      ) : (
        <>
          <FleetSummary snapshots={validSnapshots} instanceCount={rows.length} />
          <div className="grid gap-4 grid-cols-1 lg:grid-cols-2">
            {rows.map((row) =>
              row.snapshot ? (
                <ClientCard
                  key={row.slug}
                  slug={row.slug}
                  snapshot={row.snapshot}
                  client={row.client}
                  notRegistered={registryKnown && row.client == null}
                  onSelect={() => setSelectedSlug(row.slug)}
                  // Slug only: the heartbeat URL is client-controlled and is
                  // shown as unverified text; the operator types the address.
                  onRegister={() => setAddPrefill({ slug: row.slug, reportedUrl: row.snapshot?.mcp_url ?? null })}
                />
              ) : row.invalidSnapshot != null ? (
                <InvalidSnapshotCard
                  key={row.slug}
                  slug={row.slug}
                  error={row.invalidSnapshot}
                  client={row.client}
                  onSelect={() => setSelectedSlug(row.slug)}
                />
              ) : row.client ? (
                <WaitingClientCard key={row.slug} client={row.client} onSelect={() => setSelectedSlug(row.slug)} />
              ) : null,
            )}
          </div>
        </>
      )}
    </div>
  );
}
