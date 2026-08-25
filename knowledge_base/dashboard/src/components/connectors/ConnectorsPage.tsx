import { useMemo, useState } from "react";
import { createPortal } from "react-dom";
import { parseApiError } from "../../lib/errors";
// Shared with the Generate dialog's live-data panel: one vocabulary for a
// provider's lane, derived from its manifest `kind`, in exactly one place.
import { describeKind } from "../../lib/connectors";
import {
  useConnectorCatalog,
  useConnectorSources,
  useConnectors,
  useCreateConnector,
  useDeleteConnector,
  useDiscoverConnectorSources,
  useKibanaDataViews,
  useKibanaSpaces,
  useRotateConnectorCredential,
  useTestConnector,
  useUpdateConnector,
} from "../../hooks/useConnectors";
import {
  useCreateRuntimeSource,
  useDeleteRuntimeSource,
  useRuntimeSourceTypes,
  useRuntimeSources,
  useSyncRuntimeSource,
} from "../../hooks/useRuntimeSources";
import { useProjects } from "../../hooks/useProjects";
// Scope discovery is a one-shot probe, not cached state: it is called straight
// off the client rather than through a hook so a stale result can never be
// replayed against edited credentials.
import { api } from "../../api/client";
import type {
  ConnectorInstance,
  ConnectorProvider,
  ConnectorStatus,
  RuntimeScopeOption,
  RuntimeSource,
} from "../../types";

type Filter = "all" | "connected" | "not_connected";

interface Props {
  canManage: boolean;
}

const statusStyle: Record<ConnectorStatus, string> = {
  connected: "border-[#a0e06f]/40 text-[#a0e06f]",
  degraded: "border-amber-400/40 text-amber-400",
  not_connected: "border-slate-500/40 text-slate-400",
  disabled: "border-slate-500/40 text-slate-500",
  unknown: "border-slate-500/40 text-slate-500",
};

interface ConnectorListRow {
  key: string;
  provider: ConnectorProvider;
  connector: ConnectorInstance | null;
}

/**
 * What every connector dialog is handed. The provider is not optional: a dialog
 * cannot be opened without knowing which provider it is for.
 */
export interface ConnectorDialogProps {
  provider: ConnectorProvider;
  connector: ConnectorInstance | null;
  onClose: () => void;
}

export type ConnectorDialogComponent = (props: ConnectorDialogProps) => React.ReactElement | null;

/**
 * Which provider gets which credential form.
 *
 * This is the anti-recurrence device for the bug where pressing Connect on the
 * Talend Management Console row opened the Connect Elastic Stack form. The old
 * state was `ConnectorInstance | null | undefined`: it carried the instance but
 * not the provider, so the render site had nothing to dispatch on and the form
 * was chosen by being the only one written. Dispatching on `provider_id` here,
 * with `editing` carrying the provider, makes "wrong form" a lookup miss rather
 * than a code-review question.
 *
 * A provider with no entry is not connectable ANYWHERE in this page -- see
 * `canConnect`, which both the row and the browser dialog call. Adding a
 * provider to the catalogue therefore cannot silently produce a Connect button
 * that opens someone else's form.
 *
 * Deliberately not a generic manifest-driven renderer: `configuration_schema`
 * is a flat dict of prose hints, and generating a form from it would regress
 * conditional auth, the Kibana companion with credential reuse, HTTPS
 * validation, the CA PEM field and the create -> rotate -> discover -> test
 * sequence that ElasticStackDialog implements.
 *
 * Every key must be a provider id the server actually registers. An `elasticsearch`
 * alias used to sit beside `elastic_stack` and concealed a live 422: the manifest
 * is registered as `elastic_stack` only (`connectors/providers/elasticsearch.py`),
 * so a Connect action keyed on the alias posted a provider id the catalogue has
 * never heard of. An id that is not in the catalogue cannot reach this registry,
 * and an id that is in it must not need an alias to be found.
 */
const connectorDialogRegistry: Record<string, ConnectorDialogComponent> = {
  elastic_stack: ElasticStackDialog,
  // TMC's credential is stored in the RAG backend's runtime_sources tables, so
  // this dialog posts to the runtime-source proxy rather than /mcp/connectors.
  // The registry is where that routing decision is recorded: a provider is
  // connectable here exactly when a form exists that knows its lane.
  talend_tmc: TalendTmcDialog,
};

/** Provider ids this build can render a credential form for. Exported so a key
 *  that no server manifest registers is a failing test, not a 422 at Connect. */
export function registeredDialogProviderIds(): string[] {
  return Object.keys(connectorDialogRegistry);
}

/**
 * The ONE predicate behind every Connect affordance on this page.
 *
 * The row and the browser dialog used to disagree (the row ignored
 * `provider.enabled`), and neither knew about credential ownership.
 *
 * `managed_by` is deliberately NOT consulted. It used to be, and that is what
 * sent an operator to a second application to paste a Talend token: the field
 * says where a credential is *stored*, and storage location was being read as
 * permission. The registry is the safe gate instead -- a provider is connectable
 * exactly when this build has a form that knows which lane to post to. That is a
 * strictly better guarantee than the old check, which would happily have opened
 * a connector-store form for a runtime-managed provider had one been registered.
 */
export function canConnect(provider: ConnectorProvider): boolean {
  return (
    provider.build_state === "available" &&
    provider.enabled &&
    connectorDialogRegistry[provider.provider_id] !== undefined
  );
}

/** True when this provider's credential is stored by the RAG backend's scheduler. */
export function isRuntimeManaged(provider: ConnectorProvider): boolean {
  return provider.managed_by === "runtime_sources";
}

/** Base URL of the RAG app, where runtime sources are managed. Optional. */
function ragAppUrl(): string | null {
  const raw = import.meta.env.VITE_RAG_APP_URL;
  return typeof raw === "string" && raw.trim() ? raw.trim().replace(/\/+$/, "") : null;
}

function runtimeSourcesUrl(): string | null {
  const base = ragAppUrl();
  return base ? `${base}/admin/runtime` : null;
}

function displayStatus(status: ConnectorStatus) {
  return status === "not_connected" ? "Not connected" : status[0].toUpperCase() + status.slice(1);
}

function formatDate(value: string | null) {
  if (!value) return "Never checked";
  return new Date(value).toLocaleString();
}

