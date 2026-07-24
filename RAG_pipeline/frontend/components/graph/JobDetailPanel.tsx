"use client";

import { useCallback, useEffect, useState } from "react";
import { useJobDetail } from "@/hooks/useJobDetail";

interface JobMetadata {
  purpose?: string;
  component_count?: number;
  component_types?: string[];
  external_systems?: string[];
  summary?: string;
  direction?: string;
  artifact_kind?: string;
  artifact_role?: string;
}

interface JobDetailPanelProps {
  projectId: string;
  jobName: string;
  onClose: () => void;
  metadata?: JobMetadata;
}

// System color palette for connection cards
const SYSTEM_COLORS = [
  "#3b82f6", "#f59e0b", "#22c55e", "#a855f7",
  "#14b8a6", "#ec4899", "#6366f1", "#f97316",
];

const DIRECTION_BADGE: Record<string, { label: string; cls: string }> = {
  OnRamp: { label: "IN", cls: "bg-emerald-500" },
  OffRamp: { label: "OUT", cls: "bg-blue-500" },
  Bidirectional: { label: "BOTH", cls: "bg-amber-500" },
};

export function JobDetailPanel({ projectId, jobName, onClose, metadata }: JobDetailPanelProps) {
  const { data, isLoading: loading, error: queryError } = useJobDetail(projectId, jobName);
  const error = queryError instanceof Error ? queryError.message : null;
  const [allExpanded, setAllExpanded] = useState(false);

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

  // Use metadata from graph response (backend-computed), fall back to scan_data
  const externalSystems = metadata?.external_systems ?? [];
  const componentCount = metadata?.component_count ?? data?.scan_data?.components?.length ?? 0;
  const componentTypes = metadata?.component_types ?? [];
  const direction = data?.direction || metadata?.direction || "";
  const summary = data?.summary || metadata?.summary || metadata?.purpose || data?.scan_data?.purpose || "";
  const artifactKind = metadata?.artifact_kind || data?.scan_data?.artifact_kind;
  const artifactLabel =
    artifactKind === "route"
      ? "Talend Route"
      : artifactKind === "job"
        ? "Talend Job"
        : "Component";

  return (
    <div className="absolute top-0 right-0 bottom-0 w-[400px] bg-card border-l shadow-xl z-30 flex flex-col overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b bg-muted/30">
        <div className="min-w-0">
          <h2 className="text-sm font-semibold truncate">{jobName}</h2>
          <div className="flex items-center gap-2 mt-0.5 text-[11px] text-muted-foreground">
            {artifactKind && (
              <span
                className={
                  artifactKind === "route"
                    ? "rounded bg-orange-50 px-1.5 py-0.5 font-semibold text-orange-700"
                    : "rounded bg-blue-50 px-1.5 py-0.5 font-semibold text-blue-700"
                }
              >
                {artifactLabel}
              </span>
            )}
            {data && (
              <>
                <span className="px-1.5 py-0.5 rounded bg-muted font-medium">{data.group}</span>
                {direction && DIRECTION_BADGE[direction] && (
                  <span
                    className={`text-[9px] font-bold px-1 py-0.5 rounded text-white ${DIRECTION_BADGE[direction].cls}`}
                  >
                    {DIRECTION_BADGE[direction].label}
                  </span>
                )}
                {data.scan_data?.modification_date && (
                  <span>{data.scan_data.modification_date.split("T")[0]}</span>
                )}
              </>
            )}
          </div>
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

      {/* Content */}
      <div className="flex-1 overflow-y-auto p-4">
        {loading && (
          <div className="flex items-center gap-2 text-muted-foreground text-sm">
            <div className="h-4 w-4 border-2 border-current border-t-transparent rounded-full animate-spin" />
            Loading...
          </div>
        )}

        {error && !loading && (
          <div className="text-sm text-destructive">{error}</div>
        )}

        {data && !loading && (
          <div className="space-y-4">
            {/* What this executable design does */}
            {summary && (
              <div className="space-y-1">
                <h3 className="text-[11px] font-semibold text-muted-foreground uppercase tracking-wide">
                  What This {artifactLabel} Does
                </h3>
                <p className="text-sm leading-relaxed">{summary}</p>
              </div>
            )}

            {/* External systems (backend-computed) */}
            {externalSystems.length > 0 && (
              <div className="space-y-2">
                <h3 className="text-[11px] font-semibold text-muted-foreground uppercase tracking-wide">
                  External Systems
                </h3>
                <div className="flex flex-wrap gap-1.5">
                  {externalSystems.map((sys, i) => (
                    <span
                      key={sys}
                      className="inline-flex items-center gap-1.5 px-2 py-1 rounded-md border text-xs font-medium"
                    >
                      <span
                        className="w-2 h-2 rounded-full flex-shrink-0"
                        style={{ backgroundColor: SYSTEM_COLORS[i % SYSTEM_COLORS.length] }}
                      />
                      {sys}
                    </span>
                  ))}
                </div>
              </div>
            )}

            {/* Processing summary */}
            <div className="space-y-1">
              <h3 className="text-[11px] font-semibold text-muted-foreground uppercase tracking-wide">
                Processing
              </h3>
              <div className="text-sm text-muted-foreground">
                <p>{componentCount} component{componentCount !== 1 ? "s" : ""}</p>
              </div>
              {componentTypes.length > 0 && (
                <div className="flex flex-wrap gap-1 mt-1">
                  {componentTypes.map((t) => (
                    <span
                      key={t}
                      className="px-1.5 py-0.5 text-[11px] rounded bg-muted text-muted-foreground font-mono"
                    >
                      {t}
                    </span>
                  ))}
                </div>
              )}
            </div>

            {/* Doc sections */}
            {data.doc_sections.length > 0 && (
              <div className="space-y-2">
                <div className="flex items-center justify-between">
                  <h3 className="text-[11px] font-semibold text-muted-foreground uppercase tracking-wide">
                    From the Docs
                  </h3>
                  <button
                    onClick={() => setAllExpanded((v) => !v)}
                    className="text-[10px] text-muted-foreground hover:text-foreground"
                  >
                    {allExpanded ? "collapse all" : "expand all"}
                  </button>
                </div>
                {data.doc_sections.map((sec, i) => (
                  <DocSectionControlled
                    key={`${sec.heading}-${i}`}
                    heading={sec.heading}
                    content={sec.content}
                    forceExpanded={allExpanded}
                  />
                ))}
              </div>
            )}

            {/* File path */}
            {data.scan_data?.path && (
              <div className="text-[11px] text-muted-foreground font-mono truncate pt-2 border-t" title={data.scan_data.path}>
                {data.scan_data.path}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

function DocSectionControlled({
  heading,
  content,
  forceExpanded,
}: {
  heading: string;
  content: string;
  forceExpanded: boolean;
}) {
  const [localExpanded, setLocalExpanded] = useState(false);
  const expanded = forceExpanded || localExpanded;
  const preview = content.slice(0, 120).replace(/\n/g, " ").trim();

  return (
    <div className="border rounded-md">
      <button
        onClick={() => setLocalExpanded((v) => !v)}
        className="w-full flex items-center gap-2 px-3 py-2 text-left hover:bg-muted/40 transition-colors"
      >
        <span className="text-[11px] text-muted-foreground flex-shrink-0">
          {expanded ? "\u25BE" : "\u25B8"}
        </span>
        <span className="text-xs font-medium truncate">{heading}</span>
      </button>
      {!expanded && (
        <div className="px-3 pb-2 text-[11px] text-muted-foreground truncate">
          {preview}...
        </div>
      )}
      {expanded && (
        <div className="px-3 pb-3 max-h-64 overflow-y-auto">
          <pre className="text-xs whitespace-pre-wrap font-mono text-foreground/80 leading-relaxed">
            {content}
          </pre>
        </div>
      )}
    </div>
  );
}
