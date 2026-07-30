"use client";

import { AlertTriangle, X } from "lucide-react";
import type { LandscapeDiagnostic } from "@/types/landscape";

export function LandscapeDiagnosticsDrawer({ diagnostics, onClose }: { diagnostics: LandscapeDiagnostic[]; onClose: () => void }) {
  return (
    <aside className="absolute right-3 top-3 z-20 w-[min(390px,calc(100%-1.5rem))] rounded-xl border border-amber-200 bg-white/95 p-4 shadow-xl backdrop-blur" aria-label="Landscape diagnostics">
      <div className="flex items-center justify-between gap-3"><div className="flex items-center gap-2 text-sm font-bold text-slate-800"><AlertTriangle className="h-4 w-4 text-amber-600" /> Diagnostics ({diagnostics.length})</div><button type="button" onClick={onClose} aria-label="Close diagnostics" className="rounded p-1 text-slate-400 hover:bg-slate-100"><X className="h-4 w-4" /></button></div>
      <div className="mt-3 max-h-[60vh] space-y-2 overflow-auto pr-1">
        {diagnostics.length === 0 ? <p className="text-xs text-slate-500">No scanner diagnostics for this landscape.</p> : diagnostics.map((diagnostic, index) => <div key={`${diagnostic.kind}:${index}`} className="rounded-lg border border-slate-100 p-2.5"><div className="flex justify-between gap-2"><span className="text-xs font-semibold text-slate-700">{diagnostic.kind.replaceAll("_", " ")}</span><span className="text-[10px] font-bold uppercase text-amber-700">{diagnostic.severity}</span></div><p className="mt-1 text-xs text-slate-600">{diagnostic.description}</p>{diagnostic.evidence.length > 0 && <p className="mt-1 truncate text-[10px] text-slate-400" title={diagnostic.evidence.join("\n")}>{diagnostic.evidence.length} evidence record{diagnostic.evidence.length === 1 ? "" : "s"}</p>}</div>)}
      </div>
    </aside>
  );
}
