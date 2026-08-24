import { useEffect, useState } from "react";
import { parseApiError } from "../../lib/errors";
import {
  useDeleteProjectConnectorBinding,
  useConnectorCatalog,
  useProjectAvailableConnectors,
  useProjectConnectorBindings,
  usePutProjectConnectorBinding,
} from "../../hooks/useConnectors";
import type { ConnectorCapability, ConnectorInstance, ConnectorSource, ProjectConnectorBinding } from "../../types";

interface Props {
  projectId: string;
}

export default function ProjectConnectorBindings({ projectId }: Props) {
  // This endpoint is deliberately project-scoped: a project owner can bind
  // approved sources without needing workspace-wide connector visibility.
  const availableConnectors = useProjectAvailableConnectors(projectId);
  const catalog = useConnectorCatalog();
  const bindings = useProjectConnectorBindings(projectId);
  const putBinding = usePutProjectConnectorBinding();
  const deleteBinding = useDeleteProjectConnectorBinding();
  const available = availableConnectors.data?.connectors ?? [];

  return (
    <section aria-labelledby="project-connectors-heading" className="space-y-3">
      <div>
        <h4 id="project-connectors-heading" className="text-sm font-medium text-slate-200">Runtime evidence sources</h4>
        <p className="mt-1 text-xs text-slate-500">Choose which workspace connector sources OpenCode may use for this project. Credentials remain hidden and the legacy pipeline is unaffected.</p>
      </div>
      {bindings.isLoading || availableConnectors.isLoading ? <p className="text-sm text-slate-500">Loading connector bindings…</p> : null}
      {bindings.isError ? <p className="text-sm text-red-400">Could not load bindings: {parseApiError(bindings.error)}</p> : null}
      {availableConnectors.isError ? <p className="text-sm text-red-400">Could not load available connectors: {parseApiError(availableConnectors.error)}</p> : null}
      {!bindings.isLoading && !availableConnectors.isLoading && available.length === 0 && <p className="rounded border border-white/[0.07] p-3 text-sm text-slate-500">No approved workspace connectors are available for this project.</p>}
      <div className="space-y-3">
        {available.map(({ connector, sources }) => (
          <BindingEditor
            key={connector.id}
            projectId={projectId}
            connector={connector}
            sources={sources}
            capabilities={catalog.data?.providers.find((provider) => provider.provider_id === connector.provider_id)?.capabilities ?? []}
            binding={(bindings.data?.bindings ?? []).find((item) => item.connector_instance_id === connector.id) ?? null}
            saving={putBinding.isPending || deleteBinding.isPending}
            onSave={(data) => putBinding.mutate({ projectId, connectorId: connector.id, ...data })}
            onRemove={() => deleteBinding.mutate({ projectId, connectorId: connector.id })}
          />
        ))}
      </div>
    </section>
  );
}