export default function ConnectorsPage({ canManage }: Props) {
  const catalog = useConnectorCatalog();
  const connectors = useConnectors();
  const [filter, setFilter] = useState<Filter>("all");
  const [query, setQuery] = useState("");
  const [searchOpen, setSearchOpen] = useState(false);
  const [browseOpen, setBrowseOpen] = useState(false);
  // Provider identity travels with the dialog request. Without it the render
  // site cannot dispatch, which is exactly how the TMC row came to open the
  // Elastic form.
  const [editing, setEditing] = useState<
    { provider: ConnectorProvider; connector: ConnectorInstance | null } | null
  >(null);
  const [discoveryConnector, setDiscoveryConnector] = useState<ConnectorInstance | null>(null);
  // Keyed on the ROW, not on a connector instance id: a runtime-managed row has
  // no ConnectorInstance behind it but still expands to show its coverage.
  const [expandedRowKey, setExpandedRowKey] = useState<string | null>(null);

  const rows = useMemo<ConnectorListRow[]>(() => {
    const providers = catalog.data?.providers ?? [];
    const instances = connectors.data?.connectors ?? [];
    const result: ConnectorListRow[] = [];

    for (const provider of providers) {
      const configured = instances.filter((item) => item.provider_id === provider.provider_id);
      if (configured.length === 0) {
        result.push({ key: `provider:${provider.provider_id}`, provider, connector: null });
      } else {
        for (const connector of configured) {
          result.push({ key: `connector:${connector.id}`, provider, connector });
        }
      }
    }

    // Keep configured instances visible if their provider was removed or disabled
    // after the instance was created.
    for (const connector of instances) {
      if (providers.some((provider) => provider.provider_id === connector.provider_id)) continue;
      result.push({
        key: `connector:${connector.id}`,
        connector,
        provider: {
          provider_id: connector.provider_id,
          label: connector.provider_id,
          version: connector.provider_version,
          authentication_types: [],
          capabilities: [],
          enabled: false,
          // An orphaned instance whose provider left the catalogue: keep it
          // visible so it can be retired, but never offer a Connect action.
          kind: "live_evidence",
          build_state: "coming_soon",
          managed_by: "connector_store",
          description: "Provider is no longer installed.",
        },
      });
    }

    const normalizedQuery = query.trim().toLowerCase();
    return result.filter((row) => {
      const connected = row.connector?.status === "connected" || row.connector?.status === "degraded";
      if (filter === "connected" && !connected) return false;
      if (filter === "not_connected" && connected) return false;
      if (!normalizedQuery) return true;
      return `${row.connector?.display_name ?? ""} ${row.provider.label} ${row.provider.provider_id}`
        .toLowerCase()
        .includes(normalizedQuery);
    });
  }, [catalog.data?.providers, connectors.data?.connectors, filter, query]);

  return (
    <div className="overflow-hidden rounded-xl border border-white/[0.08] bg-[#121821] shadow-2xl">
      <div className="flex min-h-20 flex-wrap items-center justify-between gap-4 border-b border-white/[0.07] px-6 py-4">
        <div>
          <h2 className="text-xl font-semibold text-slate-100">Connectors</h2>
          {/* Not "available to OpenCode": a runtime-sync provider is mirrored on
              a schedule and read by the documentation pipeline, not queried by
              OpenCode during a run. */}
          <p className="mt-1 text-xs text-slate-500">Systems Nexus may read for evidence in documentation runs.</p>
        </div>
        <div className="flex items-center gap-2">
          {searchOpen ? (
            <div className="flex items-center gap-2 rounded-lg border border-white/[0.12] bg-black/20 px-3 py-2">
              <SearchIcon />
              <input
                autoFocus
                aria-label="Search connectors"
                className="w-48 bg-transparent text-sm text-slate-200 outline-none placeholder:text-slate-600"
                placeholder="Search connectors"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
              />
              <button aria-label="Close search" className="text-slate-500 hover:text-slate-200" onClick={() => { setQuery(""); setSearchOpen(false); }}>×</button>
            </div>
          ) : (
            <button aria-label="Search connectors" className="rounded-lg p-2.5 text-slate-400 hover:bg-white/[0.06] hover:text-slate-100" onClick={() => setSearchOpen(true)}>
              <SearchIcon />
            </button>
          )}
          {canManage && (
            <button className="btn-primary px-5" onClick={() => setBrowseOpen(true)}>Browse</button>
          )}
        </div>
      </div>

      <div className="flex gap-2 border-b border-white/[0.07] px-6 pt-4">
        {(["all", "connected", "not_connected"] as Filter[]).map((item) => (
          <button
            key={item}
            className={`rounded-t-lg border px-4 py-2 text-sm transition-colors ${filter === item ? "border-white/[0.14] bg-white/[0.08] text-slate-100" : "border-transparent text-slate-500 hover:text-slate-300"}`}
            onClick={() => setFilter(item)}
          >
            {item === "not_connected" ? "Not connected" : item[0].toUpperCase() + item.slice(1)}
          </button>
        ))}
      </div>

      <div className="grid grid-cols-[minmax(0,1fr)_minmax(10rem,0.32fr)_minmax(9rem,0.25fr)] border-b border-white/[0.07] px-6 py-3 text-xs font-medium uppercase tracking-wider text-slate-500">
        <span>Connector</span><span>Type</span><span>Status</span>
      </div>

      {(connectors.isLoading || catalog.isLoading) && <p className="px-6 py-8 text-sm text-slate-500">Loading connectors…</p>}
      {(connectors.isError || catalog.isError) && (
        <p className="px-6 py-8 text-sm text-red-400">Could not load connectors: {parseApiError(connectors.error ?? catalog.error)}</p>
      )}
      {!connectors.isLoading && !catalog.isLoading && !connectors.isError && !catalog.isError && rows.length === 0 && (
        <div className="px-6 py-10 text-center text-sm text-slate-500">
          No connectors match this view.
        </div>
      )}
      <div>
        {rows.map((row) => (
          <ConnectorRow
            key={row.key}
            row={row}
            canManage={canManage}
            expanded={row.key === expandedRowKey}
            onToggle={() => setExpandedRowKey((current) => (current === row.key ? null : row.key))}
            onConnect={() => setEditing({ provider: row.provider, connector: null })}
            onEdit={() => row.connector && setEditing({ provider: row.provider, connector: row.connector })}
            onDiscover={() => row.connector && setDiscoveryConnector(row.connector)}
          />
        ))}
      </div>

      {browseOpen && (
        <ConnectorBrowserDialog
          providers={catalog.data?.providers ?? []}
          onConnect={(provider) => { setBrowseOpen(false); setEditing({ provider, connector: null }); }}
          onClose={() => setBrowseOpen(false)}
        />
      )}
      {editing && (
        <ConnectorDialogHost editing={editing} onClose={() => setEditing(null)} />
      )}
      {discoveryConnector && (
        <DiscoveryDialog connector={discoveryConnector} onClose={() => setDiscoveryConnector(null)} />
      )}
    </div>
  );
}

