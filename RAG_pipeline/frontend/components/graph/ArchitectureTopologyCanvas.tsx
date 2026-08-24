"use client";

import { memo, useCallback, useEffect, useMemo, useState } from "react";
import {
  Background,
  BackgroundVariant,
  Controls,
  Handle,
  MarkerType,
  Position,
  ReactFlow,
  type Edge,
  type Node,
  type NodeMouseHandler,
  useEdgesState,
  useNodesState,
  type ReactFlowInstance,
} from "@xyflow/react";
import { ChevronRight, Database, Network, Search, X } from "lucide-react";
import {
  architectureFlowsForPair,
  architectureRelationshipPairs,
  normalizeArchitectureL1,
  type ArchitectureColumn,
  type ArchitectureFlowProjection,
  type ArchitectureL1ViewModel,
  type ArchitectureNode,
  type ArchitecturePairRelationship,
} from "@/lib/architectureViewModel";
import { architectureColumnRank, layoutArchitectureNodes } from "@/lib/architectureLayout";
import { assistantRoleShadow } from "@/lib/nodeStyles";
import type { BusinessTopology, ProjectLandscapeResponse } from "@/types/landscape";
import type { AssistantCanvasEmphasis, AssistantFocusRequest, CopilotViewContext } from "@/types/copilot";
import { aggregatedRelationshipId } from "@/lib/landscapeTransform";

interface ArchitectureNodeData extends Record<string, unknown> {
  architectureNode: ArchitectureNode;
  column: ArchitectureColumn;
  selected: boolean;
  dimmed: boolean;
  incomingPorts: string[];
  outgoingPorts: string[];
  assistantVisualRole?: string;
}

interface ArchitectureEdgeData extends Record<string, unknown> {
  pair: ArchitecturePairRelationship;
}

const NODE_WIDTH = 244;
const NODE_HEIGHT = 116;

const COLUMN_LABEL: Record<ArchitectureColumn, string> = {
  upstream: "Upstream / inputs",
  anchor: "Core",
  downstream: "Downstream / execution",
  secondary: "Secondary consumers",
  supporting: "Supporting systems",
  integration: "Integration",
};

function statusLabel(status: string): string {
  if (status === "confirmed") return "Confirmed";
  if (status === "inferred") return "Inferred";
  if (status === "proposed") return "Proposed";
  return "Mixed evidence";
}

function statusClasses(status: string): string {
  if (status === "confirmed") return "bg-emerald-50 text-emerald-700";
  if (status === "inferred") return "bg-violet-50 text-violet-700";
  return "bg-amber-50 text-amber-700";
}

function portTop(index: number, count: number): string {
  return `${((index + 1) / (count + 1)) * 100}%`;
}

function ArchitectureSystemNodeComponent({ data }: { data: ArchitectureNodeData }) {
  const node = data.architectureNode;
  const anchor = node.column === "anchor";
  return (
    <div
      data-assistant-role={data.assistantVisualRole}
      style={{ boxShadow: assistantRoleShadow(data.assistantVisualRole) }}
      className={`relative h-[116px] w-[244px] rounded-2xl border px-4 py-3 shadow-sm transition-all duration-200 ${
        anchor
          ? "border-slate-900 bg-slate-900 text-white shadow-lg"
          : data.selected && !data.assistantVisualRole
            ? "border-blue-500 bg-white ring-4 ring-blue-100"
            : data.assistantVisualRole
              ? "border-blue-500 bg-white"
            : "border-slate-200 bg-white text-slate-900"
      } ${data.dimmed ? "opacity-20" : "opacity-100"}`}
      aria-label={`${COLUMN_LABEL[node.column]} system ${node.label}`}
    >
      {data.incomingPorts.map((id, index) => (
        <Handle
          key={id}
          id={id}
          type="target"
          position={Position.Left}
          style={{ top: portTop(index, data.incomingPorts.length) }}
          className="!h-2 !w-2 !border-2 !border-white !bg-blue-500"
        />
      ))}
      {data.outgoingPorts.map((id, index) => (
        <Handle
          key={id}
          id={id}
          type="source"
          position={Position.Right}
          style={{ top: portTop(index, data.outgoingPorts.length) }}
          className="!h-2 !w-2 !border-2 !border-white !bg-blue-500"
        />
      ))}
      <p className={`text-[9px] font-bold uppercase tracking-[0.14em] ${anchor ? "text-blue-200" : "text-blue-700"}`}>
        {COLUMN_LABEL[node.column]}
      </p>
      <h3 className="mt-1 truncate text-base font-bold" title={node.label}>{node.label}</h3>
      <p className={`mt-1 line-clamp-2 text-[10px] leading-snug ${anchor ? "text-slate-200" : "text-slate-500"}`} title={node.description}>
        {node.description}
      </p>
      <span className={`absolute bottom-3 left-4 rounded px-1.5 py-0.5 text-[9px] font-semibold ${anchor ? "bg-white/10 text-white" : statusClasses(node.status)}`}>
        {statusLabel(node.status)}
      </span>
    </div>
  );
}

