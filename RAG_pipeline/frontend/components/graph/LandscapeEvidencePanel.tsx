"use client";

import { X, ArrowRight } from "lucide-react";
import type { BusinessSystem, LandscapeApplication, LandscapeSystem } from "@/types/landscape";
import { connectedEdges, landscapeEntityLabel, topologyTerm, type TopologyEdge } from "@/lib/landscapeTransform";
import type { ProjectLandscapeResponse } from "@/types/landscape";

type Selection =
  | { kind: "application"; item: LandscapeApplication }
  | { kind: "system"; item: LandscapeSystem }
  | { kind: "businessSystem"; item: BusinessSystem }
  | { kind: "edge"; item: TopologyEdge };

export function LandscapeEvidencePanel({ landscape, selection, onClose, onExplore, onViewDesigns }: { landscape: ProjectLandscapeResponse; selection: Selection; onClose: () => void; onExplore: () => void; onViewDesigns?: (sourceRoot: string) => void }) {
  const title = selection.kind === "edge"
    ? `${landscapeEntityLabel(landscape, selection.item.source)} → ${landscapeEntityLabel(landscape, selection.item.target)}`
    : selection.kind === "application"
      ? (selection.item.label || selection.item.source_root)
      : selection.item.name;
  const relationships = selection.kind === "edge" ? [] : connectedEdges(landscape, selection.item.id);
  const sourceRoot = selection.kind === "application" ? selection.item.source_root : undefined;
  return <aside className="absolute bottom-3 right-3 z-20 w-[min(420px,calc(100%-1.5rem))] rounded-xl border border-slate-200 bg-white/95 p-4 shadow-xl backdrop-blur" aria-label="Landscape evidence">
    <div className="flex gap-3"><div className="min-w-0 flex-1"><p className="text-[10px] font-bold uppercase tracking-wide text-slate-500">{selection.kind === "edge" ? "Relationship evidence" : selection.kind}</p><h2 className="truncate text-sm font-bold text-slate-800" title={title}>{title}</h2></div><button type="button" onClick={onClose} aria-label="Close evidence" className="h-7 rounded p-1 text-slate-400 hover:bg-slate-100"><X className="h-4 w-4" /></button></div>
    {selection.kind === "edge" ? <div className="mt-3 grid grid-cols-2 gap-2 text-xs">{selection.item.businessStream && <div className="col-span-2 rounded-lg bg-sky-50 p-2"><p className="text-[10px] font-semibold uppercase text-sky-700">Business data stream</p><p className="mt-1 font-semibold text-slate-800">{selection.item.businessStream.name}</p><p className="mt-1 text-[10px] text-slate-500">{selection.item.businessStream.business_summary || "No business summary was established."}</p></div>}<Fact label="Mechanism" value={topologyTerm(selection.item.mechanism)} /><Fact label="Confidence" value={topologyTerm(selection.item.confidence)} /><Fact label="Implementation" value={`${selection.item.businessStream?.artifact_ids.length ?? selection.item.provenance.length} design(s)`} /><Fact label="Direction" value={topologyTerm(selection.item.direction)} /><div className="col-span-2 rounded-lg bg-slate-50 p-2"><p className="text-[10px] font-semibold uppercase text-slate-500">Technical evidence</p><p className="mt-1 text-xs text-slate-700">{selection.item.provenance.length} scanner provenance record{selection.item.provenance.length === 1 ? "" : "s"}</p></div></div> : <div className="mt-3 space-y-2 text-xs text-slate-600">{selection.kind === "application" ? <><Fact label="Imported copies" value={`${selection.item.source_roots.length || 1}`} /><Fact label="Artifacts" value={`${selection.item.artifact_count}`} /></> : selection.kind === "businessSystem" ? <><Fact label="Kind" value={topologyTerm(selection.item.kind)} /><Fact label="Status" value={`${topologyTerm(selection.item.status)} · ${Math.round(selection.item.confidence * 100)}%`} />{selection.item.description && <p>{selection.item.description}</p>}</> : <><Fact label="Kind" value={topologyTerm(selection.item.kind)} /><Fact label="Signals" value={`${selection.item.evidence.length} evidence signals`} /></>}<p>{relationships.length} incoming/outgoing relationship{relationships.length === 1 ? "" : "s"} in this landscape.</p></div>}
    <div className="mt-4 flex flex-wrap gap-2"><button type="button" onClick={onExplore} className="inline-flex items-center gap-1 rounded-md bg-slate-900 px-3 py-1.5 text-xs font-semibold text-white hover:bg-slate-700">Explore integration <ArrowRight className="h-3.5 w-3.5" /></button>{sourceRoot && onViewDesigns && <button type="button" onClick={() => onViewDesigns(sourceRoot)} className="rounded-md border border-slate-200 px-3 py-1.5 text-xs font-semibold text-slate-700 hover:bg-slate-50">View designs</button>}</div>
  </aside>;
}

function Fact({ label, value }: { label: string; value: string }) { return <div className="rounded-lg bg-slate-50 p-2"><p className="text-[10px] font-semibold uppercase text-slate-500">{label}</p><p className="mt-0.5 truncate font-medium text-slate-700" title={value}>{value}</p></div>; }
