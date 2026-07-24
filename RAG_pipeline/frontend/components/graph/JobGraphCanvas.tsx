"use client";

import { useEffect, useMemo, useCallback, useState } from "react";
import {
  ReactFlow,
  Background,
  BackgroundVariant,
  Controls,
  MiniMap,
  ReactFlowProvider,
  useNodesState,
  useEdgesState,
  useReactFlow,
  type Node,
  type Edge,
  type NodeMouseHandler,
} from "@xyflow/react";

import { ComponentNode } from "@/components/graph/ComponentNode";
import { ChainSummaryNode } from "@/components/graph/ChainSummaryNode";
import { ComponentInspector } from "@/components/graph/ComponentInspector";
import { JobPurposeBanner } from "@/components/graph/JobPurposeBanner";
import { useJobGraph } from "@/hooks/useJobGraph";
import {
  jobGraphToFlow,
  applyJobGraphLayout,
  classifyComponentDoubleClick,
  componentConnectors,
  type ComponentNodeData,
} from "@/lib/jobGraphTransform";
import { collapseLinearChains, CHAIN_SUMMARY_NODE_TYPE } from "@/lib/chainCollapse";

interface JobGraphCanvasProps {
  projectId: string;
  jobName: string;
  artifactKind?: string;
  /** Called when a tRunJob portal node is double-clicked. */
  onOpenJob: (jobName: string) => void;
  /** Called when a has_subgraph (tMap) node is double-clicked - descends
   *  to that component's level-3 table graph. */
  onOpenComponent: (componentName: string) => void;
  /** S5 field lineage ("heritage mode"): `comp:{unique_name}` ids touched by
   *  the active lineage path (intra-job, cross-component hops - see
   *  lib/lineageHighlight.ts::jobLineageHighlight). Null/undefined when no
   *  lineage selection is active, or the selection belongs to a different
   *  job than the one this canvas shows. */
  lineageComponentIds?: Set<string> | null;
}

const nodeTypes = {
  componentNode: ComponentNode,
  [CHAIN_SUMMARY_NODE_TYPE]: ChainSummaryNode,
};

function EdgeStyleLegend({
  dataCount,
  controlCount,
}: {
  dataCount: number;
  controlCount: number;
}) {
  return (
    <div className="absolute bottom-3 left-3 z-10 rounded-lg border bg-card/95 px-3 py-2 shadow-sm text-[10px] text-muted-foreground space-y-1">
      <div className="flex items-center gap-2">
        <svg width="24" height="6" aria-hidden="true">
          <line x1="0" y1="3" x2="24" y2="3" stroke="#64748b" strokeWidth="2" />
        </svg>
        <span>Data flow ({dataCount})</span>
      </div>
      <div className="flex items-center gap-2">
        <svg width="24" height="6" aria-hidden="true">
          <line
            x1="0"
            y1="3"
            x2="24"
            y2="3"
            stroke="#cbd5e1"
            strokeWidth="2"
            strokeDasharray="5 3"
          />
        </svg>
        <span>Control flow ({controlCount})</span>
      </div>
    </div>
  );
}

