"use client";

import { useCallback, useEffect, useMemo } from "react";
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
} from "@xyflow/react";

import { TableNode } from "@/components/graph/TableNode";
import { MappingEdge } from "@/components/graph/MappingEdge";
import { LineageEdge } from "@/components/graph/LineageEdge";
import { ComponentInspector } from "@/components/graph/ComponentInspector";
import {
  useComponentGraph,
  ComponentGraphFallbackError,
} from "@/hooks/useComponentGraph";
import {
  componentGraphToFlow,
  applyComponentGraphLayout,
  type TableNodeData,
} from "@/lib/componentGraphTransform";
import {
  buildLineageOverlayEdges,
  tableLineageProjection,
  LINEAGE_EDGE_TYPE,
  type TableGraphLineageHighlight,
} from "@/lib/lineageHighlight";
import type { FieldLineageQuery } from "@/types/lineage";
import type { AssistantCanvasEmphasis, AssistantFocusRequest, CopilotViewContext } from "@/types/copilot";

interface ComponentGraphCanvasProps {
  projectId: string;
  jobName: string;
  componentName: string;
  // Ascends back to the job's level-2 canvas. Used both for normal
  // breadcrumb-equivalent navigation and to close the fallback inspector
  // rendered in this slot when the level-3 endpoint 404s.
  onAscend: () => void;
  // S5 field lineage ("heritage mode") - state lives one level up (the
  // topology page) since a selection must survive ascending/descending
  // between levels; this canvas only renders it and reports clicks.
  lineageQuery?: FieldLineageQuery | null;
  lineageHighlight?: TableGraphLineageHighlight | null;
  onColumnLineageClick?: (jobName: string, componentName: string, table: string, column: string) => void;
  assistantEmphasis?: AssistantCanvasEmphasis | null;
  assistantFocusRequest?: AssistantFocusRequest | null;
  onContextChange?: (context: CopilotViewContext) => void;
}

const nodeTypes = { tableNode: TableNode };
const edgeTypes = { mappingEdge: MappingEdge, [LINEAGE_EDGE_TYPE]: LineageEdge };