function ConnectorRow({ row, canManage, expanded, onToggle, onConnect, onEdit, onDiscover }: {
  row: ConnectorListRow;
  canManage: boolean;
  expanded: boolean;
  onToggle: () => void;
  onConnect: () => void;
  onEdit: () => void;
  onDiscover: () => void;
}) {
  const { connector, provider } = row;
  const test = useTestConnector();
  const update = useUpdateConnector();
  const remove = useDeleteConnector();
  // Connectability comes from the manifest and the dialog registry, not a
  // hardcoded id list, and it is the same predicate the browser dialog uses.
  const supported = canConnect(provider);
  const runtimeManaged = isRuntimeManaged(provider);
  // Only fetched for providers whose credential really lives in the runtime
  // store; every other row would be asking a question with no answer.
  const runtime = useRuntimeSources(runtimeManaged ? provider.provider_id : null, runtimeManaged);
  const runtimeSources = runtime.data?.sources ?? [];

  return (
    <article className="border-b border-white/[0.07] last:border-b-0">
      <div className="grid min-h-[74px] grid-cols-[minmax(0,1fr)_minmax(10rem,0.32fr)_minmax(9rem,0.25fr)] items-center px-6 transition-colors hover:bg-white/[0.025]">
        <button className="flex min-w-0 items-center gap-3 text-left" disabled={!connector && runtimeSources.length === 0} onClick={onToggle}>
          <ProviderIcon providerId={provider.provider_id} />
          <span className="min-w-0">
            <span className="block truncate text-base font-medium text-slate-100">{connector?.display_name ?? provider.label}</span>
            {connector && connector.display_name !== provider.label && <span className="mt-0.5 block truncate text-xs text-slate-500">{provider.label}</span>}
            {/* Which projects are actually covered. A runtime source is bound to
                exactly one project, so a bare "Connected" would imply coverage
                the mirror does not have. */}
            {runtimeManaged && runtimeSources.length > 0 && (
              <span className="mt-0.5 block truncate text-xs text-slate-500">
                {describeRuntimeCoverage(runtimeSources)}
              </span>
            )}
          </span>
        </button>
        {/* The Type column is manifest-driven. The literal "Live API" plus an
            "OpenCode" badge was wrong for every runtime-sync provider: it is
            mirrored on a schedule and OpenCode never touches it. */}
        <div className="flex items-center gap-2 text-sm text-slate-400">
          <span className="truncate" title={describeKind(provider)}>{describeKind(provider)}</span>
        </div>
        <div className="flex items-center justify-start">
          {runtimeManaged ? (
            <RuntimeStatusCell
              query={runtime}
              sources={runtimeSources}
              canManage={canManage}
              supported={supported}
              expanded={expanded}
              onToggle={onToggle}
              onConnect={onConnect}
            />
          ) : connector ? (
            <button
              className={`min-w-28 rounded-lg border px-3 py-2 text-sm ${statusStyle[connector.status]}`}
              onClick={onToggle}
              aria-expanded={expanded}
            >
              {connector.status === "connected" ? "✓ Connected" : displayStatus(connector.status)}
            </button>
          ) : canManage && supported ? (
            <button className="btn-primary min-w-24" onClick={onConnect}>Connect</button>
          ) : (
            <span className="text-sm text-slate-600">Unavailable</span>
          )}
        </div>
      </div>
      {runtimeManaged && expanded && (
        <RuntimeSourcePanel
          provider={provider}
          sources={runtimeSources}
          canManage={canManage}
          onConnect={onConnect}
        />
      )}
      {!runtimeManaged && connector && expanded && (
        <div className="border-t border-white/[0.05] bg-black/10 px-6 py-4">
          <div className="grid grid-cols-2 gap-4 text-sm md:grid-cols-4">
            <div><div className="text-xs text-slate-600">Last check</div><div className="mt-1 text-slate-400">{formatDate(connector.last_checked_at)}</div></div>
            <div><div className="text-xs text-slate-600">Credentials</div><div className="mt-1 text-slate-400">{connector.has_credential ? "Configured" : "Missing"}</div></div>
            <div><div className="text-xs text-slate-600">Scope</div><div className="mt-1 text-slate-400">{connector.ownership_scope}</div></div>
            <div><div className="text-xs text-slate-600">Sources</div><div className="mt-1 text-slate-400">Manage per project</div></div>
          </div>
          {connector.health_code && <p className="mt-3 text-xs text-amber-400 break-words">{connector.health_code.replace(/_/g, " ")}</p>}
          {canManage && (
            <div className="mt-4 flex flex-wrap gap-2">
              <button className="btn-ghost" disabled={test.isPending} onClick={() => test.mutate(connector.id)}>Test connection</button>
              <button className="btn-ghost" onClick={onDiscover}>Discover sources</button>
              <button className="btn-ghost" onClick={onEdit}>Edit</button>
              <button className="btn-ghost" disabled={update.isPending} onClick={() => update.mutate({ connectorId: connector.id, data: { enabled: !connector.enabled } })}>{connector.enabled ? "Disable" : "Enable"}</button>
              <button
                className="ml-auto text-sm text-red-400 hover:text-red-300 disabled:opacity-50"
                disabled={remove.isPending}
                title="Delete connector (the server will block deletion while bindings exist)."
                onClick={() => { if (window.confirm(`Delete ${connector.display_name}?`)) remove.mutate(connector.id); }}
              >
                Delete
              </button>
            </div>
          )}
          {test.isError && <p className="mt-3 text-xs text-red-400">Test failed: {parseApiError(test.error)}</p>}
          {test.data && <p className="mt-3 text-xs text-slate-400">{test.data.message ?? test.data.status}</p>}
        </div>
      )}
    </article>
  );
}

