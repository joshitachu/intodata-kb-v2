"use client";

import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, ChevronDown, Loader2, PlugZap, Search } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

type ConnectorStatus = "connected" | "degraded" | "not_connected" | "disabled" | "unknown";

interface ConnectorInstance {
  id: string;
  provider_id: string;
  display_name: string;
  status: ConnectorStatus;
  enabled: boolean;
}

interface ConnectorSource {
  id: string;
  display_label: string;
  source_type: string;
  enabled: boolean;
}

interface AvailableConnector {
  connector: ConnectorInstance;
  sources: ConnectorSource[];
}

interface ConnectorBinding {
  connector_instance_id: string;
  source_ids: string[];
  capability_ids: string[];
  max_time_window_days: number | null;
  enabled: boolean;
  version: number;
}

interface ConnectorProjectData {
  project_id: string;
  // False for users who may read this project but not configure it. The
  // connector catalogue is withheld in that case, so the panel renders a
  // summary instead of an editor.
  can_manage: boolean;
  connectors: AvailableConnector[];
  bindings: ConnectorBinding[];
}

const DEFAULT_CAPABILITIES = [
  "evidence.sources.describe",
  "runtime.events.aggregate",
  "runtime.events.search",
  "runtime.events.get_item",
];

async function errorMessage(response: Response): Promise<string> {
  const data = await response.json().catch(() => null);
  const detail = data?.detail;
  if (typeof detail === "string") return detail;
  if (typeof detail?.message === "string") return detail.message;
  if (typeof data?.error === "string") return data.error;
  return `Connector request failed (${response.status})`;
}

async function fetchProjectConnectors(projectId: string): Promise<ConnectorProjectData> {
  const response = await fetch(
    `/api/source/connectors/projects/${encodeURIComponent(projectId)}`,
    { credentials: "include", cache: "no-store" },
  );
  if (!response.ok) throw new Error(await errorMessage(response));
  return response.json();
}

export function ProjectConnectorBindings({ projectId }: { projectId: string }) {
  const queryClient = useQueryClient();
  const queryKey = ["project-connectors", projectId] as const;
  const query = useQuery({
    queryKey,
    queryFn: () => fetchProjectConnectors(projectId),
    staleTime: 15_000,
  });
  const mutation = useMutation({
    mutationFn: async ({ connectorId, body }: { connectorId: string; body: Record<string, unknown> }) => {
      const response = await fetch(
        `/api/source/connectors/projects/${encodeURIComponent(projectId)}/${encodeURIComponent(connectorId)}`,
        {
          method: "PUT",
          credentials: "include",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        },
      );
      if (!response.ok) throw new Error(await errorMessage(response));
      return response.json();
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey });
      // Keeps the "used by N projects" rollup on the connection card honest.
      queryClient.invalidateQueries({ queryKey: ["connector-project-usage"] });
    },
  });

  if (query.isLoading) {
    return (
      <div className="flex items-center gap-2 border-t border-border pt-4 text-sm text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin" /> Loading live evidence connectors…
      </div>
    );
  }

  if (query.isError) {
    return (
      <div className="border-t border-border pt-4">
        <p className="text-sm font-medium">Live evidence connectors</p>
        <p className="mt-1 text-xs text-muted-foreground">
          {query.error instanceof Error ? query.error.message : "Connectors could not be loaded."}
        </p>
      </div>
    );
  }

  const connectors = query.data?.connectors ?? [];
  const bindings = query.data?.bindings ?? [];
  const enabledBindings = bindings.filter((binding) => binding.enabled);
  const canManage = query.data?.can_manage ?? false;

  return (
    <section className="border-t border-border pt-4" aria-label="Live evidence connectors">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h4 className="text-sm font-semibold">Live evidence connectors</h4>
          <p className="mt-1 text-xs text-muted-foreground">
            Runtime data this project may read during documentation runs.
          </p>
        </div>
        <span className="shrink-0 text-xs text-muted-foreground">
          {enabledBindings.length} enabled
        </span>
      </div>

      {!canManage ? (
        <p className="mt-3 rounded-md bg-muted/40 px-3 py-2 text-xs text-muted-foreground">
          {enabledBindings.length === 0
            ? "No live connection is enabled for this project."
            : `${enabledBindings.length} connection${enabledBindings.length === 1 ? "" : "s"} · ` +
              `${enabledBindings.reduce((sum, binding) => sum + binding.source_ids.length, 0)} sources enabled. ` +
              "Only a project owner can change this."}
        </p>
      ) : connectors.length === 0 ? (
        <p className="mt-3 rounded-md bg-muted/40 px-3 py-2 text-xs text-muted-foreground">
          No workspace connectors are available yet. Connect and discover one in the Connectors dashboard first.
        </p>
      ) : (
        <div className="mt-3 divide-y divide-border border-y border-border">
          {connectors.map(({ connector, sources }) => (
            <ConnectorBindingRow
              key={connector.id}
              connector={connector}
              sources={sources}
              binding={
                bindings.find((binding) => binding.connector_instance_id === connector.id) ?? null
              }
              saving={mutation.isPending && mutation.variables?.connectorId === connector.id}
              onSave={(body) => mutation.mutate({ connectorId: connector.id, body })}
            />
          ))}
        </div>
      )}
      {mutation.isError && (
        <p className="mt-2 text-xs text-destructive">
          {mutation.error instanceof Error ? mutation.error.message : "Connector binding could not be saved."}
        </p>
      )}
    </section>
  );
}