const ArchitectureSystemNode = memo(ArchitectureSystemNodeComponent);
const nodeTypes = { architectureSystem: ArchitectureSystemNode };

function pairNodeOrientation(
  pair: ArchitecturePairRelationship,
  nodesById: Map<string, ArchitectureNode>,
): { source: string; target: string } {
  const first = nodesById.get(pair.first);
  const second = nodesById.get(pair.second);
  if (!first || !second) return { source: pair.first, target: pair.second };
  const rankDifference = architectureColumnRank(first.column) - architectureColumnRank(second.column);
  if (rankDifference < 0) return { source: first.id, target: second.id };
  if (rankDifference > 0) return { source: second.id, target: first.id };
  return first.label.localeCompare(second.label) <= 0
    ? { source: first.id, target: second.id }
    : { source: second.id, target: first.id };
}

function buildFlow(
  model: ArchitectureL1ViewModel,
  pairs: ArchitecturePairRelationship[],
): { nodes: Node<ArchitectureNodeData>[]; edges: Edge<ArchitectureEdgeData>[] } {
  const visibleNodes = model.nodes.filter((node) => node.column !== "integration");
  const nodesById = new Map(visibleNodes.map((node) => [node.id, node]));
  const visiblePairs = pairs.filter((pair) => nodesById.has(pair.first) && nodesById.has(pair.second));
  const incoming = new Map<string, string[]>();
  const outgoing = new Map<string, string[]>();
  const orientations = new Map<string, { source: string; target: string }>();
  for (const pair of visiblePairs) {
    const orientation = pairNodeOrientation(pair, nodesById);
    orientations.set(pair.id, orientation);
    outgoing.set(orientation.source, [...(outgoing.get(orientation.source) ?? []), `out:${pair.id}`]);
    incoming.set(orientation.target, [...(incoming.get(orientation.target) ?? []), `in:${pair.id}`]);
  }
  const nodes: Node<ArchitectureNodeData>[] = visibleNodes.map((architectureNode) => ({
    id: architectureNode.id,
    type: "architectureSystem",
    position: { x: 0, y: 0 },
    width: NODE_WIDTH,
    height: NODE_HEIGHT,
    data: {
      architectureNode,
      column: architectureNode.column,
      selected: false,
      dimmed: false,
      incomingPorts: (incoming.get(architectureNode.id) ?? []).sort(),
      outgoingPorts: (outgoing.get(architectureNode.id) ?? []).sort(),
    },
  }));
  const edges: Edge<ArchitectureEdgeData>[] = visiblePairs.map((pair) => {
    const orientation = orientations.get(pair.id)!;
    const bidirectional = pair.directions.length > 1;
    return {
      id: pair.id,
      source: orientation.source,
      target: orientation.target,
      sourceHandle: `out:${pair.id}`,
      targetHandle: `in:${pair.id}`,
      type: "smoothstep",
      pathOptions: { borderRadius: 12, offset: 28 },
      markerEnd: { type: MarkerType.ArrowClosed, color: "#64748b", width: 15, height: 15 },
      markerStart: bidirectional ? { type: MarkerType.ArrowClosed, color: "#64748b", width: 15, height: 15 } : undefined,
      style: {
        stroke: pair.status === "confirmed" ? "#64748b" : pair.status === "inferred" ? "#7c3aed" : "#94a3b8",
        strokeWidth: 2,
        strokeDasharray: pair.status === "confirmed" ? undefined : pair.status === "inferred" ? "7 5" : "2 6",
        cursor: "pointer",
      },
      data: { pair },
    };
  });
  return { nodes, edges };
}

