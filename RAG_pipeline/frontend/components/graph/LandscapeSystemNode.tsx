import { memo } from "react";
import { Handle, Position } from "@xyflow/react";
import type { LandscapeSystem } from "@/types/landscape";
import { assistantRoleShadow } from "@/lib/nodeStyles";

export interface LandscapeSystemNodeData { system: LandscapeSystem; highlighted?: boolean; dimmed?: boolean; assistantVisualRole?: string }

function LandscapeSystemNodeComponent({ data }: { data: LandscapeSystemNodeData }) {
  const { system } = data;
  const entityLabel = system.kind === "unknown"
    ? "Configured dependency"
    : system.kind === "database"
      ? "Data system"
      : "External system";
  return (
    <div data-assistant-role={data.assistantVisualRole} style={{ boxShadow: assistantRoleShadow(data.assistantVisualRole) }} className={`relative w-[205px] rounded-xl border border-violet-200 bg-white px-4 py-3 shadow-sm ${data.highlighted && !data.assistantVisualRole ? "ring-4 ring-violet-200" : ""}`} aria-label={`${entityLabel} ${system.name}`}>
      <Handle type="target" position={Position.Left} className="!h-2.5 !w-2.5 !border-2 !border-white !bg-violet-400" />
      <div className="absolute inset-y-0 left-0 w-1 rounded-l-xl bg-violet-600" />
      <p className="text-[10px] font-semibold uppercase tracking-wide text-violet-600">{entityLabel}</p>
      <h3 className="truncate text-sm font-bold text-slate-800" title={system.name}>{system.name}</h3>
      <div className="mt-2 flex items-center justify-between gap-2">
        <span className="truncate rounded bg-violet-50 px-1.5 py-0.5 text-[9px] font-semibold text-violet-700" title={system.kind}>{system.kind}</span>
        <span className="shrink-0 text-[10px] text-slate-500">{system.evidence.length} signals</span>
      </div>
      {system.source_roots.length > 0 && <p className="mt-2 truncate text-[9px] text-slate-400" title={system.source_roots.join("\n")}>{system.source_roots.length} source root{system.source_roots.length === 1 ? "" : "s"}</p>}
      <Handle type="source" position={Position.Right} className="!h-2.5 !w-2.5 !border-2 !border-white !bg-violet-400" />
    </div>
  );
}

export const LandscapeSystemNode = memo(LandscapeSystemNodeComponent);
