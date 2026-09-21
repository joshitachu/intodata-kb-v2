"use client";

import { memo, Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ChevronDown, Eye, EyeOff, X } from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
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
import { LandscapeCanvas, type LandscapeSelection } from "@/components/graph/LandscapeCanvas";
import { IntegrationDetailCanvas, IntegrationRelationshipChooser } from "@/components/graph/IntegrationDetailCanvas";
import { BusinessContextBanner } from "@/components/graph/BusinessContextBanner";
import { TopologyBreadcrumb } from "@/components/graph/TopologyBreadcrumb";
import { LineagePathBanner } from "@/components/graph/LineagePathBanner";
import { useTopologyNavigation, type TopologyLevel } from "@/hooks/useTopologyNavigation";
import { useProjectLandscape, LandscapeUnavailableError } from "@/hooks/useProjectLandscape";
import { useFieldLineage } from "@/hooks/useFieldLineage";
import { useJobHealth } from "@/hooks/useJobHealth";
import { useDeepDiveProjects } from "@/hooks/useDeepDiveProjects";
import { queryOutcome } from "@/lib/queryState";
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
  manifestToLandscapeFlow,
  applyDagreLayout,
  applyCompoundDagreLayout,
  computeGroupBoundingBoxes,
  applyConnectionVisibility,
  filterManifestByArtifactKind,
  filterManifestByGroup,
  filterManifestBySourceRoot,
  topologyGroupForJob,
  getDimensionsForId,
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
import { findLandscapeEdge, landscapeEdgeKey, type TopologyEdge } from "@/lib/landscapeTransform";
import type { LandscapeProvenance } from "@/types/landscape";
import { PageCopilot, copilotNotice } from "@/components/PageCopilot";
import { useProgressiveTargets } from "@/components/copilot/useProgressiveTargets";
import { AutoNavigationBanner } from "@/components/copilot/AutoNavigationBanner";
import { usePageCopilotChat } from "@/hooks/usePageCopilotChat";
import { useCopilotCollapsed } from "@/hooks/useCopilotCollapsed";
import { useFitOnContainerResize } from "@/hooks/useFitOnContainerResize";
import { DEFAULT_COPILOT_WIDTH, useCopilotWidth } from "@/hooks/useCopilotWidth";
import { useLoadedJobComponents } from "@/hooks/useLoadedJobComponents";
import {
  activeIdSpace,
  buildTopologyGraphData,
  describeTopologyLevel,
  isTopologyLevelResolvable,
  navigateToTarget,
  resolveTopologyTargets,
  toCopilotTarget,
  topologyLevelKey,
  type ResolvedTopologyTarget,
} from "@/lib/topologyTargets";
import {
  findRelationshipTarget,
  pickMovementTarget,
  relationshipShape,
  selectRelevantTargets,
  synthesizeRelationshipTarget,
  type AutoMovementKind,
} from "@/lib/topologyFollow";
import {
  buildTopologyPageScope,
  designAssistantEmphasis,
  highlightNodeIdsForTargets,
  integrationAssistantEmphasis,
  integrationSelectionEntityId,
  jobAssistantEmphasis,
  landscapeAssistantEmphasis,
  mappingAssistantEmphasis,
  readTopologyTargetsPayload,
  topologyAnswerSource,
  topologyCopilotScope,
  topologyCopilotSuggestions,
} from "@/lib/topologyCopilot";
import {
  TOPOLOGY_CONVERSATION_PARAM,
  TOPOLOGY_LEVEL_PARAMS,
  TOPOLOGY_PROJECT_PARAM,
  parseTopologyConversationFromParams,
  parseTopologyLevelFromParams,
  parseTopologyProjectFromParams,
  topologyLevelToParams,
} from "@/lib/topologyUrl";
import type { AssistantFocusRequest, CopilotNotice, CopilotTarget, CopilotViewContext } from "@/types/copilot";
import type { ConversationMessageRequest, PublicAgentEvent } from "@/types";

// Default focus-mode horizon: focal job ± this many dependency hops.
const DEFAULT_FOCUS_HOPS = 2;

// Floor for every automatic/manual "fit" zoom. The ReactFlow instance itself
// allows zooming out to 0.05 (interactive pinch/scroll on huge graphs), but
// fitView/fitBounds must never land past legible label size on their own —
// large near-edgeless groups (dozens of loosely-connected jobs) would
// otherwise fit-to-view at ~5%, rendering every label sub-pixel. Below this
// floor a fit simply can't show the whole graph and the user scrolls/pans
// instead, which is the correct tradeoff over an illegible overview.
const FIT_MIN_ZOOM = 0.35;

// Whether the docked copilot is collapsed is a preference, not incidental UI
// state -- it survives navigation and reloads under this key.
const COPILOT_COLLAPSED_KEY = "copilot:collapsed:topology";
const COPILOT_FOLLOW_KEY = "copilot:follow:topology";
const COPILOT_WIDTH_KEY = "copilot:width:topology";

// Shared empty list for the "no events yet" case, so `useProgressiveTargets`
// does not see a new array reference (and re-derive) on every render.
const NO_AGENT_EVENTS: readonly PublicAgentEvent[] = [];

interface ProjectOption {
  id: string;
  display_name: string;
  platform: string;
  job_count: number;
  artifact_counts?: {
    total_artifacts?: number;
    job_count?: number;
    route_count?: number;
  };
}

/** Stable reference so the effects below do not re-run on every render. */
const EMPTY_PROJECTS: ProjectOption[] = [];

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
        {data.group.split("/").filter(Boolean).at(-1) || data.group}
      </span>
    </div>
  );
});

const nodeTypes = {
  jobNode: JobNode,
  routeNode: JobNode,
  connectionNode: ConnectionNode,
  groupBackground: GroupBackgroundNode,
  groupSummaryNode: GroupSummaryNode,
};

