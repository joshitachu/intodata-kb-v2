import dagre from "@dagrejs/dagre";
import type { Node, Edge } from "@xyflow/react";

// 8-color palette for group categories
const GROUP_COLORS = [
  { bg: "#dbeafe", border: "#3b82f6", text: "#1e40af" }, // blue
  { bg: "#dcfce7", border: "#22c55e", text: "#166534" }, // green
  { bg: "#fef3c7", border: "#f59e0b", text: "#92400e" }, // amber
  { bg: "#fce7f3", border: "#ec4899", text: "#9d174d" }, // pink
  { bg: "#e0e7ff", border: "#6366f1", text: "#3730a3" }, // indigo
  { bg: "#ffedd5", border: "#f97316", text: "#9a3412" }, // orange
  { bg: "#f0fdf4", border: "#14b8a6", text: "#115e59" }, // teal
  { bg: "#faf5ff", border: "#a855f7", text: "#6b21a8" }, // purple
];

function hashString(s: string): number {
  let hash = 0;
  for (let i = 0; i < s.length; i++) {
    hash = (hash * 31 + s.charCodeAt(i)) | 0;
  }
  return Math.abs(hash);
}

export interface GraphConnectionInfo {
  name: string;
  connection_type: string;
  host: string;
  database: string;
}

export interface JobScanMetadata {
  purpose: string;
  author: string;
  modification_date: string;
  component_count: number;
  component_types: string[];
  subgroup: string;
  has_docs: boolean;
  external_systems: string[];
  summary: string;
  direction: string;
}

export interface ManifestData {
  job_calls: Record<string, string[]>;
  job_called_by: Record<string, string[]>;
  job_connections_used: Record<string, string[]>;
  connection_used_by: Record<string, string[]>;
  job_groups: Record<string, string>;
  routines_used_by?: Record<string, string[]>;
  job_contexts_used?: Record<string, string[]>;
}

export interface FlowResult {
  nodes: Node[];
  edges: Edge[];
  groups: string[];
  groupColorMap: Record<string, typeof GROUP_COLORS[0]>;
  stats: {
    jobCount: number;
    connectionCount: number;
    edgeCount: number;
    groupCount: number;
  };
}

export type GroupColor = typeof GROUP_COLORS[0];

export interface GroupBoundingBox {
  id: string;
  group: string;
  color: GroupColor;
  x: number;
  y: number;
  width: number;
  height: number;
}

