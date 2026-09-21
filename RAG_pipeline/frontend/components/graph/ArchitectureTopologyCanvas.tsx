"use client";

import { Fragment, memo, useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
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
  BUSINESS_RULE_KIND_LABEL,
  REACHES_TARGET_LABEL,
  STREAM_CONDITION_KIND_LABEL,
  TRANSFORMATION_KIND_LABEL,
  type ArchitectureColumn,
  type ArchitectureFlowProjection,
  type ArchitectureL1ViewModel,
  type ArchitectureNode,
  type ArchitecturePairRelationship,
  type StreamConditionKind,
  type StreamLifecycle,
} from "@/lib/architectureViewModel";
import { architectureColumnRank, layoutArchitectureNodes } from "@/lib/architectureLayout";
import { FieldPath } from "@/components/graph/FieldPath";
import { assistantRoleShadow } from "@/lib/nodeStyles";
import type { BusinessRule, BusinessSystem, BusinessTopology, DeploymentAgreement, ProjectLandscapeResponse, StreamCondition } from "@/types/landscape";
import type { AssistantCanvasEmphasis, AssistantFocusRequest, CopilotViewContext } from "@/types/copilot";
import { aggregatedRelationshipId } from "@/lib/landscapeTransform";
import { useFitOnContainerResize } from "@/hooks/useFitOnContainerResize";

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

/**
 * Claim-status ("observed" / "inferred" / "proposed") wordt niet getoond.
 *
 * Reden: op een gecureerde topologie draagt vrijwel elke claim de waarde
 * "inferred", omdat "observed" is voorbehouden aan een scanner-bewezen
 * systeem-naar-systeem-edge. Die bestaan in dit landschap niet, dus de waarde
 * onderscheidt niets en leest ten onrechte als giswerk voor uitspraken die
 * rechtstreeks uit productieconfiguratie komen. Het `confidence`-getal draagt
 * wel informatie en blijft zichtbaar.
 *
 * Zet op `true` om de badges terug te zetten.
 */