/**
 * Row subtitle for a runtime-managed provider: which projects are covered.
 *
 * A runtime source is bound to exactly ONE project, so "Connected" on its own
 * would imply blanket coverage the mirror does not have. Naming the projects (up
 * to a couple, then a count) keeps the row honest about a partial rollout.
 */
function describeRuntimeCoverage(sources: RuntimeSource[]): string {
  const projects = Array.from(new Set(sources.map((source) => source.project_id)));
  if (projects.length === 0) return "No projects connected";
  if (projects.length <= 2) return `Mirroring ${projects.join(", ")}`;
  return `Mirroring ${projects.length} projects`;
}

/** Newest successful mirror across a provider's sources, or null if none synced. */
function latestSync(sources: RuntimeSource[]): string | null {
  const stamps = sources.map((source) => source.last_synced_at).filter((value): value is string => Boolean(value));
  if (stamps.length === 0) return null;
  return stamps.reduce((latest, current) => (current > latest ? current : latest));
}

/**
 * Status cell for a provider whose credential lives in the runtime store.
 *
 * Reports what is actually true rather than the old flat "Managed in Runtime
 * sources": nothing connected yet offers Connect; some projects connected shows
 * a degraded state when any source is failing, because a green tick over a
 * failing mirror is exactly the lie this row used to tell by omission.
 */
function RuntimeStatusCell({ query, sources, canManage, supported, expanded, onToggle, onConnect }: {
  query: { isLoading: boolean; isError: boolean };
  sources: RuntimeSource[];
  canManage: boolean;
  supported: boolean;
  expanded: boolean;
  onToggle: () => void;
  onConnect: () => void;
}) {
  if (query.isLoading) return <span className="text-sm text-slate-500">Checking…</span>;
  // An unreachable proxy is not the same as "not connected": say so rather than
  // inviting a duplicate registration that will 409.
  if (query.isError) return <span className="text-sm text-amber-400">Status unavailable</span>;
  if (sources.length === 0) {
    return canManage && supported
      ? <button className="btn-primary min-w-24" onClick={onConnect}>Connect</button>
      : <span className="text-sm text-slate-600">Not connected</span>;
  }
  const failing = sources.some((source) => source.status === "failed" || !source.enabled);
  return (
    <button
      className={`min-w-28 rounded-lg border px-3 py-2 text-sm ${failing ? statusStyle.degraded : statusStyle.connected}`}
      onClick={onToggle}
      aria-expanded={expanded}
    >
      {failing ? "Attention" : "✓ Connected"}
    </button>
  );
}

/**
 * Expanded detail for a runtime-managed provider: one line per project.
 *
 * Per-source rather than per-provider because that is the real shape of the
 * data -- one credential, one project, its own sync clock and its own failures.
 */
function RuntimeSourcePanel({ provider, sources, canManage, onConnect }: {
  provider: ConnectorProvider;
  sources: RuntimeSource[];
  canManage: boolean;
  onConnect: () => void;
}) {
  const sync = useSyncRuntimeSource();
  const remove = useDeleteRuntimeSource();
  const advanced = runtimeSourcesUrl();

  return (
    <div className="border-t border-white/[0.05] bg-black/10 px-6 py-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <div className="text-xs text-slate-600">Runtime mirror</div>
          <div className="mt-1 text-sm text-slate-400">
            Last sync: {formatDate(latestSync(sources))}
          </div>
        </div>
        {canManage && (
          <button className="btn-ghost" onClick={onConnect}>Connect another project</button>
        )}
      </div>

      <div className="mt-4 space-y-2">
        {sources.map((source) => (
          <div key={source.id} className="flex flex-wrap items-center gap-3 rounded border border-white/[0.08] px-3 py-2">
            <div className="min-w-0 flex-1">
              <div className="truncate text-sm text-slate-200">{source.project_id}</div>
              <div className="mt-0.5 truncate text-xs text-slate-500">
                {source.label || provider.label} · {source.enabled ? source.status : "disabled"} · every {source.sync_interval_seconds}s
              </div>
              {source.disabled_reason && (
                <div className="mt-0.5 text-xs text-amber-400 break-words">{source.disabled_reason}</div>
              )}
            </div>
            <div className="text-xs text-slate-500">{formatDate(source.last_synced_at)}</div>
            {canManage && (
              <>
                <button
                  className="btn-ghost text-xs"
                  disabled={sync.isPending}
                  onClick={() => sync.mutate(source.id)}
                >
                  Sync now
                </button>
                <button
                  className="text-xs text-red-400 hover:text-red-300 disabled:opacity-50"
                  disabled={remove.isPending}
                  onClick={() => {
                    if (window.confirm(`Disconnect ${provider.label} from ${source.project_id}? Mirrored history is kept; new executions stop arriving.`)) {
                      remove.mutate(source.id);
                    }
                  }}
                >
                  Disconnect
                </button>
              </>
            )}
          </div>
        ))}
      </div>

      {sync.isError && <p className="mt-3 text-xs text-red-400">Sync failed: {parseApiError(sync.error)}</p>}
      {sync.data && (
        <p className="mt-3 text-xs text-slate-400">
          Sync {sync.data.status}: {sync.data.records_ingested} records mirrored.
        </p>
      )}
      {remove.isError && <p className="mt-3 text-xs text-red-400">Disconnect failed: {parseApiError(remove.error)}</p>}

      {/* Secondary, not the primary path: everything above is doable here now.
          The deep link remains for the settings this page deliberately omits
          (auto-disable thresholds, credential rotation, workspace mapping). */}
      {advanced && (
        <p className="mt-4 text-xs text-slate-600">
          Advanced settings:{" "}
          <a className="text-accent-primary/80 hover:text-accent-primary underline" href={advanced} target="_blank" rel="noreferrer">
            Runtime sources
          </a>
        </p>
      )}
    </div>
  );
}

/** Resolves the registered dialog for the provider being connected or edited. */
function ConnectorDialogHost({ editing, onClose }: {
  editing: { provider: ConnectorProvider; connector: ConnectorInstance | null };
  onClose: () => void;
}) {
  const Dialog = connectorDialogRegistry[editing.provider.provider_id];
  // Unreachable while `canConnect` gates every entry point, and deliberately
  // silent rather than a fallback form: rendering *some* dialog is how the
  // original bug looked to the operator.
  if (!Dialog) return null;
  return <Dialog provider={editing.provider} connector={editing.connector} onClose={onClose} />;
}

