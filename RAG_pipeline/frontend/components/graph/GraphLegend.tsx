import { useState } from "react";
import { X, Info } from "lucide-react";
import { STATUS_META, FAILING_EDGE_COLOR } from "@/lib/healthStatusMeta";
import type { EffectiveStatus } from "@/lib/healthPropagation";

interface GroupColor {
  bg: string;
  border: string;
  text: string;
}

interface GraphLegendProps {
  groupColorMap: Record<string, GroupColor>;
  collapsedMode?: boolean;
  showHealth?: boolean;
  showArtifactTaxonomy?: boolean;
}

const HEALTH_LEGEND_ORDER: EffectiveStatus[] = [
  "down",
  "impacted",
  "degraded",
  "running",
  "healthy",
  "unknown",
];

export function GraphLegend({
  groupColorMap,
  collapsedMode,
  showHealth,
  showArtifactTaxonomy = false,
}: GraphLegendProps) {
  const groups = Object.entries(groupColorMap);
  const [collapsed, setCollapsed] = useState(false);

  if (collapsed) {
    return (
      <button
        type="button"
        onClick={() => setCollapsed(false)}
        aria-label="Show legend"
        title="Show legend"
        className="absolute bottom-3 left-3 z-10 flex items-center gap-1.5 rounded-full border border-slate-200 bg-white/90 backdrop-blur-md px-3 py-1.5 text-[11px] font-semibold text-slate-600 shadow-lg hover:bg-white"
      >
        <Info className="h-3.5 w-3.5" />
        Legend
      </button>
    );
  }

  return (
    <div className="absolute bottom-3 left-3 bg-white/90 backdrop-blur-md border border-slate-200 rounded-xl p-3.5 text-xs shadow-lg max-w-[240px] z-10">
      <div className="flex items-center justify-between mb-2.5">
        <div className="font-bold text-slate-800 text-[11px] uppercase tracking-wider">Legend</div>
        <button
          type="button"
          onClick={() => setCollapsed(true)}
          aria-label="Hide legend"
          title="Hide legend"
          className="rounded p-0.5 text-slate-400 hover:bg-slate-100 hover:text-slate-600"
        >
          <X className="h-3.5 w-3.5" />
        </button>
      </div>

      {/* Runtime-health overlay (S3) */}
      {showHealth && (
        <div className="space-y-1.5 mb-2.5 pb-2.5 border-b border-slate-100" data-testid="health-legend">
          <div className="font-semibold mb-1 text-slate-400 text-[10px] uppercase tracking-wider">Runtime health</div>
          {HEALTH_LEGEND_ORDER.map((status) => {
            const meta = STATUS_META[status];
            return (
              <div key={status} className="flex items-center gap-2.5">
                <span
                  className="h-2.5 w-2.5 rounded-full shrink-0"
                  style={{ background: meta.color }}
                />
                <span className="text-slate-500">
                  {meta.label} <span className="text-slate-300">— {meta.description}</span>
                </span>
              </div>
            );
          })}
          <div className="flex items-center gap-2.5">
            <div className="w-8 h-0 border-t-[2.5px]" style={{ borderColor: FAILING_EDGE_COLOR }} />
            <span className="text-slate-500">Failing dependency path</span>
          </div>
        </div>
      )}

      {/* Edge types */}
      <div className="space-y-1.5 mb-2.5 pb-2.5 border-b border-slate-100">
        <div className="flex items-center gap-2.5">
          <div className="w-8 h-0 border-t-[2px] border-slate-400 relative">
            <div className="absolute -right-0.5 -top-[3px] w-0 h-0 border-l-[4px] border-l-slate-400 border-y-[3px] border-y-transparent" />
          </div>
          <span className="text-slate-500">
            {showArtifactTaxonomy ? "Job calls Job" : "Component orchestration"}
          </span>
        </div>
        {showArtifactTaxonomy && (
          <div className="flex items-center gap-2.5">
            <div className="w-8 h-0 border-t-[2px] border-orange-600 relative">
              <div className="absolute -right-0.5 -top-[3px] w-0 h-0 border-l-[4px] border-l-orange-600 border-y-[3px] border-y-transparent" />
            </div>
            <span className="text-slate-500">Route invokes Job</span>
          </div>
        )}
        {showArtifactTaxonomy && collapsedMode ? (
          <>
            <div className="flex items-center gap-2.5">
              <div className="w-8 h-0 border-t-[2px] border-violet-500 relative">
                <div className="absolute -right-0.5 -top-[3px] w-0 h-0 border-l-[4px] border-l-violet-500 border-y-[3px] border-y-transparent" />
              </div>
              <span className="text-slate-500">Produces / consumes resource</span>
            </div>
            <div className="flex items-center gap-2.5">
              <div className="w-8 h-0 border-t-[1.5px] border-dashed" style={{ borderColor: "#c4b5fd" }} />
              <span className="text-slate-500">Resource direction unknown</span>
            </div>
          </>
        ) : (
          <div className="flex items-center gap-2.5">
            <div className="w-8 h-0 border-t-[1.5px] border-dashed" style={{ borderColor: "#c4b5fd" }} />
            <span className="text-slate-500">Connection usage</span>
          </div>
        )}
        {collapsedMode && (
          <div className="flex items-center gap-2.5">
            <div className="w-8 h-0 border-t-[2.5px] border-slate-500" />
            <span className="text-slate-500">Inter-group (aggregated)</span>
          </div>
        )}
      </div>

      {/* Node types */}
      <div className="space-y-1.5 mb-2.5 pb-2.5 border-b border-slate-100">
        <div className="flex items-center gap-2.5">
          <div className="w-4 h-3 rounded border border-slate-200 bg-white overflow-hidden flex shadow-sm">
            <div className="w-[3px] h-full bg-blue-500" />
          </div>
          <span className="text-slate-500">{showArtifactTaxonomy ? "Talend Job" : "Component"}</span>
        </div>
        {showArtifactTaxonomy && (
          <div className="flex items-center gap-2.5">
            <div className="w-4 h-3 rounded border border-orange-200 bg-white overflow-hidden flex shadow-sm">
              <div className="w-[3px] h-full bg-orange-600" />
            </div>
            <span className="text-slate-500">Talend Route</span>
          </div>
        )}
        <div className="flex items-center gap-2.5">
          <div className="w-4 h-3 rounded border border-slate-200 bg-white shadow-sm flex items-center justify-center">
            <div className="w-1.5 h-1.5 rounded-full bg-purple-400" />
          </div>
          <span className="text-slate-500">
            {showArtifactTaxonomy && collapsedMode
              ? "Shared source / destination"
              : "Connection"}
          </span>
        </div>
        {collapsedMode && (
          <div className="flex items-center gap-2.5">
            <div className="w-4 h-3 rounded border border-slate-200 bg-white overflow-hidden flex shadow-sm">
              <div className="w-[3px] h-full bg-indigo-400" />
            </div>
            <span className="text-slate-500">
              Group (click to {showArtifactTaxonomy ? "explore" : "expand"})
            </span>
          </div>
        )}
        {showArtifactTaxonomy && collapsedMode && (
          <div className="text-[9px] leading-snug text-slate-400">
            Hover a group or resource to trace its visible relationships.
          </div>
        )}
      </div>

      {/* Group colors */}
      {groups.length > 0 && (
        <div>
          <div className="font-semibold mb-1.5 text-slate-400 text-[10px] uppercase tracking-wider">Groups</div>
          <div className="grid grid-cols-2 gap-1.5">
            {groups.map(([name, color]) => (
              <div key={name} className="flex items-center gap-1.5">
                <div
                  className="w-3 h-3 rounded-[3px] shrink-0"
                  style={{ background: color.border }}
                />
                <span className="text-slate-500 truncate" title={name}>
                  {name}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
