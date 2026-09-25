"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { Background, BackgroundVariant, Controls, MiniMap, ReactFlow, type Edge, type EdgeMouseHandler, type Node, type NodeMouseHandler, type ReactFlowInstance, useEdgesState, useNodesState } from "@xyflow/react";
import { Filter, Network, Search, X } from "lucide-react";
import { LandscapeApplicationNode } from "@/components/graph/LandscapeApplicationNode";
import { LandscapeSystemNode } from "@/components/graph/LandscapeSystemNode";
import { BusinessSystemNode } from "@/components/graph/BusinessSystemNode";
import { LandscapeEvidencePanel } from "@/components/graph/LandscapeEvidencePanel";
import { LandscapeDiagnosticsDrawer } from "@/components/graph/LandscapeDiagnosticsDrawer";
import { ArchitectureTopologyCanvas } from "@/components/graph/ArchitectureTopologyCanvas";
import {
  landscapeToFlow,
  findLandscapeEdge,
  isAggregatedRelationshipEdge,
  landscapeEdgeKey,
  topologyTerm,
  type LandscapeEdgeData,
  type LandscapeNodeData,
  type TopologyEdge,
  type LandscapeFilters,
} from "@/lib/landscapeTransform";
import type { BusinessSystem, LandscapeApplication, LandscapeSystem, ProjectLandscapeResponse } from "@/types/landscape";
import type { AssistantCanvasEmphasis, AssistantFocusRequest, CopilotViewContext } from "@/types/copilot";
import type { BusinessHealthFeed } from "@/lib/businessHealth";
import { useFitOnContainerResize } from "@/hooks/useFitOnContainerResize";

export type LandscapeSelection =
  | { kind: "application"; item: LandscapeApplication }
  | { kind: "system"; item: LandscapeSystem }
  | { kind: "businessSystem"; item: BusinessSystem }
  | { kind: "edge"; item: TopologyEdge }
  | { kind: "relationship"; source: string; target: string; streamKeys: string[] };

/** L1 edges rendered by `businessLandscapeToFlow` are aggregated one-per-
 * directed-system-pair (`pair:...` ids carrying `data.streamKeys`); everything
 * else (single business streams, technical scanner edges) resolves the usual
 * way through `findLandscapeEdge`. */
function selectionForEdge(
  landscape: ProjectLandscapeResponse,
  edgeId: string,
  data: LandscapeEdgeData | undefined,
): LandscapeSelection | null {
  if (isAggregatedRelationshipEdge(data)) {
    return { kind: "relationship", source: data.pair.source, target: data.pair.target, streamKeys: data.streamKeys };
  }
  const landscapeEdge = findLandscapeEdge(landscape, edgeId);
  return landscapeEdge ? { kind: "edge", item: landscapeEdge } : null;
}

const nodeTypes = {
  landscapeApplication: LandscapeApplicationNode,
  landscapeSystem: LandscapeSystemNode,
  businessSystem: BusinessSystemNode,
};

