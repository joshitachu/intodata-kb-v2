import { memo } from "react";
import { Handle, Position } from "@xyflow/react";
import type { BusinessSystem } from "@/types/landscape";
import { assistantRoleShadow } from "@/lib/nodeStyles";

function BusinessSystemNodeComponent({ data }: { data: { system: BusinessSystem; highlighted?: boolean; dimmed?: boolean; assistantVisualRole?: string } }) {
  const { system } = data;
  const kindLabel = system.kind === "data_system"
    ? "Data system"
    : system.kind === "external_service"
      ? "External service"
      : system.kind === "organization"
        ? "Organization"
        : system.kind === "person_role"
          ? "Role"
          : system.kind === "unresolved"
            ? "Unresolved system"
            : "Software system";
  const status = system.status === "confirmed" || system.status === "observed"
    ? "Evidence-backed"
    : "Proposed for review";
  return <div data-assistant-role={data.assistantVisualRole} style={{ boxShadow: assistantRoleShadow(data.assistantVisualRole) }} className={`relative w-[220px] rounded-xl border border-sky-200 bg-white px-4 py-3 shadow-sm ${data.highlighted && !data.assistantVisualRole ? "ring-4 ring-sky-200" : ""}`} aria-label={`${kindLabel} ${system.name}`}><Handle type="target" position={Position.Left} className="!h-2.5 !w-2.5 !border-2 !border-white !bg-sky-500" /><div className="absolute inset-y-0 left-0 w-1 rounded-l-xl bg-sky-600" /><p className="text-[10px] font-semibold uppercase tracking-wide text-sky-700">{kindLabel}</p><h3 className="truncate text-sm font-bold text-slate-900" title={system.name}>{system.name}</h3>{(system.description || system.purpose) && <p className="mt-1 line-clamp-2 text-[10px] leading-snug text-slate-500" title={system.description || system.purpose}>{system.description || system.purpose}</p>}<div className="mt-2"><span className={`truncate rounded px-1.5 py-0.5 text-[9px] font-semibold ${system.status === "confirmed" || system.status === "observed" ? "bg-emerald-50 text-emerald-700" : "bg-amber-50 text-amber-700"}`}>{status}</span></div>{system.owner && <p className="mt-1 truncate text-[9px] text-slate-400" title={system.owner}>Owner: {system.owner}</p>}<Handle type="source" position={Position.Right} className="!h-2.5 !w-2.5 !border-2 !border-white !bg-sky-500" /></div>;
}

export const BusinessSystemNode = memo(BusinessSystemNodeComponent);