function ConnectorBrowserDialog({ providers, onConnect, onClose }: { providers: ConnectorProvider[]; onConnect: (provider: ConnectorProvider) => void; onClose: () => void }) {
  return createPortal(
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 p-4" onClick={onClose}>
      <div className="w-full max-w-xl rounded-xl border border-white/[0.14] bg-surface-raised shadow-2xl" onClick={(event) => event.stopPropagation()}>
        <div className="flex items-start justify-between border-b border-white/[0.08] px-6 py-5">
          <div><h3 className="text-lg font-semibold text-slate-100">Browse connectors</h3><p className="mt-1 text-xs text-slate-500">Providers marked &quot;Coming soon&quot; are on the roadmap and cannot be connected yet.</p></div>
          <button className="btn-icon-action" aria-label="Close" onClick={onClose}>×</button>
        </div>
        <div className="divide-y divide-white/[0.07]">
          {providers.map((provider) => {
            // Same predicate as the row. These two disagreeing is what let a
            // Connect action exist with no form behind it.
            const supported = canConnect(provider);
            return (
              <div key={provider.provider_id} className="flex items-center gap-3 px-6 py-4">
                <ProviderIcon providerId={provider.provider_id} />
                <div className="min-w-0 flex-1">
                  <div className="font-medium text-slate-100">{provider.label}</div>
                  <div className="mt-0.5 text-xs text-slate-500">{provider.description || describeKind(provider)}</div>
                  {/* Where the secret will be written. Stated because it is true
                      and useful, not to withhold the action. */}
                  {isRuntimeManaged(provider) && (
                    <div className="mt-0.5 text-xs text-slate-600">Credential stored in Runtime sources</div>
                  )}
                </div>
                <button className="btn-primary" disabled={!supported} onClick={() => onConnect(provider)}>{supported ? "Connect" : provider.build_state === "coming_soon" ? "Coming soon" : "Unavailable"}</button>
              </div>
            );
          })}
          {providers.length === 0 && <p className="px-6 py-8 text-sm text-slate-500">No connector providers are installed.</p>}
        </div>
      </div>
    </div>,
    document.body,
  );
}

/** Initials for any provider id. No per-provider branch: a hardcoded Elastic
 *  case made every other connector look like an afterthought, and a new
 *  provider had to edit this file to stop looking broken. */
function providerInitials(providerId: string): string {
  const parts = providerId.split(/[_\-.]+/).filter(Boolean);
  if (parts.length >= 2) return (parts[0][0] + parts[1][0]).toUpperCase();
  return (parts[0] ?? providerId).slice(0, 2).toUpperCase();
}

function ProviderIcon({ providerId }: { providerId: string }) {
  return (
    <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg border border-white/[0.1] bg-white/[0.04] text-sm font-bold text-slate-400">
      {providerInitials(providerId)}
    </span>
  );
}

function SearchIcon() {
  return <svg aria-hidden="true" viewBox="0 0 24 24" className="h-5 w-5" fill="none" stroke="currentColor" strokeWidth="1.8"><circle cx="11" cy="11" r="7" /><path d="m16.25 16.25 4 4" /></svg>;
}