export function ArchitectureTopologyCanvas({
  landscape,
  onOpenFlow,
  assistantEmphasis,
  assistantFocusRequest,
  onContextChange,
}: {
  landscape: ProjectLandscapeResponse;
  onOpenFlow: (streamId: string) => void;
  assistantEmphasis?: AssistantCanvasEmphasis | null;
  assistantFocusRequest?: AssistantFocusRequest | null;
  onContextChange?: (context: CopilotViewContext) => void;
}) {
  const topology = landscape.business_topology!;
  const model = useMemo(() => normalizeArchitectureL1(topology), [topology]);
  const pairs = useMemo(() => architectureRelationshipPairs(model), [model]);
  const baseFlow = useMemo(() => buildFlow(model, pairs), [model, pairs]);
  const [nodes, setNodes, onNodesChange] = useNodesState(baseFlow.nodes);
  const [edges, setEdges, onEdgesChange] = useEdgesState(baseFlow.edges);
  const [instance, setInstance] = useState<ReactFlowInstance<Node<ArchitectureNodeData>, Edge<ArchitectureEdgeData>> | null>(null);
  const [query, setQuery] = useState("");
  const [selectedSystemId, setSelectedSystemId] = useState<string | null>(null);
  const [selectedPairId, setSelectedPairId] = useState<string | null>(null);
  const [selectedFlowId, setSelectedFlowId] = useState<string | null>(null);

  const nodesById = useMemo(() => new Map(model.nodes.map((node) => [node.id, node])), [model.nodes]);
  const selectedPair = useMemo(() => pairs.find((pair) => pair.id === selectedPairId) ?? null, [pairs, selectedPairId]);
  const pairFlows = useMemo(() => selectedPair ? architectureFlowsForPair(topology, selectedPair) : [], [topology, selectedPair]);
  const selectedFlow = useMemo(() => pairFlows.find((flow) => flow.id === selectedFlowId) ?? null, [pairFlows, selectedFlowId]);
  const systemPairs = useMemo(
    () => selectedSystemId ? pairs.filter((pair) => pair.first === selectedSystemId || pair.second === selectedSystemId) : [],
    [pairs, selectedSystemId],
  );

  useEffect(() => {
    let cancelled = false;
    setEdges(baseFlow.edges);
    void layoutArchitectureNodes(baseFlow.nodes, baseFlow.edges).then((laidOut) => {
      if (cancelled) return;
      setNodes(laidOut);
      window.setTimeout(() => void instance?.fitView({ padding: 0.16, duration: 450, minZoom: 0.35, maxZoom: 1.2 }), 0);
    });
    return () => { cancelled = true; };
  }, [baseFlow, instance, setEdges, setNodes]);

  useEffect(() => {
    if (!assistantFocusRequest || !instance) return;
    const focusNodes = nodes.filter((node) => assistantFocusRequest.nodeIds.includes(node.id));
    if (focusNodes.length > 0) void instance.fitView({ nodes: focusNodes, padding: 0.3, duration: 450 });
  }, [assistantFocusRequest, instance, nodes]);

  useEffect(() => {
    const selectedEntity = selectedFlow
      ? { kind: "edge" as const, label: selectedFlow.name, entity_id: selectedFlow.id }
      : selectedPair
        ? {
            kind: "relationship" as const,
            label: `${nodesById.get(selectedPair.first)?.label ?? selectedPair.first} ↔ ${nodesById.get(selectedPair.second)?.label ?? selectedPair.second}`,
            entity_id: selectedPair.id,
          }
        : selectedSystemId
          ? {
              kind: "system" as const,
              label: nodesById.get(selectedSystemId)?.label ?? selectedSystemId,
              entity_id: selectedSystemId,
            }
          : undefined;
    onContextChange?.({
      selectedEntity,
      activeDetailTab: selectedFlow ? "technical_flow" : selectedPair ? "relationship" : selectedSystemId ? "system" : "landscape",
      filters: [{ name: "query", value: query || "all" }],
    });
  }, [selectedFlow, selectedPair, selectedSystemId, nodesById, query, onContextChange]);

  const activeNodeIds = useMemo(() => {
    if (selectedPair) return new Set([selectedPair.first, selectedPair.second]);
    if (selectedSystemId) return new Set([
      selectedSystemId,
      ...systemPairs.flatMap((pair) => [pair.first, pair.second]),
    ]);
    return null;
  }, [selectedPair, selectedSystemId, systemPairs]);

  const displayedNodes = useMemo(() => {
    const normalizedQuery = query.trim().toLocaleLowerCase();
    return nodes.map((node) => {
      const architectureNode = node.data.architectureNode;
      const queryDimmed = Boolean(normalizedQuery) && !`${architectureNode.label} ${architectureNode.description}`.toLocaleLowerCase().includes(normalizedQuery);
      const focusDimmed = activeNodeIds ? !activeNodeIds.has(node.id) : false;
      const assistantRole = assistantEmphasis?.nodeRoles.get(node.id);
      const assistantActive = Boolean(assistantEmphasis?.nodeRoles.size || assistantEmphasis?.edgeRoles.size);
      return {
        ...node,
        data: {
          ...node.data,
          selected: node.id === selectedSystemId || Boolean(selectedPair && (node.id === selectedPair.first || node.id === selectedPair.second)),
          dimmed: queryDimmed || focusDimmed || (!activeNodeIds && assistantActive && !assistantRole),
          assistantVisualRole: assistantRole,
        },
      };
    });
  }, [nodes, query, activeNodeIds, selectedSystemId, selectedPair, assistantEmphasis]);

  const displayedEdges = useMemo(() => edges.map((edge) => {
    const pair = edge.data!.pair;
    const active = selectedPair
      ? pair.id === selectedPair.id
      : selectedSystemId
        ? pair.first === selectedSystemId || pair.second === selectedSystemId
        : true;
    const assistantRole = assistantEmphasis?.edgeRoles.get(pair.id)
      ?? assistantEmphasis?.edgeRoles.get(aggregatedRelationshipId(pair.first, pair.second))
      ?? assistantEmphasis?.edgeRoles.get(aggregatedRelationshipId(pair.second, pair.first));
    const assistantActive = Boolean(assistantEmphasis?.nodeRoles.size || assistantEmphasis?.edgeRoles.size);
    return {
      ...edge,
      style: {
        ...edge.style,
        opacity: active && (!assistantActive || Boolean(assistantRole)) ? 1 : 0.08,
        strokeWidth: selectedPair?.id === pair.id || assistantRole === "primary" ? 3.5 : assistantRole ? 2.8 : 2,
        transition: "opacity 180ms ease, stroke-width 180ms ease",
      },
      data: { pair, assistantVisualRole: assistantRole },
    };
  }), [edges, selectedPair, selectedSystemId, assistantEmphasis]);

  const selectSystem: NodeMouseHandler<Node<ArchitectureNodeData>> = useCallback((_event, node) => {
    setSelectedSystemId(node.id);
    setSelectedPairId(null);
    setSelectedFlowId(null);
  }, []);

  const selectPair = useCallback((pair: ArchitecturePairRelationship) => {
    setSelectedPairId(pair.id);
    setSelectedFlowId(null);
    setSelectedSystemId(null);
  }, []);

  const clearSelection = useCallback(() => {
    setSelectedSystemId(null);
    setSelectedPairId(null);
    setSelectedFlowId(null);
  }, []);

  const selectFlow = useCallback((flow: ArchitectureFlowProjection) => {
    setSelectedFlowId(flow.id);
  }, []);

  return (
    <div className="absolute inset-0 overflow-hidden bg-slate-50" data-testid="architecture-topology-canvas">
      <header className="absolute inset-x-0 top-0 z-30 border-b border-slate-200 bg-white/95 px-4 py-3 shadow-sm backdrop-blur">
        <div className="flex flex-wrap items-center gap-3">
          <div className="flex min-w-0 flex-1 items-center gap-2">
            <Network className="h-5 w-5 shrink-0 text-blue-700" />
            <div className="min-w-0">
              <h1 className="truncate text-sm font-bold text-slate-900">Werkorder architecture</h1>
              <p className="text-[10px] text-slate-500">Interactive company landscape</p>
            </div>
          </div>
          <label className="relative">
            <Search className="pointer-events-none absolute left-2.5 top-2 h-3.5 w-3.5 text-slate-400" />
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Search systems"
              aria-label="Search architecture systems"
              className="h-8 w-52 rounded-lg border border-slate-200 bg-white pl-8 pr-3 text-xs outline-none focus:border-blue-400"
            />
          </label>
          <span className="rounded-full bg-blue-50 px-2.5 py-1 text-[10px] font-bold uppercase tracking-wide text-blue-700">
            Level: {selectedFlow ? "Technical flow" : selectedPair ? "Relationship" : selectedSystemId ? "System focus" : "Landscape"}
          </span>
        </div>
        <nav className="mt-2 flex min-h-5 items-center gap-1 overflow-hidden text-[11px] text-slate-500" aria-label="Architecture breadcrumb">
          <button type="button" onClick={clearSelection} className="font-semibold text-slate-700 hover:text-blue-700">Landscape</button>
          {selectedSystemId && <><ChevronRight className="h-3 w-3" /><span className="truncate font-semibold text-slate-800">{nodesById.get(selectedSystemId)?.label}</span></>}
          {selectedPair && <><ChevronRight className="h-3 w-3" /><button type="button" onClick={() => setSelectedFlowId(null)} className="truncate font-semibold text-slate-800 hover:text-blue-700">{nodesById.get(selectedPair.first)?.label} ⇄ {nodesById.get(selectedPair.second)?.label}</button></>}
          {selectedFlow && <><ChevronRight className="h-3 w-3" /><span className="truncate font-semibold text-slate-800">{selectedFlow.businessObjects[0] ?? selectedFlow.name}</span></>}
        </nav>
      </header>

      <div className="absolute inset-x-0 top-[82px] z-10 grid grid-cols-4 border-b border-slate-200 bg-white/75 px-16 py-1.5 text-center text-[9px] font-bold uppercase tracking-[0.16em] text-slate-400 backdrop-blur">
        <span>Upstream / inputs</span><span>Core</span><span>Downstream / execution</span><span>Secondary</span>
      </div>

      <div className={`absolute bottom-0 left-0 top-[105px] transition-[right] duration-200 ${selectedSystemId || selectedPair ? "right-[390px]" : "right-0"}`}>
        <ReactFlow<Node<ArchitectureNodeData>, Edge<ArchitectureEdgeData>>
          nodes={displayedNodes}
          edges={displayedEdges}
          nodeTypes={nodeTypes}
          onNodesChange={onNodesChange}
          onEdgesChange={onEdgesChange}
          onNodeClick={selectSystem}
          onEdgeClick={(_event, edge) => selectPair((edge.data as ArchitectureEdgeData).pair)}
          onPaneClick={clearSelection}
          onInit={setInstance}
          nodesDraggable={false}
          nodesConnectable={false}
          elementsSelectable
          fitView
          minZoom={0.25}
          maxZoom={1.8}
          panActivationKeyCode={null}
          proOptions={{ hideAttribution: true }}
          className="bg-slate-50"
        >
          <Background variant={BackgroundVariant.Dots} gap={28} size={0.7} color="#cbd5e1" className="opacity-20" />
          <Controls showInteractive={false} className="!border !border-slate-200 !bg-white !shadow-sm" />
        </ReactFlow>
      </div>

      {(selectedSystemId || selectedPair) && (
        <ArchitectureDetailsPanel
          topology={topology}
          model={model}
          selectedSystemId={selectedSystemId}
          systemPairs={systemPairs}
          selectedPair={selectedPair}
          pairFlows={pairFlows}
          selectedFlow={selectedFlow}
          onClose={clearSelection}
          onSelectPair={selectPair}
          onSelectFlow={selectFlow}
          onBackToPair={() => setSelectedFlowId(null)}
          onOpenFlow={onOpenFlow}
        />
      )}
    </div>
  );
}

