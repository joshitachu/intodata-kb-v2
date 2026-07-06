"use client";

import { memo, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ChevronDown } from "lucide-react";
import { useChatContext } from "@/contexts/ChatContext";
import {
  ReactFlow,
  MiniMap,
  Controls,
  Background,
  BackgroundVariant,
  useNodesState,
  useEdgesState,
  useReactFlow,
  ReactFlowProvider,
  type Node,
  type Edge,
  type NodeMouseHandler,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";

import { JobNode } from "@/components/graph/JobNode";
import { ConnectionNode } from "@/components/graph/ConnectionNode";
import { GroupSummaryNode } from "@/components/graph/GroupSummaryNode";
import { GraphLegend } from "@/components/graph/GraphLegend";
import { GraphToolbar } from "@/components/graph/GraphToolbar";
import { JobDetailPanel } from "@/components/graph/JobDetailPanel";
import { MobileTopBar } from "@/components/mobile/MobileTopBar";
import { MobileBottomSheet } from "@/components/mobile/MobileBottomSheet";
import { useViewport } from "@/hooks/useViewport";
import { Maximize2 } from "lucide-react";
import {
  manifestToFlow,
  manifestToCollapsedFlow,
  applyDagreLayout,
  applyCompoundDagreLayout,
  localExpandLayout,
  computeGroupBoundingBoxes,
  type ManifestData,
  type GraphConnectionInfo,
  type JobScanMetadata,
  type FlowResult,
  type GroupBoundingBox,
  type ExpandAnchor,
} from "@/lib/graphTransform";

interface ProjectOption {
  id: string;
  display_name: string;
  platform: string;
  job_count: number;
}

interface GraphData {
  project_id: string;
  manifest: ManifestData;
  connections: GraphConnectionInfo[];
  job_metadata?: Record<string, JobScanMetadata>;
}

// Group background node rendered behind job clusters
const GroupBackgroundNode = memo(function GroupBackgroundNode({ data }: { data: GroupBoundingBox & { width: number; height: number } }) {
  return (
    <div
      className="rounded-2xl pointer-events-none"
      style={{
        width: data.width,
        height: data.height,
        background: `linear-gradient(135deg, ${data.color.border}08, ${data.color.border}04)`,
        border: `1px dashed ${data.color.border}25`,
      }}
    >
      <span
        className="absolute top-2 left-3.5 text-[10px] font-bold tracking-wide pointer-events-none uppercase"
        style={{ color: `${data.color.border}60` }}
      >
        {data.group}
      </span>
    </div>
  );
});

const nodeTypes = {
  jobNode: JobNode,
  connectionNode: ConnectionNode,
  groupBackground: GroupBackgroundNode,
  groupSummaryNode: GroupSummaryNode,
};

function TopologyContent() {
  const { selectedProject: globalProject, setSelectedProject: setGlobalProject } = useChatContext();
  const [projects, setProjects] = useState<ProjectOption[]>([]);
  const [selectedProject, setSelectedProjectLocal] = useState("");
  const [graphData, setGraphData] = useState<GraphData | null>(null);
  const [flowResult, setFlowResult] = useState<FlowResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [direction, setDirection] = useState<"TB" | "LR">("TB");
  const [showConnections, setShowConnections] = useState(true);
  const [selectedJob, setSelectedJob] = useState<string | null>(null);

  // Existing state
  const [hiddenGroups, setHiddenGroups] = useState<Set<string>>(new Set());
  const [searchQuery, setSearchQuery] = useState("");
  const [hoveredNodeId, setHoveredNodeId] = useState<string | null>(null);
  // Always start in compact node rendering — full nodes are memory-heavy on large graphs.
  const [compactMode, setCompactMode] = useState(true);

  // Always start in collapsed (group-summary) mode regardless of project size.
  // The user can opt into the full expanded view via the toolbar. Rendering
  // every job + connection on first load uses too much memory on big projects.
  const [collapsedMode, setCollapsedMode] = useState<boolean | null>(true);
  const [expandedGroups, setExpandedGroups] = useState<Set<string>>(new Set());

  const [nodes, setNodes, onNodesChange] = useNodesState<Node>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);
  const { fitView, getNodes } = useReactFlow();
  const { isMobile } = useViewport();

  // Write to both local and global state when user picks a project
  const setSelectedProject = useCallback((id: string) => {
    setSelectedProjectLocal(id);
    setGlobalProject(id);
  }, [setGlobalProject]);

  // Ref to store position context before expand/collapse for in-place layout
  const expandAnchorRef = useRef<ExpandAnchor | null>(null);

  // Tracks the last "meaningful" layout identity that triggered a fitView.
  // Prevents fitView animations from stacking when the layout effect re-runs
  // for unrelated reasons (e.g. groupMembership rebuilding with the same shape).
  // ReactFlow's fitView animation dominates frame time on heavy graphs.
  const lastFitKeyRef = useRef<string | null>(null);

  // Determine effective collapsed mode (resolve auto)
  const isCollapsed = useMemo(() => {
    if (collapsedMode !== null) return collapsedMode;
    // Auto: collapse if >30 jobs
    return (flowResult?.stats.jobCount ?? 0) > 30;
  }, [collapsedMode, flowResult]);

  // Build the flow result based on mode
  const activeFlowResult = useMemo(() => {
    if (!graphData) return null;
    if (isCollapsed) {
      return manifestToCollapsedFlow(
        graphData.manifest,
        graphData.connections,
        graphData.job_metadata,
        expandedGroups,
      );
    }
    return manifestToFlow(graphData.manifest, graphData.connections, graphData.job_metadata);
  }, [graphData, isCollapsed, expandedGroups]);

  // Keep flowResult in sync
  useEffect(() => {
    if (activeFlowResult) {
      setFlowResult(activeFlowResult);
    }
  }, [activeFlowResult]);

  // Build group membership map for compound layout (jobNodeId → groupId)
  const groupMembership = useMemo(() => {
    const map = new Map<string, string>();
    if (!graphData) return map;
    const jobGroups = graphData.manifest.job_groups || {};
    for (const [job, group] of Object.entries(jobGroups)) {
      map.set(`job:${job}`, group);
    }
    return map;
  }, [graphData]);

  // Visible nodes/edges from flowResult + filters
  const { visibleNodes, visibleEdges } = useMemo(() => {
    if (!flowResult) return { visibleNodes: [] as Node[], visibleEdges: [] as Edge[] };

    let vNodes = flowResult.nodes;
    let vEdges = flowResult.edges;

    if (hiddenGroups.size > 0) {
      const hiddenNodeIds = new Set<string>();
      for (const node of vNodes) {
        if (node.id.startsWith("job:") && hiddenGroups.has(node.data.group as string)) {
          hiddenNodeIds.add(node.id);
        }
        if (node.id.startsWith("group:") && hiddenGroups.has(node.data.groupName as string)) {
          hiddenNodeIds.add(node.id);
        }
      }
      vNodes = vNodes.filter((n) => !hiddenNodeIds.has(n.id));
      vEdges = vEdges.filter((e) => !hiddenNodeIds.has(e.source) && !hiddenNodeIds.has(e.target));
      const connectedNodeIds = new Set<string>();
      for (const e of vEdges) {
        connectedNodeIds.add(e.source);
        connectedNodeIds.add(e.target);
      }
      vNodes = vNodes.filter((n) => !n.id.startsWith("conn:") || connectedNodeIds.has(n.id));
    }

    if (!showConnections) {
      vNodes = vNodes.filter((n) => !n.id.startsWith("conn:"));
      vEdges = vEdges.filter((e) => e.data?.edgeType !== "uses");
    }

    return { visibleNodes: vNodes, visibleEdges: vEdges };
  }, [flowResult, hiddenGroups, showConnections]);

  // Fetch available projects
  useEffect(() => {
    fetch("/api/deep-dive/projects")
      .then((res) => res.json())
      .then((data) => {
        if (data.projects) {
          setProjects(data.projects);
        }
      })
      .catch(() => setError("Failed to load projects"));
  }, []);

  // Sync with global project selection, fall back to first scanned project
  useEffect(() => {
    if (projects.length === 0) return;
    if (globalProject && projects.some((p) => p.id === globalProject)) {
      setSelectedProjectLocal(globalProject);
    } else if (!selectedProject) {
      setSelectedProjectLocal(projects[0].id);
    }
  }, [globalProject, projects]); // eslint-disable-line react-hooks/exhaustive-deps

  // Reset state on project change
  useEffect(() => {
    if (!selectedProject) return;
    setLoading(true);
    setError(null);
    setSelectedJob(null);
    setHiddenGroups(new Set());
    setSearchQuery("");
    setHoveredNodeId(null);
    // Force the lightweight starting view on every project switch:
    //   compact nodes + collapsed (group-summary) mode.
    // Never default to the full expanded graph — it blows up memory on
    // projects with many jobs/connections.
    setCompactMode(true);
    setCollapsedMode(true);
    setExpandedGroups(new Set());
    // Reset the fit-key so the new project always gets an initial fitView.
    lastFitKeyRef.current = null;

    fetch(`/api/projects/${encodeURIComponent(selectedProject)}/graph`)
      .then(async (res) => {
        if (!res.ok) {
          const data = await res.json().catch(() => null);
          throw new Error(data?.error || `Failed to load graph (${res.status})`);
        }
        return res.json();
      })
      .then((data: GraphData) => {
        setGraphData(data);
      })
      .catch((err) => {
        setError(err instanceof Error ? err.message : "Failed to load graph");
        setGraphData(null);
        setFlowResult(null);
      })
      .finally(() => setLoading(false));
  }, [selectedProject]);

  // Apply layout when visible nodes/edges, direction, or compact mode changes
  useEffect(() => {
    if (!flowResult || visibleNodes.length === 0) {
      setNodes([]);
      setEdges([]);
      return;
    }

    // Consume the anchor ref (if set by expand/collapse click)
    const anchor = expandAnchorRef.current;
    expandAnchorRef.current = null;

    // Add compact flag to node data
    const nodesWithCompact = visibleNodes.map((n) => ({
      ...n,
      data: { ...n.data, compact: compactMode },
    }));

    let laid: Node[];

    if (anchor) {
      // Partial layout: expand/collapse in place
      laid = localExpandLayout(nodesWithCompact, visibleEdges, anchor, direction, compactMode);
    } else {
      // Full layout (initial load, mode switch, direction change, etc.)
      const hasJobNodes = nodesWithCompact.some((n) => n.id.startsWith("job:"));
      const useCompound = hasJobNodes && groupMembership.size > 0;

      if (useCompound) {
        laid = applyCompoundDagreLayout(
          nodesWithCompact,
          visibleEdges,
          direction,
          compactMode,
          groupMembership,
        );
      } else {
        laid = applyDagreLayout(nodesWithCompact, visibleEdges, direction, compactMode);
      }
    }

    // Compute group bounding boxes (only for expanded job nodes in view)
    const groupBoxes = computeGroupBoundingBoxes(
      laid,
      flowResult.groupColorMap,
      compactMode,
    );

    // Create background nodes from bounding boxes
    const bgNodes: Node[] = groupBoxes.map((box) => ({
      id: box.id,
      type: "groupBackground",
      position: { x: box.x, y: box.y },
      data: { ...box, width: box.width, height: box.height },
      draggable: false,
      selectable: false,
      connectable: false,
      style: { zIndex: -1, width: box.width, height: box.height },
    }));

    // Initialize dimmed/highlighted on every laid-out node so the hover
    // effect's same-reference fast path works on the FIRST hover after a
    // layout. Without these defaults, n.data.dimmed is undefined and the
    // strict-equality `=== false` check fails for every node, causing the
    // entire graph to be rewritten on the first mouse-enter — exactly when
    // the user notices stutter.
    const laidWithDefaults = laid.map((n) => ({
      ...n,
      data: { ...n.data, dimmed: false, highlighted: false },
    }));
    setNodes([...bgNodes, ...laidWithDefaults]);
    setEdges(visibleEdges);

    // Smart zoom based on action
    if (anchor?.action === "expand") {
      // Zoom to the expanded group's nodes
      requestAnimationFrame(() => {
        const expandedJobNodes = laid.filter(
          (n) => n.id.startsWith("job:") && (n.data.group as string) === anchor.groupName,
        );
        if (expandedJobNodes.length > 0) {
          fitView({
            nodes: expandedJobNodes.map((n) => ({ id: n.id })),
            padding: 0.4,
            duration: 400,
          });
        }
      });
    } else if (!anchor) {
      // Full layout: fit everything — but only when the meaningful layout
      // identity changed. Otherwise the 300ms zoom animation re-fires every
      // time groupMembership rebuilds with the same shape, stealing the
      // viewport and dominating frame time on heavy graphs.
      const fitKey = `${selectedProject}|${direction}|${compactMode}|${laid.length}`;
      if (fitKey !== lastFitKeyRef.current) {
        lastFitKeyRef.current = fitKey;
        requestAnimationFrame(() => {
          fitView({ padding: 0.1, duration: 300 });
        });
      }
    }
    // Collapse: no zoom change (keep current viewport)
  }, [flowResult, visibleNodes, visibleEdges, direction, compactMode, groupMembership, selectedProject, setNodes, setEdges, fitView]);

  // Search matching — in collapsed mode, also match job names inside collapsed groups
  const searchMatchIds = useMemo<Set<string> | null>(() => {
    if (!searchQuery.trim()) return null;
    const q = searchQuery.toLowerCase();
    const matches = new Set<string>();

    for (const node of visibleNodes) {
      const label = (node.data.label as string) || "";
      if (label.toLowerCase().includes(q)) {
        matches.add(node.id);
      }
    }

    // In collapsed mode, also search within collapsed group job names
    if (isCollapsed && graphData) {
      const jobGroups = graphData.manifest.job_groups || {};
      for (const [job, group] of Object.entries(jobGroups)) {
        if (job.toLowerCase().includes(q) && !expandedGroups.has(group)) {
          // The group summary node matches
          matches.add(`group:${group}`);
        }
      }
    }

    return matches;
  }, [searchQuery, visibleNodes, isCollapsed, graphData, expandedGroups]);

  // Track previous searchMatchIds to avoid redundant updates
  const prevSearchRef = useRef<Set<string> | null>(null);

  // Apply search dimming.
  // CRITICAL: returns same-reference nodes/edges when their state hasn't
  // actually changed. ReactFlow's setNodes reconciles the entire graph and
  // re-renders any node whose data reference changed; React.memo on the node
  // components skips render only when the data ref is identical. Without this
  // stability, every search keystroke rewrites all 80+ nodes.
  useEffect(() => {
    if (searchMatchIds === prevSearchRef.current) {
      return;
    }
    prevSearchRef.current = searchMatchIds;

    if (searchMatchIds === null) {
      setNodes((nds) =>
        nds.map((n) => {
          if (n.id.startsWith("group-bg:")) return n;
          if (n.data.dimmed === false && n.data.highlighted === false) return n;
          return { ...n, data: { ...n.data, dimmed: false, highlighted: false } };
        }),
      );
      setEdges((eds) =>
        eds.map((e) => {
          if (e.style?.opacity === undefined) return e;
          const { opacity: _drop, ...restStyle } = e.style;
          return { ...e, style: restStyle };
        }),
      );
      return;
    }

    setNodes((nds) =>
      nds.map((n) => {
        if (n.id.startsWith("group-bg:")) return n;
        const isMatch = searchMatchIds.has(n.id);
        if (n.data.dimmed === !isMatch && n.data.highlighted === isMatch) return n;
        return {
          ...n,
          data: { ...n.data, dimmed: !isMatch, highlighted: isMatch },
        };
      }),
    );
    setEdges((eds) =>
      eds.map((e) => {
        const visible = searchMatchIds.has(e.source) || searchMatchIds.has(e.target);
        const newOpacity = visible ? undefined : 0.15;
        if ((e.style?.opacity ?? undefined) === newOpacity) return e;
        if (newOpacity === undefined) {
          const { opacity: _drop, ...restStyle } = e.style ?? {};
          return { ...e, style: restStyle };
        }
        return { ...e, style: { ...e.style, opacity: newOpacity } };
      }),
    );

    // Only auto-zoom when the match set is small enough to be useful.
    // Single-letter searches that match dozens of nodes shouldn't trigger
    // a 400ms zoom-fit animation across the entire viewport on every
    // keystroke — that animation dominates frame time on heavy graphs.
    if (searchMatchIds.size > 0 && searchMatchIds.size <= 8) {
      requestAnimationFrame(() => {
        fitView({
          nodes: [...searchMatchIds].map((id) => ({ id })),
          padding: 0.3,
          duration: 400,
        });
      });
    }
  }, [searchMatchIds, setNodes, setEdges, fitView]);

  // Adjacency map for hover
  const adjacencyMap = useMemo(() => {
    const map = new Map<string, Set<string>>();
    for (const edge of visibleEdges) {
      if (!map.has(edge.source)) map.set(edge.source, new Set());
      if (!map.has(edge.target)) map.set(edge.target, new Set());
      map.get(edge.source)!.add(edge.target);
      map.get(edge.target)!.add(edge.source);
    }
    return map;
  }, [visibleEdges]);

  // Track previous hoveredNodeId to early-out when state hasn't changed.
  // ReactFlow's setNodes reconciles the entire graph; without this guard
  // every unrelated re-render would rewrite all node objects.
  const prevHoverRef = useRef<string | null>(null);

  // Apply hover dimming.
  // Same reference-stability rule as the search effect: only return new node
  // refs for nodes whose dimmed/highlighted state actually changed. This is
  // the dominant perf path because hover events fire on every mouse-enter.
  useEffect(() => {
    if (searchMatchIds !== null) return;
    if (hoveredNodeId === prevHoverRef.current) return;
    prevHoverRef.current = hoveredNodeId;

    if (hoveredNodeId === null) {
      setNodes((nds) =>
        nds.map((n) => {
          if (n.id.startsWith("group-bg:")) return n;
          if (n.data.dimmed === false && n.data.highlighted === false) return n;
          return { ...n, data: { ...n.data, dimmed: false, highlighted: false } };
        }),
      );
      setEdges((eds) =>
        eds.map((e) => {
          if (e.style?.opacity === undefined) return e;
          const { opacity: _drop, ...restStyle } = e.style;
          return { ...e, style: restStyle };
        }),
      );
      return;
    }

    const neighbors = adjacencyMap.get(hoveredNodeId) || new Set<string>();
    const connectedSet = new Set<string>([hoveredNodeId, ...neighbors]);

    setNodes((nds) =>
      nds.map((n) => {
        if (n.id.startsWith("group-bg:")) return n;
        const isConnected = connectedSet.has(n.id);
        const newDimmed = !isConnected;
        const newHighlighted = n.id === hoveredNodeId;
        if (n.data.dimmed === newDimmed && n.data.highlighted === newHighlighted) return n;
        return {
          ...n,
          data: { ...n.data, dimmed: newDimmed, highlighted: newHighlighted },
        };
      }),
    );
    setEdges((eds) =>
      eds.map((e) => {
        const visible = connectedSet.has(e.source) && connectedSet.has(e.target);
        const newOpacity = visible ? undefined : 0.15;
        if ((e.style?.opacity ?? undefined) === newOpacity) return e;
        if (newOpacity === undefined) {
          const { opacity: _drop, ...restStyle } = e.style ?? {};
          return { ...e, style: restStyle };
        }
        return { ...e, style: { ...e.style, opacity: newOpacity } };
      }),
    );
  }, [hoveredNodeId, searchMatchIds, adjacencyMap, setNodes, setEdges]);

  // Callbacks
  const handleToggleDirection = useCallback(() => {
    setDirection((d) => (d === "TB" ? "LR" : "TB"));
  }, []);

  const handleToggleConnections = useCallback(() => {
    setShowConnections((s) => !s);
  }, []);

  const handleFitView = useCallback(() => {
    fitView({ padding: 0.1, duration: 300 });
  }, [fitView]);

  const handleNodeClick: NodeMouseHandler = useCallback((_event, node) => {
    if (node.id.startsWith("job:")) {
      const jobName = node.id.slice(4);
      setSelectedJob(jobName);
    } else if (node.id.startsWith("group:")) {
      // Capture all current positions before toggling
      const groupName = node.id.slice(6);
      const currentNodes = getNodes();
      const positions = new Map<string, { x: number; y: number }>();
      currentNodes.forEach((n) => positions.set(n.id, n.position));

      expandAnchorRef.current = {
        savedPositions: positions,
        action: "expand",
        groupName,
        groupPosition: node.position,
      };

      setExpandedGroups((prev) => {
        const next = new Set(prev);
        if (next.has(groupName)) {
          next.delete(groupName);
        } else {
          next.add(groupName);
        }
        return next;
      });
    }
  }, [getNodes]);

  const handleClosePanel = useCallback(() => {
    setSelectedJob(null);
  }, []);

  // Group toggle: in collapsed mode toggles expand/collapse, in detail mode toggles hide/show
  const handleToggleGroup = useCallback((group: string) => {
    if (isCollapsed) {
      const currentNodes = getNodes();
      const positions = new Map<string, { x: number; y: number }>();
      currentNodes.forEach((n) => positions.set(n.id, n.position));

      const isExpanding = !expandedGroups.has(group);
      let groupPosition: { x: number; y: number };

      if (isExpanding) {
        const groupNode = currentNodes.find((n) => n.id === `group:${group}`);
        groupPosition = groupNode?.position ?? { x: 0, y: 0 };
      } else {
        // Centroid of the group's job nodes
        const groupJobs = currentNodes.filter(
          (n) => n.id.startsWith("job:") && n.data.group === group,
        );
        if (groupJobs.length > 0) {
          groupPosition = {
            x: groupJobs.reduce((s, n) => s + n.position.x, 0) / groupJobs.length,
            y: groupJobs.reduce((s, n) => s + n.position.y, 0) / groupJobs.length,
          };
        } else {
          groupPosition = { x: 0, y: 0 };
        }
      }

      expandAnchorRef.current = {
        savedPositions: positions,
        action: isExpanding ? "expand" : "collapse",
        groupName: group,
        groupPosition,
      };

      setExpandedGroups((prev) => {
        const next = new Set(prev);
        if (next.has(group)) {
          next.delete(group);
        } else {
          next.add(group);
        }
        return next;
      });
    } else {
      setHiddenGroups((prev) => {
        const next = new Set(prev);
        if (next.has(group)) {
          next.delete(group);
        } else {
          next.add(group);
        }
        return next;
      });
    }
  }, [isCollapsed, getNodes, expandedGroups]);

  const handleShowAllGroups = useCallback(() => {
    if (isCollapsed) {
      setExpandedGroups(new Set());
    } else {
      setHiddenGroups(new Set());
    }
  }, [isCollapsed]);

  const handleHideAllGroups = useCallback(() => {
    if (isCollapsed) {
      setExpandedGroups(new Set());
    } else if (flowResult) {
      setHiddenGroups(new Set(flowResult.groups));
    }
  }, [isCollapsed, flowResult]);

  const handleCollapseAll = useCallback(() => {
    setCollapsedMode(true);
    setExpandedGroups(new Set());
  }, []);

  const handleExpandAll = useCallback(() => {
    setCollapsedMode(false);
    setExpandedGroups(new Set());
    setHiddenGroups(new Set());
  }, []);

  const handleSearchChange = useCallback((query: string) => {
    setSearchQuery(query);
  }, []);

  const handleToggleCompact = useCallback(() => {
    setCompactMode((c) => !c);
  }, []);

  const handleNodeMouseEnter: NodeMouseHandler = useCallback((_event, node) => {
    if (!node.id.startsWith("group-bg:")) {
      setHoveredNodeId(node.id);
    }
  }, []);

  const handleNodeMouseLeave: NodeMouseHandler = useCallback(() => {
    setHoveredNodeId(null);
  }, []);

  const stats = useMemo(
    () =>
      flowResult?.stats || {
        jobCount: 0,
        connectionCount: 0,
        edgeCount: 0,
        groupCount: 0,
      },
    [flowResult],
  );

  const groupColorMap = useMemo(
    () => flowResult?.groupColorMap || {},
    [flowResult],
  );

  const groups = useMemo(
    () => flowResult?.groups || [],
    [flowResult],
  );

  const minimapNodeColor = useCallback((node: Node) => {
    if (node.id.startsWith("group-bg:")) return "transparent";
    if (node.id.startsWith("conn:")) return "#a855f7";
    if (node.id.startsWith("group:")) {
      const color = (node.data as Record<string, unknown>).color as { border: string } | undefined;
      return color?.border || "#6366f1";
    }
    const color = (node.data as Record<string, unknown>).color as { border: string } | undefined;
    return color?.border || "#3b82f6";
  }, []);

  return (
    <div className="flex flex-col h-full" data-tour="topology-page">
      {isMobile && (
        <MobileTopBar
          title="Topology"
          backHref="/"
          rightAction={
            <button
              onClick={() => fitView({ padding: 0.15, duration: 300 })}
              className="p-2 rounded-md text-foreground hover:bg-muted transition-colors min-h-[40px] min-w-[40px] flex items-center justify-center"
              aria-label="Fit to screen"
              title="Fit to screen"
            >
              <Maximize2 className="h-4 w-4" />
            </button>
          }
        />
      )}

      {/* Header */}
      <div className="flex items-center gap-4 px-4 py-3 border-b">
        <h1 className="hidden sm:block text-lg font-semibold">Project Topology</h1>
        <div className="relative inline-block">
          <select
            value={selectedProject}
            onChange={(e) => setSelectedProject(e.target.value)}
            className="appearance-none cursor-pointer rounded-full border border-input bg-muted/40 pl-3 pr-7 py-1 text-xs text-muted-foreground hover:text-foreground hover:bg-muted focus:outline-none focus:ring-1 focus:ring-ring transition-colors"
          >
            {projects.length === 0 && (
              <option value="">No scanned projects</option>
            )}
            {projects.map((p) => (
              <option key={p.id} value={p.id}>
                {p.display_name} ({p.platform}, {p.job_count} components)
              </option>
            ))}
          </select>
          <ChevronDown className="pointer-events-none absolute right-2 top-1/2 -translate-y-1/2 h-3 w-3 text-muted-foreground" />
        </div>
      </div>

      {/* Toolbar */}
      {flowResult && (
        <GraphToolbar
          direction={direction}
          showConnections={showConnections}
          stats={stats}
          onToggleDirection={handleToggleDirection}
          onToggleConnections={handleToggleConnections}
          onFitView={handleFitView}
          groups={groups}
          groupColorMap={groupColorMap}
          hiddenGroups={hiddenGroups}
          onToggleGroup={handleToggleGroup}
          onShowAllGroups={handleShowAllGroups}
          onHideAllGroups={handleHideAllGroups}
          searchQuery={searchQuery}
          onSearchChange={handleSearchChange}
          compactMode={compactMode}
          onToggleCompact={handleToggleCompact}
          collapsedMode={isCollapsed}
          onCollapseAll={handleCollapseAll}
          onExpandAll={handleExpandAll}
          expandedGroups={expandedGroups}
        />
      )}

      {/* Graph area */}
      <div className="flex-1 relative min-h-[500px]">
        {loading && (
          <div className="absolute inset-0 flex items-center justify-center bg-background/80 z-20">
            <div className="flex items-center gap-2 text-muted-foreground">
              <div className="h-4 w-4 border-2 border-current border-t-transparent rounded-full animate-spin" />
              Loading topology...
            </div>
          </div>
        )}

        {error && !loading && (
          <div className="absolute inset-0 flex items-center justify-center">
            <div className="text-center text-muted-foreground">
              <p className="text-sm">{error}</p>
              <button
                onClick={() => setSelectedProject(selectedProject)}
                className="mt-2 text-xs underline hover:text-foreground"
              >
                Retry
              </button>
            </div>
          </div>
        )}

        {!loading && !error && flowResult && nodes.length === 0 && (
          <div className="absolute inset-0 flex items-center justify-center">
            <p className="text-sm text-muted-foreground">
              No topology data available for this project.
            </p>
          </div>
        )}

        <ReactFlow
          nodes={nodes}
          edges={edges}
          onNodesChange={onNodesChange}
          onEdgesChange={onEdgesChange}
          onNodeClick={handleNodeClick}
          onNodeMouseEnter={handleNodeMouseEnter}
          onNodeMouseLeave={handleNodeMouseLeave}
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
          <Background variant={BackgroundVariant.Dots} gap={24} size={0.8} color="#cbd5e1" className="opacity-20" />
          <Controls
            showInteractive={false}
            className="!bg-card !border !shadow-sm [&>button]:!bg-background [&>button]:!border-border [&>button]:!text-foreground [&>button:hover]:!bg-muted"
          />
          {(visibleNodes.length <= 150 || isCollapsed) && (
            <MiniMap
              nodeStrokeWidth={3}
              className="!bg-card !border !shadow-sm"
              maskColor="rgba(0,0,0,0.1)"
              nodeColor={minimapNodeColor}
            />
          )}
          {flowResult && <GraphLegend groupColorMap={groupColorMap} collapsedMode={isCollapsed} />}
        </ReactFlow>

        {/* Job Detail Panel */}
        {selectedJob && selectedProject && isMobile && (
          <MobileBottomSheet
            open={true}
            onClose={handleClosePanel}
            title="Component Detail"
            ariaLabel="Component Detail"
          >
            <JobDetailPanel
              projectId={selectedProject}
              jobName={selectedJob}
              onClose={handleClosePanel}
              metadata={graphData?.job_metadata?.[selectedJob]}
            />
          </MobileBottomSheet>
        )}
        {selectedJob && selectedProject && !isMobile && (
          <JobDetailPanel
            projectId={selectedProject}
            jobName={selectedJob}
            onClose={handleClosePanel}
            metadata={graphData?.job_metadata?.[selectedJob]}
          />
        )}
      </div>
    </div>
  );
}

export default function TopologyPage() {
  return (
    <ReactFlowProvider>
      <TopologyContent />
    </ReactFlowProvider>
  );
}
