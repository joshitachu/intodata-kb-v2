import { useMemo, useState } from "react";
import { createPortal } from "react-dom";
import { parseApiError } from "../../lib/errors";
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
import type { ConnectorInstance, ConnectorProvider, ConnectorStatus } from "../../types";

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
  const [editing, setEditing] = useState<ConnectorInstance | null | undefined>(undefined);
  const [discoveryConnector, setDiscoveryConnector] = useState<ConnectorInstance | null>(null);
  const [expandedConnectorId, setExpandedConnectorId] = useState<string | null>(null);

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
          <p className="mt-1 text-xs text-slate-500">Live evidence available to OpenCode documentation runs.</p>
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
            expanded={row.connector?.id === expandedConnectorId}
            onToggle={() => row.connector && setExpandedConnectorId((current) => current === row.connector!.id ? null : row.connector!.id)}
            onConnect={() => setEditing(null)}
            onEdit={() => row.connector && setEditing(row.connector)}
            onDiscover={() => row.connector && setDiscoveryConnector(row.connector)}
          />
        ))}
      </div>

      {browseOpen && (
        <ConnectorBrowserDialog
          providers={catalog.data?.providers ?? []}
          onConnect={() => { setBrowseOpen(false); setEditing(null); }}
          onClose={() => setBrowseOpen(false)}
        />
      )}
      {editing !== undefined && (
        <ElasticStackDialog
          connector={editing}
          onClose={() => setEditing(undefined)}
        />
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
  // Connectability comes from the manifest, not a hardcoded id list: a new
  // provider becomes connectable here the moment the catalogue says so.
  const supported = provider.build_state === "available";

  return (
    <article className="border-b border-white/[0.07] last:border-b-0">
      <div className="grid min-h-[74px] grid-cols-[minmax(0,1fr)_minmax(10rem,0.32fr)_minmax(9rem,0.25fr)] items-center px-6 transition-colors hover:bg-white/[0.025]">
        <button className="flex min-w-0 items-center gap-3 text-left" disabled={!connector} onClick={onToggle}>
          <ProviderIcon providerId={provider.provider_id} />
          <span className="min-w-0">
            <span className="block truncate text-base font-medium text-slate-100">{connector?.display_name ?? provider.label}</span>
            {connector && connector.display_name !== provider.label && <span className="mt-0.5 block truncate text-xs text-slate-500">{provider.label}</span>}
          </span>
        </button>
        <div className="flex items-center gap-2 text-sm text-slate-400">
          <span>Live API</span>
          <span className="rounded bg-white/[0.06] px-2 py-0.5 text-xs text-slate-500">OpenCode</span>
        </div>
        <div className="flex items-center justify-start">
          {connector ? (
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
      {connector && expanded && (
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

/** Type column copy. A runtime-sync provider mirrors on a schedule; a
 *  live-evidence provider is queried during a run. */
function describeKind(provider: ConnectorProvider): string {
  return provider.kind === "runtime_sync"
    ? "Runtime sync - mirrored on a schedule"
    : `Live evidence - ${provider.capabilities.length} governed read capabilities`;
}

function ConnectorBrowserDialog({ providers, onConnect, onClose }: { providers: ConnectorProvider[]; onConnect: () => void; onClose: () => void }) {
  return createPortal(
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 p-4" onClick={onClose}>
      <div className="w-full max-w-xl rounded-xl border border-white/[0.14] bg-surface-raised shadow-2xl" onClick={(event) => event.stopPropagation()}>
        <div className="flex items-start justify-between border-b border-white/[0.08] px-6 py-5">
          <div><h3 className="text-lg font-semibold text-slate-100">Browse connectors</h3><p className="mt-1 text-xs text-slate-500">Providers marked &quot;Coming soon&quot; are on the roadmap and cannot be connected yet.</p></div>
          <button className="btn-icon-action" aria-label="Close" onClick={onClose}>×</button>
        </div>
        <div className="divide-y divide-white/[0.07]">
          {providers.map((provider) => {
            const supported = provider.build_state === "available";
            return (
              <div key={provider.provider_id} className="flex items-center gap-3 px-6 py-4">
                <ProviderIcon providerId={provider.provider_id} />
                <div className="min-w-0 flex-1"><div className="font-medium text-slate-100">{provider.label}</div><div className="mt-0.5 text-xs text-slate-500">{provider.description || describeKind(provider)}</div></div>
                <button className="btn-primary" disabled={!supported || !provider.enabled} onClick={onConnect}>{supported && provider.enabled ? "Connect" : provider.build_state === "coming_soon" ? "Coming soon" : "Unavailable"}</button>
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

function ProviderIcon({ providerId }: { providerId: string }) {
  const elastic = providerId === "elasticsearch" || providerId === "elastic_stack";
  return (
    <span className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-lg border text-sm font-bold ${elastic ? "border-amber-400/30 bg-amber-400/10 text-amber-400" : "border-white/[0.1] bg-white/[0.04] text-slate-400"}`}>
      {elastic ? "ES" : providerId.slice(0, 2).toUpperCase()}
    </span>
  );
}

function SearchIcon() {
  return <svg aria-hidden="true" viewBox="0 0 24 24" className="h-5 w-5" fill="none" stroke="currentColor" strokeWidth="1.8"><circle cx="11" cy="11" r="7" /><path d="m16.25 16.25 4 4" /></svg>;
}

function ElasticStackDialog({ connector, onClose }: { connector: ConnectorInstance | null; onClose: () => void }) {
  const create = useCreateConnector();
  const update = useUpdateConnector();
  const rotate = useRotateConnectorCredential();
  const test = useTestConnector();
  const discover = useDiscoverConnectorSources();
  const [displayName, setDisplayName] = useState(connector?.display_name ?? "Elastic Stack");
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
        : await create.mutateAsync({ provider_id: "elasticsearch", display_name: displayName.trim(), safe_config: safeConfig!, credential_type: authType, credential, ...(kibanaEndpoint && !reuseCredentials ? { kibana_credential: kibanaApiKey } : {}) });
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
          <div><h3 className="text-lg font-semibold text-slate-100">{connector ? "Edit Elastic Stack" : "Connect Elastic Stack"}</h3><p className="mt-1 text-xs text-slate-500">Credentials are write-only and never returned to this browser.</p></div>
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