function summaryCount(summary: Record<string, unknown>, key: string, fallback: number): number {
  const value = summary[key];
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

interface LandscapeCanvasProps {
  landscape: ProjectLandscapeResponse;
  onExplore: (selection: LandscapeSelection) => void;
  onViewDesigns: (sourceRoot: string) => void;
  assistantEmphasis?: AssistantCanvasEmphasis | null;
  assistantFocusRequest?: AssistantFocusRequest | null;
  onContextChange?: (context: CopilotViewContext) => void;
  /**
   * Live runtime mirror for the business overlay. Optional: the technical
   * scanner fallback below has no business streams to hang health on, so it
   * ignores this entirely rather than inventing a second join.
   */
  health?: BusinessHealthFeed;
}

export function LandscapeCanvas(props: LandscapeCanvasProps) {
  const hasBusinessTopology = Boolean(
    props.landscape.business_topology
    && props.landscape.topology_state?.validated
    && !props.landscape.topology_state.stale,
  );
  if (hasBusinessTopology) {
    return <ArchitectureTopologyCanvas
      landscape={props.landscape}
      health={props.health}
      assistantEmphasis={props.assistantEmphasis}
      assistantFocusRequest={props.assistantFocusRequest}
      onContextChange={props.onContextChange}
      onOpenFlow={(streamId) => {
        const edge = findLandscapeEdge(props.landscape, `business-stream\u001f${streamId}`);
        if (edge) props.onExplore({ kind: "edge", item: edge });
      }}
    />;
  }
  return <TechnicalLandscapeCanvas {...props} />;
}

function TechnicalLandscapeCanvas({ landscape, onExplore, onViewDesigns, assistantEmphasis, assistantFocusRequest, onContextChange }: LandscapeCanvasProps) {
  const [query, setQuery] = useState("");
  const [showSystems, setShowSystems] = useState(true);
  const [dependencyType, setDependencyType] = useState("business");
  const [mechanism, setMechanism] = useState("all");
  const [showInfrastructure, setShowInfrastructure] = useState(false);
  const [showDiagnostics, setShowDiagnostics] = useState(false);
  const [selection, setSelection] = useState<LandscapeSelection | null>(null);
  const [instance, setInstance] = useState<ReactFlowInstance<Node<LandscapeNodeData>, Edge<LandscapeEdgeData>> | null>(null);

  // The diagram follows the docked copilot: widening that panel narrows this
  // column, and without a refit the graph slides out of view on the right.
  const refit = useCallback(
    () => void instance?.fitView({ padding: 0.12, duration: 300 }),
    [instance],
  );
  const canvasRef = useFitOnContainerResize(refit);
  // Focus+context: which system/relationship the cursor is over. Drives the
  // highlight so a dense graph stays readable -- hover a node or line to trace
  // only what connects to it; everything else fades and labels are hidden.
  const [focus, setFocus] = useState<{ id: string; kind: "node" | "edge" } | null>(null);
  const filters = useMemo<LandscapeFilters>(() => ({
    query,
    showSystems,
    dependencyTypes: dependencyType === "all"
      ? undefined
      : dependencyType === "business"
        ? new Set(["data", "integration", "control", "control_flow"])
        : new Set([dependencyType]),
    mechanisms: mechanism === "all" ? undefined : new Set([mechanism]),
    showInfrastructure,
  }), [query, showSystems, dependencyType, mechanism, showInfrastructure]);
  const flow = useMemo(() => landscapeToFlow(landscape, filters), [landscape, filters]);
  const [nodes, setNodes, onNodesChange] = useNodesState(flow.nodes);
  const [edges, setEdges, onEdgesChange] = useEdgesState(flow.edges);
  useEffect(() => { setNodes(flow.nodes); setEdges(flow.edges); }, [flow, setNodes, setEdges]);
  useEffect(() => {
    if (!assistantFocusRequest || !instance) return;
    const focusNodes = nodes.filter((node) => assistantFocusRequest.nodeIds.includes(node.id));
    if (focusNodes.length > 0) void instance.fitView({ nodes: focusNodes, padding: 0.35, duration: 450 });
  }, [assistantFocusRequest, instance, nodes]);
  useEffect(() => {
    const selectedEntity = selection?.kind === "application"
      ? { kind: "application" as const, label: selection.item.label, entity_id: selection.item.id }
      : selection?.kind === "system" || selection?.kind === "businessSystem"
        ? { kind: "system" as const, label: selection.item.name, entity_id: selection.item.id }
        : selection?.kind === "relationship"
          ? { kind: "relationship" as const, label: `${selection.source} → ${selection.target}`, entity_id: `${selection.source}->${selection.target}` }
          : selection?.kind === "edge"
            ? { kind: "edge" as const, label: topologyTerm(selection.item.dependency_type), entity_id: landscapeEdgeKey(selection.item) }
            : undefined;
    onContextChange?.({
      selectedEntity,
      filters: [
        { name: "query", value: query || "all" },
        { name: "dependency_type", value: dependencyType },
        { name: "mechanism", value: mechanism },
        { name: "infrastructure", value: showInfrastructure ? "shown" : "hidden" },
      ],
    });
  }, [selection, query, dependencyType, mechanism, showInfrastructure, onContextChange]);
  // Given the focused node/edge, which nodes and edges are "active" (the thing
  // plus its direct neighbourhood). null = nothing focused (resting state).
  const active = useMemo(() => {
    if (!focus) return null;
    const activeNodes = new Set<string>();
    const activeEdges = new Set<string>();
    if (focus.kind === "edge") {
      const edge = edges.find((item) => item.id === focus.id);
      if (edge) { activeEdges.add(edge.id); activeNodes.add(edge.source); activeNodes.add(edge.target); }
    } else {
      activeNodes.add(focus.id);
      for (const edge of edges) {
        if (edge.source === focus.id || edge.target === focus.id) {
          activeEdges.add(edge.id); activeNodes.add(edge.source); activeNodes.add(edge.target);
        }
      }
    }
    return { activeNodes, activeEdges };
  }, [focus, edges]);
  // Resting state hides all edge labels (they otherwise pile into unreadable
  // text soup) and softens the lines; the focused relationship(s) get full
  // colour + their label, everything else fades right back.
  const displayNodes = useMemo(() => nodes.map((node) => {
    const assistantRole = assistantEmphasis?.nodeRoles.get(node.id);
    const assistantActive = Boolean(assistantEmphasis?.nodeRoles.size || assistantEmphasis?.edgeRoles.size);
    const dim = active ? !active.activeNodes.has(node.id) : assistantActive && !assistantRole;
    return {
      ...node,
      data: { ...node.data, highlighted: Boolean(assistantRole), dimmed: dim, assistantVisualRole: assistantRole },
      style: { ...node.style, opacity: dim ? 0.12 : 1, transition: "opacity 120ms ease" },
    };
  }), [nodes, active, assistantEmphasis]);
  const displayEdges = useMemo(() => edges.map((edge) => {
    const assistantRole = assistantEmphasis?.edgeRoles.get(edge.id);
    const assistantActive = Boolean(assistantEmphasis?.nodeRoles.size || assistantEmphasis?.edgeRoles.size);
    const focused = active ? active.activeEdges.has(edge.id) : Boolean(assistantRole);
    const dimmed = active ? !focused : assistantActive && !assistantRole;
    const baseWidth = (edge.style?.strokeWidth as number | undefined) ?? 2;
    return {
      ...edge,
      label: focused ? edge.label : undefined,
      style: {
        ...edge.style,
        opacity: dimmed ? 0.05 : active ? 1 : 0.5,
        strokeWidth: focused ? baseWidth + (assistantRole === "primary" ? 2 : 1.2) : baseWidth,
        transition: "opacity 120ms ease",
      },
      data: edge.data ? { ...edge.data, assistantVisualRole: assistantRole } : edge.data,
    };
  }), [edges, active, assistantEmphasis]);
  const onNodeEnter: NodeMouseHandler = useCallback((_event, node) => setFocus({ id: node.id, kind: "node" }), []);
  const onEdgeEnter: EdgeMouseHandler = useCallback((_event, edge) => setFocus({ id: edge.id, kind: "edge" }), []);
  const clearFocus = useCallback(() => setFocus(null), []);
  const hasBusinessTopology = Boolean(landscape.business_topology && landscape.topology_state?.validated && !landscape.topology_state.stale);
  const dependencyTypes = useMemo(() => hasBusinessTopology ? ["data"] : [...new Set(landscape.edges.map((edge) => edge.dependency_type))].sort(), [hasBusinessTopology, landscape]);
  const mechanisms = useMemo(() => hasBusinessTopology
    ? [...new Set(landscape.business_topology!.delivery_paths.flatMap((path) => path.segments.map((segment) => segment.mechanism)).filter(Boolean))].sort()
    : [...new Set(landscape.edges.map((edge) => edge.mechanism))].sort(), [landscape]);
  const handleNodeClick: NodeMouseHandler = useCallback((_event, node) => {
    const data = node.data as {
      entityType?: string;
      application?: LandscapeApplication;
      system?: LandscapeSystem | BusinessSystem;
    };
    if (data.entityType === "application" && data.application) setSelection({ kind: "application", item: data.application });
    if (data.entityType === "system" && data.system) setSelection({ kind: "system", item: data.system as LandscapeSystem });
    if (data.entityType === "businessSystem" && data.system) setSelection({ kind: "businessSystem", item: data.system as BusinessSystem });
  }, []);
  const handleEdgeClick: EdgeMouseHandler = useCallback((_event, edge) => { const next = selectionForEdge(landscape, edge.id, edge.data as LandscapeEdgeData | undefined); if (next) setSelection(next); }, [landscape]);
  const explore = useCallback(() => { if (selection) onExplore(selection); }, [selection, onExplore]);
  const doubleClickNode: NodeMouseHandler = useCallback((_event, node) => {
    const data = node.data as {
      entityType?: string;
      application?: LandscapeApplication;
      system?: LandscapeSystem | BusinessSystem;
    };
    if (data.entityType === "application" && data.application) onExplore({ kind: "application", item: data.application });
    if (data.entityType === "system" && data.system) onExplore({ kind: "system", item: data.system as LandscapeSystem });
    if (data.entityType === "businessSystem" && data.system) onExplore({ kind: "businessSystem", item: data.system as BusinessSystem });
  }, [onExplore]);
  const doubleClickEdge: EdgeMouseHandler = useCallback((_event, edge) => { const next = selectionForEdge(landscape, edge.id, edge.data as LandscapeEdgeData | undefined); if (next) onExplore(next); }, [landscape, onExplore]);
  return <div ref={canvasRef} className="absolute inset-0" data-testid="landscape-canvas">
    <div className="absolute left-3 top-3 z-20 flex max-w-[calc(100%-1.5rem)] flex-wrap items-center gap-2 rounded-xl border border-slate-200 bg-white/95 p-2 shadow-sm backdrop-blur">
      <div className="flex items-center gap-1.5 px-1 text-xs font-bold text-slate-700"><Network className="h-4 w-4 text-blue-600" /> L1 Landscape</div>
      <label className="relative"><Search className="pointer-events-none absolute left-2 top-1.5 h-3.5 w-3.5 text-slate-400" /><input aria-label="Search applications and systems" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search systems" className="h-7 w-36 rounded border border-slate-200 pl-7 pr-2 text-xs outline-none focus:border-blue-400" /></label>
      <label className="flex items-center gap-1 text-[11px] text-slate-600"><Filter className="h-3.5 w-3.5" /><select aria-label="Filter dependency type" value={dependencyType} onChange={(event) => setDependencyType(event.target.value)} className="h-7 rounded border border-slate-200 bg-white px-1 text-xs"><option value="business">Main data & integration flows</option><option value="all">All technical dependencies</option>{dependencyTypes.map((item) => <option key={item} value={item}>{topologyTerm(item)}</option>)}</select></label>
      <select aria-label="Filter mechanism" value={mechanism} onChange={(event) => setMechanism(event.target.value)} className="h-7 rounded border border-slate-200 bg-white px-1 text-xs"><option value="all">All mechanisms</option>{mechanisms.map((item) => <option key={item}>{item}</option>)}</select>
      {!hasBusinessTopology && <label className="flex items-center gap-1 text-[11px] text-slate-600"><input type="checkbox" checked={showSystems} onChange={(event) => setShowSystems(event.target.checked)} /> Systems</label>}
      <label className="flex items-center gap-1 text-[11px] text-slate-600"><input type="checkbox" checked={showInfrastructure} onChange={(event) => setShowInfrastructure(event.target.checked)} /> Infrastructure & logging</label>
      <button type="button" onClick={() => setShowDiagnostics((current) => !current)} className="rounded border border-amber-200 bg-amber-50 px-2 py-1 text-[11px] font-semibold text-amber-800">{landscape.diagnostics.length} diagnostics</button>
    </div>
    <div className="absolute bottom-3 left-3 z-10 rounded-lg border border-slate-200 bg-white/95 px-3 py-2 text-[10px] text-slate-600 shadow-sm">{hasBusinessTopology ? <><p><span className="font-semibold text-sky-700">Boxes</span> are organization systems · arrows are business data streams</p><p className="mt-0.5"><span className="font-semibold">Hover a system or line</span> to trace only what connects to it; labels appear on hover. Click a line to drill into its flows.</p></> : <><p><span className="font-semibold text-blue-700">Blue</span> applications · <span className="font-semibold text-violet-700">Violet</span> external systems</p><p className="mt-0.5">Showing the scanner-computed technical landscape until business topology is available.</p></>}</div>
    <div className="absolute right-3 bottom-3 z-10 rounded-lg border border-slate-200 bg-white/95 px-3 py-2 text-[10px] text-slate-600 shadow-sm">{hasBusinessTopology ? <>{landscape.business_topology!.systems.length} organization systems · {landscape.business_topology!.data_streams.length} data streams</> : <>{summaryCount(landscape.summary, "application_count", landscape.applications.length)} applications · {summaryCount(landscape.summary, "system_count", landscape.systems.length)} systems · {summaryCount(landscape.summary, "edge_count", landscape.edges.length)} relationships</>}</div>
    <ReactFlow<Node<LandscapeNodeData>, Edge<LandscapeEdgeData>> nodes={displayNodes} edges={displayEdges} nodeTypes={nodeTypes} onNodesChange={onNodesChange} onEdgesChange={onEdgesChange} onNodeClick={handleNodeClick} onNodeDoubleClick={doubleClickNode} onNodeMouseEnter={onNodeEnter} onNodeMouseLeave={clearFocus} onEdgeClick={handleEdgeClick} onEdgeDoubleClick={doubleClickEdge} onEdgeMouseEnter={onEdgeEnter} onEdgeMouseLeave={clearFocus} onInit={setInstance} fitView minZoom={0.05} maxZoom={4} panActivationKeyCode={null} onlyRenderVisibleElements proOptions={{ hideAttribution: true }} className="bg-background"><Background variant={BackgroundVariant.Dots} gap={24} size={0.8} color="#cbd5e1" className="opacity-20" /><Controls showInteractive={false} className="!bg-card !border !shadow-sm" /><MiniMap nodeStrokeWidth={2} className="!bg-card !border !shadow-sm" nodeColor={(node) => node.type === "businessSystem" ? "#0284c7" : node.type === "landscapeApplication" ? "#2563eb" : "#7c3aed"} /></ReactFlow>
    {selection && <LandscapeEvidencePanel landscape={landscape} selection={selection} onClose={() => setSelection(null)} onExplore={explore} onViewDesigns={onViewDesigns} />}
    {showDiagnostics && <LandscapeDiagnosticsDrawer diagnostics={flow.diagnostics} onClose={() => setShowDiagnostics(false)} />}
  </div>;
}