function ArchitectureDetailsPanel({
  topology,
  model,
  selectedSystemId,
  systemPairs,
  selectedPair,
  pairFlows,
  selectedFlow,
  onClose,
  onSelectPair,
  onSelectFlow,
  onBackToPair,
  onOpenFlow,
}: {
  topology: BusinessTopology;
  model: ArchitectureL1ViewModel;
  selectedSystemId: string | null;
  systemPairs: ArchitecturePairRelationship[];
  selectedPair: ArchitecturePairRelationship | null;
  pairFlows: ArchitectureFlowProjection[];
  selectedFlow: ArchitectureFlowProjection | null;
  onClose: () => void;
  onSelectPair: (pair: ArchitecturePairRelationship) => void;
  onSelectFlow: (flow: ArchitectureFlowProjection) => void;
  onBackToPair: () => void;
  onOpenFlow: (streamId: string) => void;
}) {
  const nodesById = new Map(model.nodes.map((node) => [node.id, node]));
  const systemsById = new Map(topology.systems.map((system) => [system.id, system]));
  if (selectedFlow) {
    return (
      <aside className="absolute bottom-0 right-0 top-[82px] z-20 w-[390px] overflow-y-auto border-l border-slate-200 bg-white shadow-xl" aria-label="Technical flow details">
        <PanelHeader eyebrow="Technical delivery" title={selectedFlow.businessObjects[0] ?? selectedFlow.name} onClose={onClose} />
        <div className="space-y-5 p-4">
          <button type="button" onClick={onBackToPair} className="text-[11px] font-semibold text-blue-700 hover:text-blue-900">← Back to exchanged data</button>
          <div className="grid grid-cols-[1fr_auto_1fr] items-center gap-2 rounded-xl bg-slate-50 p-3 text-center">
            <SystemMiniCard name={systemsById.get(selectedFlow.sourceSystemId)?.name ?? selectedFlow.sourceSystemId} />
            <ChevronRight className="h-4 w-4 text-slate-400" />
            <SystemMiniCard name={systemsById.get(selectedFlow.destinationSystemId)?.name ?? selectedFlow.destinationSystemId} />
          </div>
          <Section title="Purpose"><p className="text-xs leading-relaxed text-slate-600">{selectedFlow.summary || "Business purpose has not been established."}</p></Section>
          <div className="flex flex-wrap gap-1.5">
            <StatusBadge status={selectedFlow.status} />
            {selectedFlow.protocols.map((protocol) => <Tag key={protocol}>{protocol}</Tag>)}
            {selectedFlow.integrationSolutions.map((solution) => <Tag key={solution}>{solution}</Tag>)}
          </div>
          <Section title="Delivery path">
            {selectedFlow.deliveryPath ? (
              <ol className="space-y-0">
                {selectedFlow.deliveryPath.segments.map((segment, index) => (
                  <li key={segment.id} className="relative flex gap-3 pb-4 last:pb-0">
                    {index < selectedFlow.deliveryPath!.segments.length - 1 && <span className="absolute left-[13px] top-7 h-[calc(100%-20px)] w-px bg-slate-200" />}
                    <span className="relative z-10 flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-blue-50 text-[10px] font-bold text-blue-700">{segment.sequence}</span>
                    <div className="min-w-0 rounded-lg border border-slate-200 bg-white p-2.5">
                      <p className="text-xs font-semibold text-slate-800">{segment.name}</p>
                      <p className="mt-0.5 text-[10px] text-slate-500">{[segment.protocol, segment.component, segment.resource].filter(Boolean).join(" · ")}</p>
                    </div>
                  </li>
                ))}
              </ol>
            ) : <EmptyFact text="An ordered delivery path has not yet been structured for this flow." />}
          </Section>
          {selectedFlow.transformations.length > 0 && <Section title="Known transformations">
            <div className="space-y-2">{selectedFlow.transformations.flatMap((transformation) => transformation.mappings.map((mapping) => (
              <div key={`${transformation.id}:${mapping.source}:${mapping.target}`} className="grid grid-cols-[1fr_auto_1fr] items-center gap-2 rounded-lg bg-slate-50 p-2 text-[10px]">
                <code className="truncate text-slate-700">{mapping.source}</code><ChevronRight className="h-3 w-3 text-slate-400" /><code className="truncate text-slate-700">{mapping.target}</code>
              </div>
            )))}</div>
          </Section>}
          <Section title="Implementation & evidence">
            <div className="grid grid-cols-2 gap-2"><Fact label="Artifacts" value={`${selectedFlow.artifactIds.length}`} /><Fact label="Evidence references" value={`${selectedFlow.evidenceIds.length}`} /></div>
          </Section>
          <button type="button" onClick={() => onOpenFlow(selectedFlow.id)} className="w-full rounded-lg bg-slate-900 px-3 py-2 text-xs font-semibold text-white hover:bg-slate-700">Open implementation details</button>
        </div>
      </aside>
    );
  }

  if (selectedPair) {
    const first = nodesById.get(selectedPair.first);
    const second = nodesById.get(selectedPair.second);
    return (
      <aside className="absolute bottom-0 right-0 top-[82px] z-20 w-[390px] overflow-y-auto border-l border-slate-200 bg-white shadow-xl" aria-label="Relationship details">
        <PanelHeader eyebrow="Business relationship" title={`${first?.label ?? selectedPair.first} ⇄ ${second?.label ?? selectedPair.second}`} onClose={onClose} />
        <div className="space-y-4 p-4">
          <div className="grid grid-cols-3 gap-2"><Fact label="Flows" value={`${pairFlows.length}`} /><Fact label="Objects" value={`${selectedPair.businessObjects.length}`} /><Fact label="Status" value={statusLabel(selectedPair.status)} /></div>
          <Section title="Data exchanged">
            <div className="space-y-2">
              {pairFlows.map((flow) => (
                <button key={flow.id} type="button" onClick={() => onSelectFlow(flow)} className="block w-full rounded-xl border border-slate-200 bg-white p-3 text-left transition hover:border-blue-300 hover:bg-blue-50/40">
                  <div className="flex items-start justify-between gap-3"><div className="min-w-0"><p className="truncate text-xs font-bold text-slate-800">{flow.businessObjects.join(", ") || flow.name}</p><p className="mt-0.5 text-[10px] text-slate-500">{systemsById.get(flow.sourceSystemId)?.name} → {systemsById.get(flow.destinationSystemId)?.name}</p></div><ChevronRight className="mt-0.5 h-4 w-4 shrink-0 text-slate-400" /></div>
                  <p className="mt-2 line-clamp-2 text-[10px] leading-relaxed text-slate-500">{flow.summary}</p>
                  <div className="mt-2 flex flex-wrap gap-1">{flow.integrationSolutions.map((item) => <Tag key={item}>{item}</Tag>)}<StatusBadge status={flow.status} /></div>
                </button>
              ))}
            </div>
          </Section>
        </div>
      </aside>
    );
  }

  const system = selectedSystemId ? nodesById.get(selectedSystemId) : null;
  return (
    <aside className="absolute bottom-0 right-0 top-[82px] z-20 w-[390px] overflow-y-auto border-l border-slate-200 bg-white shadow-xl" aria-label="System focus details">
      <PanelHeader eyebrow="System focus" title={system?.label ?? "System"} onClose={onClose} />
      <div className="space-y-4 p-4">
        <p className="text-xs leading-relaxed text-slate-600">{system?.description}</p>
        <div className="grid grid-cols-2 gap-2"><Fact label="Relationships" value={`${systemPairs.length}`} /><Fact label="Role" value={system ? COLUMN_LABEL[system.column] : ""} /></div>
        <Section title="Connected architecture">
          <div className="space-y-2">{systemPairs.map((pair) => {
            const neighbourId = pair.first === selectedSystemId ? pair.second : pair.first;
            return <button key={pair.id} type="button" onClick={() => onSelectPair(pair)} className="flex w-full items-center justify-between gap-3 rounded-xl border border-slate-200 p-3 text-left hover:border-blue-300 hover:bg-blue-50/40"><div className="min-w-0"><p className="truncate text-xs font-bold text-slate-800">{nodesById.get(neighbourId)?.label ?? neighbourId}</p><p className="mt-1 truncate text-[10px] text-slate-500">{pair.businessObjects.slice(0, 3).join(" · ")}{pair.businessObjects.length > 3 ? ` +${pair.businessObjects.length - 3}` : ""}</p></div><span className="shrink-0 rounded bg-slate-100 px-2 py-1 text-[10px] font-semibold text-slate-600">{pair.streamIds.length} flow{pair.streamIds.length === 1 ? "" : "s"}</span></button>;
          })}</div>
        </Section>
      </div>
    </aside>
  );
}

