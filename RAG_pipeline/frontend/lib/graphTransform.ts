import dagre from "@dagrejs/dagre";
import type { Node, Edge } from "@xyflow/react";
import {
  AGGREGATE_EDGE_COLOR,
  CALL_EDGE_COLOR,
  CALL_EDGE_STYLE,
  USES_EDGE_COLOR,
  USES_EDGE_STYLE,
} from "@/lib/nodeStyles";

// Project-level edges use smooth/curved routing so overlapping near-straight
// runs fan out into readable arcs. Scoped to this module ONLY — the level-2
// job view (jobGraphTransform.ts / applyJobGraphLayout) is deliberately left
// on its own routing and must not be touched.
const PROJECT_EDGE_TYPE = "smoothstep";

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

/**
 * Project-level connection-visibility filter. When connections are hidden we
 * drop every connection node AND every edge that touches one, so the layout
 * reflows to jobs + job->job calls (+ collapsed group summaries) with no
 * dangling edges. Endpoint-based rather than edgeType-based so it stays correct
 * for any current or future edge that points at a connection node.
 */
export function applyConnectionVisibility(
  nodes: Node[],
  edges: Edge[],
  showConnections: boolean,
): { nodes: Node[]; edges: Edge[] } {
  if (showConnections) return { nodes, edges };
  return {
    nodes: nodes.filter((n) => !n.id.startsWith("conn:")),
    edges: edges.filter(
      (e) => !e.source.startsWith("conn:") && !e.target.startsWith("conn:"),
    ),
  };
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
        type: PROJECT_EDGE_TYPE,
        animated: false,
        style: { ...CALL_EDGE_STYLE },
        markerEnd: { type: "arrowclosed" as const, color: CALL_EDGE_COLOR, width: 16, height: 16 },
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
        type: PROJECT_EDGE_TYPE,
        style: { ...USES_EDGE_STYLE },
        markerEnd: { type: "arrowclosed" as const, color: USES_EDGE_COLOR, width: 14, height: 14 },
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

// Node footprint keyed off the id prefix alone (group:/conn:/job:). Kept
// id-based so callers that only hold a node id (e.g. the removed-node "hole"
// in localExpandLayout below) can size a node without a full Node object.
export function getDimensionsForId(id: string, compact: boolean): { width: number; height: number } {
  if (id.startsWith("group:")) {
    return { width: GROUP_SUMMARY_NODE_WIDTH, height: GROUP_SUMMARY_NODE_HEIGHT };
  }
  if (id.startsWith("conn:")) {
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

function getNodeDimensions(node: Node, compact: boolean): { width: number; height: number } {
  return getDimensionsForId(node.id, compact);
}

// ── De-flattening oversized ranks ────────────────────────────────────
//
// dagre only creates a new rank when an edge forces one. A large set of
// nodes with few or no edges between them (e.g. a group of 70+ jobs that
// rarely call each other directly) all land on the SAME rank, and dagre
// spreads them along the perpendicular axis with no limit - the result is
// one endless line ("barcode"), not a genuine layout problem so much as an
// absent one. This wraps any rank above `MAX_NODES_PER_FLAT_RANK` into a
// grid instead, preserving whatever order dagre/barycenter did assign.
// `clusterOf` (compound layouts only) keeps wrapping scoped within a
// node's own group cluster, never merging two groups' members together.

export const MAX_NODES_PER_FLAT_RANK = 12;

export function deflattenOversizedRanks(
  nodes: Node[],
  direction: "TB" | "LR",
  compact: boolean,
  nodesep: number,
  ranksep: number,
  clusterOf?: Map<string, string>,
): Node[] {
  const rankAxis: "x" | "y" = direction === "TB" ? "y" : "x";
  const orderAxis: "x" | "y" = direction === "TB" ? "x" : "y";

  const buckets = new Map<string, Node[]>();
  for (const n of nodes) {
    const rankCoord = Math.round(n.position[rankAxis]);
    const cluster = clusterOf?.get(n.id) ?? "";
    const key = `${cluster}:${rankCoord}`;
    if (!buckets.has(key)) buckets.set(key, []);
    buckets.get(key)!.push(n);
  }

  const result: Node[] = [];
  for (const members of buckets.values()) {
    if (members.length <= MAX_NODES_PER_FLAT_RANK) {
      result.push(...members);
      continue;
    }

    // Preserve dagre's chosen order along the perpendicular axis.
    const sorted = [...members].sort((a, b) => a.position[orderAxis] - b.position[orderAxis]);
    const rankBase = sorted[0].position[rankAxis];
    const orderBase = sorted[0].position[orderAxis];

    let maxW = 0;
    let maxH = 0;
    for (const n of sorted) {
      const { width, height } = getNodeDimensions(n, compact);
      maxW = Math.max(maxW, width);
      maxH = Math.max(maxH, height);
    }
    const along = direction === "TB" ? maxW + nodesep : maxH + nodesep;
    const across = direction === "TB" ? maxH + ranksep : maxW + ranksep;

    sorted.forEach((n, idx) => {
      const row = Math.floor(idx / MAX_NODES_PER_FLAT_RANK);
      const col = idx % MAX_NODES_PER_FLAT_RANK;
      const orderPos = orderBase + col * along;
      const rankPos = rankBase + row * across;
      result.push({
        ...n,
        position: direction === "TB" ? { x: orderPos, y: rankPos } : { x: rankPos, y: orderPos },
      });
    });
  }

  return result;
}

export function applyDagreLayout(
  nodes: Node[],
  edges: Edge[],
  direction: "TB" | "LR" = "TB",
  compact = false,
): Node[] {
  // Detect if overview mode (has group summary nodes) — use wider spacing.
  // Rank separation is intentionally generous: smoothstep edges arc between
  // ranks, so extra room between ranks trades vertical space for far fewer
  // edge crossings and overlaps (the project-level "spaghetti fan" fix).
  const hasGroupNodes = nodes.some((n) => n.id.startsWith("group:"));
  const nodesep = hasGroupNodes ? 90 : (compact ? 40 : 70);
  const ranksep = hasGroupNodes ? 150 : (compact ? 80 : 130);

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

  const positioned = nodes.map((node) => {
    const pos = g.node(node.id);
    const { width, height } = getNodeDimensions(node, compact);
    return {
      ...node,
      position: { x: pos.x - width / 2, y: pos.y - height / 2 },
    };
  });

  return deflattenOversizedRanks(positioned, direction, compact, nodesep, ranksep);
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
  // Dedup the aggregated group->system edges: at most one per (group, conn)
  // pair regardless of how many jobs in a collapsed group use the connection.
  const emittedGroupUses = new Set<string>();

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
          type: PROJECT_EDGE_TYPE,
          animated: false,
          style: { ...CALL_EDGE_STYLE },
          markerEnd: { type: "arrowclosed" as const, color: CALL_EDGE_COLOR, width: 16, height: 16 },
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
            type: PROJECT_EDGE_TYPE,
            animated: false,
            style: { ...CALL_EDGE_STYLE },
            markerEnd: { type: "arrowclosed" as const, color: CALL_EDGE_COLOR, width: 16, height: 16 },
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
      type: PROJECT_EDGE_TYPE,
      animated: false,
      label: count > 1 ? String(count) : undefined,
      labelStyle: { fill: "#475569", fontWeight: 700, fontSize: 11 },
      labelBgStyle: { fill: "#f1f5f9", fillOpacity: 0.95 },
      labelBgPadding: [6, 4] as [number, number],
      labelBgBorderRadius: 6,
      style: { stroke: AGGREGATE_EDGE_COLOR, strokeWidth: 2.5 },
      markerEnd: { type: "arrowclosed" as const, color: AGGREGATE_EDGE_COLOR, width: 20, height: 20 },
      data: { edgeType: "calls", aggregated: true },
    });
  }

  // ── Connection edges ─────────────────────────────────────────────
  //
  // Expanded group  -> one edge PER JOB per connection (`uses:{job}:{conn}`):
  //   the detail view wants to see exactly which job talks to which system.
  // Collapsed group -> ONE aggregated edge per (group, connection) pair
  //   (`grp-uses:{group}:{conn}`): a connection "belongs" to a group when ANY
  //   job in that group uses it. This is the overview's clean answer to "what
  //   does this group talk to?" instead of every job fanning its own dashed
  //   line to the shared connection nodes (the old spaghetti). Mixed state
  //   works because the decision is made per job by its own group: a connection
  //   used by both an expanded and a collapsed group receives the expanded
  //   group's per-job edge AND the collapsed group's aggregated edge, while the
  //   single conn node is emitted once below.
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
          type: PROJECT_EDGE_TYPE,
          style: { ...USES_EDGE_STYLE },
          markerEnd: { type: "arrowclosed" as const, color: USES_EDGE_COLOR, width: 14, height: 14 },
          data: { edgeType: "uses" },
        });
      } else {
        const edgeId = `grp-uses:${jobGroup}:${conn}`;
        if (!emittedGroupUses.has(edgeId)) {
          emittedGroupUses.add(edgeId);
          referencedConns.add(conn);
          edges.push({
            id: edgeId,
            source: `group:${jobGroup}`,
            target: `conn:${conn}`,
            type: PROJECT_EDGE_TYPE,
            style: { ...USES_EDGE_STYLE },
            markerEnd: { type: "arrowclosed" as const, color: USES_EDGE_COLOR, width: 14, height: 14 },
            data: { edgeType: "group-uses" },
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
  // Generous rank separation for the same reason as applyDagreLayout: curved
  // project-level edges read far better with breathing room between ranks.
  const hasGroupNodes = nodes.some((n) => n.id.startsWith("group:"));
  const nodesep = hasGroupNodes ? 90 : (compact ? 40 : 70);
  const ranksep = hasGroupNodes ? 150 : (compact ? 90 : 150);

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

  const positioned = nodes.map((node) => {
    const pos = g.node(node.id);
    const { width, height } = getNodeDimensions(node, compact);
    return {
      ...node,
      position: { x: pos.x - width / 2, y: pos.y - height / 2 },
    };
  });

  return deflattenOversizedRanks(positioned, direction, compact, nodesep, ranksep, groupMembership);
}