export function manifestToFlow(
  manifest: ManifestData,
  connections: GraphConnectionInfo[],
  jobMetadata?: Record<string, JobScanMetadata>,
): FlowResult {
  const jobNames = new Set<string>();
  const connNames = new Set<string>();

  // Collect all job names from every manifest field
  for (const name of Object.keys(manifest.job_groups || {})) {
    jobNames.add(name);
  }
  for (const [caller, callees] of Object.entries(manifest.job_calls || {})) {
    jobNames.add(caller);
    for (const c of callees) jobNames.add(c);
  }
  for (const [callee, callers] of Object.entries(manifest.job_called_by || {})) {
    jobNames.add(callee);
    for (const c of callers) jobNames.add(c);
  }
  for (const [job, conns] of Object.entries(manifest.job_connections_used || {})) {
    jobNames.add(job);
    for (const c of conns) connNames.add(c);
  }
  for (const name of Object.keys(manifest.connection_used_by || {})) {
    connNames.add(name);
  }

  // Build group -> color mapping
  const groups = [...new Set(Object.values(manifest.job_groups || {}))].sort();
  const groupColorMap: Record<string, typeof GROUP_COLORS[0]> = {};
  for (const group of groups) {
    groupColorMap[group] = GROUP_COLORS[hashString(group) % GROUP_COLORS.length];
  }

  // Build connection lookup
  const connLookup = new Map<string, GraphConnectionInfo>();
  for (const c of connections) {
    connLookup.set(c.name, c);
  }

  // Count edges per job
  const jobCallsOut = new Map<string, number>();
  const jobCalledByCount = new Map<string, number>();
  const jobConnCount = new Map<string, number>();

  for (const [caller, callees] of Object.entries(manifest.job_calls || {})) {
    jobCallsOut.set(caller, (jobCallsOut.get(caller) || 0) + callees.length);
    for (const c of callees) {
      jobCalledByCount.set(c, (jobCalledByCount.get(c) || 0) + 1);
    }
  }
  for (const [job, conns] of Object.entries(manifest.job_connections_used || {})) {
    jobConnCount.set(job, conns.length);
  }

  // Create job nodes
  const nodes: Node[] = [];
  for (const name of jobNames) {
    const group = (manifest.job_groups || {})[name] || "Ungrouped";
    const color = groupColorMap[group] || GROUP_COLORS[0];
    const meta = jobMetadata?.[name];
    nodes.push({
      id: `job:${name}`,
      type: "jobNode",
      position: { x: 0, y: 0 },
      data: {
        label: name,
        group,
        color,
        callsOut: jobCallsOut.get(name) || 0,
        calledBy: jobCalledByCount.get(name) || 0,
        connCount: jobConnCount.get(name) || 0,
        purpose: meta?.purpose || "",
        componentCount: meta?.component_count || 0,
        hasDocs: meta?.has_docs || false,
        externalSystems: meta?.external_systems || [],
        summary: meta?.summary || "",
        direction: meta?.direction || "",
      },
    });
  }

  // Create connection nodes
  for (const name of connNames) {
    const info = connLookup.get(name);
    const usedBy = (manifest.connection_used_by || {})[name] || [];
    nodes.push({
      id: `conn:${name}`,
      type: "connectionNode",
      position: { x: 0, y: 0 },
      data: {
        label: name,
        connectionType: info?.connection_type || "",
        host: info?.host || "",
        database: info?.database || "",
        usageCount: usedBy.length,
      },
    });
  }

  // Create edges
  const edges: Edge[] = [];
  for (const [caller, callees] of Object.entries(manifest.job_calls || {})) {
    for (const callee of callees) {
      edges.push({
        id: `calls:${caller}:${callee}`,
        source: `job:${caller}`,
        target: `job:${callee}`,
        type: "default",
        animated: true,
        style: { stroke: "#3b82f6", strokeWidth: 1.5 },
        markerEnd: { type: "arrowclosed" as const, color: "#3b82f6", width: 16, height: 16 },
        data: { edgeType: "calls" },
      });
    }
  }
  for (const [job, conns] of Object.entries(manifest.job_connections_used || {})) {
    for (const conn of conns) {
      edges.push({
        id: `uses:${job}:${conn}`,
        source: `job:${job}`,
        target: `conn:${conn}`,
        type: "default",
        style: { stroke: "#a855f7", strokeWidth: 1, strokeDasharray: "6 3" },
        markerEnd: { type: "arrowclosed" as const, color: "#a855f7", width: 14, height: 14 },
        data: { edgeType: "uses" },
      });
    }
  }

  return {
    nodes,
    edges,
    groups,
    groupColorMap,
    stats: {
      jobCount: jobNames.size,
      connectionCount: connNames.size,
      edgeCount: edges.length,
      groupCount: groups.length,
    },
  };
}

const JOB_NODE_WIDTH = 200;
const JOB_NODE_HEIGHT = 40;
const CONN_NODE_WIDTH = 160;
const CONN_NODE_HEIGHT = 40;

export const JOB_NODE_WIDTH_COMPACT = 150;
export const JOB_NODE_HEIGHT_COMPACT = 36;
export const CONN_NODE_WIDTH_COMPACT = 120;
export const CONN_NODE_HEIGHT_COMPACT = 32;

function getNodeDimensions(node: Node, compact: boolean): { width: number; height: number } {
  if (node.id.startsWith("group:")) {
    return { width: GROUP_SUMMARY_NODE_WIDTH, height: GROUP_SUMMARY_NODE_HEIGHT };
  }
  if (node.id.startsWith("conn:")) {
    return {
      width: compact ? CONN_NODE_WIDTH_COMPACT : CONN_NODE_WIDTH,
      height: compact ? CONN_NODE_HEIGHT_COMPACT : CONN_NODE_HEIGHT,
    };
  }
  return {
    width: compact ? JOB_NODE_WIDTH_COMPACT : JOB_NODE_WIDTH,
    height: compact ? JOB_NODE_HEIGHT_COMPACT : JOB_NODE_HEIGHT,
  };
}