const SHOW_CLAIM_STATUS = false;

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
      {SHOW_CLAIM_STATUS && <span className={`absolute bottom-3 left-4 rounded px-1.5 py-0.5 text-[9px] font-semibold ${anchor ? "bg-white/10 text-white" : statusClasses(node.status)}`}>
        {statusLabel(node.status)}
      </span>}
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
  // The quiet default map caps how many relationships it draws. Opting in is a
  // user choice rather than a larger default, because the omission is only a
  // problem when it is invisible: the cap is announced below and reversible.
  const [showAllRelationships, setShowAllRelationships] = useState(false);
  const model = useMemo(
    () => normalizeArchitectureL1(
      topology,
      showAllRelationships ? { maxVisibleRelationships: Number.MAX_SAFE_INTEGER } : {},
    ),
    [topology, showAllRelationships],
  );
  const cappedModel = useMemo(() => normalizeArchitectureL1(topology), [topology]);
  const hiddenRelationshipCount = cappedModel.hiddenRelationshipIds.length;
  const pairs = useMemo(() => architectureRelationshipPairs(model), [model]);
  const baseFlow = useMemo(() => buildFlow(model, pairs), [model, pairs]);
  const [nodes, setNodes, onNodesChange] = useNodesState(baseFlow.nodes);
  const [edges, setEdges, onEdgesChange] = useEdgesState(baseFlow.edges);
  const [instance, setInstance] = useState<ReactFlowInstance<Node<ArchitectureNodeData>, Edge<ArchitectureEdgeData>> | null>(null);

  // The diagram follows the docked copilot: widening that panel narrows this
  // column, and without a refit the graph slides out of view on the right.
  const refit = useCallback(
    () => void instance?.fitView({ padding: 0.16, duration: 300, minZoom: 0.35, maxZoom: 1.2 }),
    [instance],
  );
  const canvasRef = useFitOnContainerResize(refit);
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
    <div ref={canvasRef} className="absolute inset-0 overflow-hidden bg-slate-50" data-testid="architecture-topology-canvas">
      <header className="absolute inset-x-0 top-0 z-30 border-b border-slate-200 bg-white/95 px-4 py-3 shadow-sm backdrop-blur">
        <div className="flex flex-wrap items-center gap-3">
          <div className="flex min-w-0 flex-1 items-center gap-2">
            <Network className="h-5 w-5 shrink-0 text-blue-700" />
            <div className="min-w-0">
              <h1 className="truncate text-sm font-bold text-slate-900">{model.title}</h1>
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
          {hiddenRelationshipCount > 0 && (
            <button
              type="button"
              onClick={() => setShowAllRelationships((previous) => !previous)}
              data-testid="toggle-hidden-relationships"
              aria-pressed={showAllRelationships}
              className={`h-8 rounded-lg border px-2.5 text-[10px] font-semibold transition ${
                showAllRelationships
                  ? "border-blue-300 bg-blue-50 text-blue-800"
                  : "border-amber-300 bg-amber-50 text-amber-800 hover:bg-amber-100"
              }`}
            >
              {showAllRelationships
                ? `Showing all ${cappedModel.relationships.length + hiddenRelationshipCount} relationships`
                : `${hiddenRelationshipCount} relationship${hiddenRelationshipCount === 1 ? "" : "s"} hidden -- show all`}
            </button>
          )}
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
      <FlowDetailsPanel
        flow={selectedFlow}
        systemsById={systemsById}
        onClose={onClose}
        onBackToPair={onBackToPair}
        onOpenFlow={onOpenFlow}
      />
    );
  }

  if (selectedPair) {
    const first = nodesById.get(selectedPair.first);
    const second = nodesById.get(selectedPair.second);
    return (
      <aside className="absolute bottom-0 right-0 top-[82px] z-20 w-[390px] overflow-y-auto border-l border-slate-200 bg-white shadow-xl" aria-label="Relationship details">
        <PanelHeader eyebrow="Business relationship" title={`${first?.label ?? selectedPair.first} ⇄ ${second?.label ?? selectedPair.second}`} onClose={onClose} />
        <div className="space-y-4 p-4">
          <div className={`grid ${SHOW_CLAIM_STATUS ? "grid-cols-3" : "grid-cols-2"} gap-2`}><Fact label="Flows" value={`${pairFlows.length}`} /><Fact label="Objects" value={`${selectedPair.businessObjects.length}`} />{SHOW_CLAIM_STATUS && <Fact label="Status" value={statusLabel(selectedPair.status)} />}</div>
          <Section title="Data exchanged">
            <div className="space-y-2">
              {pairFlows.map((flow) => (
                <button key={flow.id} type="button" data-testid={`architecture-flow:${flow.id}`} onClick={() => onSelectFlow(flow)} className="block w-full rounded-xl border border-slate-200 bg-white p-3 text-left transition hover:border-blue-300 hover:bg-blue-50/40">
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

/** How many field rows the 390px panel shows before deferring to the L2 table. */
const PANEL_FIELD_ROWS = 8;

const LIFECYCLE_LABEL: Record<StreamLifecycle, string> = {
  active: "Actief",
  duplicate: "Duplicaat",
  stopped: "Gestopt",
  dormant: "Slapend",
  reference_only: "Alleen referentie",
  unknown: "Niet vastgesteld",
};

const DIRECTION_PATTERN_LABEL: Record<ArchitectureFlowProjection["directionPattern"], string> = {
  push: "Push",
  pull: "Pull",
  hybrid: "Hybride",
  unknown: "Niet vastgesteld",
};

function lifecycleClasses(lifecycle: StreamLifecycle): string {
  if (lifecycle === "active") return "bg-emerald-50 text-emerald-700";
  if (lifecycle === "duplicate") return "bg-amber-50 text-amber-700";
  if (lifecycle === "stopped") return "bg-red-50 text-red-700";
  if (lifecycle === "reference_only") return "bg-violet-50 text-violet-700";
  return "bg-slate-100 text-slate-600";
}

function reachesClasses(reaches: ArchitectureFlowProjection["fieldRows"][number]["reachesTarget"]): string {
  if (reaches === "yes") return "bg-emerald-50 text-emerald-700";
  if (reaches === "no" || reaches === "disabled") return "bg-red-50 text-red-700";
  if (reaches === "partial") return "bg-amber-50 text-amber-700";
  return "bg-slate-100 text-slate-500";
}

function contractNumber(value: number | null | undefined): string {
  return typeof value === "number" ? `${value}` : "—";
}

/**
 * The business-readable panel for one data stream.
 *
 * Every block below the badges is conditional on the data it renders. This is
 * not cosmetic: only some projects publish the structured slots, and a heading
 * over an empty body (or worse, an "unavailable" note) reads as a finding about
 * the integration rather than an absence in the analysis.
 */
/**
 * Whether the analysed definition is the one running, in one phrase. The raw
 * enum ("supplied_behind") is a producer-side token; a client-facing panel has
 * to say which side is behind.
 */
const DEPLOYMENT_AGREEMENT_LABEL: Record<NonNullable<DeploymentAgreement["agreement"]>, string> = {
  equal: "Gelijk aan productie",
  supplied_behind: "Aangeleverd loopt achter",
  supplied_ahead: "Niet uitgerold werk",
  unknown: "Onbekend",
};

function FlowDetailsPanel({
  flow,
  systemsById,
  onClose,
  onBackToPair,
  onOpenFlow,
}: {
  flow: ArchitectureFlowProjection;
  systemsById: Map<string, BusinessSystem>;
  onClose: () => void;
  onBackToPair: () => void;
  onOpenFlow: (streamId: string) => void;
}) {
  const contract = flow.contractWidth;
  const hasContractNumbers = Boolean(
    contract && [contract.offered, contract.mapped, contract.written].some((value) => typeof value === "number"),
  );
  const showContract = hasContractNumbers || Boolean(flow.verdict) || Boolean(flow.keyForm) || Boolean(contract?.note);
  // A `deployment` object whose every field is still at its default carries no
  // fact. Gating on the rendered STRING, not on the object, is what stops the
  // heading appearing above an empty paragraph.
  const deploymentSummary = flow.deployment
    ? [
      flow.deployment.supplied_version && `Aangeleverd ${flow.deployment.supplied_version}`,
      flow.deployment.deployed_version && `In productie ${flow.deployment.deployed_version}`,
      flow.deployment.agreement !== "unknown" ? flow.deployment.agreement.replace(/_/g, " ") : "",
      flow.deployment.note,
    ].filter(Boolean).join(" · ")
    : "";
  const showRuntime = flow.runtimeMetrics.length > 0 || Boolean(deploymentSummary);
  const conditionGroups = groupConditions(flow.conditions);
  // Only short tokens belong in the badge row. `mechanism` is a paragraph on
  // most streams, so it moves into the Techniek disclosure: rendered as a badge
  // it pushed every real fact below the fold.
  const badges: Array<{ key: string; node: ReactNode }> = [];
  if (flow.lifecycle !== "unknown") {
    badges.push({ key: "lifecycle", node: <span className={`rounded px-1.5 py-0.5 text-[9px] font-semibold ${lifecycleClasses(flow.lifecycle)}`}>{LIFECYCLE_LABEL[flow.lifecycle]}</span> });
  }
  if (flow.directionPattern !== "unknown") {
    badges.push({ key: "direction", node: <Tag>{DIRECTION_PATTERN_LABEL[flow.directionPattern]}</Tag> });
  }
  if (flow.generation) badges.push({ key: "generation", node: <Tag>{flow.generation}</Tag> });
  for (const protocol of flow.protocols) badges.push({ key: `protocol:${protocol}`, node: <Tag>{protocol}</Tag> });
  for (const solution of flow.integrationSolutions) badges.push({ key: `solution:${solution}`, node: <Tag>{solution}</Tag> });

  // The strip is the whole point of the redesign: at most a handful of
  // label/value lines, each fact appearing EXACTLY once in the panel. Anything
  // that needs a sentence lives behind a disclosure.
  const contractSummary = hasContractNumbers
    ? [contract?.offered, contract?.mapped, contract?.written].map(contractNumber).join(" \u2192 ")
    : "";
  const headlineMetric = flow.runtimeMetrics[0];
  const keyFacts = [
    contractSummary && { label: "Contract", value: contractSummary, hint: "aangeboden \u2192 gemapt \u2192 weggeschreven" },
    headlineMetric && { label: headlineMetric.label, value: [headlineMetric.value, headlineMetric.unit].filter(Boolean).join(" "), hint: headlineMetric.window },
    flow.keyForm && { label: "Sleutel", value: flow.keyForm, hint: "" },
    flow.deployment && flow.deployment.agreement !== "unknown"
      ? { label: "Versie", value: DEPLOYMENT_AGREEMENT_LABEL[flow.deployment.agreement], hint: [flow.deployment.supplied_version, flow.deployment.deployed_version].filter(Boolean).join(" aangeleverd / in productie ") }
      : null,
  ].filter((item): item is { label: string; value: string; hint: string } => Boolean(item && item.value));

  // Only the prose the new slots introduced. Anything that was already a badge
  // stays a badge, so a stream published before these fields renders exactly as
  // it did.
  const techniek = [
    flow.mechanism && { label: "Mechanisme", value: flow.mechanism },
    contract?.note && { label: "Contractbreedte", value: contract.note },
    flow.deployment?.note && { label: "Versie", value: flow.deployment.note },
  ].filter((item): item is { label: string; value: string } => Boolean(item));

  const restMetrics = flow.runtimeMetrics.slice(1);

  return (
    <aside className="absolute bottom-0 right-0 top-[82px] z-20 w-[390px] overflow-y-auto border-l border-slate-200 bg-white shadow-xl" aria-label="Technical flow details">
      <PanelHeader eyebrow="Technical delivery" title={flow.businessObjects[0] ?? flow.name} onClose={onClose} />
      <div className="space-y-3 p-4">
        <button type="button" onClick={onBackToPair} className="text-[11px] font-semibold text-blue-700 hover:text-blue-900">&#8592; Back to exchanged data</button>
        <div className="grid grid-cols-[1fr_auto_1fr] items-center gap-2 rounded-xl bg-slate-50 p-3 text-center">
          <SystemMiniCard name={systemsById.get(flow.sourceSystemId)?.name ?? flow.sourceSystemId} />
          <ChevronRight className="h-4 w-4 text-slate-400" />
          <SystemMiniCard name={systemsById.get(flow.destinationSystemId)?.name ?? flow.destinationSystemId} />
        </div>

        {badges.length > 0 && (
          <div className="flex flex-wrap gap-1.5">
            <StatusBadge status={flow.status} />
            {badges.map((badge) => <Fragment key={badge.key}>{badge.node}</Fragment>)}
          </div>
        )}

        {flow.summary && (
          <p className="line-clamp-3 text-xs leading-relaxed text-slate-600" title={flow.summary}>{flow.summary}</p>
        )}

        {keyFacts.length > 0 && (
          <dl className="divide-y divide-slate-100 rounded-xl border border-slate-200">
            {keyFacts.map((fact) => (
              <div key={fact.label} className="flex items-baseline justify-between gap-3 px-2.5 py-1.5">
                <dt className="shrink-0 text-[9px] font-bold uppercase tracking-wide text-slate-400">{fact.label}</dt>
                <dd className="min-w-0 text-right">
                  <span className="block truncate text-[11px] font-semibold text-slate-800" title={fact.value}>{fact.value}</span>
                  {fact.hint && <span className="block truncate text-[9px] text-slate-400" title={fact.hint}>{fact.hint}</span>}
                </dd>
              </div>
            ))}
          </dl>
        )}

        {flow.verdict && (
          <div className="rounded-lg border border-blue-100 bg-blue-50/60 p-2.5">
            <p className="text-[9px] font-bold uppercase tracking-wide text-blue-700">Verdict</p>
            <p className="mt-1 line-clamp-3 text-[11px] leading-relaxed text-slate-700" title={flow.verdict}>{flow.verdict}</p>
          </div>
        )}

        <div className="divide-y divide-slate-100 rounded-xl border border-slate-200">
          <Disclosure title="Delivery path" count={flow.deliveryPath?.segments.length}>
            {flow.deliveryPath ? (
              <ol className="space-y-0">
                {flow.deliveryPath.segments.map((segment, index) => (
                  <li key={segment.id} className="relative flex gap-3 pb-4 last:pb-0">
                    {index < flow.deliveryPath!.segments.length - 1 && <span className="absolute left-[13px] top-7 h-[calc(100%-20px)] w-px bg-slate-200" />}
                    <span className="relative z-10 flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-blue-50 text-[10px] font-bold text-blue-700">{segment.sequence}</span>
                    <div className="min-w-0 rounded-lg border border-slate-200 bg-white p-2.5">
                      <p className="text-xs font-semibold text-slate-800">{segment.name}</p>
                      <p className="mt-0.5 text-[10px] text-slate-500">{[segment.protocol, segment.component, segment.resource].filter(Boolean).join(" \u00b7 ")}</p>
                    </div>
                  </li>
                ))}
              </ol>
            ) : <EmptyFact text="An ordered delivery path has not yet been structured for this flow." />}
          </Disclosure>

          {flow.fieldRows.length > 0 && (
            <Disclosure title="Velden" count={flow.fieldRows.length}>
              <MappingRows rows={flow.fieldRows} />
            </Disclosure>
          )}

          {flow.messageRows.length > 0 && (
            <Disclosure title="Berichtopbouw" count={flow.messageRows.length}>
              <p className="mb-2 text-[10px] leading-relaxed text-slate-500">
                Een berichtroute heeft geen veldafbeelding zoals een verwerkingsketen die
                heeft. Zij zet kopvelden voor routering en besturing en stelt de inhoud van
                het bericht samen. Dat staat hieronder.
              </p>
              <MappingRows rows={flow.messageRows} />
            </Disclosure>
          )}

          {flow.valueDomainGroups.length > 0 && (
            <Disclosure title="Waardetabel" count={flow.valueDomainGroups.length}>
              <div className="space-y-2">
                {flow.valueDomainGroups.map((group) => (
                  <div key={group.targetField} className="rounded-lg border border-slate-200 p-2.5">
                    <FieldPath value={group.targetField} className="block text-[10px] font-bold text-slate-800" />
                    {group.transformations.map((transformation) => (
                      <div key={transformation.id} className="mt-1.5">
                        {transformation.name && transformation.name !== group.targetField && (
                          <p className="text-[9px] font-semibold uppercase tracking-wide text-slate-400">{transformation.name}</p>
                        )}
                        <div className="mt-1 space-y-0.5">
                          {transformation.mappings.map((mapping, index) => (
                            <div key={`${transformation.id}:${index}`} className="grid grid-cols-[minmax(0,1fr)_auto_minmax(0,1fr)] items-center gap-1.5">
                              <FieldPath value={mapping.expression || mapping.source} className="text-[10px] text-slate-600" />
                              <ChevronRight className="h-3 w-3 shrink-0 text-slate-400" />
                              <FieldPath value={mapping.target} className="text-[10px] text-slate-700" />
                            </div>
                          ))}
                        </div>
                      </div>
                    ))}
                  </div>
                ))}
              </div>
            </Disclosure>
          )}

          {flow.businessRules.length > 0 && (
            <Disclosure title="Businessregels" count={flow.businessRules.length}>
              <div className="space-y-2">{flow.businessRules.map((rule) => <BusinessRuleCard key={rule.id} rule={rule} />)}</div>
            </Disclosure>
          )}

          {conditionGroups.length > 0 && (
            <Disclosure title="Voorwaarden" count={flow.conditions.length}>
              <div className="space-y-2">
                {conditionGroups.map(([kind, conditions]) => (
                  <div key={kind}>
                    <p className="text-[9px] font-bold uppercase tracking-wide text-slate-400">{STREAM_CONDITION_KIND_LABEL[kind]}</p>
                    <ul className="mt-1 space-y-1">
                      {conditions.map((condition, index) => (
                        <li key={`${kind}:${index}`} className="rounded-lg bg-slate-50 p-2 text-[10px] leading-relaxed text-slate-700">
                          {condition.statement}
                          {condition.evidence_note && <span className="mt-0.5 block text-[9px] text-slate-400">{condition.evidence_note}</span>}
                        </li>
                      ))}
                    </ul>
                  </div>
                ))}
              </div>
            </Disclosure>
          )}

          {restMetrics.length > 0 && (
            <Disclosure title="In productie" count={flow.runtimeMetrics.length}>
              <div className="grid grid-cols-2 gap-2">
                {restMetrics.map((metric) => (
                  <div key={metric.id} className="rounded-lg bg-slate-50 p-2">
                    <p className="truncate text-[9px] font-semibold uppercase text-slate-400" title={metric.label}>{metric.label}</p>
                    <p className="mt-0.5 truncate text-xs font-semibold text-slate-700">{[metric.value, metric.unit].filter(Boolean).join(" ")}</p>
                    {metric.window && <p className="mt-0.5 truncate text-[9px] text-slate-400" title={metric.window}>{metric.window}</p>}
                  </div>
                ))}
              </div>
            </Disclosure>
          )}

          {flow.unclassifiedTransformations.length > 0 && (
            <Disclosure title="Known transformations" count={flow.unclassifiedTransformations.reduce((total, item) => total + item.mappings.length, 0)}>
              <div className="space-y-2">{flow.unclassifiedTransformations.flatMap((transformation) => transformation.mappings.map((mapping) => (
                <div key={`${transformation.id}:${mapping.source}:${mapping.target}`} className="grid grid-cols-[1fr_auto_1fr] items-center gap-2 rounded-lg bg-slate-50 p-2 text-[10px]">
                  <FieldPath value={mapping.source} className="text-slate-700" /><ChevronRight className="h-3 w-3 shrink-0 self-center text-slate-400" /><FieldPath value={mapping.target} className="text-slate-700" />
                </div>
              )))}</div>
            </Disclosure>
          )}

          {flow.unknowns.length > 0 && (
            <Disclosure title="Open vragen" count={flow.unknowns.length}>
              <ul className="space-y-1">{flow.unknowns.map((unknown) => (
                <li key={unknown.id} className="rounded-lg bg-slate-50 p-2 text-[10px] leading-relaxed text-slate-700">
                  <span className="block font-semibold text-slate-800">{unknown.name}</span>
                  {(unknown.question || unknown.description) && (
                    <span className="mt-0.5 block">{unknown.question || unknown.description}</span>
                  )}
                </li>
              ))}</ul>
            </Disclosure>
          )}

          {flow.risks.length > 0 && (
            <Disclosure title="Risico's" count={flow.risks.length}>
              <ul className="space-y-1">{flow.risks.map((risk) => (
                <li key={risk.id} className="rounded-lg border border-amber-100 bg-amber-50/70 p-2 text-[10px] leading-relaxed text-slate-700">
                  <span className="block font-semibold">{risk.name || "Risk"}</span>
                  {risk.description && <span className="mt-0.5 block line-clamp-3" title={risk.description}>{risk.description}</span>}
                </li>
              ))}</ul>
            </Disclosure>
          )}

          {flow.conflicts.length > 0 && (
            <Disclosure title="Tegenspraken" count={flow.conflicts.length}>
              <ul className="space-y-1">{flow.conflicts.map((conflict) => (
                <li key={conflict.id} className="rounded-lg border border-red-100 bg-red-50/70 p-2 text-[10px] leading-relaxed text-slate-700">
                  <span className="block font-semibold">{conflict.name || "Discrepancy"}</span>
                  {conflict.description && <span className="mt-0.5 block line-clamp-3" title={conflict.description}>{conflict.description}</span>}
                </li>
              ))}</ul>
            </Disclosure>
          )}

          {techniek.length > 0 && (
            <Disclosure title="Techniek" count={techniek.length}>
              <dl className="space-y-1.5">
                {techniek.map((item) => (
                  <div key={item.label}>
                    <dt className="text-[9px] font-bold uppercase tracking-wide text-slate-400">{item.label}</dt>
                    <dd className="text-[10px] leading-relaxed text-slate-600">{item.value}</dd>
                  </div>
                ))}
              </dl>
            </Disclosure>
          )}

          {/*
            No Bronnen row, by request. `documentRefs` stays on the projection
            and in the published artifact: it is provenance, and dropping it from
            the DATA would make a claim unverifiable. It is just not what a
            reader of this panel is after, and eight citation lines per stream
            crowded out the facts being cited.
          */}

          <Disclosure title="Implementation & evidence" count={flow.artifactIds.length}>
            <div className="grid grid-cols-2 gap-2">
              <Fact label="Artifacts" value={`${flow.artifactIds.length}`} />
              <Fact label="Evidence references" value={`${flow.evidenceIds.length}`} />
            </div>
          </Disclosure>
        </div>

        <button type="button" onClick={() => onOpenFlow(flow.id)} className="w-full rounded-lg bg-slate-900 px-3 py-2 text-xs font-semibold text-white hover:bg-slate-700">Open implementation details</button>
      </div>
    </aside>
  );
}

/**
 * One mapping per row: what it becomes, said in words, with the verbatim rule
 * underneath as evidence.
 *
 * The sentence is the row and the expression is the footnote, not the other way
 * around. A 390px row cannot carry
 * `SELECT count(*) AS elements ${exchangeProperty.from}` AND stay readable, and
 * a reader who only ever sees the expression has to reverse-engineer the intent
 * the producer already wrote down.
 */
function MappingRows({ rows }: { rows: ArchitectureFlowProjection["fieldRows"] }) {
  return (
    <div className="space-y-1.5">
      {rows.slice(0, PANEL_FIELD_ROWS).map((row) => (
        <div key={`${row.transformationId}:${row.index}`} className="rounded-lg bg-slate-50 p-2">
          <div className="flex items-baseline justify-between gap-2">
            <FieldPath value={row.targetField} className="text-[10px] font-bold text-slate-800" />
            <span className="shrink-0 text-[9px] font-semibold uppercase text-slate-400">{row.mappingType}</span>
          </div>
          <FieldPath value={row.sourceOrExpression} className="mt-0.5 block text-[10px] text-slate-600" />
          {/* Only when it adds something: repeating the sentence as its own
              footnote is noise, and an empty footnote row is worse. */}
          {row.expression && row.expression !== row.sourceOrExpression && (
            <p className="mt-1 truncate font-mono text-[9px] text-slate-400" title={row.expression}>
              {row.expression}
            </p>
          )}
          <div className="mt-1 flex flex-wrap items-center gap-1">
            {row.reachesTarget !== "unknown" && (
              <span className={`rounded px-1.5 py-0.5 text-[9px] font-semibold ${reachesClasses(row.reachesTarget)}`}>{REACHES_TARGET_LABEL[row.reachesTarget]}</span>
            )}
            {row.category && <Tag>{row.category}</Tag>}
          </div>
        </div>
      ))}
      {rows.length > PANEL_FIELD_ROWS && (
        <p className="text-[10px] text-slate-500">+{rows.length - PANEL_FIELD_ROWS} meer in de volledige veldentabel.</p>
      )}
    </div>
  );
}

/**
 * A collapsed, counted section. Collapsed BY DEFAULT and that is the point: an
 * enriched stream carries a dozen sections, and rendering them all expanded put
 * every measured number below the fold behind repeated prose. The count is on
 * the closed row so nothing is hidden without saying how much.
 */
function Disclosure({ title, count, children }: { title: string; count?: number; children: React.ReactNode }) {
  const [open, setOpen] = useState(false);
  return (
    <div>
      <button
        type="button"
        onClick={() => setOpen((previous) => !previous)}
        aria-expanded={open}
        data-testid={`disclosure:${title}`}
        className="flex w-full items-center gap-2 px-2.5 py-2 text-left hover:bg-slate-50"
      >
        <ChevronRight className={`h-3.5 w-3.5 shrink-0 text-slate-400 transition-transform ${open ? "rotate-90" : ""}`} />
        <span className="min-w-0 flex-1 truncate text-[11px] font-bold text-slate-700">{title}</span>
        {typeof count === "number" && <span className="shrink-0 text-[10px] font-semibold text-slate-400">{count}</span>}
      </button>
      {open && <div className="px-2.5 pb-3">{children}</div>}
    </div>
  );
}

function BusinessRuleCard({ rule }: { rule: BusinessRule }) {
  return (
    <div className="rounded-lg border border-slate-200 p-2.5">
      <div className="flex items-center justify-between gap-2">
        <Tag>{BUSINESS_RULE_KIND_LABEL[rule.kind ?? "unknown"]}</Tag>
        {rule.owner && <span className="truncate text-[9px] font-semibold text-slate-500" title={rule.owner}>{rule.owner}</span>}
      </div>
      <p className="mt-1.5 text-[11px] leading-relaxed text-slate-700">{rule.statement || rule.description || rule.name}</p>
      {(rule.inputs.length > 0 || rule.output) && (
        <p className="mt-1 truncate text-[9px] text-slate-400" title={`${rule.inputs.join(", ")} → ${rule.output}`}>
          {[rule.inputs.join(", "), rule.output].filter(Boolean).join(" → ")}
        </p>
      )}
      {rule.owner_question && <p className="mt-1.5 text-[10px] italic leading-relaxed text-slate-500">{rule.owner_question}</p>}
    </div>
  );
}

/** Conditions by kind, in the schema's own kind order so two streams read the same. */
function groupConditions(conditions: StreamCondition[]): Array<[StreamConditionKind, StreamCondition[]]> {
  const groups = new Map<StreamConditionKind, StreamCondition[]>();
  for (const condition of conditions) {
    const kind = condition.kind ?? "unknown";
    groups.set(kind, [...(groups.get(kind) ?? []), condition]);
  }
  const order = Object.keys(STREAM_CONDITION_KIND_LABEL) as StreamConditionKind[];
  return order.filter((kind) => groups.has(kind)).map((kind) => [kind, groups.get(kind)!]);
}

function PanelHeader({ eyebrow, title, onClose }: { eyebrow: string; title: string; onClose: () => void }) {
  return <div className="sticky top-0 z-10 flex items-start gap-3 border-b border-slate-200 bg-white/95 p-4 backdrop-blur"><div className="min-w-0 flex-1"><p className="text-[9px] font-bold uppercase tracking-[0.16em] text-blue-700">{eyebrow}</p><h2 className="mt-1 truncate text-sm font-bold text-slate-900" title={title}>{title}</h2></div><button type="button" onClick={onClose} aria-label="Close details" className="rounded p-1 text-slate-400 hover:bg-slate-100"><X className="h-4 w-4" /></button></div>;
}

function Section({ title, count, children }: { title: string; count?: number; children: React.ReactNode }) {
  return (
    <section>
      <h3 className="mb-2 flex items-center gap-2 text-[10px] font-bold uppercase tracking-[0.14em] text-slate-500">
        <span>{title}</span>
        {count !== undefined && <span className="rounded bg-slate-100 px-1.5 py-0.5 text-[9px] font-bold text-slate-600">{count}</span>}
      </h3>
      {children}
    </section>
  );
}

function Fact({ label, value }: { label: string; value: string }) {
  return <div className="rounded-lg bg-slate-50 p-2"><p className="text-[9px] font-semibold uppercase text-slate-400">{label}</p><p className="mt-0.5 truncate text-xs font-semibold text-slate-700" title={value}>{value}</p></div>;
}

function Tag({ children }: { children: React.ReactNode }) {
  return <span className="rounded bg-slate-100 px-1.5 py-0.5 text-[9px] font-semibold text-slate-600">{children}</span>;
}

function StatusBadge({ status }: { status: string }) {
  if (!SHOW_CLAIM_STATUS) return null;
  return <span className={`rounded px-1.5 py-0.5 text-[9px] font-semibold ${statusClasses(status)}`}>{statusLabel(status)}</span>;
}

function EmptyFact({ text }: { text: string }) {
  return <div className="rounded-lg border border-dashed border-slate-200 bg-slate-50 p-3 text-[10px] leading-relaxed text-slate-500">{text}</div>;
}

function SystemMiniCard({ name }: { name: string }) {
  return <div className="min-w-0"><Database className="mx-auto h-4 w-4 text-blue-600" /><p className="mt-1 truncate text-[10px] font-bold text-slate-700" title={name}>{name}</p></div>;
}
