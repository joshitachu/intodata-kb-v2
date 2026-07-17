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
import { ComponentInspector } from "@/components/graph/ComponentInspector";
import { useJobGraph } from "@/hooks/useJobGraph";
import {
  jobGraphToFlow,
  applyJobGraphLayout,
  classifyComponentDoubleClick,
  componentConnectors,
  type ComponentNodeData,
} from "@/lib/jobGraphTransform";

interface JobGraphCanvasProps {
  projectId: string;
  jobName: string;
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
          <line x1="0" y1="3" x2="24" y2="3" stroke="#3b82f6" strokeWidth="2" />
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
            stroke="#94a3b8"
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
  onOpenJob,
  onOpenComponent,
  lineageComponentIds = null,
}: JobGraphCanvasProps) {
  const { data, isLoading, error } = useJobGraph(projectId, jobName);
  const [nodes, setNodes, onNodesChange] = useNodesState<Node>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);
  const { fitView } = useReactFlow();
  // Component with no deeper level, opened via double-click (D4) - an
  // overlay panel at this same level, not a navigation stack entry.
  const [inspectorComponent, setInspectorComponent] = useState<string | null>(null);

  const flowResult = useMemo(() => (data ? jobGraphToFlow(data) : null), [data]);

  // Fresh dagre layout per descent, then an animated fitView transition.
  // Never re-triggered by a lineage selection (see the effect below).
  useEffect(() => {
    if (!flowResult) {
      setNodes([]);
      setEdges([]);
      return;
    }
    const laid = applyJobGraphLayout(flowResult.nodes, flowResult.edges, "LR");
    setNodes(laid);
    setEdges(flowResult.edges);
    requestAnimationFrame(() => {
      fitView({ padding: 0.15, duration: 400 });
    });
  }, [flowResult, setNodes, setEdges, fitView]);

  // S5 lineage overlay: dim every component node not on the active
  // lineage path, glow the ones that are. Independent of the layout effect
  // so a column click elsewhere never re-triggers dagre on this canvas.
  useEffect(() => {
    setNodes((nds) =>
      nds.map((n) => {
        const dimmed = Boolean(lineageComponentIds) && !lineageComponentIds!.has(n.id);
        const highlighted = Boolean(lineageComponentIds) && lineageComponentIds!.has(n.id);
        if (n.data.dimmed === dimmed && n.data.highlighted === highlighted) return n;
        return { ...n, data: { ...n.data, dimmed, highlighted } };
      }),
    );
  }, [lineageComponentIds, setNodes]);

  const handleNodeDoubleClick: NodeMouseHandler = useCallback(
    (_event, node) => {
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

  const inspectorConnectors = useMemo(
    () =>
      data && inspectorComponent ? componentConnectors(data.edges, inspectorComponent) : [],
    [data, inspectorComponent],
  );

  return (
    <div className="absolute inset-0" data-testid="job-graph-canvas">
      {isLoading && (
        <div className="absolute inset-0 flex items-center justify-center bg-background/80 z-20">
          <div className="flex items-center gap-2 text-muted-foreground">
            <div className="h-4 w-4 border-2 border-current border-t-transparent rounded-full animate-spin" />
            Loading job graph...
          </div>
        </div>
      )}

      {error && !isLoading && (
        <div className="absolute inset-0 flex items-center justify-center z-20">
          <p className="text-sm text-muted-foreground">
            {error instanceof Error ? error.message : "Failed to load job graph"}
          </p>
        </div>
      )}

      {!isLoading && !error && flowResult && flowResult.nodes.length === 0 && (
        <div className="absolute inset-0 flex items-center justify-center z-20">
          <p className="text-sm text-muted-foreground">
            No components found for this job.
          </p>
        </div>
      )}

      <ReactFlow
        nodes={nodes}
        edges={edges}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onNodeDoubleClick={handleNodeDoubleClick}
        nodeTypes={nodeTypes}
        fitView
        minZoom={0.05}
        maxZoom={4}
        panOnDrag
        zoomOnPinch
        panActivationKeyCode={null}
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
        {nodes.length <= 150 && (
          <MiniMap
            nodeStrokeWidth={3}
            className="!bg-card !border !shadow-sm"
            maskColor="rgba(0,0,0,0.1)"
          />
        )}
      </ReactFlow>

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