function ElasticStackDialog({ provider, connector, onClose }: ConnectorDialogProps) {
  const create = useCreateConnector();
  const update = useUpdateConnector();
  const rotate = useRotateConnectorCredential();
  const test = useTestConnector();
  const discover = useDiscoverConnectorSources();
  const [displayName, setDisplayName] = useState(connector?.display_name ?? provider.label);
  const [endpoint, setEndpoint] = useState("");
  const [authType, setAuthType] = useState<"api_key" | "basic">("api_key");
  const [apiKey, setApiKey] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [verifyTls, setVerifyTls] = useState(true);
  const [customCa, setCustomCa] = useState("");
  const [kibanaEndpoint, setKibanaEndpoint] = useState("");
  const [kibanaSpace, setKibanaSpace] = useState("");
  const [reuseCredentials, setReuseCredentials] = useState(true);
  const [kibanaApiKey, setKibanaApiKey] = useState("");
  const [error, setError] = useState<string | null>(null);

  const pending = create.isPending || update.isPending || rotate.isPending || test.isPending || discover.isPending;
  const credential = authType === "api_key" ? apiKey : `${username}:${password}`;

  async function save(andTest: boolean) {
    setError(null);
    if (!displayName.trim() || (!connector && !endpoint.trim())) { setError("Display name and Elasticsearch HTTPS endpoint are required."); return; }
    if (endpoint && !endpoint.startsWith("https://")) { setError("Elasticsearch endpoint must use HTTPS."); return; }
    if (!connector && ((authType === "api_key" && !apiKey) || (authType === "basic" && (!username || !password)))) {
      setError("Enter credentials. They are write-only and will not be shown again."); return;
    }
    if (connector && authType === "basic" && Boolean(username) !== Boolean(password)) {
      setError("Enter both username and password when rotating Basic authentication."); return;
    }
    if (kibanaEndpoint && !reuseCredentials && authType === "api_key" && !connector && !kibanaApiKey) {
      setError("Enter a Kibana API key or choose credential reuse."); return;
    }
    const safeConfig = endpoint ? {
        endpoint: endpoint.trim(),
        auth_type: authType,
        verify_tls: verifyTls,
        custom_ca_pem: customCa || undefined,
        kibana_endpoint: kibanaEndpoint.trim() || undefined,
        kibana_space: kibanaSpace.trim() || undefined,
        kibana_reuse_elasticsearch_credentials: reuseCredentials,
      } : undefined;
    try {
      const saved = connector
        ? await update.mutateAsync({ connectorId: connector.id, data: { display_name: displayName.trim(), ...(safeConfig ? { safe_config: safeConfig } : {}) } })
        // The provider the operator actually clicked, never a literal: the
        // registry decided this form is right for it.
        : await create.mutateAsync({ provider_id: provider.provider_id, display_name: displayName.trim(), safe_config: safeConfig!, credential_type: authType, credential, ...(kibanaEndpoint && !reuseCredentials ? { kibana_credential: kibanaApiKey } : {}) });
      const hasReplacementCredential = authType === "api_key"
        ? Boolean(apiKey || (kibanaEndpoint && !reuseCredentials && kibanaApiKey))
        : Boolean(username || password);
      if (connector && hasReplacementCredential) {
        await rotate.mutateAsync({ connectorId: saved.id, credentials: { credential, credential_type: authType, ...(kibanaEndpoint && !reuseCredentials ? { kibana_credential: kibanaApiKey } : {}), reuse_elasticsearch_credential: reuseCredentials } });
      }
      // Clear all credential state immediately after it leaves the browser form.
      setApiKey(""); setPassword(""); setKibanaApiKey("");
      // A successful connection should be usable immediately. Discover the
      // safe source catalogue as part of Connect/Save so project settings never
      // require a second technical setup step.
      try {
        await discover.mutateAsync(saved.id);
      } catch {
        // Keep the connection even when discovery is temporarily unavailable;
        // the project screen will surface a retryable empty-source state.
      }
      if (andTest) await test.mutateAsync(saved.id);
      onClose();
    } catch (reason) { setError(parseApiError(reason)); }
  }

  return createPortal(
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 p-4" onClick={onClose}>
      <div className="w-full max-w-2xl max-h-[90vh] overflow-y-auto rounded-lg border border-white/[0.14] bg-surface-raised shadow-2xl" onClick={(event) => event.stopPropagation()}>
        <div className="flex items-start justify-between border-b border-white/[0.08] px-6 py-5">
          <div><h3 className="text-lg font-semibold text-slate-100">{connector ? `Edit ${provider.label}` : `Connect ${provider.label}`}</h3><p className="mt-1 text-xs text-slate-500">Credentials are write-only and never returned to this browser.</p></div>
          <button className="btn-icon-action" aria-label="Close" onClick={onClose}>×</button>
        </div>
        <div className="space-y-5 px-6 py-5">
          <Field label="Display name"><input className="input-dark" value={displayName} onChange={(event) => setDisplayName(event.target.value)} /></Field>
          <Field label={connector ? "Elasticsearch HTTPS endpoint (re-enter to change)" : "Elasticsearch HTTPS endpoint"}><input className="input-dark" type="url" placeholder={connector ? "Endpoint is write-only; enter a replacement to change it" : "https://elasticsearch.example.com:9200"} value={endpoint} onChange={(event) => setEndpoint(event.target.value)} /></Field>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <Field label="Authentication"><select className="input-dark" value={authType} onChange={(event) => setAuthType(event.target.value as "api_key" | "basic")}><option value="api_key">API key (recommended)</option><option value="basic">Basic authentication</option></select></Field>
            {authType === "api_key" ? <Field label={connector ? "New API key (leave blank to keep existing)" : "API key"}><input className="input-dark" type="password" autoComplete="new-password" value={apiKey} onChange={(event) => setApiKey(event.target.value)} /></Field> : <div className="grid grid-cols-2 gap-2"><Field label="Username"><input className="input-dark" autoComplete="off" value={username} onChange={(event) => setUsername(event.target.value)} /></Field><Field label={connector ? "New password" : "Password"}><input className="input-dark" type="password" autoComplete="new-password" value={password} onChange={(event) => setPassword(event.target.value)} /></Field></div>}
          </div>
          <label className="flex items-center gap-2 text-sm text-slate-300"><input type="checkbox" checked={verifyTls} onChange={(event) => setVerifyTls(event.target.checked)} /> Verify TLS certificates</label>
          <Field label="Custom CA certificate (optional)"><textarea className="input-dark min-h-20" value={customCa} onChange={(event) => setCustomCa(event.target.value)} placeholder="PEM certificate; never shown after save" /></Field>
          <div className="border-t border-white/[0.08] pt-4 space-y-4"><div><h4 className="text-sm font-medium text-slate-200">Optional Kibana companion</h4><p className="mt-1 text-xs text-slate-500">Kibana discovers spaces and data views; Elasticsearch supplies runtime evidence.</p></div><div className="grid grid-cols-1 sm:grid-cols-2 gap-4"><Field label="Kibana HTTPS endpoint"><input className="input-dark" type="url" placeholder="https://kibana.example.com" value={kibanaEndpoint} onChange={(event) => setKibanaEndpoint(event.target.value)} /></Field><Field label="Kibana space (optional)"><input className="input-dark" value={kibanaSpace} onChange={(event) => setKibanaSpace(event.target.value)} /></Field></div>{kibanaEndpoint && <><label className="flex items-center gap-2 text-sm text-slate-300"><input type="checkbox" checked={reuseCredentials} onChange={(event) => setReuseCredentials(event.target.checked)} /> Reuse Elasticsearch credential</label>{!reuseCredentials && authType === "api_key" && <Field label="Kibana API key"><input className="input-dark" type="password" autoComplete="new-password" value={kibanaApiKey} onChange={(event) => setKibanaApiKey(event.target.value)} /></Field>}</>}</div>
          {error && <p role="alert" className="text-sm text-red-400">{error}</p>}
        </div>
        <div className="flex justify-end gap-2 border-t border-white/[0.08] px-6 py-4"><button className="btn-ghost" disabled={pending} onClick={onClose}>Cancel</button><button className="btn-primary" disabled={pending} onClick={() => save(false)}>{pending ? "Saving…" : "Save"}</button><button className="btn-ghost" disabled={pending} onClick={() => save(true)}>{pending ? "Testing…" : connector ? "Save & test" : "Connect & test"}</button></div>
      </div>
    </div>,
    document.body,
  );
}

/**
 * Connect Talend Management Console, in this page, in one step.
 *
 * Where the credential goes: NOT the connector store. It is posted to
 * `/mcp/runtime-sources`, which proxies to the RAG backend's runtime-sources API
 * on the operator's own session, so it lands in the `runtime_sources` tables
 * that the mirroring scheduler polls every 60 seconds. Writing it into the
 * connector store instead would render as "connected" and never sync.
 *
 * The form is the provider's own `credential_schema()`, fetched rather than
 * hardcoded, plus a project selector. Nothing else: TMC has four fields and this
 * dialog must not invent a fifth.
 */
