"use client";

import { useCallback, useEffect } from "react";
import { useJobDetail } from "@/hooks/useJobDetail";
import type { ComponentConnectorSummary } from "@/lib/jobGraphTransform";

interface ComponentInspectorProps {
  projectId: string;
  jobName: string;
  componentName: string;
  // Wiring derived from the job's connections array (level-2 data already
  // in memory) - omitted only in the rare 404-fallback path where the
  // level-3 canvas opens this panel without a loaded job graph.
  connectors?: ComponentConnectorSummary[];
  onClose: () => void;
}

// Level-3 fallback panel (D4): any component without has_subgraph - i.e.
// everything except tMap today - opens this instead of a synthesized
// graph. Shares its data source with JobDetailPanel (useJobDetail) rather
// than re-fetching: the job detail endpoint's scan_data already carries
// every component's raw params/code/connection fields.
export function ComponentInspector({
  projectId,
  jobName,
  componentName,
  connectors = [],
  onClose,
}: ComponentInspectorProps) {
  const { data, isLoading, error } = useJobDetail(projectId, jobName);

  const handleKeyDown = useCallback(
    (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    },
    [onClose],
  );

  useEffect(() => {
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [handleKeyDown]);

  const component = data?.scan_data.components?.find(
    (c) => c.unique_name === componentName,
  );

  const hasDetail = Boolean(
    component &&
      (component.metadata_connection_ref ||
        connectors.length > 0 ||
        component.params.length > 0 ||
        component.code),
  );

  return (
    <div
      className="absolute top-0 right-0 bottom-0 w-[400px] bg-card border-l shadow-xl z-30 flex flex-col overflow-hidden"
      data-testid="component-inspector"
    >
      <div className="flex items-center justify-between px-4 py-3 border-b bg-muted/30">
        <div className="min-w-0">
          <h2 className="text-sm font-semibold truncate">{componentName}</h2>
          {component && (
            <span className="text-[11px] text-muted-foreground font-mono">
              {component.component_type}
            </span>
          )}
        </div>
        <button
          onClick={onClose}
          className="p-1.5 rounded hover:bg-muted text-muted-foreground hover:text-foreground flex-shrink-0"
          title="Close (Esc)"
        >
          <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
            <path d="M4 4l8 8M12 4l-8 8" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
          </svg>
        </button>
      </div>

      <div className="flex-1 overflow-y-auto p-4 space-y-4">
        {isLoading && (
          <div className="flex items-center gap-2 text-muted-foreground text-sm">
            <div className="h-4 w-4 border-2 border-current border-t-transparent rounded-full animate-spin" />
            Loading...
          </div>
        )}

        {error && !isLoading && (
          <div className="text-sm text-destructive">
            {error instanceof Error ? error.message : "Failed to load component detail"}
          </div>
        )}

        {!isLoading && !error && !component && (
          <p className="text-sm text-muted-foreground">
            No scan data found for this component.
          </p>
        )}

        {component && (
          <>
            {component.metadata_connection_ref && (
              <div className="space-y-1">
                <h3 className="text-[11px] font-semibold text-muted-foreground uppercase tracking-wide">
                  Connection
                </h3>
                <span
                  className="inline-flex items-center px-2 py-1 rounded-md border text-xs font-medium bg-purple-50 text-purple-800"
                  data-testid="inspector-connection-ref"
                >
                  {component.metadata_connection_ref}
                </span>
              </div>
            )}

            {connectors.length > 0 && (
              <div className="space-y-2">
                <h3 className="text-[11px] font-semibold text-muted-foreground uppercase tracking-wide">
                  Connectors
                </h3>
                <ul className="space-y-1">
                  {connectors.map((c, i) => (
                    <li
                      key={`${c.direction}-${c.peer}-${c.rowName}-${i}`}
                      className="flex items-center gap-1.5 text-xs"
                      data-testid="inspector-connector-row"
                    >
                      <span
                        className={`px-1 py-0.5 rounded text-[9px] font-bold text-white ${
                          c.direction === "incoming" ? "bg-emerald-500" : "bg-blue-500"
                        }`}
                      >
                        {c.direction === "incoming" ? "IN" : "OUT"}
                      </span>
                      <span className="font-mono truncate">{c.peer}</span>
                      <span className="text-muted-foreground">via {c.rowName}</span>
                      {c.isControlFlow && (
                        <span className="text-[9px] text-amber-600">(control)</span>
                      )}
                    </li>
                  ))}
                </ul>
              </div>
            )}

            {component.params.length > 0 && (
              <div className="space-y-2">
                <h3 className="text-[11px] font-semibold text-muted-foreground uppercase tracking-wide">
                  Params
                </h3>
                <div className="rounded-md border divide-y" data-testid="inspector-params">
                  {component.params.map((p) => (
                    <div
                      key={p.name}
                      className="flex items-start justify-between gap-2 px-2 py-1 text-xs"
                    >
                      <span className="font-mono text-muted-foreground shrink-0">{p.name}</span>
                      <span className="font-mono text-right break-all">{p.value}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {component.code && (
              <div className="space-y-1">
                <h3 className="text-[11px] font-semibold text-muted-foreground uppercase tracking-wide">
                  Code
                </h3>
                <pre
                  className="text-xs whitespace-pre-wrap font-mono bg-muted/40 rounded-md p-2 max-h-64 overflow-y-auto"
                  data-testid="inspector-code"
                >
                  {component.code}
                </pre>
              </div>
            )}

            {!hasDetail && (
              <p className="text-sm text-muted-foreground">
                No additional detail captured for this component.
              </p>
            )}
          </>
        )}
      </div>
    </div>
  );
}
