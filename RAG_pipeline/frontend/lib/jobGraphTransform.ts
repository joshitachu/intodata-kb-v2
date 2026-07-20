import dagre from "@dagrejs/dagre";
import type { Node, Edge } from "@xyflow/react";
import type { ComponentRole, JobGraphEdge, JobGraphResponse } from "@/types/jobGraph";
import { CONTROL_EDGE_COLOR, DATA_EDGE_COLOR } from "@/lib/nodeStyles";

// ── Role palette (fixed per role, mirrors the group palette style) ──

export interface RoleColor {
  bg: string;
  border: string;
  text: string;
}

export const ROLE_COLORS: Record<ComponentRole, RoleColor> = {
  input: { bg: "#dcfce7", border: "#22c55e", text: "#166534" }, // green
  transform: { bg: "#fef3c7", border: "#f59e0b", text: "#92400e" }, // amber
  output: { bg: "#dbeafe", border: "#3b82f6", text: "#1e40af" }, // blue
  orchestration: { bg: "#e0e7ff", border: "#6366f1", text: "#3730a3" }, // indigo
  logging: { bg: "#f1f5f9", border: "#64748b", text: "#334155" }, // slate
};

// ── Edge classification ─────────────────────────────────────────────
//
// Edges come straight from ParsedJob.connections. Only FLOW/LOOKUP-style
// rows carry data (line_style 0); everything else — COMPONENT_OK,
// COMPONENT_ERROR, SUBJOB_OK, RUN_IF, ITERATE, ... — is a trigger link
// rendered as dashed control flow.

const DATA_FLOW_CONNECTORS = new Set(["FLOW", "LOOKUP", "MERGE"]);

export function isControlFlowEdge(connectorName: string, lineStyle: number): boolean {
  if (lineStyle !== 0) return true;
  return !DATA_FLOW_CONNECTORS.has(connectorName.toUpperCase());
}

// ── Node data shape consumed by ComponentNode ───────────────────────

export interface ComponentNodeData {
  label: string;
  componentType: string;
  role: ComponentRole;
  color: RoleColor;
  connectionRef: string;
  hasCode: boolean;
  hasSubgraph: boolean;
  subjobTarget: string | null;
  // Plain-English component description from the generated inventory doc;
  // "" when the doc has no matching row (never fabricated).
  description: string;
  paramSummary: Record<string, string>;
  inputTableCount: number;
  outputTableCount: number;
  dimmed?: boolean;
  highlighted?: boolean;
  [key: string]: unknown;
}

export interface JobFlowResult {
  nodes: Node[];
  edges: Edge[];
  stats: {
    componentCount: number;
    dataFlowEdgeCount: number;
    controlFlowEdgeCount: number;
  };
}

// Edge colors come from lib/nodeStyles (shared topology palette): neutral
// slate tones, no animation — motion is reserved for failing/highlighted
// edges only.

export function jobGraphToFlow(graph: JobGraphResponse): JobFlowResult {
  const nodes: Node[] = [];
  const edges: Edge[] = [];

  for (const comp of graph.nodes) {
    const tmap = graph.tmap_tables[comp.unique_name];
    const paramSummary: Record<string, string> = {};
    for (const [key, value] of Object.entries(comp.param_summary)) {
      paramSummary[key] = value === null ? "" : String(value);
    }

    const data: ComponentNodeData = {
      label: comp.unique_name,
      componentType: comp.component_type,
      role: comp.role,
      color: ROLE_COLORS[comp.role],
      connectionRef: comp.connection_ref ?? "",
      hasCode: comp.has_code,
      hasSubgraph: comp.has_subgraph,
      subjobTarget: comp.subjob_target ?? null,
      description: comp.description ?? "",
      paramSummary,
      inputTableCount: tmap?.input_tables.length ?? 0,
      outputTableCount: tmap?.output_tables.length ?? 0,
    };

    nodes.push({
      id: `comp:${comp.unique_name}`,
      type: "componentNode",
      position: { x: 0, y: 0 },
      data,
    });
  }

  const knownIds = new Set(nodes.map((n) => n.id));
  let dataFlowEdgeCount = 0;
  let controlFlowEdgeCount = 0;

  for (const conn of graph.edges) {
    const sourceId = `comp:${conn.source}`;
    const targetId = `comp:${conn.target}`;
    // Skip dangling connections rather than crashing the layout — the scan
    // JSON is external input and may reference components missing from nodes.
    if (!knownIds.has(sourceId) || !knownIds.has(targetId)) continue;

    const isControl = isControlFlowEdge(conn.connector_name, conn.line_style);
    if (isControl) {
      controlFlowEdgeCount++;
    } else {
      dataFlowEdgeCount++;
    }

    edges.push({
      id: `flow:${conn.name}:${conn.source}:${conn.target}`,
      source: sourceId,
      target: targetId,
      type: "default",
      animated: false,
      label: isControl ? conn.connector_name : undefined,
      labelStyle: isControl
        ? { fill: "#64748b", fontSize: 9, fontWeight: 600 }
        : undefined,
      labelBgStyle: isControl ? { fill: "#f8fafc", fillOpacity: 0.85 } : undefined,
      style: isControl
        ? { stroke: CONTROL_EDGE_COLOR, strokeWidth: 1.2, strokeDasharray: "6 3" }
        : { stroke: DATA_EDGE_COLOR, strokeWidth: 1.5 },
      markerEnd: {
        type: "arrowclosed" as const,
        color: isControl ? CONTROL_EDGE_COLOR : DATA_EDGE_COLOR,
        width: 16,
        height: 16,
      },
      data: {
        edgeType: isControl ? "control" : "data",
        connectorName: conn.connector_name,
        lineStyle: conn.line_style,
        rowName: conn.name,
      },
    });
  }

  return {
    nodes,
    edges,
    stats: {
      componentCount: nodes.length,
      dataFlowEdgeCount,
      controlFlowEdgeCount,
    },
  };
}