function TopologyContent() {
  const router = useRouter();
  const { selectedProject: globalProject, setSelectedProject: setGlobalProject } = useChatContext();
  // The project list comes from the same shared hook the toolbar selectors
  // use, so it inherits the authed-fetch refresh-and-replay, the retry policy
  // and — crucially — an honest three-way state. The previous inline
  // `fetch(...).then(res => res.json())` had no `res.ok` check: a backend
  // error body ({error: ...}) parses as JSON perfectly well, `data.projects`
  // came back undefined, the `if (data.projects)` guard silently skipped, and
  // `.catch` never fired because it only covers transport/JSON failures. The
  // picker then said "No scanned projects" — a backend failure rendered as an
  // empty account.
  const projectsQuery = useDeepDiveProjects();
  const projectsOutcome = queryOutcome(projectsQuery, "Could not load scanned projects");
  const projects: ProjectOption[] = projectsOutcome.kind === "ready"
    ? projectsOutcome.data.projects
    : EMPTY_PROJECTS;
  const [selectedProject, setSelectedProjectLocal] = useState("");
  const [graphData, setGraphData] = useState<GraphData | null>(null);
  const [flowResult, setFlowResult] = useState<FlowResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [direction, setDirection] = useState<"TB" | "LR">("LR");
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
  const [artifactFilter, setArtifactFilter] = useState<"all" | "route" | "job">("all");
  const [hoveredNodeId, setHoveredNodeId] = useState<string | null>(null);
  const selectedProjectOption = useMemo(
    () => projects.find((project) => project.id === selectedProject),
    [projects, selectedProject],
  );
  const showArtifactTaxonomy =
    selectedProjectOption?.platform.toLowerCase() === "talend";
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

  // ── Page copilot emphasis state ──────────────────────────────────────────
  // Node ids the copilot is pointing at. Declared alongside the other emphasis
  // sources because it feeds the same dimmed/highlighted channel (see the
  // copilot emphasis effect for the precedence rules).
  const [copilotHighlightIds, setCopilotHighlightIds] = useState<Set<string> | null>(null);
  // A hovered chip narrows the emphasis to exactly one target. Wrapped rather
  // than stored bare so "hovering a chip with nothing on this canvas" is
  // distinguishable from "not hovering" -- the former must dim nothing, not
  // fall back to the whole answer.
  const [copilotHover, setCopilotHover] = useState<{ ids: Set<string> } | null>(null);
  // The project the copilot's thread belongs to. Compared against the selected
  // project rather than assumed equal: on a project switch this still holds the
  // OLD id for the render before the reset effect runs, which is exactly the
  // window in which a stale chip would otherwise point into the previous
  // project's graph.
  const [copilotThreadProject, setCopilotThreadProject] = useState<string | null>(null);
  const [copilotViewContext, setCopilotViewContext] = useState<CopilotViewContext>({});
  const [assistantFocusRequest, setAssistantFocusRequest] = useState<AssistantFocusRequest | null>(null);

  // ── Deep link (see lib/topologyUrl.ts for the contract) ──────────────────
  // Read ONCE, on the first render. The URL is an opening position, not a
  // binding: after that the address bar follows the user (see the write-back
  // effect below), so re-reading it would make in-page navigation fight the
  // very thing it just wrote.
  const searchParams = useSearchParams();
  const [deepLink] = useState(() => ({
    level: parseTopologyLevelFromParams(searchParams),
    projectId: parseTopologyProjectFromParams(searchParams),
    conversationId: parseTopologyConversationFromParams(searchParams),
  }));
  // The level still waiting to be applied. It cannot be applied on mount: a
  // level is only trustworthy once there is a graph to check it against.
  const [pendingDeepLinkLevel, setPendingDeepLinkLevel] = useState<TopologyLevel | null>(
    deepLink.level,
  );
  // A link that named something this scan does not have. Reported rather than
  // silently swallowed -- the user followed a link and ended up somewhere else.
  const [deepLinkRejected, setDeepLinkRejected] = useState(false);
  // Consumed by the project-selection and thread effects, which own that state.
  const pendingDeepLinkProjectRef = useRef<string | null>(deepLink.projectId);
  const pendingDeepLinkThreadRef = useRef<number | null>(deepLink.conversationId);

  // The jump an answer made on the user's behalf, and the stack to put back.
  const [autoNavigation, setAutoNavigation] = useState<{
    /** The answer it came from, so one answer can only ever move the view once. */
    messageId: number;
    label: string;
    detail: string;
    levelKey: string;
    previousStack: TopologyLevel[];
    previousViewport: { x: number; y: number; zoom: number };
    reason: "explicit" | "follow";
    /** What the view actually did, so the banner cannot overstate it. */
    movement: AutoMovementKind;
  } | null>(null);
  // Separate from the state above so dismissing the banner cannot re-arm the
  // jump: this records that the answer HAS been acted on, banner or not.
  const autoNavigatedMessageRef = useRef<number | null>(null);

  const [nodes, setNodes, onNodesChange] = useNodesState<Node>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);
  const { fitView, fitBounds, getViewport, setViewport } = useReactFlow();
  const { isMobile, isDesktop } = useViewport();
  const [followConversation, setFollowConversation] = useState<boolean | null>(null);

  useEffect(() => {
    if (followConversation !== null) return;
    const saved = window.localStorage.getItem(COPILOT_FOLLOW_KEY);
    setFollowConversation(saved === null ? isDesktop : saved === "follow");
  }, [followConversation, isDesktop]);

  const handleFollowModeChange = useCallback((follow: boolean) => {
    setFollowConversation(follow);
    window.localStorage.setItem(COPILOT_FOLLOW_KEY, follow ? "follow" : "stay");
  }, []);

  // Drill-down navigation stack (Project > Job > ...). Level 0/1 (this
  // page's canvas) stays mounted-but-hidden while a job level is open so
  // ascending restores the prior viewport, expand state, and layout.
  // The whole navigation object is kept (not only its members) because
  // `navigateToTarget` dispatches a copilot chip onto it as a unit.
  const navigation = useTopologyNavigation();
  const {
    stack: navStack,
    current: navCurrent,
    depth: navDepth,
    descendToIntegration,
    descendToGroup,
    descendToJob,
    descendToComponent,
    ascend,
    jumpTo,
    reset: resetNavigation,
    restore: restoreNavigation,
    replaceStack: replaceNavigationStack,
  } = navigation;
  const focusedGroupLevel = navStack.find((level) => level.kind === "group");
  const focusedGroup = focusedGroupLevel?.kind === "group" ? focusedGroupLevel.groupName : null;
  const focusedSourceRoot = focusedGroupLevel?.kind === "group" ? focusedGroupLevel.sourceRoot : undefined;
  const currentJobName = navCurrent.kind === "job" ? navCurrent.jobName : null;
  const currentComponent = navCurrent.kind === "component" ? navCurrent : null;
  const atProjectLevel =
    navCurrent.kind === "project" || navCurrent.kind === "group";
  useEffect(() => {
    setCopilotViewContext({});
  }, [navCurrent]);
  const landscapeQuery = useProjectLandscape(selectedProject);
  // The business canvas is useful only when the validated overlay establishes
  // at least one directional business stream. A valid, evidence-bounded
  // overlay may intentionally contain no streams; in that case the existing
  // design overview is the more useful and truthful root view.
  const hasRenderableBusinessLandscape = Boolean(
    landscapeQuery.data?.business_topology
    && landscapeQuery.data.topology_state?.validated
    && !landscapeQuery.data.topology_state.stale
    && landscapeQuery.data.business_topology.data_streams.length > 0,
  );
  // The scanner-computed landscape supersedes the legacy group roll-up at the
  // root only when it can actually render inter-system relationships. Group
  // detail remains available as the existing Designs path.
  const showLandscapeRoot = navCurrent.kind === "project"
    && landscapeQuery.isSuccess
    && hasRenderableBusinessLandscape;
  const landscapeUnavailable = landscapeQuery.error instanceof LandscapeUnavailableError;
  const landscapeHasNoDirectionalStreams = navCurrent.kind === "project"
    && landscapeQuery.isSuccess
    && !hasRenderableBusinessLandscape;
  const landscapeFailure = Boolean(
    selectedProject && navCurrent.kind === "project" && landscapeQuery.isError && !landscapeUnavailable,
  );
  const showLegacyCanvas = navCurrent.kind === "group" || (
    navCurrent.kind === "project"
    && (landscapeUnavailable || landscapeHasNoDirectionalStreams)
  );
  const integrationSelection = navCurrent.kind === "integration" ? navCurrent.selection : null;
  const selectedLandscapeEdge = useMemo<TopologyEdge | undefined>(() => {
    if (!landscapeQuery.data || integrationSelection?.kind !== "edge") return undefined;
    return findLandscapeEdge(landscapeQuery.data, integrationSelection.edgeKey);
  }, [landscapeQuery.data, integrationSelection]);
  // Claims carry opaque IDs. The API provides a scanner identity catalogue so
  // annotations are attached only after an explicit ID bridge is resolved.
  const jobBusinessAnnotations = useMemo(() => {
    const topology = landscapeQuery.data?.business_topology;
    const evidence = landscapeQuery.data?.topology_evidence;
    if (!topology || !evidence || !currentJobName) return [];
    const artifactIds = new Set(evidence.artifacts
      .filter((artifact) => artifact.job_id === currentJobName || artifact.name === currentJobName)
      .map((artifact) => artifact.id));
    return topology.artifact_annotations.filter((annotation) => artifactIds.has(annotation.artifact_id));
  }, [landscapeQuery.data?.business_topology, landscapeQuery.data?.topology_evidence, currentJobName]);
  const componentBusinessAnnotations = useMemo(() => {
    const topology = landscapeQuery.data?.business_topology;
    const evidence = landscapeQuery.data?.topology_evidence;
    if (!topology || !evidence || !currentComponent) return [];
    const jobArtifactIds = new Set(evidence.artifacts
      .filter((artifact) => artifact.job_id === currentComponent.jobName || artifact.name === currentComponent.jobName)
      .map((artifact) => artifact.id));
    const componentIds = new Set(evidence.components
      .filter((component) => component.name === currentComponent.componentName && jobArtifactIds.has(component.artifact_id))
      .map((component) => component.id));
    return topology.component_annotations.filter((annotation) => componentIds.has(annotation.component_id));
  }, [landscapeQuery.data?.business_topology, landscapeQuery.data?.topology_evidence, currentComponent]);
  const lineageBusinessAnnotations = useMemo(() => {
    const topology = landscapeQuery.data?.business_topology;
    const evidence = landscapeQuery.data?.topology_evidence;
    if (!topology || !evidence || !currentComponent) return [];
    const lineageIds = new Set(evidence.lineage
      .filter((lineage) => lineage.job_name === currentComponent.jobName && (lineage.source_component === currentComponent.componentName || lineage.target_component === currentComponent.componentName))
      .map((lineage) => lineage.id));
    return topology.lineage_annotations.filter((annotation) => lineageIds.has(annotation.lineage_id));
  }, [landscapeQuery.data?.business_topology, landscapeQuery.data?.topology_evidence, currentComponent]);

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
    for (const job of Object.keys(graphData.manifest.job_groups || {})) {
      const group = topologyGroupForJob(graphData.manifest, job);
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

  useEffect(() => {
    setArtifactFilter("all");
  }, [selectedProject]);

  // Tracks the last "meaningful" layout identity that triggered a fitView.
  // Prevents fitView animations from stacking when the layout effect re-runs
  // for unrelated reasons (e.g. groupMembership rebuilding with the same shape).
  // ReactFlow's fitView animation dominates frame time on heavy graphs.
  const lastFitKeyRef = useRef<string | null>(null);

  // Set when the user expands a collapsed group (click or toolbar chip): the
  // layout effect zooms to THAT group's jobs instead of re-fitting the whole
  // canvas — otherwise expanding a 200-job group throws the viewport into a
  // fit-everything view where nothing is readable.
  const expandTargetRef = useRef<string | null>(null);

  // Determine effective collapsed mode (resolve auto)
  const isCollapsed = useMemo(() => {
    if (showArtifactTaxonomy) return focusedGroup === null;
    if (collapsedMode !== null) return collapsedMode;
    // Auto: collapse if >30 jobs
    return (flowResult?.stats.jobCount ?? 0) > 30;
  }, [showArtifactTaxonomy, focusedGroup, collapsedMode, flowResult]);

  const activeManifest = useMemo(
    () => {
      if (!graphData) return null;
      const scoped = showArtifactTaxonomy && focusedSourceRoot
        ? filterManifestBySourceRoot(graphData.manifest, focusedSourceRoot)
        : showArtifactTaxonomy && focusedGroup
          ? filterManifestByGroup(graphData.manifest, focusedGroup)
          : graphData.manifest;
      return filterManifestByArtifactKind(scoped, artifactFilter);
    },
    [graphData, showArtifactTaxonomy, focusedGroup, focusedSourceRoot, artifactFilter],
  );

  // Build the flow result based on mode
  const activeFlowResult = useMemo(() => {
    if (!graphData || !activeManifest) return null;
    if (showArtifactTaxonomy && !focusedGroup) {
      return manifestToLandscapeFlow(
        activeManifest,
        graphData.connections,
        graphData.job_metadata,
      );
    }
    if (showArtifactTaxonomy && focusedGroup) {
      return manifestToFlow(
        activeManifest,
        graphData.connections,
        graphData.job_metadata,
      );
    }
    if (isCollapsed) {
      return manifestToCollapsedFlow(
        activeManifest,
        graphData.connections,
        graphData.job_metadata,
        expandedGroups,
      );
    }
    return manifestToFlow(activeManifest, graphData.connections, graphData.job_metadata);
  }, [
    graphData,
    activeManifest,
    showArtifactTaxonomy,
    focusedGroup,
    isCollapsed,
    expandedGroups,
  ]);

  // Keep flowResult in sync
  useEffect(() => {
    if (activeFlowResult) {
      setFlowResult(activeFlowResult);
    }
  }, [activeFlowResult]);

  // Build group membership map for compound layout (jobNodeId → groupId)
  const groupMembership = useMemo(() => {
    const map = new Map<string, string>();
    if (!activeManifest) return map;
    const jobGroups = activeManifest.job_groups || {};
    for (const job of Object.keys(jobGroups)) {
      const group = topologyGroupForJob(activeManifest, job);
      map.set(`job:${job}`, group);
    }
    return map;
  }, [activeManifest]);

  // Focus neighbourhood (pure) — null unless a focal job is selected.
  const focusResult = useMemo<FocusResult | null>(() => {
    if (!focusJobId || !activeManifest) return null;
    const m = activeManifest;
    return computeFocus(
      focusJobId,
      m.job_calls ?? {},
      m.job_called_by ?? {},
      m.job_connections_used ?? {},
      focusHops,
    );
  }, [focusJobId, activeManifest, focusHops]);

  const focusActive = focusResult !== null;

  // The focused subgraph as ready-to-lay-out nodes/edges. Built by filtering the
  // full DETAIL flow (manifestToFlow) to the in-focus id set, so every node
  // keeps its normal colour/metadata decoration. Focal + boundary annotations
  // are attached here for JobNode to render its ring and "+N" expand badges.
  const focusFlow = useMemo<{ nodes: Node[]; edges: Edge[] } | null>(() => {
    if (!focusResult || !graphData || !activeManifest) return null;
    const full = manifestToFlow(activeManifest, graphData.connections, graphData.job_metadata);
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
  }, [focusResult, graphData, activeManifest]);

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

  // ── Page copilot ─────────────────────────────────────────────────────────
  // The assistant docked beside this canvas. Everything domain-specific is
  // derived here and handed over as flat descriptors: the panel itself knows
  // nothing about landscapes, manifests or id spaces (components/PageCopilot).

  // Component names for the job graphs this browser has actually fetched.
  // Read from the query cache rather than re-fetched (see the hook): job
  // graphs load lazily, so this is normally sparse, and its sparseness is the
  // point -- a job that IS loaded gets a strict component check, a job that is
  // not stays `assumed` instead of being wrongly declared unknown.
  const loadedJobComponents = useLoadedJobComponents(selectedProject);

  // What a target is validated against: exactly what THIS page has loaded,
  // filters included, so a chip can never navigate into an empty canvas.
  const copilotGraph = useMemo(
    () =>
      buildTopologyGraphData({
        landscape: landscapeQuery.data ?? null,
        manifest: activeManifest,
        componentNamesByJob: loadedJobComponents,
      }),
    [landscapeQuery.data, activeManifest, loadedJobComponents],
  );

  // Integration levels are identified by hashed ids, so the scope chip would
  // otherwise read as a hash. The landscape artifact is the only thing that can
  // resolve them to a name.
  const integrationDisplayName = useMemo(() => {
    const landscape = landscapeQuery.data;
    if (!integrationSelection || !landscape) return undefined;
    if (integrationSelection.kind === "application") {
      return landscape.applications.find(
        (item) => item.id === integrationSelection.applicationId,
      )?.label;
    }
    if (integrationSelection.kind === "system") {
      return (
        landscape.business_topology?.systems.find(
          (item) => item.id === integrationSelection.systemId,
        )?.name ??
        landscape.systems.find((item) => item.id === integrationSelection.systemId)?.name
      );
    }
    return undefined;
  }, [integrationSelection, landscapeQuery.data]);

  const copilotScope = useMemo(
    () => topologyCopilotScope(navCurrent, integrationDisplayName),
    [navCurrent, integrationDisplayName],
  );

  // Starter prompts change as the user drills: they are the only place the
  // panel gets to show what it can answer at this level.
  const copilotSuggestions = useMemo(
    () => topologyCopilotSuggestions(navCurrent),
    [navCurrent],
  );

  // Degraded conditions, mapped from real page state. None of them implies the
  // diagram is broken -- that has its own error states above.
  const copilotNotices = useMemo<CopilotNotice[]>(() => {
    const notices: CopilotNotice[] = [];
    // Same rule the canvas uses to choose an id space: an overlay that is
    // absent, unvalidated or stale means every name comes from the scan.
    if (landscapeQuery.data && activeIdSpace(landscapeQuery.data) === "scanner") {
      notices.push(copilotNotice("stale-overlay"));
    }
    if (landscapeUnavailable) notices.push(copilotNotice("missing-artifact"));
    // A 404 is a documented absence (handled above); any other failure means
    // the service serving the artifacts answers are built from did not respond.
    if (landscapeQuery.isError && !landscapeUnavailable) {
      notices.push(copilotNotice("service-unreachable"));
    }
    return notices;
  }, [landscapeQuery.data, landscapeQuery.isError, landscapeUnavailable]);

  const copilotScopeContext = useMemo<CopilotViewContext>(() => {
    const selectedEntity = copilotViewContext.selectedEntity ?? (
      navCurrent.kind === "group"
        ? { kind: "group" as const, label: navCurrent.groupName, entity_id: navCurrent.groupName }
        : navCurrent.kind === "job"
          ? { kind: "job" as const, label: navCurrent.jobName, entity_id: navCurrent.jobName }
          : navCurrent.kind === "component"
            ? {
                kind: "component" as const,
                label: navCurrent.componentName,
                entity_id: `${navCurrent.jobName}::${navCurrent.componentName}`,
              }
            : navCurrent.kind === "integration"
              ? {
                  kind: navCurrent.selection.kind,
                  label: integrationDisplayName ?? describeTopologyLevel(navCurrent),
                  // The selection's OWN identifier, not the level render key:
                  // `integration:edge:<key>` never resolved server-side. A
                  // relationship contributes none, because it names two systems
                  // and `level.selection` already carries both.
                  ...(integrationSelectionEntityId(navCurrent.selection)
                    ? { entity_id: integrationSelectionEntityId(navCurrent.selection) }
                    : {}),
                }
              : selectedJob
                ? { kind: "job" as const, label: selectedJob, entity_id: selectedJob }
                : undefined
    );
    const rootFilters = showLegacyCanvas && atProjectLevel
      ? [
          { name: "search", value: searchQuery || "all" },
          { name: "artifact", value: artifactFilter },
          { name: "connections", value: showConnections ? "shown" : "hidden" },
        ]
      : undefined;
    return {
      selectedEntity,
      activeDetailTab: copilotViewContext.activeDetailTab,
      filters: copilotViewContext.filters ?? rootFilters,
    };
  }, [
    copilotViewContext,
    navCurrent,
    integrationDisplayName,
    selectedJob,
    showLegacyCanvas,
    atProjectLevel,
    searchQuery,
    artifactFilter,
    showConnections,
  ]);

  // The scope envelope travels with every message. It is a prompt hint only:
  // the backend never treats it as an access or retrieval filter, and nothing
  // in this UI implies it narrows what can be asked.
  const buildCopilotRequest = useCallback(
    (query: string): ConversationMessageRequest => {
      const pageScope = buildTopologyPageScope(selectedProject, navCurrent, {
        selected_entity: copilotScopeContext.selectedEntity,
        active_detail_tab: copilotScopeContext.activeDetailTab,
        filters: copilotScopeContext.filters,
      });
      return {
        query,
        project_id: selectedProject || undefined,
        ...(pageScope ? { page_scope: pageScope } : {}),
      };
    },
    [selectedProject, navCurrent, copilotScopeContext],
  );

  const copilotChat = usePageCopilotChat(buildCopilotRequest);
  // Reference-stable, so the project-change effect can depend on them without
  // re-running (and re-fetching the graph) on every render.
  const {
    startNewThread: copilotStartNewThread,
    adoptThread: copilotAdoptThread,
  } = copilotChat;

  // Three columns only fit on a desktop-width viewport; narrower ones start as
  // the 44px rail. There is no separate mobile form of this panel.
  const [copilotCollapsed, setCopilotCollapsed] = useCopilotCollapsed(
    COPILOT_COLLAPSED_KEY,
    !isDesktop,
  );

  // Read at call time rather than captured: the clamp has to reflect the window
  // the user is resizing in, not the one the page mounted in.
  const viewportWidth = useCallback(
    () => (typeof window === "undefined" ? DEFAULT_COPILOT_WIDTH * 3 : window.innerWidth),
    [],
  );
  const [copilotWidth, setCopilotWidth] = useCopilotWidth(COPILOT_WIDTH_KEY, viewportWidth);
  const resetCopilotWidth = useCallback(
    () => setCopilotWidth(DEFAULT_COPILOT_WIDTH),
    [setCopilotWidth],
  );

  // The finished answer's targets come from the PERSISTED message rather than
  // the stream buffer, so a non-streaming turn produces the same chips. Derived
  // rather than stored: state would have to be kept in step with both the
  // thread and the project, and effect ordering against the project reset below
  // is not something worth reasoning about.
  const lastAssistantMessage = useMemo(() => {
    for (let index = copilotChat.messages.length - 1; index >= 0; index -= 1) {
      const message = copilotChat.messages[index];
      if (message.role === "assistant") return message;
    }
    return null;
  }, [copilotChat.messages]);

  const copilotResolutions = useMemo<ResolvedTopologyTarget[]>(() => {
    if (!lastAssistantMessage || copilotThreadProject !== selectedProject) return [];
    return resolveTopologyTargets(
      readTopologyTargetsPayload(lastAssistantMessage.structured_answer),
      copilotGraph,
    );
  }, [lastAssistantMessage, copilotThreadProject, selectedProject, copilotGraph]);

  // The question the answer is answering. Needed because the SUBJECT of a turn
  // is not always something the answer's own targets name -- "what flows from A
  // to B" is about the A<->B relationship even when the model returned A and B
  // as two separate entities.
  const lastUserQuery = useMemo(() => {
    for (let index = copilotChat.messages.length - 1; index >= 0; index -= 1) {
      const message = copilotChat.messages[index];
      if (message.role === "user") return message.content;
    }
    return null;
  }, [copilotChat.messages]);

  // The relationship this turn is about, if any, and the graph-derived target
  // for it. Computed independently of Follow mode: Stay Put still highlights
  // the relationship, it just does not reframe the viewport for it.
  const copilotRelationship = useMemo(
    () => relationshipShape({
      resolutions: copilotResolutions,
      selectedEntity: copilotScopeContext.selectedEntity,
      query: lastUserQuery,
      graph: copilotGraph,
    }),
    [copilotResolutions, copilotScopeContext.selectedEntity, lastUserQuery, copilotGraph],
  );

  /**
   * The answer's targets plus the relationship it is about.
   *
   * Prepended rather than appended so the subject is the first chip and wins
   * `visual_role` priority on the endpoints it shares with the answer's own
   * targets: an endpoint the model called `supporting` is part of the primary
   * subject here, and the canvas must not fade it.
   */
  const copilotAnswerResolutions = useMemo(() => {
    if (!copilotRelationship) return copilotResolutions;
    if (findRelationshipTarget(copilotResolutions, copilotRelationship.endpoints)) {
      return copilotResolutions;
    }
    const synthesized = synthesizeRelationshipTarget(
      copilotRelationship.endpoints,
      copilotGraph,
    );
    return synthesized ? [synthesized, ...copilotResolutions] : copilotResolutions;
  }, [copilotRelationship, copilotResolutions, copilotGraph]);

  const copilotTargets = useMemo(
    () => selectRelevantTargets(copilotAnswerResolutions).map(toCopilotTarget),
    [copilotAnswerResolutions],
  );

  const landscapeCopilotEmphasis = useMemo(
    () => landscapeAssistantEmphasis(copilotAnswerResolutions),
    [copilotAnswerResolutions],
  );
  const designCopilotEmphasis = useMemo(
    () => designAssistantEmphasis(copilotResolutions),
    [copilotResolutions],
  );
  const integrationCopilotEmphasis = useMemo(
    () => integrationAssistantEmphasis(
      copilotResolutions,
      selectedLandscapeEdge?.provenance ?? [],
    ),
    [copilotResolutions, selectedLandscapeEdge],
  );
  const jobCopilotEmphasis = useMemo(
    () => currentJobName
      ? jobAssistantEmphasis(copilotResolutions, currentJobName)
      : null,
    [copilotResolutions, currentJobName],
  );
  const mappingCopilotEmphasis = useMemo(
    () => currentComponent
      ? mappingAssistantEmphasis(
          copilotResolutions,
          currentComponent.jobName,
          currentComponent.componentName,
        )
      : null,
    [copilotResolutions, currentComponent],
  );

  // Progressive highlight: nodes light up as tool results arrive, about a
  // second in, instead of after the prose finishes. Emphasis only.
  const handleProgressiveHighlight = useCallback((resolutions: ResolvedTopologyTarget[]) => {
    const ids = highlightNodeIdsForTargets(resolutions);
    setCopilotHighlightIds(ids.size > 0 ? ids : null);
  }, []);

  const copilotLiveResolutions = useProgressiveTargets({
    events: copilotChat.streamState.agentEvents ?? NO_AGENT_EVENTS,
    graph: copilotGraph,
    onHighlight: handleProgressiveHighlight,
  });

  // Same relevance cap as the finished answer: the progressive pass is fed by
  // the raw retrieval step, which is where the thirty-chip panel came from.
  const copilotLiveTargets = useMemo(
    () => selectRelevantTargets(copilotLiveResolutions).map(toCopilotTarget),
    [copilotLiveResolutions],
  );

  // The saved answer takes the emphasis over from the progressive pass. Set
  // rather than cleared-and-reset so it never blinks between the last tool
  // result and the persisted answer landing.
  useEffect(() => {
    const ids = highlightNodeIdsForTargets(copilotResolutions);
    if (ids.size === 0) return;
    setCopilotHighlightIds(ids);
  }, [copilotResolutions]);

  const copilotResolutionByKey = useMemo(() => {
    const byKey = new Map<string, ResolvedTopologyTarget>();
    // Answer resolutions last: once an answer is saved it supersedes the
    // provisional resolution of the same target.
    for (const resolution of [...copilotLiveResolutions, ...copilotAnswerResolutions]) {
      byKey.set(resolution.key, resolution);
    }
    return byKey;
  }, [copilotLiveResolutions, copilotAnswerResolutions]);

  const handleCopilotTargetHover = useCallback(
    (target: CopilotTarget | null) => {
      if (!target) {
        setCopilotHover(null);
        return;
      }
      const resolution = copilotResolutionByKey.get(target.key);
      setCopilotHover({
        ids: resolution ? highlightNodeIdsForTargets([resolution]) : new Set<string>(),
      });
    },
    [copilotResolutionByKey],
  );

  // What actually backed this answer. Derived here, next to the resolutions it
  // depends on, so the panel's diagram-flavoured chrome cannot imply the
  // diagram vouched for an answer that came from documents or runtime data.
  const copilotAnswerSource = useMemo(
    () =>
      copilotThreadProject === selectedProject
        ? topologyAnswerSource(lastAssistantMessage, copilotResolutions)
        : null,
    [lastAssistantMessage, copilotResolutions, copilotThreadProject, selectedProject],
  );

  // ── Evidence-driven visual follow ────────────────────────────────────────
  // Explicit navigation remains authoritative in either preference. Follow adds
  // at most one move per answer, and `pickMovementTarget` decides WHAT that move
  // is about -- the relationship when the turn is relationship-shaped, the
  // answer's one graph-confirmed primary entity otherwise, and nothing when a
  // relationship the user is looking at cannot be grounded.
  const movementDecision = useMemo(
    () => pickMovementTarget({
      resolutions: copilotAnswerResolutions,
      graph: copilotGraph,
      followConversation: followConversation === true,
      selectedEntity: copilotScopeContext.selectedEntity,
      query: lastUserQuery,
    }),
    [
      copilotAnswerResolutions,
      copilotGraph,
      followConversation,
      copilotScopeContext.selectedEntity,
      lastUserQuery,
    ],
  );

  useEffect(() => {
    if (!lastAssistantMessage) return;
    // `null` means the stored preference has not been read yet; acting now would
    // move (or refuse to move) on a default the user never chose.
    if (followConversation === null && movementDecision?.reason !== "explicit") return;
    // One move per answer. The ref (not the banner state) is the record, so
    // dismissing the banner, toggling Follow, or moving away cannot re-trigger
    // the jump. Stay put consumes the answer without moving it.
    if (autoNavigatedMessageRef.current === lastAssistantMessage.id) return;
    autoNavigatedMessageRef.current = lastAssistantMessage.id;

    if (!movementDecision) return;
    const { target: movementTarget, reason } = movementDecision;
    const explicit = reason === "explicit";
    const level = movementTarget.target.level;
    // Captured BEFORE the dispatch: this is what Undo puts back. A component
    // jump grows the stack by two, so a single `ascend` would strand the user
    // on a job they never asked for.
    const previousStack = navigation.stack;
    const previousViewport = getViewport();

    // Primary entities already visible on the current canvas are focused in
    // place. Deeper entities drill through the existing validated navigator.
    let focusRequest: AssistantFocusRequest | null = null;
    if (!explicit && navCurrent.kind === "project" && level.kind === "integration") {
      // Systems, flows and relationships all live ON the landscape. Focusing
      // them reframes the level the user is already on -- both endpoints and the
      // connecting edge for a relationship -- and the stack does not change.
      const emphasis = landscapeAssistantEmphasis([movementTarget]);
      focusRequest = {
        key: `${lastAssistantMessage.id}:${movementTarget.key}`,
        nodeIds: [...emphasis.nodeRoles.keys()],
        edgeIds: [...emphasis.edgeRoles.keys()],
      };
    } else if (
      !explicit &&
      showLegacyCanvas &&
      atProjectLevel &&
      (level.kind === "group" || level.kind === "job")
    ) {
      const ids = [...highlightNodeIdsForTargets([movementTarget])];
      if (ids.length > 0) {
        const focusNodes = nodes.filter((node) => ids.includes(node.id));
        if (focusNodes.length > 0) void fitView({ nodes: focusNodes, padding: 0.4, duration: 450 });
        focusRequest = { key: `${lastAssistantMessage.id}:${movementTarget.key}`, nodeIds: ids };
      }
    } else if (
      !explicit &&
      navCurrent.kind === "component" &&
      level.kind === "component" &&
      topologyLevelKey(navCurrent) === topologyLevelKey(level)
    ) {
      const emphasis = mappingAssistantEmphasis(
        [movementTarget],
        navCurrent.jobName,
        navCurrent.componentName,
      );
      focusRequest = {
        key: `${lastAssistantMessage.id}:${movementTarget.key}`,
        nodeIds: [...emphasis.nodeRoles.keys()],
        edgeIds: [...emphasis.edgeRoles.keys()],
      };
    }

    if (focusRequest && (focusRequest.nodeIds.length > 0 || focusRequest.edgeIds?.length)) {
      setAssistantFocusRequest(focusRequest);
      setAutoNavigation({
        messageId: lastAssistantMessage.id,
        label: movementTarget.label,
        detail: describeTopologyLevel(level),
        // The level the user is STILL on: nothing was pushed, so the banner
        // must be retired by a move away from here, not from the target.
        levelKey: topologyLevelKey(navCurrent),
        previousStack,
        previousViewport,
        reason,
        movement: "focused",
      });
      return;
    }

    if (!navigateToTarget(movementTarget, navigation)) return;
    setSelectedJob(null);
    setCopilotHover(null);
    setAutoNavigation({
      messageId: lastAssistantMessage.id,
      label: movementTarget.label,
      detail: describeTopologyLevel(level),
      levelKey: topologyLevelKey(level),
      previousStack,
      previousViewport,
      reason,
      // Descending into the detail of a landscape entity keeps the subject and
      // changes the depth; anything else changes the subject.
      movement: level.kind === "integration" ? "opened" : "navigated",
    });
  }, [
    movementDecision,
    followConversation,
    lastAssistantMessage,
    navigation,
    navCurrent,
    showLegacyCanvas,
    atProjectLevel,
    nodes,
    fitView,
    getViewport,
  ]);

  // The banner describes the level it put the user on. The moment they move
  // again -- breadcrumb, Escape, another chip -- it is describing the past.
  useEffect(() => {
    if (!autoNavigation) return;
    if (topologyLevelKey(navCurrent) === autoNavigation.levelKey) return;
    setAutoNavigation(null);
  }, [navCurrent, autoNavigation]);

  const handleUndoAutoNavigation = useCallback(() => {
    if (!autoNavigation) return;
    replaceNavigationStack(autoNavigation.previousStack);
    setSelectedJob(null);
    setAssistantFocusRequest(null);
    requestAnimationFrame(() => {
      void setViewport(autoNavigation.previousViewport, { duration: 300 });
    });
    setAutoNavigation(null);
  }, [autoNavigation, replaceNavigationStack, setViewport]);

  const handleCopilotTargetClick = useCallback(
    (target: CopilotTarget) => {
      const resolution = copilotResolutionByKey.get(target.key);
      if (!resolution) return;
      // Clicking is the ONLY copilot path allowed to move the viewport, and
      // only because the user asked for it. An unresolvable target returns
      // false here and navigates nowhere (its chip is not even a button).
      if (!navigateToTarget(resolution, navigation)) return;
      setSelectedJob(null);
      setCopilotHover(null);
    },
    [copilotResolutionByKey, navigation],
  );

  const handleCopilotSend = useCallback(
    (query: string) => {
      // A new question supersedes the previous answer: its emphasis goes with
      // it, and the canvas falls back to whatever it showed before (the
      // failure-focus resting dim) without the viewport moving. The previous
      // answer's chips are hidden by the panel itself while a turn is in
      // flight, and replaced when the new answer is saved.
      setCopilotHighlightIds(null);
      setCopilotHover(null);
      setAssistantFocusRequest(null);
      // The previous answer's jump notice belongs to the previous answer.
      setAutoNavigation(null);
      copilotChat.send(query);
    },
    [copilotChat],
  );

  const handleContinueInChat = useCallback(() => {
    void copilotChat.continueInMainChat().then((conversationId) => {
      if (conversationId !== null) router.push(`/?c=${conversationId}`);
    });
  }, [copilotChat, router]);

  // The panel treats ANY non-null streamState as "this thread has something in
  // it" and hides the idle state -- which is where the suggested prompts live.
  // So the idle stream is reported as absent rather than as an empty object,
  // otherwise the starter prompts would never appear at all.
  const copilotStreamState = useMemo(() => {
    const state = copilotChat.streamState;
    const hasContent = Boolean(state.isStreaming || state.content || state.error);
    return hasContent ? state : null;
  }, [copilotChat.streamState]);

  // Null or non-empty, never an empty set: an empty set would dim the entire
  // canvas while also suppressing the resting dim underneath it.
  const copilotEmphasisIds = useMemo(() => {
    const ids = copilotHover ? copilotHover.ids : copilotHighlightIds;
    return ids && ids.size > 0 ? ids : null;
  }, [copilotHover, copilotHighlightIds]);

  // Sync with global project selection, fall back to first scanned project.
  // A deep link outranks both, once -- it is the most specific statement of
  // intent available, but only until the user picks something themselves.
  useEffect(() => {
    if (projects.length === 0) return;
    const requested = pendingDeepLinkProjectRef.current;
    if (requested !== null) {
      pendingDeepLinkProjectRef.current = null;
      if (projects.some((p) => p.id === requested)) {
        setSelectedProjectLocal(requested);
        setGlobalProject(requested);
        return;
      }
      // Named a project this user cannot see or that no longer exists: the
      // level cannot mean anything either, so it is not applied.
      setPendingDeepLinkLevel(null);
      setDeepLinkRejected(true);
    }
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
    // The copilot's answer was about the previous project: drop its emphasis
    // and start a fresh thread. Re-pointing `copilotThreadProject` is what
    // suppresses the old answer's chips; it is deliberately also read during
    // the render BEFORE this effect runs, so there is no frame in which a chip
    // resolves against the new project's graph.
    setCopilotHighlightIds(null);
    setCopilotHover(null);
    setAssistantFocusRequest(null);
    setAutoNavigation(null);
    autoNavigatedMessageRef.current = null;
    setCopilotThreadProject(selectedProject);
    // A deep link may name the thread the level came from, so the answer that
    // produced the link is still on screen when the page opens. Consumed once,
    // like the level: any later project switch starts a fresh thread.
    const adoptThreadId = pendingDeepLinkThreadRef.current;
    pendingDeepLinkThreadRef.current = null;
    if (adoptThreadId !== null) copilotAdoptThread(adoptThreadId);
    else copilotStartNewThread();
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
  }, [selectedProject, resetNavigation, copilotStartNewThread, copilotAdoptThread]);

  // Apply a deep link's level, once there is something to check it against.
  //
  // Deliberately gated on loaded data rather than applied on mount: the level
  // is validated by exactly the same rules as a copilot chip (job names,
  // component membership, source roots, the active id space), so a link naming
  // something this scan does not have fails safe to the landscape instead of
  // dropping the user on an empty canvas.
  useEffect(() => {
    if (!pendingDeepLinkLevel) return;
    // Nothing loaded yet: wait rather than guess. The graph fetch failing
    // leaves this pending forever, which is correct -- the page shows its own
    // error and there is no canvas to place anyone on.
    if (!copilotGraph.hasManifest && !copilotGraph.hasLandscape) return;

    setPendingDeepLinkLevel(null);
    if (!isTopologyLevelResolvable(pendingDeepLinkLevel, copilotGraph)) {
      setDeepLinkRejected(true);
      return;
    }
    // Restored as a normal stack position: the breadcrumb reads the same as if
    // the user had drilled here, and Escape/ascend walks back out.
    restoreNavigation(pendingDeepLinkLevel);
  }, [pendingDeepLinkLevel, copilotGraph, restoreNavigation]);

  // Keep the address bar on the level in view, so it stays copy-pasteable.
  //
  // `history.replaceState`, not `router.replace`: this fires on every
  // drill-down, and a router navigation would re-render the route for what is
  // only an address-bar update -- on a page whose layout effect is carefully
  // guarded against exactly that kind of incidental re-run.
  const lastWrittenUrlRef = useRef<string | null>(null);
  useEffect(() => {
    if (typeof window === "undefined") return;
    // Writing before the incoming link has been applied would erase it.
    if (pendingDeepLinkLevel) return;
    if (!selectedProject) return;

    // Start from what is there: params this page does not own stay untouched.
    const params = new URLSearchParams(window.location.search);
    for (const key of TOPOLOGY_LEVEL_PARAMS) params.delete(key);
    for (const [key, value] of topologyLevelToParams(navCurrent)) params.append(key, value);
    params.set(TOPOLOGY_PROJECT_PARAM, selectedProject);
    if (copilotChat.conversationId) {
      params.set(TOPOLOGY_CONVERSATION_PARAM, String(copilotChat.conversationId));
    } else {
      params.delete(TOPOLOGY_CONVERSATION_PARAM);
    }

    const next = `${window.location.pathname}?${params.toString()}`;
    if (next === lastWrittenUrlRef.current) return;
    lastWrittenUrlRef.current = next;
    window.history.replaceState(window.history.state, "", next);
  }, [navCurrent, selectedProject, copilotChat.conversationId, pendingDeepLinkLevel]);

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

    // Add compact flag + layout direction to node data (direction lets node
    // components swap their handle sides in LR mode).
    const nodesWithCompact = visibleNodes.map((n) => ({
      ...n,
      data: { ...n.data, compact: compactMode, dir: direction },
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

    // Fit behavior: expanding a group zooms to THAT group's jobs; everything
    // else (initial load, mode/direction change, collapse) fits the whole
    // canvas — but only when the meaningful layout identity changed, so
    // unrelated effect re-runs don't steal the viewport (see lastFitKeyRef).
    const expandTarget = expandTargetRef.current;
    const fitKey = `${selectedProject}|${direction}|${compactMode}|${laid.length}|${focusJobId ?? ""}|${focusHops}`;
    if (expandTarget) {
      expandTargetRef.current = null;
      // Swallow the next identity-based fit so it can't yank the viewport
      // back out of the expanded group right after the zoom lands.
      lastFitKeyRef.current = fitKey;
      const expandedJobNodes = laid.filter(
        (n) => n.id.startsWith("job:") && (n.data.group as string) === expandTarget,
      );
      if (expandedJobNodes.length > 0) {
        // fitBounds on bounds computed from the layout positions + known node
        // dimensions — deterministic, unlike fitView({nodes}) which reads
        // DOM measurements that don't exist yet right after setNodes.
        let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
        for (const n of expandedJobNodes) {
          const { width, height } = getDimensionsForId(n.id, compactMode);
          minX = Math.min(minX, n.position.x);
          minY = Math.min(minY, n.position.y);
          maxX = Math.max(maxX, n.position.x + width);
          maxY = Math.max(maxY, n.position.y + height);
        }
        requestAnimationFrame(() => {
          // fitBounds has no minZoom option (unlike fitView) — clamp
          // afterward instead. Re-derive the viewport that keeps the same
          // bounds-center point on screen at the floor zoom, using only
          // values already known (no container width/height needed): since
          // fitBounds just centered (cx,cy) at (x0,y0,z0), the same screen
          // center at any other zoom z1 is x0 + cx*(z0-z1), y0 + cy*(z0-z1).
          const cx = minX + (maxX - minX) / 2;
          const cy = minY + (maxY - minY) / 2;
          fitBounds(
            { x: minX, y: minY, width: maxX - minX, height: maxY - minY },
            { padding: 0.15, duration: 400 },
          ).then(() => {
            const vp = getViewport();
            if (vp.zoom < FIT_MIN_ZOOM) {
              setViewport(
                {
                  x: vp.x + cx * (vp.zoom - FIT_MIN_ZOOM),
                  y: vp.y + cy * (vp.zoom - FIT_MIN_ZOOM),
                  zoom: FIT_MIN_ZOOM,
                },
                { duration: 150 },
              );
            }
          });
        });
      }
    } else if (fitKey !== lastFitKeyRef.current) {
      // The focal id + hop count are part of the key so re-focusing or
      // expanding the horizon always refits the tight neighbourhood.
      lastFitKeyRef.current = fitKey;
      requestAnimationFrame(() => {
        fitView({ padding: focusActive ? 0.25 : 0.1, duration: 300, minZoom: FIT_MIN_ZOOM });
      });
    }
  }, [flowResult, visibleNodes, visibleEdges, direction, compactMode, groupMembership, selectedProject, focusActive, focusJobId, focusHops, setNodes, setEdges, fitView, fitBounds]);

  // Search matching — in collapsed mode, also match job names inside collapsed groups
  const searchMatchIds = useMemo<Set<string> | null>(() => {
    if (!searchQuery.trim()) return null;
    const q = searchQuery.toLowerCase();
    const matches = new Set<string>();

    for (const node of visibleNodes) {
      const label = (node.data.label as string) || "";
      const displayLabel = (node.data.displayLabel as string) || "";
      if (label.toLowerCase().includes(q) || displayLabel.toLowerCase().includes(q)) {
        matches.add(node.id);
      }
    }

    // In collapsed mode, also search within collapsed group job names
    if (isCollapsed && activeManifest) {
      const jobGroups = activeManifest.job_groups || {};
      for (const job of Object.keys(jobGroups)) {
        const group = topologyGroupForJob(activeManifest, job);
        if (job.toLowerCase().includes(q) && !expandedGroups.has(group)) {
          // The group summary node matches
          matches.add(`group:${group}`);
        }
      }
    }

    return matches;
  }, [searchQuery, visibleNodes, isCollapsed, activeManifest, expandedGroups]);

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

    const connectedSet = new Set<string>([hoveredNodeId]);
    let frontier = new Set<string>([hoveredNodeId]);
    // In the Talend landscape, a group and another group are commonly two
    // hops apart through a shared source/destination hub. Trace those two
    // hops so hovering a group reveals the actual producer -> resource ->
    // consumer relationship instead of highlighting only the middle hub.
    const maxHops =
      showArtifactTaxonomy &&
      focusedGroup === null &&
      hoveredNodeId.startsWith("group:")
        ? 2
        : 1;
    for (let hop = 0; hop < maxHops; hop += 1) {
      const next = new Set<string>();
      for (const nodeId of frontier) {
        for (const neighbor of adjacencyMap.get(nodeId) || []) {
          if (connectedSet.has(neighbor)) continue;
          connectedSet.add(neighbor);
          next.add(neighbor);
        }
      }
      frontier = next;
    }

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
  }, [
    hoveredNodeId,
    searchMatchIds,
    adjacencyMap,
    showArtifactTaxonomy,
    focusedGroup,
    setNodes,
    setEdges,
  ]);

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
  // copilot > failure-focus. While any higher source owns the fields this
  // effect no-ops; when they clear it re-runs (they are all deps) and reapplies
  // focus AFTER their reset branch, so the two never fight into an inconsistent
  // state. A live poll changing activeFocusSet also re-runs it. This is also
  // what "restores the prior canvas emphasis" means for a dismissed copilot
  // answer: copilotEmphasisIds going null re-runs this effect.
  useEffect(() => {
    if (
      searchMatchIds !== null ||
      hoveredNodeId !== null ||
      projectLineageIds !== null ||
      copilotEmphasisIds !== null
    ) {
      return;
    }
    const focusSet = activeFocusSet;
    setNodes((nds) =>
      nds.map((n) => {
        if (n.id.startsWith("group-bg:")) return n;
        // group:* and conn:* nodes are never dimmed by focus (isFocusDimmableNode).
        const newDimmed = focusSet && isFocusDimmableNode(n.id) ? !focusSet.has(n.id) : false;
        if (
          n.data.dimmed === newDimmed &&
          n.data.highlighted === false &&
          n.data.assistantVisualRole === undefined
        ) return n;
        return {
          ...n,
          data: {
            ...n.data,
            dimmed: newDimmed,
            highlighted: false,
            assistantVisualRole: undefined,
          },
        };
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
  }, [
    activeFocusSet,
    searchMatchIds,
    hoveredNodeId,
    projectLineageIds,
    copilotEmphasisIds,
    setNodes,
    setEdges,
  ]);

  // Copilot emphasis: the places the current answer points at.
  //
  // A new tier in the SAME dimmed/highlighted + edge-opacity channel as
  // search/hover/lineage/failure-focus, not a parallel mechanism (see the
  // precedence note above). It sits directly over the resting dim, so an
  // answer's targets are visible by default while still yielding to whatever
  // the user is actively doing with the mouse or the search box.
  //
  // NO VIEWPORT CALL ON THIS PATH, deliberately: this also fires mid-stream
  // from useProgressiveTargets, and panning or zooming the canvas while prose
  // is still arriving is disorienting. Only an explicit chip click navigates.
  useEffect(() => {
    if (copilotEmphasisIds === null) return;
    if (searchMatchIds !== null || hoveredNodeId !== null || projectLineageIds !== null) {
      return;
    }
    // Same reference-stability rule as every other emphasis effect: only nodes
    // whose state actually changed get a new data ref, otherwise ReactFlow
    // re-renders the whole graph (see the search effect's comment).
    setNodes((nds) =>
      nds.map((n) => {
        if (n.id.startsWith("group-bg:")) return n;
        const isMatch = copilotEmphasisIds.has(n.id);
        const assistantVisualRole = isMatch
          ? designCopilotEmphasis.nodeRoles.get(n.id)
          : undefined;
        if (
          n.data.dimmed === !isMatch &&
          n.data.highlighted === isMatch &&
          n.data.assistantVisualRole === assistantVisualRole
        ) return n;
        return {
          ...n,
          data: { ...n.data, dimmed: !isMatch, highlighted: isMatch, assistantVisualRole },
        };
      }),
    );
    setEdges((eds) =>
      eds.map((e) => {
        const visible =
          copilotEmphasisIds.has(e.source) && copilotEmphasisIds.has(e.target);
        const newOpacity = visible ? undefined : 0.15;
        if ((e.style?.opacity ?? undefined) === newOpacity) return e;
        if (newOpacity === undefined) {
          const { opacity: _drop, ...restStyle } = e.style ?? {};
          return { ...e, style: restStyle };
        }
        return { ...e, style: { ...e.style, opacity: newOpacity } };
      }),
    );
  }, [
    copilotEmphasisIds,
    searchMatchIds,
    hoveredNodeId,
    projectLineageIds,
    designCopilotEmphasis,
    setNodes,
    setEdges,
  ]);

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

  // The diagram follows the panel: widening the copilot narrows this column, and
  // without a refit the graph simply slides out of view on the right. Disabled
  // in focus mode, where the framing is the user's own and a full-graph fit
  // would throw away the neighbourhood they resized the panel to look at.
  const refitCanvas = useCallback(
    () => fitView({ padding: 0.1, duration: 300, minZoom: FIT_MIN_ZOOM }),
    [fitView],
  );
  const canvasColumnRef = useFitOnContainerResize(refitCanvas, { enabled: !focusActive });

  // Callbacks
  const handleToggleDirection = useCallback(() => {
    setDirection((d) => (d === "TB" ? "LR" : "TB"));
  }, []);

  const handleToggleConnections = useCallback(() => {
    setShowConnections((s) => !s);
  }, []);

  const handleFitView = useCallback(() => {
    fitView({ padding: 0.1, duration: 300, minZoom: FIT_MIN_ZOOM });
  }, [fitView]);

  const handleExploreLandscape = useCallback((selection: LandscapeSelection) => {
    setSelectedJob(null);
    setFocusJobId(null);
    setLineageQuery(null);
    if (selection.kind === "edge") {
      descendToIntegration({ kind: "edge", edgeKey: landscapeEdgeKey(selection.item) });
    } else if (selection.kind === "relationship") {
      descendToIntegration({ kind: "relationship", source: selection.source, target: selection.target, streamKeys: selection.streamKeys });
    } else if (selection.kind === "application") {
      descendToIntegration({ kind: "application", applicationId: selection.item.id });
    } else {
      descendToIntegration({ kind: "system", systemId: selection.item.id });
    }
  }, [descendToIntegration]);

  const handleOpenLandscapeArtifact = useCallback((record: LandscapeProvenance) => {
    setSelectedJob(null);
    // `artifact_id` is the scanner's exact design identity. `sourceRoot` is
    // retained in navigation for evidence/breadcrumb continuity even when the
    // legacy job endpoint itself has only a job-name path parameter.
    descendToJob(record.artifact_id, record.source_root);
  }, [descendToJob]);

  const handleViewApplicationDesigns = useCallback((sourceRoot: string) => {
    // The legacy functional-group canvas is intentionally retained as an
    // application-internals/design view. Source-root filtering is represented
    // in navigation now; the legacy graph remains its established fallback.
    descendToGroup(sourceRoot, sourceRoot);
  }, [descendToGroup]);

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
      const groupName = node.id.slice(6);
      if (showArtifactTaxonomy) {
        descendToGroup(groupName);
        setSelectedJob(null);
        setFocusJobId(null);
        setLineageQuery(null);
        return;
      }
      // Toggling triggers the main layout effect's full compound-dagre
      // relayout (see its comment) - no position bookkeeping needed here.
      setExpandedGroups((prev) => {
        const next = new Set(prev);
        if (next.has(groupName)) {
          next.delete(groupName);
        } else {
          next.add(groupName);
          expandTargetRef.current = groupName;
        }
        return next;
      });
    }
  }, [showArtifactTaxonomy, descendToGroup]);

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
    if (showArtifactTaxonomy) {
      if (focusedGroup === group) {
        jumpTo(0);
      } else {
        descendToGroup(group);
      }
      setSelectedJob(null);
      setFocusJobId(null);
      setLineageQuery(null);
      return;
    }
    if (isCollapsed) {
      setExpandedGroups((prev) => {
        const next = new Set(prev);
        if (next.has(group)) {
          next.delete(group);
        } else {
          next.add(group);
          expandTargetRef.current = group;
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
  }, [
    showArtifactTaxonomy,
    focusedGroup,
    jumpTo,
    descendToGroup,
    isCollapsed,
  ]);

  const handleShowAllGroups = useCallback(() => {
    if (showArtifactTaxonomy) {
      jumpTo(0);
      return;
    }
    if (isCollapsed) {
      setExpandedGroups(new Set());
    } else {
      setHiddenGroups(new Set());
    }
  }, [showArtifactTaxonomy, jumpTo, isCollapsed]);

  const handleHideAllGroups = useCallback(() => {
    if (showArtifactTaxonomy) return;
    if (isCollapsed) {
      setExpandedGroups(new Set());
    } else if (flowResult) {
      setHiddenGroups(new Set(flowResult.groups));
    }
  }, [showArtifactTaxonomy, isCollapsed, flowResult]);

  const handleCollapseAll = useCallback(() => {
    if (showArtifactTaxonomy) {
      jumpTo(0);
      setSelectedJob(null);
      setFocusJobId(null);
      setLineageQuery(null);
    }
    setCollapsedMode(true);
    setExpandedGroups(new Set());
  }, [showArtifactTaxonomy, jumpTo]);

  const handleExpandAll = useCallback(() => {
    if (showArtifactTaxonomy) return;
    setCollapsedMode(false);
    setExpandedGroups(new Set());
    setHiddenGroups(new Set());
  }, [showArtifactTaxonomy]);

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
        contextCount: 0,
        edgeCount: 0,
        groupCount: 0,
        routeCount: 0,
        talendJobCount: 0,
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
    if ((node.data as Record<string, unknown>).artifactKind === "route") {
      return "#ea580c";
    }
    if ((node.data as Record<string, unknown>).artifactKind === "job") {
      return "#2563eb";
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
              onClick={() => fitView({ padding: 0.15, duration: 300, minZoom: FIT_MIN_ZOOM })}
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
      <div className="flex items-center gap-4 px-4 py-3 border-b" data-tour="topology-header">
        <h1 className="hidden sm:block text-lg font-semibold">Project Topology</h1>
        <div className="relative inline-block">
          <select
            value={selectedProject}
            onChange={(e) => setSelectedProject(e.target.value)}
            className="appearance-none cursor-pointer rounded-full border border-input bg-muted/40 pl-3 pr-7 py-1 text-xs text-muted-foreground hover:text-foreground hover:bg-muted focus:outline-none focus:ring-1 focus:ring-ring transition-colors"
          >
            {projects.length === 0 && (
              // Three distinct placeholders for three distinct states. Only a
              // successful, genuinely empty response may claim the account has
              // nothing scanned.
              <option value="">
                {projectsOutcome.kind === "unknown"
                  ? "Loading projects…"
                  : projectsOutcome.kind === "failed"
                    ? "Projects unavailable — retry"
                    : "No scanned projects"}
              </option>
            )}
            {projects.map((p) => {
              const counts = p.artifact_counts;
              const total = counts?.total_artifacts ?? p.job_count;
              const countLabel =
                p.platform.toLowerCase() === "talend" && counts
                  ? `${total} designs — ${counts.job_count ?? 0} Jobs · ${counts.route_count ?? 0} Routes`
                  : `${p.job_count} components`;
              return (
                <option key={p.id} value={p.id}>
                  {p.display_name} ({p.platform}, {countLabel})
                </option>
              );
            })}
          </select>
          <ChevronDown className="pointer-events-none absolute right-2 top-1/2 -translate-y-1/2 h-3 w-3 text-muted-foreground" />
        </div>
        <button
          type="button"
          aria-pressed={Boolean(followConversation)}
          aria-label={followConversation ? "Follow conversation enabled" : "Stay put enabled"}
          onClick={() => handleFollowModeChange(!followConversation)}
          className="inline-flex items-center gap-1.5 rounded-full border border-input bg-muted/30 px-2.5 py-1 text-xs font-medium text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
          title={followConversation ? "Assistant may focus or drill to one exact primary target" : "Assistant highlights answers without moving the view"}
        >
          {followConversation ? <Eye className="h-3.5 w-3.5" aria-hidden /> : <EyeOff className="h-3.5 w-3.5" aria-hidden />}
          {followConversation ? "Follow conversation" : "Stay put"}
        </button>
        {navDepth > 1 && (
          <TopologyBreadcrumb
            projectLabel={
              showArtifactTaxonomy
                ? "Landscape"
                : projects.find((p) => p.id === selectedProject)?.display_name ||
                  selectedProject
            }
            stack={navStack}
            onNavigate={jumpTo}
          />
        )}
      </div>

      {/* A link that named something this scan does not have. Stated rather
          than swallowed: the user followed a link and landed elsewhere, and
          silently showing the landscape would read as the link being wrong
          about the level rather than about the project's contents. */}
      {deepLinkRejected && (
        <div
          role="status"
          data-testid="deep-link-rejected"
          className="flex items-start gap-2 border-b border-amber-400/50 bg-amber-400/10 px-4 py-2 text-xs text-amber-800 dark:text-amber-300"
        >
          <p className="min-w-0 flex-1">
            That link points at something that is not in the topology loaded here. Showing the
            landscape instead.
          </p>
          <button
            type="button"
            onClick={() => setDeepLinkRejected(false)}
            aria-label="Dismiss link notice"
            className="shrink-0 rounded p-0.5 hover:bg-amber-400/20"
          >
            <X className="h-3.5 w-3.5" aria-hidden />
          </button>
        </div>
      )}

      {/* Toolbar (level 0/1 only) */}
      {flowResult && atProjectLevel && showLegacyCanvas && (
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
          artifactFilter={artifactFilter}
          onArtifactFilterChange={setArtifactFilter}
          showArtifactTaxonomy={showArtifactTaxonomy}
          focusedGroup={focusedGroup}
        />
      )}

      {/* Graph area + docked copilot.
          A flex ROW, not a third absolute overlay. Every panel this page
          already floats -- JobDetailPanel (`right-0`), LandscapeEvidencePanel
          (`bottom-3 right-3`), IntegrationDetailCanvas's explanation aside --
          is absolutely positioned against the nearest positioned ancestor,
          which is the canvas container below (each level's canvas root is
          `absolute inset-0` inside it). Keeping the copilot a SIBLING of that
          container is therefore what makes those overlays anchor to the
          canvas's right edge -- beside the panel rather than underneath it --
          without touching a single one of them.
          `min-h-[500px]` stays on the row so the canvas keeps its existing
          minimum height, and the row's definite height is what lets the
          panel's `h-full` and its internal message scroller resolve. */}
      <div className="flex flex-1 min-h-[500px] overflow-hidden">
        <div ref={canvasColumnRef} className="relative flex-1 min-h-0 min-w-0 overflow-hidden">
        {/* Level 0/1 canvas: kept mounted (hidden) during drill-down so
            ascending restores the prior viewport and expand state. */}
        <div className={showLegacyCanvas ? "absolute inset-0" : "absolute inset-0 hidden"}>
        {landscapeUnavailable && navCurrent.kind === "project" && (
          <div className="absolute inset-x-4 top-3 z-30 rounded-lg border border-blue-200 bg-blue-50 px-3 py-2 text-xs text-blue-900 shadow-sm">
            This project needs a rescan for the organization landscape. Showing the existing project-design topology instead.
          </div>
        )}
        {landscapeHasNoDirectionalStreams && (
          <div className="absolute inset-x-4 top-3 z-30 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-950 shadow-sm">
            No directional business-system streams were established by this scan. Showing the project-design topology and evidenced external-system participation instead.
          </div>
        )}
        {loading && (
          <div className="absolute inset-0 flex items-center justify-center bg-background/80 z-20">
            <div className="flex items-center gap-2 text-muted-foreground">
              <div className="h-4 w-4 border-2 border-current border-t-transparent rounded-full animate-spin" />
              Loading topology...
            </div>
          </div>
        )}

        {/* A project-list failure is its own failure with its own retry, and
            must not be silently rendered as an account with nothing scanned. */}
        {projectsOutcome.kind === "failed" && !selectedProject && !loading && (
          <div className="absolute inset-0 flex items-center justify-center">
            <div className="text-center text-muted-foreground">
              <p className="text-sm">{projectsOutcome.message}</p>
              <p className="mt-1 text-xs">
                The project list could not be loaded. Your session is still valid.
              </p>
              <button
                onClick={() => void projectsQuery.refetch()}
                className="mt-2 text-xs underline hover:text-foreground"
              >
                Retry
              </button>
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
          {flowResult && (
            <GraphLegend
              groupColorMap={groupColorMap}
              collapsedMode={isCollapsed}
              showHealth={overlayActive}
              showArtifactTaxonomy={showArtifactTaxonomy}
            />
          )}
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

        {navCurrent.kind === "project" && landscapeQuery.isLoading && selectedProject && (
          <div className="absolute inset-0 flex items-center justify-center bg-background/80 z-20">
            <div className="flex items-center gap-2 text-muted-foreground"><div className="h-4 w-4 border-2 border-current border-t-transparent rounded-full animate-spin" />Loading organization landscape...</div>
          </div>
        )}

        {landscapeFailure && navCurrent.kind === "project" && (
          <div className="absolute inset-0 flex items-center justify-center p-6">
            <div className="max-w-md rounded-xl border border-rose-200 bg-rose-50 p-5 text-center text-sm text-rose-900"><p className="font-semibold">The organization landscape could not be loaded.</p><p className="mt-1 text-xs">{landscapeQuery.error instanceof Error ? landscapeQuery.error.message : "Retry or rescan the project."}</p><button type="button" onClick={() => landscapeQuery.refetch()} className="mt-3 rounded border border-rose-300 bg-white px-3 py-1.5 text-xs font-semibold hover:bg-rose-100">Retry</button></div>
          </div>
        )}

        {showLandscapeRoot && landscapeQuery.data && (
          <LandscapeCanvas
            landscape={landscapeQuery.data}
            onExplore={handleExploreLandscape}
            onViewDesigns={handleViewApplicationDesigns}
            assistantEmphasis={landscapeCopilotEmphasis}
            assistantFocusRequest={assistantFocusRequest}
            onContextChange={setCopilotViewContext}
          />
        )}

        {navCurrent.kind === "integration" && landscapeQuery.data && (
          selectedLandscapeEdge ? (
            <IntegrationDetailCanvas
              landscape={landscapeQuery.data}
              edge={selectedLandscapeEdge}
              onBack={ascend}
              onOpenArtifact={handleOpenLandscapeArtifact}
              assistantEmphasis={integrationCopilotEmphasis}
              assistantFocusRequest={assistantFocusRequest}
              onContextChange={setCopilotViewContext}
            />
          ) : integrationSelection?.kind === "application" ? (
            <IntegrationRelationshipChooser landscape={landscapeQuery.data} entityId={integrationSelection.applicationId} title={landscapeQuery.data.applications.find((item) => item.id === integrationSelection.applicationId)?.label || integrationSelection.applicationId} onBack={ascend} onChoose={(edge) => descendToIntegration({ kind: "edge", edgeKey: landscapeEdgeKey(edge) })} />
          ) : integrationSelection?.kind === "system" ? (
            <IntegrationRelationshipChooser landscape={landscapeQuery.data} entityId={integrationSelection.systemId} title={landscapeQuery.data.business_topology?.systems.find((item) => item.id === integrationSelection.systemId)?.name || landscapeQuery.data.systems.find((item) => item.id === integrationSelection.systemId)?.name || integrationSelection.systemId} onBack={ascend} onChoose={(edge) => descendToIntegration({ kind: "edge", edgeKey: landscapeEdgeKey(edge) })} />
          ) : (
            <div className="absolute inset-0 flex items-center justify-center text-sm text-muted-foreground">This integration is no longer present in the current scan. Return to the landscape and select it again.</div>
          )
        )}

        {/* Level 2 canvas: a fresh React Flow instance per descent. The key
            forces a remount (new layout + animated fitView) for every level,
            including tRunJob portal hops between sibling jobs. */}
        {navCurrent.kind === "job" && currentJobName && selectedProject && (
          <>
          <JobGraphCanvas
            key={`${navDepth}:${currentJobName}`}
            projectId={selectedProject}
            jobName={currentJobName}
            artifactKind={graphData?.job_metadata?.[currentJobName]?.artifact_kind}
            onOpenJob={descendToJob}
            onOpenComponent={descendToComponent}
            lineageComponentIds={jobLineageIds}
            assistantEmphasis={jobCopilotEmphasis}
            assistantFocusRequest={assistantFocusRequest}
            onContextChange={setCopilotViewContext}
          />
          <BusinessContextBanner annotations={jobBusinessAnnotations} level="L3 job" />
          </>
        )}

        {/* Level 3 canvas: one component's table graph (tMap) - or, on the
            documented 404 fallback, the inspector rendered in this same
            slot. Also remounts fresh per descent. */}
        {navCurrent.kind === "component" && currentComponent && selectedProject && (
          <>
          <ComponentGraphCanvas
            key={`${navDepth}:${currentComponent.jobName}:${currentComponent.componentName}`}
            projectId={selectedProject}
            jobName={currentComponent.jobName}
            componentName={currentComponent.componentName}
            onAscend={ascend}
            lineageQuery={lineageQuery}
            lineageHighlight={componentLineage}
            onColumnLineageClick={handleColumnLineageClick}
            assistantEmphasis={mappingCopilotEmphasis}
            assistantFocusRequest={assistantFocusRequest}
            onContextChange={setCopilotViewContext}
          />
          <BusinessContextBanner annotations={[...componentBusinessAnnotations, ...lineageBusinessAnnotations]} level="L4 mapping" />
          </>
        )}

        {/* The view moved because the answer was asked to move it. Rendered
            over whichever level's canvas is mounted, since the jump can land
            on any of them. */}
        {autoNavigation && (
          <AutoNavigationBanner
            label={autoNavigation.label}
            detail={
              autoNavigation.detail === autoNavigation.label ? undefined : autoNavigation.detail
            }
            onUndo={handleUndoAutoNavigation}
            reason={autoNavigation.reason}
            movement={autoNavigation.movement}
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

        <PageCopilot
          scope={copilotScope}
          messages={copilotChat.messages}
          conversationId={copilotChat.conversationId}
          streamState={copilotStreamState}
          pendingUserMessage={copilotChat.pendingUserMessage}
          isSending={copilotChat.isSending}
          onSend={handleCopilotSend}
          onRetry={copilotChat.retry}
          onContinueInChat={handleContinueInChat}
          isContinuingInChat={copilotChat.isContinuingInMainChat}
          errorMessage={copilotChat.errorMessage}
          suggestions={copilotSuggestions}
          onSuggestionClick={handleCopilotSend}
          targets={copilotTargets}
          liveTargets={copilotLiveTargets}
          onTargetHover={handleCopilotTargetHover}
          onTargetClick={handleCopilotTargetClick}
          answerSource={copilotAnswerSource}
          notices={copilotNotices}
          collapsed={copilotCollapsed}
          onCollapsedChange={setCopilotCollapsed}
          width={copilotWidth}
          onWidthChange={setCopilotWidth}
          onWidthReset={resetCopilotWidth}
          placeholder="Ask about this diagram..."
        />
      </div>
    </div>
    </FocusInteractionContext.Provider>
  );
}

export default function TopologyPage() {
  // Suspense because the page reads the deep link with `useSearchParams`,
  // which opts the route into dynamic rendering unless it is bounded.
  return (
    <Suspense fallback={null}>
      <ReactFlowProvider>
        <TopologyContent />
      </ReactFlowProvider>
    </Suspense>
  );
}
