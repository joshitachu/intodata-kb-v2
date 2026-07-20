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
  useViewport as useFlowViewport,
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
import { JobGraphCanvas } from "@/components/graph/JobGraphCanvas";
import { ComponentGraphCanvas } from "@/components/graph/ComponentGraphCanvas";
import { TopologyBreadcrumb } from "@/components/graph/TopologyBreadcrumb";
import { LineagePathBanner } from "@/components/graph/LineagePathBanner";
import { useTopologyNavigation } from "@/hooks/useTopologyNavigation";
import { useFieldLineage } from "@/hooks/useFieldLineage";
import { useJobHealth } from "@/hooks/useJobHealth";
import {
  componentLineageHighlight,
  jobLineageHighlight,
  projectLineageHighlight,
} from "@/lib/lineageHighlight";
import { joinHealthToJobs } from "@/lib/jobHealth";
import { propagateHealth, callEdgeKey, type EffectiveStatus } from "@/lib/healthPropagation";
import { worstEffectiveStatus, FAILING_EDGE_COLOR } from "@/lib/healthStatusMeta";
import type { FieldLineageQuery } from "@/types/lineage";
import { MobileTopBar } from "@/components/mobile/MobileTopBar";
import { MobileBottomSheet } from "@/components/mobile/MobileBottomSheet";
import { useViewport } from "@/hooks/useViewport";
import { Maximize2 } from "lucide-react";
import {
  manifestToFlow,
  manifestToCollapsedFlow,
  applyDagreLayout,
  applyCompoundDagreLayout,
  computeGroupBoundingBoxes,
  applyConnectionVisibility,
  type ManifestData,
  type GraphConnectionInfo,
  type JobScanMetadata,
  type FlowResult,
  type GroupBoundingBox,
} from "@/lib/graphTransform";
import { computeFailureFocusSet, isFocusDimmableNode, isFocusDimmableEdge } from "@/lib/failureFocus";
import { computeFocus, type FocusResult } from "@/lib/focusMode";
import { FocusInteractionContext, type FocusInteraction } from "@/components/graph/focusInteraction";
import { CALL_EDGE_COLOR } from "@/lib/nodeStyles";

// Default focus-mode horizon: focal job ± this many dependency hops.
const DEFAULT_FOCUS_HOPS = 2;

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