function ComponentGraphCanvasInner({
  projectId,
  jobName,
  componentName,
  onAscend,
  lineageQuery = null,
  lineageHighlight = null,
  onColumnLineageClick,
  assistantEmphasis,
  assistantFocusRequest,
  onContextChange,
}: ComponentGraphCanvasProps) {
  const { data, isLoading, error } = useComponentGraph(
    projectId,
    jobName,
    componentName,
  );
  const [nodes, setNodes, onNodesChange] = useNodesState<Node>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);
  const { fitView } = useReactFlow();

  const isFallback = error instanceof ComponentGraphFallbackError;

  const flowResult = useMemo(() => (data ? componentGraphToFlow(data) : null), [data]);

  const handleColumnClick = useCallback(
    (table: string, column: string) => {
      onColumnLineageClick?.(jobName, componentName, table, column);
    },
    [onColumnLineageClick, jobName, componentName],
  );

  // Fresh dagre layout only when the underlying graph data changes - never
  // re-triggered by a lineage selection (see the effect below), which would
  // otherwise re-animate/reposition the whole canvas on every column click.
  useEffect(() => {
    if (!flowResult) {
      setNodes([]);
      setEdges([]);
      return;
    }
    const laid = applyComponentGraphLayout(flowResult.nodes, flowResult.edges);
    setNodes(laid);
    setEdges(flowResult.edges);
    requestAnimationFrame(() => {
      fitView({ padding: 0.2, duration: 400 });
    });
  }, [flowResult, setNodes, setEdges, fitView]);

  // Applies the S5 lineage overlay on top of the already-laid-out canvas:
  // dims tables not on the highlighted path, marks touched/unresolved
  // columns, and draws real confidence-styled column edges for resolved
  // hops. Runs independently of the layout effect above so clicking a
  // column never re-triggers dagre or the fitView animation.
  useEffect(() => {
    const activeTable =
      lineageQuery && lineageQuery.jobName === jobName && lineageQuery.component === componentName
        ? lineageQuery.table
        : null;
    const activeColumn = activeTable ? lineageQuery!.column : null;

    setNodes((nds) =>
      nds.map((n) => {
        const tableName = (n.data as TableNodeData).tableName;
        const dimmed = Boolean(lineageHighlight) && !lineageHighlight!.tableIds.has(n.id);
        const highlighted = Boolean(lineageHighlight) && lineageHighlight!.tableIds.has(n.id);
        const projection = lineageHighlight
          ? tableLineageProjection(lineageHighlight, tableName)
          : { touchedColumns: new Set<string>(), unresolvedColumns: new Map<string, string>() };
        const assistantRole = assistantEmphasis?.nodeRoles.get(n.id);
        const assistantColumnRoles = new Map(
          [...(assistantEmphasis?.columnRoles?.entries() ?? [])]
            .filter(([key]) => key.startsWith(`${tableName}::`))
            .map(([key, role]) => [key.slice(tableName.length + 2), role]),
        );
        const assistantActive = Boolean(
          assistantEmphasis?.nodeRoles.size ||
          assistantEmphasis?.columnRoles?.size ||
          assistantEmphasis?.edgeRoles.size,
        );
        const assistantMatch = Boolean(assistantRole || assistantColumnRoles.size);

        return {
          ...n,
          data: {
            ...n.data,
            dimmed: Boolean(lineageHighlight || assistantActive) && !(highlighted || assistantMatch),
            highlighted: highlighted || assistantMatch,
            assistantVisualRole: assistantRole,
            assistantHighlightedColumns: new Set(assistantColumnRoles.keys()),
            assistantColumnRoles,
            lineageTouchedColumns: projection.touchedColumns,
            lineageUnresolvedColumns: projection.unresolvedColumns,
            lineageActiveColumn: tableName === activeTable ? activeColumn : null,
            onColumnClick: onColumnLineageClick ? handleColumnClick : undefined,
          },
        };
      }),
    );

    setEdges((eds) => {
      const structural = eds
        .filter((e) => e.type !== LINEAGE_EDGE_TYPE)
        .map((e) => {
          const targetTable = e.target.startsWith("table:") ? e.target.slice(6) : e.target;
          const targetColumn = e.targetHandle?.startsWith("target:")
            ? e.targetHandle.slice(7)
            : "";
          const assistantRole = assistantEmphasis?.edgeRoles.get(`${targetTable}::${targetColumn}`);
          const assistantEdgesActive = Boolean(assistantEmphasis?.edgeRoles.size);
          return {
            ...e,
            data: { ...e.data, assistantVisualRole: assistantRole },
            style: {
              ...e.style,
              opacity: lineageHighlight
                ? 0.15
                : assistantEdgesActive
                  ? assistantRole ? 1 : 0.08
                  : undefined,
              strokeWidth: assistantRole === "primary" ? 3 : e.style?.strokeWidth,
            },
          };
        });
      const overlay = lineageHighlight ? buildLineageOverlayEdges(lineageHighlight) : [];
      return [...structural, ...overlay];
    });
  }, [
    lineageHighlight,
    lineageQuery,
    assistantEmphasis,
    jobName,
    componentName,
    handleColumnClick,
    onColumnLineageClick,
    setNodes,
    setEdges,
  ]);

  useEffect(() => {
    if (!assistantFocusRequest) return;
    const focusNodes = nodes.filter((node) => assistantFocusRequest.nodeIds.includes(node.id));
    if (focusNodes.length > 0) void fitView({ nodes: focusNodes, padding: 0.35, duration: 450 });
  }, [assistantFocusRequest, nodes, fitView]);

  useEffect(() => {
    const selectedEntity = lineageQuery
      && lineageQuery.jobName === jobName
      && lineageQuery.component === componentName
      ? {
          kind: "column" as const,
          label: `${lineageQuery.table}.${lineageQuery.column}`,
          entity_id: `${jobName}::${componentName}::${lineageQuery.table}::${lineageQuery.column}`,
        }
      : {
          kind: "component" as const,
          label: componentName,
          entity_id: `${jobName}::${componentName}`,
        };
    onContextChange?.({ selectedEntity, activeDetailTab: "mapping" });
  }, [lineageQuery, jobName, componentName, onContextChange]);

  // Documented 404 { fallback: "inspector" } contract (D4): this component
  // has no level-3 graph after all - render the inspector in this slot
  // instead of an error. "No dead ends" is the S2 acceptance criterion.
  if (isFallback) {
    return (
      <div className="absolute inset-0" data-testid="component-graph-fallback">
        <ComponentInspector
          projectId={projectId}
          jobName={jobName}
          componentName={componentName}
          onClose={onAscend}
        />
      </div>
    );
  }

  return (
    <div className="absolute inset-0" data-testid="component-graph-canvas">
      {isLoading && (
        <div className="absolute inset-0 flex items-center justify-center bg-background/80 z-20">
          <div className="flex items-center gap-2 text-muted-foreground">
            <div className="h-4 w-4 border-2 border-current border-t-transparent rounded-full animate-spin" />
            Loading component graph...
          </div>
        </div>
      )}

      {error && !isLoading && (
        <div className="absolute inset-0 flex items-center justify-center z-20">
          <p className="text-sm text-muted-foreground">
            {error instanceof Error ? error.message : "Failed to load component graph"}
          </p>
        </div>
      )}

      {!isLoading && !error && data && (
        // var_table is always present (server default), so an empty graph
        // means no input/output tables and an empty Var - check the source
        // data directly rather than flowResult.nodes.length, which is never
        // truly 0.
        data.input_tables.length === 0 &&
        data.output_tables.length === 0 &&
        data.var_table.columns.length === 0 && (
          <div className="absolute inset-0 flex items-center justify-center z-20">
            <p className="text-sm text-muted-foreground">
              No tables captured for this component.
            </p>
          </div>
        )
      )}

      <ReactFlow
        nodes={nodes}
        edges={edges}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        nodeTypes={nodeTypes}
        edgeTypes={edgeTypes}
        fitView
        minZoom={0.05}
        maxZoom={4}
        panOnDrag
        zoomOnPinch
        onlyRenderVisibleElements
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
        <MiniMap
          nodeStrokeWidth={3}
          className="!bg-card !border !shadow-sm"
          maskColor="rgba(0,0,0,0.1)"
        />
      </ReactFlow>
    </div>
  );
}

// Each descent renders its own freshly laid-out canvas: the provider is
// scoped here so the level-3 React Flow store never collides with the
// level-2 canvas it descended from (mirrors JobGraphCanvas).
export function ComponentGraphCanvas(props: ComponentGraphCanvasProps) {
  return (
    <ReactFlowProvider>
      <ComponentGraphCanvasInner {...props} />
    </ReactFlowProvider>
  );
}
