import { memo } from "react";
import { Handle, Position } from "@xyflow/react";
import type { LandscapeApplication } from "@/types/landscape";

export interface LandscapeApplicationNodeData { application: LandscapeApplication }

function LandscapeApplicationNodeComponent({ data }: { data: LandscapeApplicationNodeData }) {
  const { application } = data;
  const kinds = Object.entries(application.kinds_breakdown).filter(([, count]) => count > 0);
  const entityLabel = application.role === "shared_project"
    ? "Shared Talend project"
    : application.role === "support"
      ? "Supporting project"
      : "Talend project";
  const detail = application.description
    || (application.source_roots.length > 1
      ? `${application.source_roots.length} identical imported copies`
      : "Scanned integration project");
  return (
    <div className="relative w-[230px] rounded-xl border border-blue-200 bg-white px-4 py-3 shadow-sm" aria-label={`${entityLabel} ${application.label || application.source_root}`}>
      <Handle type="target" position={Position.Left} className="!h-2.5 !w-2.5 !border-2 !border-white !bg-blue-400" />
      <div className="absolute inset-y-0 left-0 w-1 rounded-l-xl bg-blue-600" />
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="truncate text-[10px] font-semibold uppercase tracking-wide text-blue-600">{entityLabel}</p>
          <h3 className="truncate text-sm font-bold text-slate-800" title={application.label || application.source_root}>{application.label || application.source_root}</h3>
        </div>
        <span className="rounded-full bg-blue-50 px-2 py-0.5 text-[10px] font-bold text-blue-700">{application.artifact_count}</span>
      </div>
      <p className="mt-2 line-clamp-2 text-[10px] leading-snug text-slate-500" title={detail}>{detail}</p>
      <div className="mt-2 flex flex-wrap gap-1">
        {kinds.slice(0, 3).map(([kind, count]) => <span key={kind} className="rounded bg-slate-100 px-1.5 py-0.5 text-[9px] font-semibold text-slate-600">{count} {kind}{count === 1 ? "" : "s"}</span>)}
        {kinds.length > 3 && <span className="rounded bg-slate-100 px-1.5 py-0.5 text-[9px] text-slate-500">+{kinds.length - 3}</span>}
      </div>
      <Handle type="source" position={Position.Right} className="!h-2.5 !w-2.5 !border-2 !border-white !bg-blue-400" />
    </div>
  );
}

export const LandscapeApplicationNode = memo(LandscapeApplicationNodeComponent);