// Group background node rendered behind job clusters — neutral slate wash so
// the container reads as structure, not as another colored element. The label
// scales up as you zoom out (few group boxes, so tracking the viewport is
// cheap): the cluster names stay readable as your "where am I" anchors on the
// zoomed-out overview.
const GroupBackgroundNode = memo(function GroupBackgroundNode({ data }: { data: GroupBoundingBox & { width: number; height: number } }) {
  const { zoom } = useFlowViewport();
  const labelSize = Math.min(72, Math.max(11, Math.round(16 / zoom)));
  return (
    <div
      className="rounded-2xl pointer-events-none"
      style={{
        width: data.width,
        height: data.height,
        background: "rgba(148, 163, 184, 0.07)",
        border: "1px solid rgba(148, 163, 184, 0.25)",
      }}
    >
      <span
        className="absolute top-2 left-3.5 font-bold tracking-wide pointer-events-none uppercase"
        style={{ color: "#94a3b8", fontSize: labelSize }}
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
  // Shared connection nodes (conn:*) show what external systems each group
  // talks to (MSSQL, REST API, ActiveMQ, ...) — the information the overview
  // exists to surface. Visible by default. In the collapsed overview each
  // group draws ONE aggregated edge per connection (manifestToCollapsedFlow's
  // `group-uses` edges) instead of every job fanning its own dashed line, so
  // this stays clean rather than becoming the old spaghetti. The toolbar
  // "Connections" toggle hides them (applyConnectionVisibility drops conn:*
  // nodes and every edge touching one, aggregated edges included).
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

  // ── Focus mode: click-to-centre neighbourhood exploration ────────────────
  // When focusJobId is set the canvas shows ONLY the focal job's dependency
  // neighbourhood (± focusHops), relaid-out compactly; everything else is
  // hidden. Reuses the same manifest dependency maps as S3/S5 (focusMode.ts).
  const [focusJobId, setFocusJobId] = useState<string | null>(null);
  const [focusHops, setFocusHops] = useState<number>(DEFAULT_FOCUS_HOPS);

  const [nodes, setNodes, onNodesChange] = useNodesState<Node>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);
  const { fitView } = useReactFlow();
  const { isMobile } = useViewport();

  // Drill-down navigation stack (Project > Job > ...). Level 0/1 (this
  // page's canvas) stays mounted-but-hidden while a job level is open so
  // ascending restores the prior viewport, expand state, and layout.
  const {
    stack: navStack,
    current: navCurrent,
    depth: navDepth,
    descendToJob,
    descendToComponent,
    ascend,
    jumpTo,
    reset: resetNavigation,
  } = useTopologyNavigation();
  const currentJobName = navCurrent.kind === "job" ? navCurrent.jobName : null;
  const currentComponent = navCurrent.kind === "component" ? navCurrent : null;
  const atProjectLevel = navDepth === 1;

  // S5 field lineage ("heritage mode") - a single selection lives here so it
  // survives ascending/descending between drill-down levels (each level's
  // canvas remounts fresh on every descent, see the level-2/3 `key` props
  // below). null until the user clicks a column handle in a level-3
  // TableNode; toggled off by clicking the same column again or the path
  // banner's close button.
  const [lineageQuery, setLineageQuery] = useState<FieldLineageQuery | null>(null);
  const {
    data: lineageResponse,
    isLoading: lineageLoading,
    error: lineageError,
  } = useFieldLineage(selectedProject, lineageQuery);

  const handleColumnLineageClick = useCallback(
    (jobName: string, componentName: string, table: string, column: string) => {
      // Activating a lineage trace clears any active focus neighbourhood — the
      // two spotlights are mutually exclusive (see handleFocusJob).
      setFocusJobId(null);
      setLineageQuery((prev) =>
        prev &&
        prev.jobName === jobName &&
        prev.component === componentName &&
        prev.table === table &&
        prev.column === column
          ? null
          : { jobName, table, column, component: componentName, direction: "both", maxDepth: 10 },
      );
    },
    [],
  );

  const handleClearLineage = useCallback(() => {
    setLineageQuery(null);
  }, []);

  // Level-1 highlight: the single job this response is scoped to (no
  // cross-job stitching yet server-side - see lib/lineageHighlight.ts).
  const projectLineageIds = useMemo(
    () => (lineageResponse ? projectLineageHighlight(lineageResponse) : null),
    [lineageResponse],
  );
  // Level-2 highlight: components touched by intra-job, cross-component
  // hops, scoped to whichever job's canvas is currently mounted.
  const jobLineageIds = useMemo(
    () =>
      lineageResponse && currentJobName
        ? jobLineageHighlight(lineageResponse, currentJobName)
        : null,
    [lineageResponse, currentJobName],
  );
  // Level-3 highlight: tables/columns/edges within the currently open
  // component.
  const componentLineage = useMemo(
    () =>
      lineageResponse && currentComponent
        ? componentLineageHighlight(lineageResponse, currentComponent.componentName)
        : null,
    [lineageResponse, currentComponent],
  );

  // ── S3: live runtime-health overlay + dependency failure propagation ──────
  // Both datasets are already in the browser: the scan manifest (from the
  // graph fetch) supplies structure + job->job deps; a polling hook supplies
  // live health. The join and propagation are pure client-side functions;
  // health is never fabricated (unmatched job -> unknown, no matches -> overlay
  // auto-hides). Toggle persists across project switches; auto-hide handles
  // projects with no runtime data.
  const [healthOverlayEnabled, setHealthOverlayEnabled] = useState(true);
  const jobHealthQuery = useJobHealth(selectedProject, healthOverlayEnabled);

  // Failure-focus: dim everything outside the failing cascade so the down /
  // impacted jobs and the red propagation edges pop. Enabled by default; it is
  // a no-op unless the health overlay is on AND a failure cascade exists (see
  // failureFocusIds below), so the toolbar toggle is disabled until then.
  const [focusFailures, setFocusFailures] = useState(true);

  const scanJobNames = useMemo<string[]>(() => {
    if (!graphData) return [];
    const names = new Set<string>();
    const m = graphData.manifest;
    for (const n of Object.keys(m.job_groups || {})) names.add(n);
    for (const [caller, callees] of Object.entries(m.job_calls || {})) {
      names.add(caller);
      for (const c of callees) names.add(c);
    }
    for (const [callee, callers] of Object.entries(m.job_called_by || {})) {
      names.add(callee);
      for (const c of callers) names.add(c);
    }
    return [...names];
  }, [graphData]);

  const healthJoin = useMemo(
    () => joinHealthToJobs(scanJobNames, jobHealthQuery.data?.health ?? []),
    [scanJobNames, jobHealthQuery.data],
  );

  const propagation = useMemo(
    () => propagateHealth(graphData?.manifest.job_calls ?? {}, healthJoin.statusByJob),
    [graphData, healthJoin],
  );

  const healthAvailable = healthJoin.matchCount > 0;
  const overlayActive = healthOverlayEnabled && healthAvailable;
  const noRuntimeData =
    healthOverlayEnabled && jobHealthQuery.isSuccess && !healthAvailable;

  // group -> worst effective status across its member jobs (for collapsed nodes)
  const groupWorst = useMemo(() => {
    const map = new Map<string, EffectiveStatus>();
    if (!graphData) return map;
    const membersByGroup = new Map<string, string[]>();
    for (const [job, group] of Object.entries(graphData.manifest.job_groups || {})) {
      const list = membersByGroup.get(group) ?? [];
      list.push(job);
      membersByGroup.set(group, list);
    }
    for (const [group, members] of membersByGroup) {
      map.set(
        group,
        worstEffectiveStatus(
          members.map((j) => propagation.jobs.get(j)?.effectiveStatus ?? "unknown"),
        ),
      );
    }
    return map;
  }, [graphData, propagation]);

  // The failing cascade as project-level node ids (down/impacted jobs, red-edge
  // endpoints, failing collapsed groups), or null when there is nothing to
  // focus. Only meaningful while the overlay is active — never recomputes
  // propagation, just projects it into node ids (lib/failureFocus.ts).
  const failureFocusIds = useMemo<Set<string> | null>(
    () => (overlayActive ? computeFailureFocusSet(propagation, groupWorst) : null),
    [overlayActive, propagation, groupWorst],
  );
  // Whether the "Focus failures" control has anything to act on.
  const focusAvailable = failureFocusIds !== null;
  // The set actually applied: only when the user has failure-focus enabled AND
  // Focus mode (the click-to-centre neighbourhood) is NOT active. Focus mode is
  // its own spotlight and supersedes the failure-focus resting dim, so the two
  // never fight; runtime-health COLOURS still paint on the focused nodes.
  const activeFocusSet = focusFailures && focusJobId === null ? failureFocusIds : null;

  // Decorate a node with (or strip) its runtime status. Reference-stable: only
  // returns a new node object when the status actually changed, so ReactFlow +
  // React.memo skip re-rendering unchanged nodes on every 45s poll.
  const decorateNode = useCallback(
    (node: Node): Node => {
      if (node.id.startsWith("job:")) {
        const job = node.id.slice(4);
        const p = overlayActive ? propagation.jobs.get(job) : undefined;
        const eff: EffectiveStatus | undefined = overlayActive
          ? (p?.effectiveStatus ?? "unknown")
          : undefined;
        if (eff === undefined) {
          if (node.data.effectiveStatus === undefined) return node;
          const { ownStatus: _o, effectiveStatus: _e, impactedVia: _v, runtimeTaskCount: _c, ...rest } = node.data;
          return { ...node, data: rest };
        }
        const own = p?.ownStatus ?? "unknown";
        const taskCount = healthJoin.byJob.get(job)?.matched.length ?? 0;
        if (node.data.effectiveStatus === eff && node.data.ownStatus === own) return node;
        return {
          ...node,
          data: {
            ...node.data,
            ownStatus: own,
            effectiveStatus: eff,
            impactedVia: p?.impactedVia ?? [],
            runtimeTaskCount: taskCount,
          },
        };
      }
      if (node.id.startsWith("group:")) {
        const group = (node.data.groupName as string) ?? node.id.slice(6);
        const worst: EffectiveStatus | undefined = overlayActive
          ? (groupWorst.get(group) ?? "unknown")
          : undefined;
        if (worst === undefined) {
          if (node.data.worstStatus === undefined) return node;
          const { worstStatus: _w, ...rest } = node.data;
          return { ...node, data: rest };
        }
        if (node.data.worstStatus === worst) return node;
        return { ...node, data: { ...node.data, worstStatus: worst } };
      }
      return node;
    },
    [overlayActive, propagation, groupWorst, healthJoin],
  );

  // Paint (or restore) a job->job call edge that lies on a failing path.
  const decorateEdge = useCallback(
    (edge: Edge): Edge => {
      const isJobCall =
        edge.data?.edgeType === "calls" &&
        edge.source.startsWith("job:") &&
        edge.target.startsWith("job:");
      const failing =
        overlayActive &&
        isJobCall &&
        propagation.failingEdges.has(callEdgeKey(edge.source.slice(4), edge.target.slice(4)));
      const currentlyFailing = edge.data?.failing === true;
      if (failing === currentlyFailing) return edge;
      if (failing) {
        return {
          ...edge,
          animated: true,
          style: { ...edge.style, stroke: FAILING_EDGE_COLOR, strokeWidth: 2.5 },
          markerEnd: { type: "arrowclosed" as const, color: FAILING_EDGE_COLOR, width: 18, height: 18 },
          data: { ...edge.data, failing: true },
        };
      }
      // Restore the default neutral call-edge styling (only job->job calls reach here).
      return {
        ...edge,
        animated: false,
        style: { ...edge.style, stroke: CALL_EDGE_COLOR, strokeWidth: 1.5 },
        markerEnd: { type: "arrowclosed" as const, color: CALL_EDGE_COLOR, width: 16, height: 16 },
        data: { ...edge.data, failing: false },
      };
    },
    [overlayActive, propagation],
  );

  // Refs so the layout effect can decorate freshly-laid nodes/edges without
  // taking overlay data as a dependency (which would retrigger full relayout
  // on every poll).
  const decorateNodeRef = useRef(decorateNode);
  const decorateEdgeRef = useRef(decorateEdge);
  useEffect(() => {
    decorateNodeRef.current = decorateNode;
    decorateEdgeRef.current = decorateEdge;
  }, [decorateNode, decorateEdge]);

  // Ref so the layout effect can apply the failure-focus resting dim state to
  // freshly-laid nodes/edges (surviving expand/collapse relayouts) without
  // taking activeFocusSet as a dependency — that would relayout on every poll.
  const activeFocusSetRef = useRef(activeFocusSet);
  useEffect(() => {
    activeFocusSetRef.current = activeFocusSet;
  }, [activeFocusSet]);

  const handleToggleHealthOverlay = useCallback(() => {
    setHealthOverlayEnabled((v) => !v);
  }, []);

  const handleToggleFocusFailures = useCallback(() => {
    setFocusFailures((v) => !v);
  }, []);

  // Write to both local and global state when user picks a project
  const setSelectedProject = useCallback((id: string) => {
    setSelectedProjectLocal(id);
    setGlobalProject(id);
  }, [setGlobalProject]);

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

  // Focus neighbourhood (pure) — null unless a focal job is selected.
  const focusResult = useMemo<FocusResult | null>(() => {
    if (!focusJobId || !graphData) return null;
    const m = graphData.manifest;
    return computeFocus(
      focusJobId,
      m.job_calls ?? {},
      m.job_called_by ?? {},
      m.job_connections_used ?? {},
      focusHops,
    );
  }, [focusJobId, graphData, focusHops]);

  const focusActive = focusResult !== null;

  // The focused subgraph as ready-to-lay-out nodes/edges. Built by filtering the
  // full DETAIL flow (manifestToFlow) to the in-focus id set, so every node
  // keeps its normal colour/metadata decoration. Focal + boundary annotations
  // are attached here for JobNode to render its ring and "+N" expand badges.
  const focusFlow = useMemo<{ nodes: Node[]; edges: Edge[] } | null>(() => {
    if (!focusResult || !graphData) return null;
    const full = manifestToFlow(graphData.manifest, graphData.connections, graphData.job_metadata);
    const inFocus = focusResult.inFocus;
    const nodes = full.nodes
      .filter((n) => inFocus.has(n.id))
      .map((n) => {
        if (!n.id.startsWith("job:")) return n;
        const jobName = n.id.slice(4);
        const boundary = focusResult.boundaries.get(jobName);
        return {
          ...n,
          data: {
            ...n.data,
            focusFocal: n.id === `job:${focusResult.focal}`,
            focusUpstream: boundary?.upstream ?? 0,
            focusDownstream: boundary?.downstream ?? 0,
          },
        };
      });
    const edges = full.edges.filter(
      (e) => inFocus.has(e.source) && inFocus.has(e.target),
    );
    return { nodes, edges };
  }, [focusResult, graphData]);

  // Visible nodes/edges from flowResult + filters
  const { visibleNodes, visibleEdges } = useMemo(() => {
    // Focus mode short-circuits the normal collapsed/expanded pipeline: only the
    // focal neighbourhood is shown (connection nodes still honour the toggle).
    if (focusFlow) {
      const filtered = applyConnectionVisibility(focusFlow.nodes, focusFlow.edges, showConnections);
      return { visibleNodes: filtered.nodes, visibleEdges: filtered.edges };
    }

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

    // Drop connection nodes + every edge touching one when hidden (default).
    const filtered = applyConnectionVisibility(vNodes, vEdges, showConnections);

    return { visibleNodes: filtered.nodes, visibleEdges: filtered.edges };
  }, [flowResult, hiddenGroups, showConnections, focusFlow]);

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
    resetNavigation();
    setHiddenGroups(new Set());
    setSearchQuery("");
    setHoveredNodeId(null);
    setLineageQuery(null);
    // Force the lightweight starting view on every project switch:
    //   compact nodes + collapsed (group-summary) mode.
    // Never default to the full expanded graph — it blows up memory on
    // projects with many jobs/connections.
    setCompactMode(true);
    setCollapsedMode(true);
    setExpandedGroups(new Set());
    setFocusJobId(null);
    setFocusHops(DEFAULT_FOCUS_HOPS);
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
  }, [selectedProject, resetNavigation]);

  // Escape ascends one drill-down level (breadcrumb click does the same).
  useEffect(() => {
    if (navDepth <= 1) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        ascend();
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [navDepth, ascend]);

  // Apply layout when visible nodes/edges, direction, or compact mode changes
  useEffect(() => {
    if (!flowResult || visibleNodes.length === 0) {
      setNodes([]);
      setEdges([]);
      return;
    }

    // Add compact flag to node data
    const nodesWithCompact = visibleNodes.map((n) => ({
      ...n,
      data: { ...n.data, compact: compactMode },
    }));

    let laid: Node[];

    if (focusActive) {
      // Focus mode: relay out ONLY the neighbourhood as a tight, centred
      // subgraph (plain dagre, no compound clusters, no group backgrounds) so
      // it is not sprawled across the original full-graph coordinates.
      laid = applyDagreLayout(nodesWithCompact, visibleEdges, direction, compactMode);
    } else {
      // Always a full, authoritative layout - on initial load, mode switch,
      // direction change, AND every expand/collapse toggle. A prior version
      // patched positions in-place on toggle (a "hole" the old node vacated,
      // survivors pushed just far enough to clear the new cluster) - correct
      // for a single toggle from a clean dagre layout, but the proof does not
      // compose: after one expand, the survivor set is no longer a clean dagre
      // output, so a second toggle's push math no longer guarantees clearance.
      // Confirmed live: expanding two sibling groups in sequence produced
      // overlapping group boxes and node labels. `applyCompoundDagreLayout`
      // already handles arbitrary expanded/collapsed mixes correctly in one
      // shot (it's the same path initial load already uses) - reuse it always
      // rather than patch the incremental approach further.
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

    // Group bounding boxes: skipped in focus mode (a clean neighbourhood canvas
    // has no group-cluster backgrounds).
    const groupBoxes = focusActive
      ? []
      : computeGroupBoundingBoxes(laid, flowResult.groupColorMap, compactMode);

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
    // Resting dim state on freshly-laid nodes: failure-focus (if active) dims
    // everything outside the failing cascade. Applied here via ref so it
    // survives expand/collapse relayouts; the interactive search/hover/lineage
    // effects override on top and fall back to this base when cleared.
    // Only job-level nodes/edges are dimmable by failure-focus: group summaries
    // and connection nodes (and their aggregated group->system edges) stay
    // full-colour at the overview so the down group's red DOWN badge and the
    // "what talks to what" edges remain visible (isFocusDimmableNode/Edge).
    const focusSet = activeFocusSetRef.current;
    const laidWithDefaults = laid.map((n) => ({
      ...n,
      data: {
        ...n.data,
        dimmed: focusSet && isFocusDimmableNode(n.id) ? !focusSet.has(n.id) : false,
        highlighted: false,
      },
    }));
    // Apply the runtime-health overlay to the freshly-laid nodes/edges (S3),
    // then the failure-focus edge opacity (job->job edges outside the cascade dim).
    setNodes([...bgNodes, ...laidWithDefaults.map(decorateNodeRef.current)]);
    setEdges(
      visibleEdges.map((e) => {
        const decorated = decorateEdgeRef.current(e);
        if (!focusSet) return decorated;
        const visible =
          !isFocusDimmableEdge(decorated.source, decorated.target) ||
          (focusSet.has(decorated.source) && focusSet.has(decorated.target));
        if (visible) return decorated;
        return { ...decorated, style: { ...decorated.style, opacity: 0.15 } };
      }),
    );

    // Fit everything — every layout is now a full relayout (initial load,
    // mode switch, direction change, focus, AND every expand/collapse toggle,
    // see the comment above) — but only when the meaningful layout identity
    // changed. Otherwise the 300ms zoom animation re-fires every time
    // groupMembership rebuilds with the same shape, stealing the viewport and
    // dominating frame time on heavy graphs. The focal id + hop count are part
    // of the key so re-focusing or expanding the horizon always refits the
    // tight neighbourhood.
    {
      const fitKey = `${selectedProject}|${direction}|${compactMode}|${laid.length}|${focusJobId ?? ""}|${focusHops}`;
      if (fitKey !== lastFitKeyRef.current) {
        lastFitKeyRef.current = fitKey;
        requestAnimationFrame(() => {
          fitView({ padding: focusActive ? 0.25 : 0.1, duration: 300 });
        });
      }
    }
    // Collapse: no zoom change (keep current viewport)
  }, [flowResult, visibleNodes, visibleEdges, direction, compactMode, groupMembership, selectedProject, focusActive, focusJobId, focusHops, setNodes, setEdges, fitView]);

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

  // Apply the S5 level-1 lineage overlay: dim every job node except the one
  // the active trace is scoped to. Deliberately scoped to `job:` node ids
  // only (never group/connection/background nodes) and declared after the
  // search/hover effects above so it always applies last and wins for job
  // nodes specifically, without fighting either mechanism for the rest of
  // the canvas. Runs on both activation and clearing (projectLineageIds
  // going non-null -> null), mirroring the reset branch already used by the
  // search/hover effects.
  const prevProjectLineageRef = useRef<Set<string> | null>(null);
  useEffect(() => {
    if (projectLineageIds === prevProjectLineageRef.current) return;
    prevProjectLineageRef.current = projectLineageIds;

    setNodes((nds) =>
      nds.map((n) => {
        if (!n.id.startsWith("job:")) return n;
        if (projectLineageIds === null) {
          if (n.data.dimmed === false && n.data.highlighted === false) return n;
          return { ...n, data: { ...n.data, dimmed: false, highlighted: false } };
        }
        const isMatch = projectLineageIds.has(n.id);
        if (n.data.dimmed === !isMatch && n.data.highlighted === isMatch) return n;
        return { ...n, data: { ...n.data, dimmed: !isMatch, highlighted: isMatch } };
      }),
    );
  }, [projectLineageIds, setNodes]);

  // Failure-focus dimming (project level). Reuses the SAME dimmed/highlighted
  // node fields + edge-opacity as S5 lineage and hover/search — not a parallel
  // mechanism. It is the RESTING dim layer: when the overlay is on, focus is
  // enabled, and a cascade exists, every node/edge outside the cascade drops to
  // ~12-15% opacity.
  //
  // PRECEDENCE (highest wins while active): search > hover > S5 lineage >
  // failure-focus. While any higher source owns the fields this effect no-ops;
  // when they clear it re-runs (they are deps) and reapplies focus AFTER their
  // reset branch (this effect is declared last), so the two never fight into an
  // inconsistent state. A live poll changing activeFocusSet also re-runs it.
  useEffect(() => {
    if (searchMatchIds !== null || hoveredNodeId !== null || projectLineageIds !== null) {
      return;
    }
    const focusSet = activeFocusSet;
    setNodes((nds) =>
      nds.map((n) => {
        if (n.id.startsWith("group-bg:")) return n;
        // group:* and conn:* nodes are never dimmed by focus (isFocusDimmableNode).
        const newDimmed = focusSet && isFocusDimmableNode(n.id) ? !focusSet.has(n.id) : false;
        if (n.data.dimmed === newDimmed && n.data.highlighted === false) return n;
        return { ...n, data: { ...n.data, dimmed: newDimmed, highlighted: false } };
      }),
    );
    setEdges((eds) =>
      eds.map((e) => {
        const visible =
          !focusSet ||
          !isFocusDimmableEdge(e.source, e.target) ||
          (focusSet.has(e.source) && focusSet.has(e.target));
        const newOpacity = visible ? undefined : 0.15;
        if ((e.style?.opacity ?? undefined) === newOpacity) return e;
        if (newOpacity === undefined) {
          const { opacity: _drop, ...restStyle } = e.style ?? {};
          return { ...e, style: restStyle };
        }
        return { ...e, style: { ...e.style, opacity: newOpacity } };
      }),
    );
  }, [activeFocusSet, searchMatchIds, hoveredNodeId, projectLineageIds, setNodes, setEdges]);

  // S3: live-patch node/edge health when the poll refreshes or the overlay is
  // toggled, without a full relayout. decorateNode/decorateEdge are
  // reference-stable, so unchanged nodes keep their identity and don't
  // re-render. Runs after the search/hover/lineage effects; it only touches
  // status fields, never dimmed/highlighted, so it composes with them.
  useEffect(() => {
    setNodes((nds) => {
      let changed = false;
      const next = nds.map((n) => {
        const d = decorateNode(n);
        if (d !== n) changed = true;
        return d;
      });
      return changed ? next : nds;
    });
    setEdges((eds) => {
      let changed = false;
      const next = eds.map((e) => {
        const d = decorateEdge(e);
        if (d !== e) changed = true;
        return d;
      });
      return changed ? next : eds;
    });
  }, [decorateNode, decorateEdge, setNodes, setEdges]);

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

  // ── Focus mode gestures ──────────────────────────────────────────────────
  // Enter/re-centre focus on a job, or toggle it off if it is already focal.
  // Entering focus closes any open detail panel and clears an active S5 lineage
  // selection — focus and lineage are both "spotlight" mechanisms and running
  // them together is confusing, so entering one clears the other (documented
  // precedence; the reverse direction is handled in handleColumnLineageClick).
  const handleFocusJob = useCallback((jobName: string) => {
    setFocusJobId((prev) => {
      if (prev === jobName) return null; // toggle off
      return jobName;
    });
    setFocusHops(DEFAULT_FOCUS_HOPS);
    setSelectedJob(null);
    setLineageQuery(null);
  }, []);

  const handleClearFocus = useCallback(() => {
    setFocusJobId(null);
    setFocusHops(DEFAULT_FOCUS_HOPS);
  }, []);

  // Boundary "+N" affordance: push the horizon out by one hop in both directions.
  const handleExpandHops = useCallback(() => {
    setFocusHops((h) => h + 1);
  }, []);

  const handleNodeClick: NodeMouseHandler = useCallback((_event, node) => {
    if (node.id.startsWith("job:")) {
      const jobName = node.id.slice(4);
      setSelectedJob(jobName);
    } else if (node.id.startsWith("group:")) {
      // Toggling triggers the main layout effect's full compound-dagre
      // relayout (see its comment) - no position bookkeeping needed here.
      const groupName = node.id.slice(6);
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
  }, []);

  // Double-click on a job node descends into its level-2 component graph.
  // Single-click behavior (detail panel, group expand/collapse) is unchanged.
  const handleNodeDoubleClick: NodeMouseHandler = useCallback(
    (_event, node) => {
      if (node.id.startsWith("job:")) {
        setSelectedJob(null);
        descendToJob(node.id.slice(4));
      }
    },
    [descendToJob],
  );

  const handleClosePanel = useCallback(() => {
    setSelectedJob(null);
  }, []);

  // Group toggle: in collapsed mode toggles expand/collapse, in detail mode toggles hide/show.
  // Toggling expandedGroups triggers the main layout effect's full compound-
  // dagre relayout (see its comment) - no position bookkeeping needed here.
  const handleToggleGroup = useCallback((group: string) => {
    if (isCollapsed) {
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
  }, [isCollapsed]);

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

  // Stable context value bridging focus gestures into JobNode without touching
  // node `data` (preserving the relayout/poll reference-stability guarantees).
  const focusInteraction = useMemo<FocusInteraction>(
    () => ({
      active: focusActive,
      focalJobName: focusJobId,
      onFocusJob: handleFocusJob,
      onExpandHops: handleExpandHops,
    }),
    [focusActive, focusJobId, handleFocusJob, handleExpandHops],
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
    <FocusInteractionContext.Provider value={focusInteraction}>
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
        {navDepth > 1 && (
          <TopologyBreadcrumb
            projectLabel={
              projects.find((p) => p.id === selectedProject)?.display_name ||
              selectedProject
            }
            stack={navStack}
            onNavigate={jumpTo}
          />
        )}
      </div>

      {/* Toolbar (level 0/1 only) */}
      {flowResult && atProjectLevel && (
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
          healthOverlay={healthOverlayEnabled}
          onToggleHealthOverlay={handleToggleHealthOverlay}
          healthAvailable={healthAvailable}
          focusFailures={focusFailures}
          onToggleFocusFailures={handleToggleFocusFailures}
          focusAvailable={focusAvailable}
          focusJobName={focusJobId}
          focusHops={focusHops}
          onClearFocus={handleClearFocus}
        />
      )}

      {/* Graph area */}
      <div className="flex-1 relative min-h-[500px]">
        {/* Level 0/1 canvas: kept mounted (hidden) during drill-down so
            ascending restores the prior viewport and expand state. */}
        <div className={atProjectLevel ? "absolute inset-0" : "absolute inset-0 hidden"}>
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
          onNodeDoubleClick={handleNodeDoubleClick}
          onNodeMouseEnter={handleNodeMouseEnter}
          onNodeMouseLeave={handleNodeMouseLeave}
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
          <Background variant={BackgroundVariant.Dots} gap={24} size={0.8} color="#cbd5e1" className="opacity-20" />
          <Controls
            showInteractive={false}
            className="!bg-card !border !shadow-sm [&>button]:!bg-background [&>button]:!border-border [&>button]:!text-foreground [&>button:hover]:!bg-muted"
          />
          {/* Always on — the minimap is the "overzicht" anchor, most needed
              exactly on the big graphs the old node-count cutoff hid it from. */}
          <MiniMap
            nodeStrokeWidth={3}
            className="!bg-card !border !shadow-sm"
            maskColor="rgba(0,0,0,0.1)"
            nodeColor={minimapNodeColor}
          />
          {flowResult && <GraphLegend groupColorMap={groupColorMap} collapsedMode={isCollapsed} showHealth={overlayActive} />}
        </ReactFlow>

        {/* Runtime-health overlay status chips (S3), level 0/1 only. */}
        {overlayActive && jobHealthQuery.dataUpdatedAt > 0 && (
          <div
            data-testid="health-freshness-chip"
            className="absolute top-3 right-3 z-10 flex items-center gap-1.5 rounded-full border border-slate-200 bg-white/90 px-3 py-1 text-[11px] font-medium text-slate-600 shadow-sm backdrop-blur-md"
          >
            <span className="h-1.5 w-1.5 rounded-full bg-emerald-500" />
            Runtime data as of{" "}
            {new Date(jobHealthQuery.dataUpdatedAt).toLocaleTimeString([], {
              hour: "2-digit",
              minute: "2-digit",
            })}
          </div>
        )}
        {noRuntimeData && (
          <div
            data-testid="health-no-data-note"
            className="absolute top-3 right-3 z-10 rounded-full border border-slate-200 bg-white/80 px-3 py-1 text-[11px] text-slate-400 shadow-sm backdrop-blur-md"
          >
            No runtime data for this project
          </div>
        )}

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

        {/* Level 2 canvas: a fresh React Flow instance per descent. The key
            forces a remount (new layout + animated fitView) for every level,
            including tRunJob portal hops between sibling jobs. */}
        {!atProjectLevel && currentJobName && selectedProject && (
          <JobGraphCanvas
            key={`${navDepth}:${currentJobName}`}
            projectId={selectedProject}
            jobName={currentJobName}
            onOpenJob={descendToJob}
            onOpenComponent={descendToComponent}
            lineageComponentIds={jobLineageIds}
          />
        )}

        {/* Level 3 canvas: one component's table graph (tMap) - or, on the
            documented 404 fallback, the inspector rendered in this same
            slot. Also remounts fresh per descent. */}
        {!atProjectLevel && currentComponent && selectedProject && (
          <ComponentGraphCanvas
            key={`${navDepth}:${currentComponent.jobName}:${currentComponent.componentName}`}
            projectId={selectedProject}
            jobName={currentComponent.jobName}
            componentName={currentComponent.componentName}
            onAscend={ascend}
            lineageQuery={lineageQuery}
            lineageHighlight={componentLineage}
            onColumnLineageClick={handleColumnLineageClick}
          />
        )}

        {/* Field-lineage path banner (S5): floats above whichever level is
            visible, since the highlight it summarizes can span levels 1-3. */}
        <LineagePathBanner
          response={lineageResponse ?? null}
          isLoading={lineageLoading}
          error={lineageError}
          onClear={handleClearLineage}
        />
      </div>
    </div>
    </FocusInteractionContext.Provider>
  );
}

export default function TopologyPage() {
  return (
    <ReactFlowProvider>
      <TopologyContent />
    </ReactFlowProvider>
  );
}
