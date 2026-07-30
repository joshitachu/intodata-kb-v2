"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { Background, BackgroundVariant, Controls, MiniMap, ReactFlow, type EdgeMouseHandler, type NodeMouseHandler, useEdgesState, useNodesState } from "@xyflow/react";
import { Filter, Network, Search, X } from "lucide-react";
import { LandscapeApplicationNode } from "@/components/graph/LandscapeApplicationNode";
import { LandscapeSystemNode } from "@/components/graph/LandscapeSystemNode";
import { LandscapeEvidencePanel } from "@/components/graph/LandscapeEvidencePanel";
import { LandscapeDiagnosticsDrawer } from "@/components/graph/LandscapeDiagnosticsDrawer";
import {
  landscapeToFlow,
  findLandscapeEdge,
  landscapeEdgeKey,
  topologyTerm,
  type LandscapeFilters,
} from "@/lib/landscapeTransform";
import type { LandscapeApplication, LandscapeEdge, LandscapeSystem, ProjectLandscapeResponse } from "@/types/landscape";

export type LandscapeSelection = { kind: "application"; item: LandscapeApplication } | { kind: "system"; item: LandscapeSystem } | { kind: "edge"; item: LandscapeEdge };

const nodeTypes = { landscapeApplication: LandscapeApplicationNode, landscapeSystem: LandscapeSystemNode };

