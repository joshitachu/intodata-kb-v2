import { memo } from "react";
import { Handle, Position } from "@xyflow/react";
import type { SemanticSystem } from "@/types/landscape";

function SemanticSystemNodeComponent({ data }: { data: { system: SemanticSystem } }) {
  const { system } = data;
  const status = system.status === "confirmed" ? "Confirmed" : "Proposed from scan";
  return (
    <div
      className="relative w-[205px] rounded-xl border border-sky-200 bg-white px-4 py-3 shadow-sm"
      aria-label={`Software system ${system.name}`}
    >
      <Handle type="target" position={Position.Left} className="!h-2.5 !w-2.5 !border-2 !border-white !bg-sky-500" />
      <div className="absolute inset-y-0 left-0 w-1 rounded-l-xl bg-sky-600" />
      <p className="text-[10px] font-semibold uppercase tracking-wide text-sky-700">
        Software system
      </p>
      <h3 className="truncate text-sm font-bold text-slate-900" title={system.name}>
        {system.name}
      </h3>
      <div className="mt-2 flex items-center justify-between gap-2">
        <span className={`truncate rounded px-1.5 py-0.5 text-[9px] font-semibold ${
          system.status === "confirmed"
            ? "bg-emerald-50 text-emerald-700"
            : "bg-amber-50 text-amber-700"
        }`}>
          {status}
        </span>
        <span className="shrink-0 text-[10px] text-slate-500">
          {system.evidence.length} signals
        </span>
      </div>
      <Handle type="source" position={Position.Right} className="!h-2.5 !w-2.5 !border-2 !border-white !bg-sky-500" />
    </div>
  );
}

export const SemanticSystemNode = memo(SemanticSystemNodeComponent);