function JobGraphCanvasInner({
  projectId,
  jobName,
  artifactKind,
  onOpenJob,
  onOpenComponent,
  lineageComponentIds = null,
}: JobGraphCanvasProps) {
  const designLabel = artifactKind === "route" ? "Route" : "Job";
  const { data, isLoading, error } = useJobGraph(projectId, jobName);
  const [nodes, setNodes, onNodesChange] = useNodesState<Node>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);
  const { fitView } = useReactFlow();
  // Component with no deeper level, opened via double-click (D4) - an
  // overlay panel at this same level, not a navigation stack entry.
  const [inspectorComponent, setInspectorComponent] = useState<string | null>(null);
  // Chains the user has explicitly expanded (see lib/chainCollapse.ts) -
  // keyed by chain id, which is stable across re-detection since it's
  // derived from the chain's fixed entry/exit node ids.
  const [expandedChainIds, setExpandedChainIds] = useState<Set<string>>(new Set());

  const flowResult = useMemo(() => (data ? jobGraphToFlow(data) : null), [data]);

  const collapseResult = useMemo(() => {
    if (!flowResult) return null;
    return collapseLinearChains(flowResult.nodes, flowResult.edges, expandedChainIds);
  }, [flowResult, expandedChainIds]);

  // Fresh dagre layout per descent (or chain expand/collapse), then an
  // animated fitView transition. Never re-triggered by a lineage selection
  // (see the effect below) - collapsing runs a long unbranched pipeline
  // (100+ components) down to its real branch points plus a handful of
  // expandable chain nodes, so dagre lays out a small graph instead of one
  // endless horizontal strip.
  useEffect(() => {
    if (!collapseResult) {
      setNodes([]);
      setEdges([]);
      return;
    }
    const laid = applyJobGraphLayout(collapseResult.nodes, collapseResult.edges, "LR");
    setNodes(laid);
    setEdges(collapseResult.edges);
    requestAnimationFrame(() => {
      fitView({ padding: 0.15, duration: 400 });
    });
  }, [collapseResult, setNodes, setEdges, fitView]);

  // S5 lineage overlay: dim every component node not on the active
  // lineage path, glow the ones that are. Independent of the layout effect
  // so a column click elsewhere never re-triggers dagre on this canvas. A
  // chain summary node counts as "on the path" if any absorbed member is -
  // otherwise a highlighted component hidden inside a collapsed chain would
  // silently disappear instead of showing up as its containing chain.
  useEffect(() => {
    setNodes((nds) =>
      nds.map((n) => {
        const memberIds = n.type === CHAIN_SUMMARY_NODE_TYPE
          ? (n.data as { memberIds?: string[] }).memberIds
          : undefined;
        const isMatch = Boolean(lineageComponentIds) && (
          memberIds
            ? memberIds.some((id) => lineageComponentIds!.has(id))
            : lineageComponentIds!.has(n.id)
        );
        const dimmed = Boolean(lineageComponentIds) && !isMatch;
        const highlighted = isMatch;
        if (n.data.dimmed === dimmed && n.data.highlighted === highlighted) return n;
        return { ...n, data: { ...n.data, dimmed, highlighted } };
      }),
    );
  }, [lineageComponentIds, setNodes]);

  const handleNodeDoubleClick: NodeMouseHandler = useCallback(
    (_event, node) => {
      if (node.type === CHAIN_SUMMARY_NODE_TYPE) return; // single-click expands, see below
      const nodeData = node.data as ComponentNodeData;
      const action = classifyComponentDoubleClick(nodeData);
      switch (action.kind) {
        case "openJob":
          onOpenJob(action.jobName);
          break;
        case "openComponent":
          onOpenComponent(nodeData.label);
          break;
        case "inspect":
          setInspectorComponent(nodeData.label);
          break;
      }
    },
    [onOpenJob, onOpenComponent],
  );

  // Single-click a chain summary node to expand it back into its members;
  // clicking a member's own chain id (surfaced once expanded, see below)
  // re-collapses it. Mirrors the existing group-summary expand/collapse
  // toggle used at the project level (app/topology/page.tsx).
  const handleNodeClick: NodeMouseHandler = useCallback((_event, node) => {
    if (node.type !== CHAIN_SUMMARY_NODE_TYPE) return;
    setExpandedChainIds((prev) => {
      const next = new Set(prev);
      if (next.has(node.id)) {
        next.delete(node.id);
      } else {
        next.add(node.id);
      }
      return next;
    });
  }, []);

  const inspectorConnectors = useMemo(
    () =>
      data && inspectorComponent ? componentConnectors(data.edges, inspectorComponent) : [],
    [data, inspectorComponent],
  );

  // Description comes from the level-2 node already in memory (parsed from the
  // generated inventory doc); "" when the docs have no matching row.
  const inspectorDescription = useMemo(() => {
    if (!data || !inspectorComponent) return "";
    const node = data.nodes.find((n) => n.unique_name === inspectorComponent);
    return node?.description ?? "";
  }, [data, inspectorComponent]);

  return (
    <div className="absolute inset-0" data-testid="job-graph-canvas">
      {isLoading && (
        <div className="absolute inset-0 flex items-center justify-center bg-background/80 z-20">
          <div className="flex items-center gap-2 text-muted-foreground">
            <div className="h-4 w-4 border-2 border-current border-t-transparent rounded-full animate-spin" />
            Loading {designLabel.toLowerCase()} graph...
          </div>
        </div>
      )}

      {error && !isLoading && (
        <div className="absolute inset-0 flex items-center justify-center z-20">
          <p className="text-sm text-muted-foreground">
            {error instanceof Error ? error.message : `Failed to load ${designLabel.toLowerCase()} graph`}
          </p>
        </div>
      )}

      {!isLoading && !error && flowResult && flowResult.nodes.length === 0 && (
        <div className="absolute inset-0 flex items-center justify-center z-20">
          <p className="text-sm text-muted-foreground">
            No components found for this {designLabel.toLowerCase()}.
          </p>
        </div>
      )}

      <ReactFlow
        nodes={nodes}
        edges={edges}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onNodeClick={handleNodeClick}
        onNodeDoubleClick={handleNodeDoubleClick}
        nodeTypes={nodeTypes}
        fitView
        minZoom={0.05}
        maxZoom={4}
        panOnDrag
        zoomOnPinch
        panActivationKeyCode={null}
        onlyRenderVisibleElements
        proOptions={{ hideAttribution: true }}
        className="bg-background"
      >
        <Background
          variant={BackgroundVariant.Dots}
          gap={24}
          size={0.8}
          color="#cbd5e1"
          className="opacity-20"
        />
        <Controls
          showInteractive={false}
          className="!bg-card !border !shadow-sm [&>button]:!bg-background [&>button]:!border-border [&>button]:!text-foreground [&>button:hover]:!bg-muted"
        />
        <MiniMap
          nodeStrokeWidth={3}
          className="!bg-card !border !shadow-sm"
          maskColor="rgba(0,0,0,0.1)"
        />
      </ReactFlow>

      {expandedChainIds.size > 0 && (
        <button
          type="button"
          onClick={() => setExpandedChainIds(new Set())}
          className="absolute top-3 right-3 z-10 rounded-full border bg-card/95 px-3 py-1.5 shadow-sm text-[11px] font-medium text-muted-foreground hover:bg-muted"
          data-testid="recollapse-chains-button"
        >
          Re-collapse {expandedChainIds.size} expanded chain{expandedChainIds.size === 1 ? "" : "s"}
        </button>
      )}

      {/* Top-left: job purpose/summary overlay (self-fetches, renders null
          when the backend has no truthful summary). Clears the top-right
          "Re-collapse chains" button and the bottom-left edge legend. */}
      <JobPurposeBanner projectId={projectId} jobName={jobName} />

      {flowResult && (
        <EdgeStyleLegend
          dataCount={flowResult.stats.dataFlowEdgeCount}
          controlCount={flowResult.stats.controlFlowEdgeCount}
        />
      )}

      {inspectorComponent && (
        <ComponentInspector
          projectId={projectId}
          jobName={jobName}
          componentName={inspectorComponent}
          connectors={inspectorConnectors}
          description={inspectorDescription}
          onClose={() => setInspectorComponent(null)}
        />
      )}
    </div>
  );
}

// Each descent renders its own freshly laid-out canvas: the provider is
// scoped here so the level-2 React Flow store never collides with the
// level-0/1 canvas that stays mounted (and viewport-preserved) underneath.
export function JobGraphCanvas(props: JobGraphCanvasProps) {
  return (
    <ReactFlowProvider>
      <JobGraphCanvasInner {...props} />
    </ReactFlowProvider>
  );
}