export function applyDagreLayout(
  nodes: Node[],
  edges: Edge[],
  direction: "TB" | "LR" = "TB",
  compact = false,
): Node[] {
  // Detect if overview mode (has group summary nodes) — use wider spacing
  const hasGroupNodes = nodes.some((n) => n.id.startsWith("group:"));
  const nodesep = hasGroupNodes ? 80 : (compact ? 30 : 60);
  const ranksep = hasGroupNodes ? 100 : (compact ? 40 : 80);

  const g = new dagre.graphlib.Graph();
  g.setDefaultEdgeLabel(() => ({}));
  g.setGraph({ rankdir: direction, nodesep, ranksep });

  for (const node of nodes) {
    const { width, height } = getNodeDimensions(node, compact);
    g.setNode(node.id, { width, height });
  }

  for (const edge of edges) {
    g.setEdge(edge.source, edge.target);
  }

  dagre.layout(g);

  return nodes.map((node) => {
    const pos = g.node(node.id);
    const { width, height } = getNodeDimensions(node, compact);
    return {
      ...node,
      position: { x: pos.x - width / 2, y: pos.y - height / 2 },
    };
  });
}

export function computeGroupBoundingBoxes(
  laidOutNodes: Node[],
  groupColorMap: Record<string, GroupColor>,
  compact: boolean,
): GroupBoundingBox[] {
  const padding = 24;
  const labelHeight = 24;

  const bounds = new Map<string, { minX: number; minY: number; maxX: number; maxY: number }>();

  for (const node of laidOutNodes) {
    if (!node.id.startsWith("job:")) continue;
    const group = node.data.group as string;
    if (!group) continue;

    const w = compact ? JOB_NODE_WIDTH_COMPACT : JOB_NODE_WIDTH;
    const h = compact ? JOB_NODE_HEIGHT_COMPACT : JOB_NODE_HEIGHT;

    const x = node.position.x;
    const y = node.position.y;

    const existing = bounds.get(group);
    if (existing) {
      existing.minX = Math.min(existing.minX, x);
      existing.minY = Math.min(existing.minY, y);
      existing.maxX = Math.max(existing.maxX, x + w);
      existing.maxY = Math.max(existing.maxY, y + h);
    } else {
      bounds.set(group, { minX: x, minY: y, maxX: x + w, maxY: y + h });
    }
  }

  const result: GroupBoundingBox[] = [];
  for (const [group, b] of bounds) {
    const color = groupColorMap[group];
    if (!color) continue;
    result.push({
      id: `group-bg:${group}`,
      group,
      color,
      x: b.minX - padding,
      y: b.minY - padding - labelHeight,
      width: b.maxX - b.minX + padding * 2,
      height: b.maxY - b.minY + padding * 2 + labelHeight,
    });
  }

  return result;
}

// ── Collapsed / Overview mode ───────────────────────────────────────

const GROUP_SUMMARY_NODE_WIDTH = 240;
const GROUP_SUMMARY_NODE_HEIGHT = 110;

const DIRECTION_MAP: Record<string, "in" | "out" | "both"> = {
  OnRamp: "in",
  OffRamp: "out",
  Bidirectional: "both",
};

/**
 * Build a mixed collapsed/expanded flow graph.
 * Groups NOT in `expandedGroups` become single summary nodes.
 * Groups IN `expandedGroups` render as individual job nodes.
 */