function PanelHeader({ eyebrow, title, onClose }: { eyebrow: string; title: string; onClose: () => void }) {
  return <div className="sticky top-0 z-10 flex items-start gap-3 border-b border-slate-200 bg-white/95 p-4 backdrop-blur"><div className="min-w-0 flex-1"><p className="text-[9px] font-bold uppercase tracking-[0.16em] text-blue-700">{eyebrow}</p><h2 className="mt-1 truncate text-sm font-bold text-slate-900" title={title}>{title}</h2></div><button type="button" onClick={onClose} aria-label="Close details" className="rounded p-1 text-slate-400 hover:bg-slate-100"><X className="h-4 w-4" /></button></div>;
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return <section><h3 className="mb-2 text-[10px] font-bold uppercase tracking-[0.14em] text-slate-500">{title}</h3>{children}</section>;
}

function Fact({ label, value }: { label: string; value: string }) {
  return <div className="rounded-lg bg-slate-50 p-2"><p className="text-[9px] font-semibold uppercase text-slate-400">{label}</p><p className="mt-0.5 truncate text-xs font-semibold text-slate-700" title={value}>{value}</p></div>;
}

function Tag({ children }: { children: React.ReactNode }) {
  return <span className="rounded bg-slate-100 px-1.5 py-0.5 text-[9px] font-semibold text-slate-600">{children}</span>;
}

function StatusBadge({ status }: { status: string }) {
  return <span className={`rounded px-1.5 py-0.5 text-[9px] font-semibold ${statusClasses(status)}`}>{statusLabel(status)}</span>;
}

function EmptyFact({ text }: { text: string }) {
  return <div className="rounded-lg border border-dashed border-slate-200 bg-slate-50 p-3 text-[10px] leading-relaxed text-slate-500">{text}</div>;
}

function SystemMiniCard({ name }: { name: string }) {
  return <div className="min-w-0"><Database className="mx-auto h-4 w-4 text-blue-600" /><p className="mt-1 truncate text-[10px] font-bold text-slate-700" title={name}>{name}</p></div>;
}