function TalendTmcDialog({ provider, onClose }: ConnectorDialogProps) {
  const create = useCreateRuntimeSource();
  // Only requested while this dialog is mounted -- it is form metadata.
  const sourceTypes = useRuntimeSourceTypes(true);
  const projects = useProjects();
  const [projectId, setProjectId] = useState("");
  const [label, setLabel] = useState(provider.label);
  const [values, setValues] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [scopes, setScopes] = useState<RuntimeScopeOption[] | null>(null);
  const [chosenScopes, setChosenScopes] = useState<string[]>([]);
  const [testing, setTesting] = useState(false);

  // The runtime provider behind this connector. Matched by label rather than by
  // a hardcoded `tmc`: the server owns the connector-id -> provider-key mapping,
  // and duplicating that key here is precisely the drift this design removes.
  const runtimeType = useMemo(() => {
    const types = sourceTypes.data?.source_types ?? [];
    return types.find((type) => type.provider_label === provider.label) ?? types[0] ?? null;
  }, [sourceTypes.data?.source_types, provider.label]);

  const fields = runtimeType?.credential_schema ?? [];
  // Present only for providers whose estate is partitioned (TMC environments).
  // Everything below keys off this rather than off the connector id.
  const selection = runtimeType?.scope_selection ?? null;
  const projectOptions = projects.data?.projects ?? [];
  const pending = create.isPending;

  /** Only what the operator filled in: an empty optional field is absent. */
  function filledCredentials(): Record<string, string> {
    const credentials: Record<string, string> = {};
    for (const field of fields) {
      const value = values[field.name]?.trim();
      if (value) credentials[field.name] = value;
    }
    return credentials;
  }

  function setField(name: string, value: string) {
    setValues((current) => ({ ...current, [name]: value }));
    // A scope list belongs to the credentials that produced it. Editing one
    // invalidates it, so a typo cannot be saved against the previous token's
    // environments.
    setScopes(null);
    setChosenScopes([]);
  }

  function toggleScope(id: string) {
    setChosenScopes((current) => {
      if (selection && !selection.multiple) return [id];
      return current.includes(id) ? current.filter((item) => item !== id) : [...current, id];
    });
  }

  async function testConnection() {
    setError(null);
    const missing = fields.filter((field) => field.required && !values[field.name]?.trim());
    if (missing.length > 0) {
      setError(`Enter ${missing.map((field) => field.label || field.name).join(", ")}.`);
      return;
    }
    setTesting(true);
    try {
      const result = await api.discoverRuntimeSourceScopes({
        provider_id: provider.provider_id,
        credentials: filledCredentials(),
      });
      setScopes(result.scopes);
      setChosenScopes(result.scopes.filter((s) => s.default_selected).map((s) => s.id));
    } catch (reason) {
      setError(parseApiError(reason));
    } finally {
      setTesting(false);
    }
  }

  async function save() {
    setError(null);
    // A runtime source is bound to ONE project id and that id must match the
    // intodoc project exactly. This is a select, so the only way to reach here
    // without one is to not have chosen.
    if (!projectId) {
      setError("Choose the project this Talend environment belongs to.");
      return;
    }
    const missing = fields.filter((field) => field.required && !values[field.name]?.trim());
    if (missing.length > 0) {
      setError(`Enter ${missing.map((field) => field.label || field.name).join(", ")}.`);
      return;
    }
    if (selection?.required && chosenScopes.length === 0) {
      setError(`Test the connection and choose at least one ${selection.label.toLowerCase()}.`);
      return;
    }
    const credentials: Record<string, string | string[]> = { ...filledCredentials() };
    if (selection) credentials[selection.field] = chosenScopes;
    try {
      await create.mutateAsync({
        // The connector id the operator clicked; the server maps it to the
        // runtime provider key.
        provider_id: provider.provider_id,
        project_id: projectId,
        label: label.trim() || provider.label,
        credentials,
      });
      // Clear credential state the moment it has left the form.
      setValues({});
      onClose();
    } catch (reason) {
      // The RAG backend's own words -- a 409 "That physical runtime source is
      // already registered" is the single most useful thing it can say. It
      // redacts credential-shaped substrings before they leave that service.
      setError(parseApiError(reason));
    }
  }

  return createPortal(
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 p-4" onClick={onClose}>
      <div className="w-full max-w-xl max-h-[90vh] overflow-y-auto rounded-lg border border-white/[0.14] bg-surface-raised shadow-2xl" onClick={(event) => event.stopPropagation()}>
        <div className="flex items-start justify-between border-b border-white/[0.08] px-6 py-5">
          <div>
            <h3 className="text-lg font-semibold text-slate-100">Connect {provider.label}</h3>
            <p className="mt-1 text-xs text-slate-500">
              Credentials are write-only and never returned to this browser. Stored with the runtime scheduler that mirrors executions every minute.
            </p>
          </div>
          <button className="btn-icon-action" aria-label="Close" onClick={onClose}>×</button>
        </div>
        <div className="space-y-5 px-6 py-5">
          <Field label="Project">
            {/* A dropdown of real intodoc projects, never free text. A typed
                project id that does not match an intodoc project files real
                execution history under a name documentation never reads, and
                the run then attaches runtime evidence that is mostly wrong. */}
            <select
              className="input-dark"
              value={projectId}
              onChange={(event) => setProjectId(event.target.value)}
            >
              <option value="">Select a project</option>
              {projectOptions.map((project) => (
                <option key={project.id} value={project.id}>
                  {project.display_name === project.id ? project.id : `${project.display_name} (${project.id})`}
                </option>
              ))}
            </select>
          </Field>
          {projects.isError && (
            <p className="text-xs text-amber-400">Could not load projects: {parseApiError(projects.error)}</p>
          )}
          {!projects.isLoading && !projects.isError && projectOptions.length === 0 && (
            <p className="text-xs text-amber-400">No projects exist yet. Add a project before connecting a runtime source.</p>
          )}

          <Field label="Display name">
            <input className="input-dark" value={label} onChange={(event) => setLabel(event.target.value)} />
          </Field>

          {sourceTypes.isLoading && <p className="text-sm text-slate-500">Loading credential fields…</p>}
          {sourceTypes.isError && (
            <p className="text-sm text-red-400">Could not load the credential form: {parseApiError(sourceTypes.error)}</p>
          )}
          {fields.map((field) => (
            <Field key={field.name} label={field.required ? field.label || field.name : `${field.label || field.name} (optional)`}>
              <input
                className="input-dark"
                // A `secret` field is a password input with no autofill history.
                type={field.type === "secret" ? "password" : field.type === "url" ? "url" : "text"}
                autoComplete={field.type === "secret" ? "new-password" : "off"}
                placeholder={field.type === "secret" ? "Write-only; never shown again" : undefined}
                value={values[field.name] ?? ""}
                onChange={(event) => setField(field.name, event.target.value)}
              />
            </Field>
          ))}

          {selection && (
            <div className="space-y-3 rounded-md border border-white/[0.10] bg-black/20 p-4">
              <div className="flex items-center gap-3">
                <button
                  type="button"
                  className="btn-ghost"
                  disabled={testing || fields.length === 0}
                  onClick={testConnection}
                >
                  {testing ? "Testing…" : "Test connection"}
                </button>
                {scopes !== null && !testing && (
                  <span className="text-xs text-emerald-400">
                    Connected — {scopes.length} {selection.label.toLowerCase()} found
                  </span>
                )}
              </div>
              {selection.description && (
                <p className="text-xs text-slate-500">{selection.description}</p>
              )}
              {scopes !== null &&
                (scopes.length === 0 ? (
                  <p className="text-sm text-slate-400">
                    The connection succeeded but returned no {selection.label.toLowerCase()}.
                  </p>
                ) : (
                  <ul className="space-y-1">
                    {scopes.map((scope) => (
                      <li key={scope.id}>
                        <label className="flex cursor-pointer items-center gap-2 rounded px-1 py-1 text-sm text-slate-200 hover:bg-white/[0.04]">
                          <input
                            type={selection.multiple ? "checkbox" : "radio"}
                            checked={chosenScopes.includes(scope.id)}
                            onChange={() => toggleScope(scope.id)}
                          />
                          <span className="font-medium">{scope.label}</span>
                          {scope.detail && (
                            <span className="text-xs text-slate-500">{scope.detail}</span>
                          )}
                        </label>
                      </li>
                    ))}
                  </ul>
                ))}
            </div>
          )}

          {error && <p role="alert" className="text-sm text-red-400">{error}</p>}
        </div>
        <div className="flex justify-end gap-2 border-t border-white/[0.08] px-6 py-4">
          <button className="btn-ghost" disabled={pending} onClick={onClose}>Cancel</button>
          {/* One button: the server validates the credential against Talend and
              resolves the tenant BEFORE it persists anything, so a save that
              returns 201 has already passed a real connection test. */}
          <button
            className="btn-primary"
            // A required scope must be chosen first, otherwise the save is a
            // whole-tenant poll -- exactly what the selection exists to prevent.
            disabled={
              pending ||
              fields.length === 0 ||
              (Boolean(selection?.required) && chosenScopes.length === 0)
            }
            onClick={save}
          >
            {pending ? "Connecting…" : selection ? "Connect" : "Connect & test"}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}

function DiscoveryDialog({ connector, onClose }: { connector: ConnectorInstance; onClose: () => void }) {
  const sources = useConnectorSources(connector.id);
  const discover = useDiscoverConnectorSources();
  const [loadKibana, setLoadKibana] = useState(false);
  const [space, setSpace] = useState<string | null>(null);
  const [dataViewId, setDataViewId] = useState("");
  const spaces = useKibanaSpaces(connector.id, loadKibana);
  const selectedSpace = space ?? spaces.data?.spaces[0]?.id ?? null;
  const dataViews = useKibanaDataViews(connector.id, selectedSpace, loadKibana && selectedSpace !== null);
  return createPortal(
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 p-4" onClick={onClose}>
      <div className="w-full max-w-xl rounded-lg border border-white/[0.14] bg-surface-raised shadow-2xl" onClick={(event) => event.stopPropagation()}>
        <div className="flex items-start justify-between border-b border-white/[0.08] px-6 py-5"><div><h3 className="text-lg font-semibold text-slate-100">Discover sources</h3><p className="mt-1 text-xs text-slate-500">{connector.display_name}</p></div><button className="btn-icon-action" aria-label="Close" onClick={onClose}>×</button></div>
        <div className="max-h-[55vh] overflow-y-auto px-6 py-5 space-y-3"><p className="text-sm text-slate-400">Discovery returns visible non-hidden sources. Approve individual sources in Project Settings before a run can use them.</p>{sources.isLoading && <p className="text-sm text-slate-500">Loading sources…</p>}{sources.isError && <p className="text-sm text-red-400">Could not load sources.</p>}{(sources.data?.sources ?? []).map((source) => <div key={source.id} className="rounded border border-white/[0.08] px-3 py-2"><div className="text-sm text-slate-200">{source.display_label}</div><div className="mt-1 text-xs font-mono text-slate-500">{source.source_type}</div></div>)}<div className="border-t border-white/[0.08] pt-4"><div className="flex items-center justify-between gap-3"><div><div className="text-sm font-medium text-slate-200">Optional Kibana data view</div><p className="mt-1 text-xs text-slate-500">Discover a configured Kibana space and save its selected data view as a source suggestion.</p></div><button className="btn-ghost text-xs" onClick={() => setLoadKibana(true)}>Load Kibana</button></div>{loadKibana && <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2">{spaces.isError ? <p className="text-xs text-amber-400 sm:col-span-2">Kibana is unavailable; Elasticsearch discovery remains available.</p> : <><label className="block"><span className="mb-1 block text-xs text-slate-500">Space</span><select className="input-dark w-full" value={selectedSpace ?? ""} onChange={(event) => setSpace(event.target.value || null)}><option value="">Select a space</option>{(spaces.data?.spaces ?? []).map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label><label className="block"><span className="mb-1 block text-xs text-slate-500">Data view</span><select className="input-dark w-full" value={dataViewId} onChange={(event) => setDataViewId(event.target.value)} disabled={!selectedSpace}><option value="">Select a data view</option>{(dataViews.data?.data_views ?? []).map((item) => <option key={item.id} value={item.id}>{item.title}</option>)}</select></label></>}</div>}</div></div>
        <div className="flex justify-end gap-2 border-t border-white/[0.08] px-6 py-4"><button className="btn-ghost" onClick={onClose}>Close</button><button className="btn-primary" disabled={discover.isPending} onClick={() => discover.mutate(dataViewId && selectedSpace ? { connectorId: connector.id, data: { include_kibana: true, kibana_space: selectedSpace, data_view_id: dataViewId } } : connector.id)}>{discover.isPending ? "Discovering…" : dataViewId ? "Save data view" : "Discover sources"}</button></div>
      </div>
    </div>, document.body,
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return <label className="block"><span className="mb-1 block text-xs font-mono uppercase tracking-wider text-slate-600">{label}</span>{children}</label>;
}