export function manifestToCollapsedFlow(
  manifest: ManifestData,
  connections: GraphConnectionInfo[],
  jobMetadata?: Record<string, JobScanMetadata>,
  expandedGroups?: Set<string>,
): FlowResult {
  const expanded = expandedGroups ?? new Set<string>();

  // ── Collect all job names ────────────────────────────────────────
  const jobNames = new Set<string>();
  for (const name of Object.keys(manifest.job_groups || {})) jobNames.add(name);
  for (const [caller, callees] of Object.entries(manifest.job_calls || {})) {
    jobNames.add(caller);
    for (const c of callees) jobNames.add(c);
  }
  for (const [callee, callers] of Object.entries(manifest.job_called_by || {})) {
    jobNames.add(callee);
    for (const c of callers) jobNames.add(c);
  }
  for (const [job, conns] of Object.entries(manifest.job_connections_used || {})) {
    jobNames.add(job);
  }

  // ── Build group → jobs map ───────────────────────────────────────
  const groupJobMap = new Map<string, string[]>();
  for (const name of jobNames) {
    const group = (manifest.job_groups || {})[name] || "Ungrouped";
    if (!groupJobMap.has(group)) groupJobMap.set(group, []);
    groupJobMap.get(group)!.push(name);
  }

  // ── Build group/color maps ───────────────────────────────────────
  const groups = [...groupJobMap.keys()].sort();
  const groupColorMap: Record<string, typeof GROUP_COLORS[0]> = {};
  for (const group of groups) {
    groupColorMap[group] = GROUP_COLORS[hashString(group) % GROUP_COLORS.length];
  }

  // ── job → group lookup ───────────────────────────────────────────
  const jobGroupLookup = new Map<string, string>();
  for (const [group, jobs] of groupJobMap) {
    for (const j of jobs) jobGroupLookup.set(j, group);
  }

  // Connection lookup
  const connLookup = new Map<string, GraphConnectionInfo>();
  for (const c of connections) connLookup.set(c.name, c);

  // Edge counts per job (reused for expanded jobs)
  const jobCallsOutCount = new Map<string, number>();
  const jobCalledByCount = new Map<string, number>();
  const jobConnCount = new Map<string, number>();
  for (const [caller, callees] of Object.entries(manifest.job_calls || {})) {
    jobCallsOutCount.set(caller, (jobCallsOutCount.get(caller) || 0) + callees.length);
    for (const c of callees) jobCalledByCount.set(c, (jobCalledByCount.get(c) || 0) + 1);
  }
  for (const [job, conns] of Object.entries(manifest.job_connections_used || {})) {
    jobConnCount.set(job, conns.length);
  }

  const nodes: Node[] = [];
  const edges: Edge[] = [];

  // Track which connections are actually referenced
  const referencedConns = new Set<string>();

  // ── Create nodes per group ───────────────────────────────────────
  for (const [group, jobs] of groupJobMap) {
    const color = groupColorMap[group] || GROUP_COLORS[0];

    if (expanded.has(group)) {
      // Expanded: individual job nodes (same as manifestToFlow)
      for (const name of jobs) {
        const meta = jobMetadata?.[name];
        nodes.push({
          id: `job:${name}`,
          type: "jobNode",
          position: { x: 0, y: 0 },
          data: {
            label: name,
            group,
            color,
            callsOut: jobCallsOutCount.get(name) || 0,
            calledBy: jobCalledByCount.get(name) || 0,
            connCount: jobConnCount.get(name) || 0,
            purpose: meta?.purpose || "",
            componentCount: meta?.component_count || 0,
            hasDocs: meta?.has_docs || false,
            externalSystems: meta?.external_systems || [],
            summary: meta?.summary || "",
            direction: meta?.direction || "",
          },
        });
      }
    } else {
      // Collapsed: single summary node
      const dirBreakdown = { in: 0, out: 0, both: 0, unknown: 0 };
      const systemSet = new Set<string>();
      let connCount = 0;

      for (const name of jobs) {
        const meta = jobMetadata?.[name];
        const dir = meta?.direction ? DIRECTION_MAP[meta.direction] : undefined;
        if (dir) dirBreakdown[dir]++;
        else dirBreakdown.unknown++;

        for (const sys of meta?.external_systems || []) systemSet.add(sys);
        connCount += (manifest.job_connections_used || {})[name]?.length || 0;
      }

      nodes.push({
        id: `group:${group}`,
        type: "groupSummaryNode",
        position: { x: 0, y: 0 },
        data: {
          label: group,
          groupName: group,
          color,
          jobCount: jobs.length,
          directionBreakdown: dirBreakdown,
          topSystems: [...systemSet].slice(0, 5),
          connectionCount: connCount,
        },
      });
    }
  }

  // ── Create edges ─────────────────────────────────────────────────

  // Track inter-group edges to aggregate (collapsed→collapsed gets one edge with count)
  const interGroupEdges = new Map<string, number>(); // "source→target" → count

  for (const [caller, callees] of Object.entries(manifest.job_calls || {})) {
    const callerGroup = jobGroupLookup.get(caller) || "Ungrouped";
    for (const callee of callees) {
      const calleeGroup = jobGroupLookup.get(callee) || "Ungrouped";

      const callerExpanded = expanded.has(callerGroup);
      const calleeExpanded = expanded.has(calleeGroup);

      if (callerExpanded && calleeExpanded) {
        edges.push({
          id: `calls:${caller}:${callee}`,
          source: `job:${caller}`,
          target: `job:${callee}`,
          type: "default",
          animated: true,
          style: { stroke: "#3b82f6", strokeWidth: 1.5 },
          markerEnd: { type: "arrowclosed" as const, color: "#3b82f6", width: 16, height: 16 },
          data: { edgeType: "calls" },
        });
      } else if (!callerExpanded && !calleeExpanded) {
        if (callerGroup === calleeGroup) continue;
        const key = `group:${callerGroup}→group:${calleeGroup}`;
        interGroupEdges.set(key, (interGroupEdges.get(key) || 0) + 1);
      } else {
        const sourceId = callerExpanded ? `job:${caller}` : `group:${callerGroup}`;
        const targetId = calleeExpanded ? `job:${callee}` : `group:${calleeGroup}`;
        const edgeKey = `mixed:${sourceId}→${targetId}`;
        if (!interGroupEdges.has(edgeKey)) {
          interGroupEdges.set(edgeKey, 1);
          edges.push({
            id: edgeKey,
            source: sourceId,
            target: targetId,
            type: "default",
            animated: true,
            style: { stroke: "#3b82f6", strokeWidth: 1.5 },
            markerEnd: { type: "arrowclosed" as const, color: "#3b82f6", width: 16, height: 16 },
            data: { edgeType: "calls" },
          });
        } else {
          interGroupEdges.set(edgeKey, interGroupEdges.get(edgeKey)! + 1);
        }
      }
    }
  }

  // Emit aggregated inter-group edges
  for (const [key, count] of interGroupEdges) {
    if (!key.startsWith("group:")) continue;
    const [source, target] = key.split("→");
    edges.push({
      id: `agg:${key}`,
      source,
      target,
      type: "default",
      animated: true,
      label: count > 1 ? String(count) : undefined,
      labelStyle: { fill: "#1e40af", fontWeight: 700, fontSize: 11 },
      labelBgStyle: { fill: "#dbeafe", fillOpacity: 0.9 },
      labelBgPadding: [6, 4] as [number, number],
      labelBgBorderRadius: 6,
      style: { stroke: "#3b82f6", strokeWidth: 2.5 },
      markerEnd: { type: "arrowclosed" as const, color: "#3b82f6", width: 20, height: 20 },
      data: { edgeType: "calls", aggregated: true },
    });
  }

  // ── Connection edges ─────────────────────────────────────────────
  for (const [job, conns] of Object.entries(manifest.job_connections_used || {})) {
    const jobGroup = jobGroupLookup.get(job) || "Ungrouped";
    const jobExpanded = expanded.has(jobGroup);

    for (const conn of conns) {
      if (jobExpanded) {
        referencedConns.add(conn);
        edges.push({
          id: `uses:${job}:${conn}`,
          source: `job:${job}`,
          target: `conn:${conn}`,
          type: "default",
          style: { stroke: "#a855f7", strokeWidth: 1, strokeDasharray: "6 3" },
          markerEnd: { type: "arrowclosed" as const, color: "#a855f7", width: 14, height: 14 },
          data: { edgeType: "uses" },
        });
      } else {
        const edgeId = `uses-group:${jobGroup}:${conn}`;
        if (!edges.some((e) => e.id === edgeId)) {
          referencedConns.add(conn);
          edges.push({
            id: edgeId,
            source: `group:${jobGroup}`,
            target: `conn:${conn}`,
            type: "default",
            style: { stroke: "#a855f7", strokeWidth: 1, strokeDasharray: "6 3" },
            markerEnd: { type: "arrowclosed" as const, color: "#a855f7", width: 14, height: 14 },
            data: { edgeType: "uses" },
          });
        }
      }
    }
  }

  // ── Connection nodes (only those referenced) ─────────────────────
  const connNames = new Set<string>();
  for (const conns of Object.values(manifest.job_connections_used || {})) {
    for (const c of conns) connNames.add(c);
  }
  for (const name of Object.keys(manifest.connection_used_by || {})) {
    connNames.add(name);
  }

  for (const name of connNames) {
    if (!referencedConns.has(name)) continue;
    const info = connLookup.get(name);
    const usedBy = (manifest.connection_used_by || {})[name] || [];
    nodes.push({
      id: `conn:${name}`,
      type: "connectionNode",
      position: { x: 0, y: 0 },
      data: {
        label: name,
        connectionType: info?.connection_type || "",
        host: info?.host || "",
        database: info?.database || "",
        usageCount: usedBy.length,
      },
    });
  }

  return {
    nodes,
    edges,
    groups,
    groupColorMap,
    stats: {
      jobCount: jobNames.size,
      connectionCount: referencedConns.size,
      edgeCount: edges.length,
      groupCount: groups.length,
    },
  };
}