// ── Double-click dispatch (S2) ──────────────────────────────────────
//
// Pure decision function for JobGraphCanvas's onNodeDoubleClick: a node
// either descends to its child job (tRunJob portal), descends to its
// level-3 table graph (has_subgraph, tMap today), or - since every other
// component type has no deeper level (D4) - opens the inspector panel.
// No case falls through to a dead end.

export type NodeDoubleClickAction =
  | { kind: "openJob"; jobName: string }
  | { kind: "openComponent" }
  | { kind: "inspect" };

export function classifyComponentDoubleClick(
  data: ComponentNodeData,
): NodeDoubleClickAction {
  if (typeof data.subjobTarget === "string" && data.subjobTarget) {
    return { kind: "openJob", jobName: data.subjobTarget };
  }
  if (data.hasSubgraph) {
    return { kind: "openComponent" };
  }
  return { kind: "inspect" };
}

// ── Component connectors (for the inspector fallback panel) ────────
//
// Edges come straight from the job's `connections` array (via
// JobGraphResponse.edges) - never from a component's own
// incoming/outgoing_connections lists, which can be empty even when the
// job's connections array is complete (architecture doc, section 1.2).

export interface ComponentConnectorSummary {
  direction: "incoming" | "outgoing";
  peer: string;
  rowName: string;
  connectorName: string;
  isControlFlow: boolean;
}

export function componentConnectors(
  edges: JobGraphEdge[],
  componentName: string,
): ComponentConnectorSummary[] {
  const result: ComponentConnectorSummary[] = [];
  for (const edge of edges) {
    if (edge.source === componentName) {
      result.push({
        direction: "outgoing",
        peer: edge.target,
        rowName: edge.name,
        connectorName: edge.connector_name,
        isControlFlow: isControlFlowEdge(edge.connector_name, edge.line_style),
      });
    }
    if (edge.target === componentName) {
      result.push({
        direction: "incoming",
        peer: edge.source,
        rowName: edge.name,
        connectorName: edge.connector_name,
        isControlFlow: isControlFlowEdge(edge.connector_name, edge.line_style),
      });
    }
  }
  return result;
}

// ── Layout ──────────────────────────────────────────────────────────

export const COMPONENT_NODE_WIDTH = 220;
export const COMPONENT_NODE_HEIGHT = 68;

export function applyJobGraphLayout(
  nodes: Node[],
  edges: Edge[],
  direction: "TB" | "LR" = "LR",
): Node[] {
  const g = new dagre.graphlib.Graph();
  g.setDefaultEdgeLabel(() => ({}));
  g.setGraph({ rankdir: direction, nodesep: 40, ranksep: 80 });

  for (const node of nodes) {
    g.setNode(node.id, {
      width: COMPONENT_NODE_WIDTH,
      height: COMPONENT_NODE_HEIGHT,
    });
  }
  for (const edge of edges) {
    g.setEdge(edge.source, edge.target);
  }

  dagre.layout(g);

  return nodes.map((node) => {
    const pos = g.node(node.id);
    return {
      ...node,
      position: {
        x: pos.x - COMPONENT_NODE_WIDTH / 2,
        y: pos.y - COMPONENT_NODE_HEIGHT / 2,
      },
    };
  });
}