function ConnectorBindingRow({
  connector,
  sources,
  binding,
  saving,
  onSave,
}: {
  connector: ConnectorInstance;
  sources: ConnectorSource[];
  binding: ConnectorBinding | null;
  saving: boolean;
  onSave: (body: Record<string, unknown>) => void;
}) {
  const availableSources = useMemo(() => sources.filter((source) => source.enabled), [sources]);
  const [enabled, setEnabled] = useState(binding?.enabled ?? false);
  const [sourceIds, setSourceIds] = useState<string[]>(binding?.source_ids ?? []);
  // A real Elastic cluster returns 60+ indices. Rendering them all inline buries
  // the rest of the source row, so the picker stays closed until asked for.
  const [pickerOpen, setPickerOpen] = useState(false);
  const [filter, setFilter] = useState("");

  useEffect(() => {
    setEnabled(binding?.enabled ?? false);
    setSourceIds(binding?.source_ids ?? []);
  }, [binding?.enabled, binding?.version]);

  const statusLabel = connector.status === "not_connected"
    ? "Not connected"
    : connector.status.charAt(0).toUpperCase() + connector.status.slice(1);
  const canEnable = connector.enabled && connector.status === "connected" && availableSources.length > 0;

  const query = filter.trim().toLowerCase();
  const visibleSources = query
    ? availableSources.filter((source) => source.display_label.toLowerCase().includes(query))
    : availableSources;

  const savedSourceIds = binding?.source_ids ?? [];
  const dirty =
    enabled !== (binding?.enabled ?? false) ||
    sourceIds.length !== savedSourceIds.length ||
    sourceIds.some((id) => !savedSourceIds.includes(id));

  function toggleSource(sourceId: string) {
    setSourceIds((current) =>
      current.includes(sourceId)
        ? current.filter((id) => id !== sourceId)
        : [...current, sourceId],
    );
  }

  return (
    <div className="py-3">
      <div className="flex flex-wrap items-center gap-3">
        <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md border border-amber-300/50 bg-amber-50 text-xs font-bold text-amber-700">
          {connector.provider_id.startsWith("elastic") ? "ES" : <PlugZap className="h-4 w-4" />}
        </span>
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-medium">{connector.display_name}</p>
          <p className="text-xs text-muted-foreground">Live API · OpenCode · {statusLabel}</p>
        </div>
        <label className="flex items-center gap-2 text-xs font-medium">
          <input
            type="checkbox"
            checked={enabled}
            disabled={!canEnable}
            onChange={(event) => setEnabled(event.target.checked)}
          />
          Use for this project
        </label>
      </div>

      {availableSources.length === 0 ? (
        <p className="mt-2 pl-12 text-xs text-muted-foreground">
          Discover approved sources in the Connectors dashboard before enabling this binding.
        </p>
      ) : (
        <div className="mt-2 pl-12">
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
            <span>
              <span className="font-medium text-foreground">{sourceIds.length}</span> of{" "}
              {availableSources.length} sources selected
            </span>
            <button
              type="button"
              onClick={() => setPickerOpen((open) => !open)}
              className="inline-flex items-center gap-1 font-medium text-foreground hover:text-primary"
              aria-expanded={pickerOpen}
            >
              {pickerOpen ? "Done choosing" : "Choose sources"}
              <ChevronDown className={cn("h-3.5 w-3.5 transition-transform", pickerOpen && "rotate-180")} />
            </button>
          </div>

          {pickerOpen && (
            <div className="mt-2 rounded-md border border-border">
              <div className="flex items-center gap-2 border-b border-border px-2 py-1.5">
                <Search className="h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-hidden />
                <input
                  type="search"
                  value={filter}
                  onChange={(event) => setFilter(event.target.value)}
                  placeholder={`Filter ${availableSources.length} sources`}
                  aria-label="Filter sources"
                  className="min-w-0 flex-1 bg-transparent text-xs outline-none placeholder:text-muted-foreground"
                />
                <button
                  type="button"
                  onClick={() => setSourceIds(visibleSources.map((source) => source.id))}
                  className="shrink-0 text-xs text-muted-foreground hover:text-foreground"
                >
                  Select all
                </button>
                <button
                  type="button"
                  onClick={() => setSourceIds([])}
                  className="shrink-0 text-xs text-muted-foreground hover:text-foreground"
                >
                  Clear
                </button>
              </div>
              <div className="max-h-52 overflow-y-auto p-2">
                {visibleSources.length === 0 ? (
                  <p className="px-1 py-2 text-xs text-muted-foreground">No source matches that filter.</p>
                ) : (
                  <div className="flex flex-wrap gap-1.5">
                    {visibleSources.map((source) => {
                      const selected = sourceIds.includes(source.id);
                      return (
                        <button
                          key={source.id}
                          type="button"
                          onClick={() => toggleSource(source.id)}
                          className={cn(
                            "inline-flex items-center gap-1.5 rounded-md border px-2 py-1 text-xs transition-colors",
                            selected
                              ? "border-primary/40 bg-primary/5 text-foreground"
                              : "border-border text-muted-foreground hover:text-foreground",
                          )}
                        >
                          {selected && <Check className="h-3 w-3 shrink-0" />}
                          {source.display_label}
                        </button>
                      );
                    })}
                  </div>
                )}
              </div>
            </div>
          )}
        </div>
      )}

      <div className={cn("mt-3 flex items-center justify-end gap-3", !dirty && !saving && "hidden")}>
        {enabled && sourceIds.length === 0 && (
          <span className="text-xs text-muted-foreground">Select at least one source.</span>
        )}
        <Button
          size="sm"
          variant="secondary"
          isLoading={saving}
          disabled={enabled && sourceIds.length === 0}
          onClick={() =>
            onSave({
              enabled,
              source_ids: sourceIds,
              capability_ids: binding?.capability_ids.length
                ? binding.capability_ids
                : DEFAULT_CAPABILITIES,
              budgets: {},
              max_time_window_days: binding?.max_time_window_days ?? null,
            })
          }
        >
          Save connector access
        </Button>
      </div>
    </div>
  );
}