function summaryCount(summary: Record<string, unknown>, key: string, fallback: number): number {
  const value = summary[key];
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

export function LandscapeCanvas({ landscape, onExplore, onViewDesigns }: { landscape: ProjectLandscapeResponse; onExplore: (selection: LandscapeSelection) => void; onViewDesigns: (sourceRoot: string) => void }) {
  const [query, setQuery] = useState("");
  const [showSystems, setShowSystems] = useState(true);
  const [dependencyType, setDependencyType] = useState("business");
  const [mechanism, setMechanism] = useState("all");
  const [showDiagnostics, setShowDiagnostics] = useState(false);
  const [selection, setSelection] = useState<LandscapeSelection | null>(null);
  const filters = useMemo<LandscapeFilters>(() => ({
    query,
    showSystems,
    dependencyTypes: dependencyType === "all"
      ? undefined
      : dependencyType === "business"
        ? new Set(["data", "integration", "control", "control_flow"])
        : new Set([dependencyType]),
    mechanisms: mechanism === "all" ? undefined : new Set([mechanism]),
  }), [query, showSystems, dependencyType, mechanism]);
  const flow = useMemo(() => landscapeToFlow(landscape, filters), [landscape, filters]);
  const [nodes, setNodes, onNodesChange] = useNodesState(flow.nodes);
  const [edges, setEdges, onEdgesChange] = useEdgesState(flow.edges);
  useEffect(() => { setNodes(flow.nodes); setEdges(flow.edges); }, [flow, setNodes, setEdges]);
  const dependencyTypes = useMemo(() => [...new Set(landscape.edges.map((edge) => edge.dependency_type))].sort(), [landscape]);
  const mechanisms = useMemo(() => [...new Set(landscape.edges.map((edge) => edge.mechanism))].sort(), [landscape]);
  const handleNodeClick: NodeMouseHandler = useCallback((_event, node) => { const data = node.data as { entityType?: string; application?: LandscapeApplication; system?: LandscapeSystem }; if (data.entityType === "application" && data.application) setSelection({ kind: "application", item: data.application }); if (data.entityType === "system" && data.system) setSelection({ kind: "system", item: data.system }); }, []);
  const handleEdgeClick: EdgeMouseHandler = useCallback((_event, edge) => { const landscapeEdge = findLandscapeEdge(landscape, edge.id); if (landscapeEdge) setSelection({ kind: "edge", item: landscapeEdge }); }, [landscape]);
  const explore = useCallback(() => { if (selection) onExplore(selection); }, [selection, onExplore]);
  const doubleClickNode: NodeMouseHandler = useCallback((_event, node) => { const data = node.data as { entityType?: string; application?: LandscapeApplication; system?: LandscapeSystem }; if (data.entityType === "application" && data.application) onExplore({ kind: "application", item: data.application }); if (data.entityType === "system" && data.system) onExplore({ kind: "system", item: data.system }); }, [onExplore]);
  const doubleClickEdge: EdgeMouseHandler = useCallback((_event, edge) => { const landscapeEdge = findLandscapeEdge(landscape, edge.id); if (landscapeEdge) onExplore({ kind: "edge", item: landscapeEdge }); }, [landscape, onExplore]);
  return <div className="absolute inset-0" data-testid="landscape-canvas">
    <div className="absolute left-3 top-3 z-20 flex max-w-[calc(100%-1.5rem)] flex-wrap items-center gap-2 rounded-xl border border-slate-200 bg-white/95 p-2 shadow-sm backdrop-blur">
      <div className="flex items-center gap-1.5 px-1 text-xs font-bold text-slate-700"><Network className="h-4 w-4 text-blue-600" /> L1 Landscape</div>
      <label className="relative"><Search className="pointer-events-none absolute left-2 top-1.5 h-3.5 w-3.5 text-slate-400" /><input aria-label="Search applications and systems" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search systems" className="h-7 w-36 rounded border border-slate-200 pl-7 pr-2 text-xs outline-none focus:border-blue-400" /></label>
      <label className="flex items-center gap-1 text-[11px] text-slate-600"><Filter className="h-3.5 w-3.5" /><select aria-label="Filter dependency type" value={dependencyType} onChange={(event) => setDependencyType(event.target.value)} className="h-7 rounded border border-slate-200 bg-white px-1 text-xs"><option value="business">Main data & integration flows</option><option value="all">All technical dependencies</option>{dependencyTypes.map((item) => <option key={item} value={item}>{topologyTerm(item)}</option>)}</select></label>
      <select aria-label="Filter mechanism" value={mechanism} onChange={(event) => setMechanism(event.target.value)} className="h-7 rounded border border-slate-200 bg-white px-1 text-xs"><option value="all">All mechanisms</option>{mechanisms.map((item) => <option key={item}>{item}</option>)}</select>
      <label className="flex items-center gap-1 text-[11px] text-slate-600"><input type="checkbox" checked={showSystems} onChange={(event) => setShowSystems(event.target.checked)} /> Systems</label>
      <button type="button" onClick={() => setShowDiagnostics((current) => !current)} className="rounded border border-amber-200 bg-amber-50 px-2 py-1 text-[11px] font-semibold text-amber-800">{landscape.diagnostics.length} diagnostics</button>
    </div>
    <div className="absolute bottom-3 left-3 z-10 rounded-lg border border-slate-200 bg-white/95 px-3 py-2 text-[10px] text-slate-600 shadow-sm"><p><span className="font-semibold text-blue-700">Blue</span> applications · <span className="font-semibold text-violet-700">Violet</span> external systems</p><p className="mt-0.5">Edges are scanner-computed relationships; labels show type · mechanism · evidence.</p></div>
    <div className="absolute right-3 bottom-3 z-10 rounded-lg border border-slate-200 bg-white/95 px-3 py-2 text-[10px] text-slate-600 shadow-sm">{summaryCount(landscape.summary, "application_count", landscape.applications.length)} applications · {summaryCount(landscape.summary, "system_count", landscape.systems.length)} systems · {summaryCount(landscape.summary, "edge_count", landscape.edges.length)} relationships</div>
    <ReactFlow nodes={nodes} edges={edges} nodeTypes={nodeTypes} onNodesChange={onNodesChange} onEdgesChange={onEdgesChange} onNodeClick={handleNodeClick} onNodeDoubleClick={doubleClickNode} onEdgeClick={handleEdgeClick} onEdgeDoubleClick={doubleClickEdge} fitView minZoom={0.05} maxZoom={4} panActivationKeyCode={null} onlyRenderVisibleElements proOptions={{ hideAttribution: true }} className="bg-background"><Background variant={BackgroundVariant.Dots} gap={24} size={0.8} color="#cbd5e1" className="opacity-20" /><Controls showInteractive={false} className="!bg-card !border !shadow-sm" /><MiniMap nodeStrokeWidth={2} className="!bg-card !border !shadow-sm" nodeColor={(node) => node.type === "landscapeApplication" ? "#2563eb" : "#7c3aed"} /></ReactFlow>
    {selection && <LandscapeEvidencePanel landscape={landscape} selection={selection} onClose={() => setSelection(null)} onExplore={explore} onViewDesigns={onViewDesigns} />}
    {showDiagnostics && <LandscapeDiagnosticsDrawer diagnostics={flow.diagnostics} onClose={() => setShowDiagnostics(false)} />}
  </div>;
}