export interface ExpandAnchor {
  savedPositions: Map<string, { x: number; y: number }>;
  action: "expand" | "collapse";
  groupName: string;
  groupPosition: { x: number; y: number };
}

/**
 * Partial layout: positions existing nodes at their saved positions
 * and runs a mini dagre for new nodes anchored at the group's position.
 */
export function localExpandLayout(
  nodes: Node[],
  edges: Edge[],
  anchor: ExpandAnchor,
  direction: "TB" | "LR",
  compact: boolean,
): Node[] {
  const { savedPositions, groupPosition } = anchor;

  // Separate into existing (have saved positions) and new nodes
  const existing: Node[] = [];
  const newNodes: Node[] = [];
  for (const node of nodes) {
    if (savedPositions.has(node.id)) {
      existing.push(node);
    } else {
      newNodes.push(node);
    }
  }

  // Position existing nodes at their saved positions
  const existingPositioned = existing.map((node) => ({
    ...node,
    position: { ...savedPositions.get(node.id)! },
  }));

  if (newNodes.length === 0) {
    return existingPositioned;
  }

  // For collapse (typically 1 new summary node): place directly at anchor
  if (anchor.action === "collapse") {
    const collapsePositioned = newNodes.map((node) => ({
      ...node,
      position: {
        x: groupPosition.x,
        y: groupPosition.y,
      },
    }));
    return [...existingPositioned, ...collapsePositioned];
  }

  // For expand: run mini dagre on new nodes with tight spacing
  const newNodeIds = new Set(newNodes.map((n) => n.id));
  const internalEdges = edges.filter(
    (e) => newNodeIds.has(e.source) && newNodeIds.has(e.target),
  );

  const g = new dagre.graphlib.Graph();
  g.setDefaultEdgeLabel(() => ({}));
  g.setGraph({ rankdir: direction, nodesep: compact ? 16 : 24, ranksep: compact ? 24 : 36 });
  for (const node of newNodes) {
    const { width, height } = getNodeDimensions(node, compact);
    g.setNode(node.id, { width, height });
  }
  for (const edge of internalEdges) {
    g.setEdge(edge.source, edge.target);
  }
  dagre.layout(g);
  const laid = newNodes.map((node) => {
    const pos = g.node(node.id);
    const { width, height } = getNodeDimensions(node, compact);
    return { ...node, position: { x: pos.x - width / 2, y: pos.y - height / 2 } };
  });

  // Compute centroid of the dagre result
  let cx = 0;
  let cy = 0;
  for (const n of laid) {
    cx += n.position.x;
    cy += n.position.y;
  }
  cx /= laid.length;
  cy /= laid.length;

  // Translate so centroid aligns with the group's old position
  const dx = groupPosition.x - cx;
  const dy = groupPosition.y - cy;
  const newPositioned = laid.map((node) => ({
    ...node,
    position: {
      x: node.position.x + dx,
      y: node.position.y + dy,
    },
  }));

  return [...existingPositioned, ...newPositioned];
}