function BindingEditor({ projectId: _projectId, connector, sources, capabilities, binding, saving, onSave, onRemove }: {
  projectId: string;
  connector: ConnectorInstance;
  sources: ConnectorSource[];
  capabilities: ConnectorCapability[];
  binding: ProjectConnectorBinding | null;
  saving: boolean;
  onSave: (data: { source_ids: string[]; capability_ids: string[]; budgets?: Record<string, number>; max_time_window_days?: number | null; enabled: boolean }) => void;
  onRemove: () => void;
}) {
  const providerCapabilities = capabilities.filter((capability) => capability.read_only && capability.gather_eligible);
  const capabilityIds = Array.from(new Set([...(binding?.capability_ids ?? []), ...providerCapabilities.map((capability) => capability.id)]));
  const capabilityById = new Map(providerCapabilities.map((capability) => [capability.id, capability]));
  const enabledSources = sources.filter((source) => source.enabled);
  const [enabled, setEnabled] = useState(binding?.enabled ?? false);
  const [sourceIds, setSourceIds] = useState<string[]>(binding?.source_ids ?? []);
  const [selectedCapabilities, setSelectedCapabilities] = useState<string[]>(binding?.capability_ids ?? capabilityIds);
  const [maxWindowDays, setMaxWindowDays] = useState<string>(binding?.max_time_window_days?.toString() ?? "");
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [autoConfiguring, setAutoConfiguring] = useState(false);
  const [autoConfigured, setAutoConfigured] = useState(false);

  useEffect(() => {
    setEnabled(binding?.enabled ?? false);
    setSourceIds(binding?.source_ids ?? []);
    setSelectedCapabilities(binding?.capability_ids ?? capabilityIds);
    setMaxWindowDays(binding?.max_time_window_days?.toString() ?? "");
    if (binding) setAutoConfigured(true);
  }, [binding?.version, capabilityIds.join("|")]);

  // A connected workspace connector should be immediately usable by the
  // project. Apply the safe default once: all discovered, enabled sources and
  // read-only gather capabilities. Users can narrow this later in Advanced.
  useEffect(() => {
    if (
      autoConfigured ||
      autoConfiguring ||
      binding ||
      (connector.status !== "connected" && connector.status !== "degraded") ||
      enabledSources.length === 0 ||
      providerCapabilities.length === 0
    ) return;
    setAutoConfiguring(true);
    setEnabled(true);
    setSourceIds(enabledSources.map((source) => source.id));
    setSelectedCapabilities(providerCapabilities.map((capability) => capability.id));
    onSave({
      enabled: true,
      source_ids: enabledSources.map((source) => source.id),
      capability_ids: providerCapabilities.map((capability) => capability.id),
      max_time_window_days: null,
    });
    setAutoConfigured(true);
  }, [autoConfigured, autoConfiguring, binding, connector.status, enabledSources.map((source) => source.id).join("|"), providerCapabilities.map((capability) => capability.id).join("|")]);

  function toggle(list: string[], value: string, setter: (next: string[]) => void) {
    setter(list.includes(value) ? list.filter((item) => item !== value) : [...list, value]);
  }

  if (autoConfiguring && !binding && saving) {
    return (
      <div className="rounded border border-accent-primary/20 bg-accent-primary/[0.04] p-4">
        <p className="text-sm font-medium text-slate-200">Preparing runtime evidence</p>
        <p className="mt-1 text-xs text-slate-500">Nexus is selecting the discovered sources automatically. You can refine this later.</p>
      </div>
    );
  }

  return (
    <div className="rounded border border-white/[0.08] bg-black/10 p-4 space-y-3">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h5 className="text-sm font-medium text-slate-200">{connector.display_name}</h5>
          <p className="mt-1 text-xs text-slate-500">
            {binding?.enabled
              ? `Connected automatically · ${sourceIds.length} source${sourceIds.length === 1 ? "" : "s"} available to documentation runs`
              : "Not configured for this project"}
          </p>
        </div>
        <span className={`rounded-full border px-2 py-1 text-xs ${binding?.enabled ? "border-[#a0e06f]/40 text-[#a0e06f]" : "border-slate-500/40 text-slate-500"}`}>
          {binding?.enabled ? "Ready" : "Not enabled"}
        </span>
      </div>
      <button type="button" className="text-xs text-slate-400 hover:text-slate-200" onClick={() => setAdvancedOpen((open) => !open)}>
        {advancedOpen ? "Hide advanced settings" : "Advanced settings"}
      </button>
      {advancedOpen && <>
      <div>
        <div className="mb-1.5 flex items-center justify-between gap-3"><p className="text-xs font-mono uppercase tracking-wider text-slate-600">Sources OpenCode may read</p>{enabledSources.length > 0 && <span className="flex gap-2 text-xs"><button type="button" className="text-accent-primary hover:text-accent-primary/80" onClick={() => setSourceIds(enabledSources.map((source) => source.id))}>Select all</button><button type="button" className="text-slate-500 hover:text-slate-300" onClick={() => setSourceIds([])}>Clear</button></span>}</div>
        {sources.length === 0 ? <p className="text-xs text-amber-400">No sources have been discovered yet. Open Connectors, expand this connection, and run Discover sources.</p> : <div className="grid max-h-56 grid-cols-1 gap-2 overflow-y-auto pr-1 sm:grid-cols-2">{enabledSources.map((source) => <label key={source.id} className="flex items-center gap-2 rounded border border-white/[0.06] px-2.5 py-2 text-sm text-slate-300"><input type="checkbox" checked={sourceIds.includes(source.id)} onChange={() => toggle(sourceIds, source.id, setSourceIds)} /> <span className="min-w-0 truncate" title={source.display_label}>{source.display_label}</span></label>)}</div>}
      </div>
      <div>
        <p className="mb-1.5 text-xs font-mono uppercase tracking-wider text-slate-600">Allowed capabilities</p>
        <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">{capabilityIds.length ? capabilityIds.map((capabilityId) => { const capability = capabilityById.get(capabilityId); return <label key={capabilityId} className="flex items-start gap-2 rounded border border-white/[0.06] px-2.5 py-2 text-xs text-slate-400" title={capability?.description ?? capabilityId}><input className="mt-0.5" type="checkbox" checked={selectedCapabilities.includes(capabilityId)} onChange={() => toggle(selectedCapabilities, capabilityId, setSelectedCapabilities)} /><span><span className="block text-slate-300">{capability?.label ?? capabilityId}</span>{capability?.description && <span className="mt-0.5 block text-slate-600">{capability.description}</span>}</span></label>; }) : <span className="text-xs text-slate-500">No read-only gathering capabilities are available for this provider.</span>}</div>
      </div>
      <label className="block max-w-56"><span className="mb-1 block text-xs font-mono uppercase tracking-wider text-slate-600">Maximum time window (days)</span><input className="input-dark" type="number" min="1" max="30" placeholder="Connector default" value={maxWindowDays} onChange={(event) => setMaxWindowDays(event.target.value)} /></label>
      <div className="flex items-center gap-2"><button className="btn-ghost" disabled={saving || (enabled && sourceIds.length === 0)} onClick={() => onSave({ enabled, source_ids: sourceIds, capability_ids: selectedCapabilities, max_time_window_days: maxWindowDays ? Number(maxWindowDays) : null })}>{saving ? "Saving…" : "Save binding"}</button>{binding && <button className="text-sm text-red-400 hover:text-red-300" disabled={saving} onClick={onRemove}>Remove binding</button>}{enabled && sourceIds.length === 0 && <span className="text-xs text-amber-400">Select at least one source.</span>}</div>
      </>}
    </div>
  );
}