/**
 * Layout using dagre's compound graph support.
 * Expanded group members are clustered within their parent group.
 */
export function applyCompoundDagreLayout(
  nodes: Node[],
  edges: Edge[],
  direction: "TB" | "LR" = "TB",
  compact: boolean = false,
  groupMembership: Map<string, string> = new Map(), // nodeId → groupId
): Node[] {
  const hasGroupNodes = nodes.some((n) => n.id.startsWith("group:"));
  const nodesep = hasGroupNodes ? 80 : (compact ? 30 : 60);
  const ranksep = hasGroupNodes ? 100 : (compact ? 50 : 100);

  const g = new dagre.graphlib.Graph({ compound: true });
  g.setDefaultEdgeLabel(() => ({}));
  g.setGraph({ rankdir: direction, nodesep, ranksep });

  // Add cluster parent nodes for each group that has members
  const activeGroups = new Set<string>();
  for (const groupId of groupMembership.values()) {
    activeGroups.add(groupId);
  }
  for (const groupId of activeGroups) {
    g.setNode(`cluster:${groupId}`, {});
  }

  for (const node of nodes) {
    const { width, height } = getNodeDimensions(node, compact);
    g.setNode(node.id, { width, height });

    // Set parent for grouped nodes
    const parentGroup = groupMembership.get(node.id);
    if (parentGroup && activeGroups.has(parentGroup)) {
      g.setParent(node.id, `cluster:${parentGroup}`);
    }
  }

  for (const edge of edges) {
    // Only add edge if both source and target exist
    if (g.hasNode(edge.source) && g.hasNode(edge.target)) {
      g.setEdge(edge.source, edge.target);
    }
  }

  dagre.layout(g);

  return nodes.map((node) => {
    const pos = g.node(node.id);
    const { width, height } = getNodeDimensions(node, compact);
    return {
      ...node,
      position: { x: pos.x - width / 2, y: pos.y - height / 2 },
    };
  });
}
