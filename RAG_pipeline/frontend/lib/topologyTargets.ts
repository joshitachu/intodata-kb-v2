import {
  TopologyTargetListSchema,
  type IntegrationSelection,
  type TopologyLevel,
  type TopologyTarget,
} from "@/types/topology";
import type { CopilotTarget } from "@/types/copilot";
import type { PublicAgentEvent } from "@/types";
import type { ProjectLandscapeResponse } from "@/types/landscape";
import { landscapeEdgeKey } from "@/lib/landscapeTransform";
import { topologyGroupForJob, type ManifestData } from "@/lib/graphTransform";
import type { TopologyNavigation } from "@/hooks/useTopologyNavigation";

/**
 * Resolve, validate and dispatch the navigable `targets` an answer returns
 * alongside its prose (contract: `backend/src/topology/client.py`).
 *
 * Why validation is not optional. The backend reads server-side artifacts;
 * the canvas has whatever the current project and filters have loaded. A
 * target the loaded graph cannot resolve must be reported UNRESOLVABLE so the
 * chip renders inert -- clicking it would drop the user into an empty canvas,
 * which reads as a broken product rather than as missing data.
 *
 * The two id spaces. The landscape artifact carries two MUTUALLY EXCLUSIVE id
 * spaces and the canvas picks one (see `landscapeToFlow` / `connectedEdges`,
 * same rule as `activeIdSpace` below):
 *
 *   business-overlay: `landscape-system:*` system ids, `business-stream<US><id>`
 *                     edge keys -- used when a validated, non-stale business
 *                     overlay exists.
 *   scanner:          `system:connection:N:*` system ids, 7-part <US>-joined
 *                     edge keys -- used otherwise.
 *
 * There is NO bridge between them: overlay ids are derived by hashing,
 * server-side only. The backend already emits targets from the active space
 * only. A target from the inactive space is therefore reported unresolvable
 * and its id is passed through untouched -- never translated, never
 * re-prefixed, never "fixed up".
 */

const UNIT_SEPARATOR = "\u001f";
const OVERLAY_EDGE_PREFIX = `business-stream${UNIT_SEPARATOR}`;
const OVERLAY_SYSTEM_PREFIX = "landscape-system:";

export type TopologyIdSpace = "business-overlay" | "scanner";

export type UnresolvableReason =
  | "no-graph-data"
  | "unknown-application"
  | "unknown-system"
  | "unknown-edge"
  | "unknown-relationship"
  | "unknown-group"
  | "unknown-job"
  | "unknown-component"
  | "source-root-mismatch"
  | "inactive-id-space";

/** Short, user-facing copy for an inert chip's tooltip. */
export const UNRESOLVABLE_REASON_TEXT: Record<UnresolvableReason, string> = {
  "no-graph-data": "Nothing is loaded on the diagram yet.",
  "unknown-application": "This application is not in the diagram you have loaded.",
  "unknown-system": "This system is not in the diagram you have loaded.",
  "unknown-edge": "This flow is not in the diagram you have loaded.",
  "unknown-relationship": "This relationship is not in the diagram you have loaded.",
  "unknown-group": "This group is not in the diagram you have loaded.",
  "unknown-job": "This job is not in the diagram you have loaded.",
  "unknown-component": "This component is not in the job you have loaded.",
  "source-root-mismatch": "This name belongs to a different source root than the one loaded.",
  "inactive-id-space":
    "This refers to the other identity layer of the landscape. Reload the diagram to follow it.",
};

/** Artifacts the page has actually loaded, in their raw shapes. */
export interface TopologyGraphSources {
  /** L1 landscape artifact for the current project, if loaded. */
  landscape?: ProjectLandscapeResponse | null;
  /** Legacy design-graph manifest (groups, jobs, source roots), if loaded. */
  manifest?: ManifestData | null;
  /**
   * Component names per job, for the job graphs the canvas has actually
   * fetched. Job graphs load lazily, so this is normally sparse -- see
   * `resolveTopologyTarget` for how an unloaded job is handled.
   */
  componentNamesByJob?: Readonly<Record<string, readonly string[]>>;
}

/** Membership sets a target is validated against. Build with `buildTopologyGraphData`. */
export interface TopologyGraphData {
  idSpace: TopologyIdSpace;
  hasLandscape: boolean;
  hasManifest: boolean;
  applicationIds: ReadonlySet<string>;
  systemIds: ReadonlySet<string>;
  edgeKeys: ReadonlySet<string>;
  /** Ids/keys that exist only in the id space the canvas is NOT using. */
  inactiveSystemIds: ReadonlySet<string>;
  inactiveEdgeKeys: ReadonlySet<string>;
  groupNames: ReadonlySet<string>;
  jobNames: ReadonlySet<string>;
  sourceRootByJob: ReadonlyMap<string, string>;
  sourceRootsByGroup: ReadonlyMap<string, ReadonlySet<string>>;
  componentNamesByJob: ReadonlyMap<string, ReadonlySet<string>>;
  /**
   * Active-space edge keys per DIRECTED system pair (`source<US>target`).
   *
   * This is what makes a relationship an assertion about the loaded graph
   * rather than about two names that happen to appear in one sentence: a pair
   * absent from this map has no flow between it here, so nothing may claim one.
   */
  streamKeysByDirectedPair: ReadonlyMap<string, readonly string[]>;
  /** Active-space system display names, for labelling a synthesized pair. */
  systemNameById: ReadonlyMap<string, string>;
  /**
   * Lower-cased active-space system name -> id, for matching the words a user
   * actually typed. Names shorter than `MIN_MATCHABLE_SYSTEM_NAME` and names
   * shared by more than one system are omitted: a guess here would retarget
   * the canvas to the wrong system, which is worse than not moving.
   */
  systemIdByName: ReadonlyMap<string, string>;
}

export interface ResolvedTopologyTarget {
  /** Stable identity for React keys and hover/click callbacks. */
  key: string;
  label: string;
  /** The target exactly as received. Ids are never rewritten. */
  target: TopologyTarget;
  status: "resolved" | "unresolvable";
  /** Set iff unresolvable. */
  reason?: UnresolvableReason;
  /**
   * Set iff resolved. `graph` means every id/name was found in the loaded
   * data; `assumed` means one leaf could not be checked because the graph
   * that would contain it is not loaded yet (only reachable for component
   * targets -- see `resolveTopologyTarget`).
   */
  confirmed?: "graph" | "assumed";
}

export interface TopologyTargetParse {
  targets: TopologyTarget[];
  /** How many entries failed validation. Non-zero means a contract mismatch. */
  rejected: number;
}

/**
 * The canvas's own rule for which id space is live, mirrored here so a target
 * is validated against the same space the diagram is drawing. Kept identical
 * to `landscapeToFlow`/`connectedEdges` in `lib/landscapeTransform.ts`.
 */
export function activeIdSpace(
  landscape: ProjectLandscapeResponse | null | undefined,
): TopologyIdSpace {
  const state = landscape?.topology_state;
  return landscape?.business_topology && state?.validated && !state.stale
    ? "business-overlay"
    : "scanner";
}

function manifestJobNames(manifest: ManifestData): Set<string> {
  // A job is anything the manifest names anywhere; no single map is complete
  // (an isolated job has no calls, a flat scan has no source roots).
  return new Set([
    ...Object.keys(manifest.job_calls ?? {}),
    ...Object.keys(manifest.job_called_by ?? {}),
    ...Object.keys(manifest.job_groups ?? {}),
    ...Object.keys(manifest.job_connections_used ?? {}),
    ...Object.keys(manifest.job_source_roots ?? {}),
    ...Object.keys(manifest.artifact_kinds ?? {}),
  ]);
}

/** Below this length a system name matches too much prose to be evidence. */
export const MIN_MATCHABLE_SYSTEM_NAME = 3;

/** Identity of a DIRECTED system pair. Order is meaning, not sorting. */
export function directedPairKey(source: string, target: string): string {
  return `${source}${UNIT_SEPARATOR}${target}`;
}

/**
 * Every edge key the loaded graph has between two systems, in the ACTIVE id
 * space only, keyed by directed pair.
 *
 * Self-edges and edges with an endpoint outside `systemIds` are skipped: the
 * former is not a relationship, and the latter (application/infrastructure
 * endpoints) is not one between two systems.
 */
function buildDirectedPairIndex(
  landscape: ProjectLandscapeResponse | null | undefined,
  overlay: boolean,
  systemIds: ReadonlySet<string>,
): Map<string, string[]> {
  const byPair = new Map<string, string[]>();
  const add = (source: string, target: string, key: string) => {
    if (source === target) return;
    if (!systemIds.has(source) || !systemIds.has(target)) return;
    const pair = directedPairKey(source, target);
    const keys = byPair.get(pair);
    if (!keys) byPair.set(pair, [key]);
    else if (!keys.includes(key)) keys.push(key);
  };
  if (overlay) {
    for (const stream of landscape?.business_topology?.data_streams ?? []) {
      add(
        stream.source_system_id,
        stream.destination_system_id,
        `${OVERLAY_EDGE_PREFIX}${stream.id}`,
      );
    }
  } else {
    for (const edge of landscape?.edges ?? []) {
      add(edge.source, edge.target, landscapeEdgeKey(edge));
    }
  }
  return byPair;
}

function buildSystemNameIndexes(
  landscape: ProjectLandscapeResponse | null | undefined,
  overlay: boolean,
): { nameById: Map<string, string>; idByName: Map<string, string> } {
  const systems = overlay
    ? landscape?.business_topology?.systems ?? []
    : landscape?.systems ?? [];
  const nameById = new Map<string, string>();
  const idByName = new Map<string, string>();
  const ambiguous = new Set<string>();
  for (const system of systems) {
    const name = system.name?.trim();
    if (!name) continue;
    nameById.set(system.id, name);
    const key = name.toLocaleLowerCase();
    if (key.length < MIN_MATCHABLE_SYSTEM_NAME) continue;
    const existing = idByName.get(key);
    if (existing && existing !== system.id) ambiguous.add(key);
    else idByName.set(key, system.id);
  }
  for (const key of ambiguous) idByName.delete(key);
  return { nameById, idByName };
}

export function buildTopologyGraphData(sources: TopologyGraphSources): TopologyGraphData {
  const { landscape, manifest, componentNamesByJob } = sources;
  const idSpace = activeIdSpace(landscape);
  const overlay = idSpace === "business-overlay";
  const business = landscape?.business_topology;

  const scannerSystemIds = new Set((landscape?.systems ?? []).map((system) => system.id));
  const overlaySystemIds = new Set((business?.systems ?? []).map((system) => system.id));
  const scannerEdgeKeys = new Set((landscape?.edges ?? []).map((edge) => landscapeEdgeKey(edge)));
  const overlayEdgeKeys = new Set(
    (business?.data_streams ?? []).map((stream) => `${OVERLAY_EDGE_PREFIX}${stream.id}`),
  );

  // Applications are not part of the exclusive split: `landscape.applications`
  // is present in both spaces and the integration detail canvas resolves an
  // applicationId against it either way. The overlay adds integration
  // solutions as additional selectable entities rather than replacing them.
  const applicationIds = new Set((landscape?.applications ?? []).map((item) => item.id));
  if (overlay) {
    for (const solution of business?.integration_solutions ?? []) applicationIds.add(solution.id);
  }

  const jobNames = manifest ? manifestJobNames(manifest) : new Set<string>();
  const groupNames = new Set<string>();
  const sourceRootByJob = new Map<string, string>();
  const sourceRootsByGroup = new Map<string, Set<string>>();
  if (manifest) {
    for (const job of jobNames) {
      const group = topologyGroupForJob(manifest, job);
      groupNames.add(group);
      const sourceRoot = manifest.job_source_roots?.[job];
      if (!sourceRoot) continue;
      sourceRootByJob.set(job, sourceRoot);
      const roots = sourceRootsByGroup.get(group) ?? new Set<string>();
      roots.add(sourceRoot);
      sourceRootsByGroup.set(group, roots);
    }
  }

  const components = new Map<string, ReadonlySet<string>>();
  for (const [job, names] of Object.entries(componentNamesByJob ?? {})) {
    components.set(job, new Set(names));
  }

  const activeSystemIds = overlay ? overlaySystemIds : scannerSystemIds;
  const { nameById, idByName } = buildSystemNameIndexes(landscape, overlay);

  return {
    idSpace,
    hasLandscape: Boolean(landscape),
    hasManifest: Boolean(manifest),
    applicationIds,
    systemIds: overlay ? overlaySystemIds : scannerSystemIds,
    edgeKeys: overlay ? overlayEdgeKeys : scannerEdgeKeys,
    inactiveSystemIds: overlay ? scannerSystemIds : overlaySystemIds,
    inactiveEdgeKeys: overlay ? scannerEdgeKeys : overlayEdgeKeys,
    groupNames,
    jobNames,
    sourceRootByJob,
    sourceRootsByGroup,
    componentNamesByJob: components,
    streamKeysByDirectedPair: buildDirectedPairIndex(landscape, overlay, activeSystemIds),
    systemNameById: nameById,
    systemIdByName: idByName,
  };
}

/**
 * Every edge key between two systems, both directions merged, forward first.
 *
 * A relationship is presented to the user as `A <-> B` -- one line, either
 * direction -- so its constituent streams are the union. Empty means the loaded
 * graph has no flow between them, and nothing may claim otherwise.
 */
export function relationshipStreamKeys(
  graph: TopologyGraphData | null,
  source: string,
  target: string,
): string[] {
  if (!graph) return [];
  const forward = graph.streamKeysByDirectedPair.get(directedPairKey(source, target)) ?? [];
  const backward = graph.streamKeysByDirectedPair.get(directedPairKey(target, source)) ?? [];
  return [...forward, ...backward.filter((key) => !forward.includes(key))];
}

/**
 * The direction of the pair the loaded graph actually carries more flow in, or
 * null when it carries none either way.
 *
 * A relationship level has to name a source and a target, and picking the
 * heavier direction is the one choice that is derived from evidence rather than
 * from the order two names happened to appear in a sentence. Ties keep the
 * requested order, so the caller's intent survives when the data is symmetric.
 */
export function dominantRelationshipDirection(
  graph: TopologyGraphData | null,
  source: string,
  target: string,
): { source: string; target: string } | null {
  if (!graph) return null;
  if (!graph.systemIds.has(source) || !graph.systemIds.has(target)) return null;
  const forward = graph.streamKeysByDirectedPair.get(directedPairKey(source, target))?.length ?? 0;
  const backward = graph.streamKeysByDirectedPair.get(directedPairKey(target, source))?.length ?? 0;
  if (forward === 0 && backward === 0) return null;
  return backward > forward ? { source: target, target: source } : { source, target };
}

function selectionKey(selection: IntegrationSelection): string {
  switch (selection.kind) {
    case "application":
      return `application:${selection.applicationId}`;
    case "system":
      return `system:${selection.systemId}`;
    case "edge":
      return `edge:${selection.edgeKey}`;
    case "relationship":
      return `relationship:${selection.source}->${selection.target}`;
  }
}

/** Stable identity for a LEVEL, independent of any target that points at it. */
export function topologyLevelKey(level: TopologyLevel): string {
  switch (level.kind) {
    case "project":
      return "project";
    case "integration":
      return `integration:${selectionKey(level.selection)}`;
    case "group":
      return `group:${level.sourceRoot ?? ""}:${level.groupName}`;
    case "job":
      return `job:${level.sourceRoot ?? ""}:${level.jobName}`;
    case "component":
      return `component:${level.sourceRoot ?? ""}:${level.jobName}:${level.componentName}`;
  }
}

/** Stable identity for a target: its level, not its label or array position. */
export function targetKey(target: TopologyTarget): string {
  return topologyLevelKey(target.level);
}

function describeSelection(selection: IntegrationSelection): string {
  switch (selection.kind) {
    case "application":
      return "Application";
    case "system":
      return "System";
    case "edge":
      return "Flow";
    case "relationship":
      return "Relationship";
  }
}

/** Short human description of a level, for chips and the scope indicator. */
export function describeTopologyLevel(level: TopologyLevel): string {
  switch (level.kind) {
    case "project":
      return "Landscape";
    case "integration":
      return describeSelection(level.selection);
    case "group":
      return level.groupName;
    case "job":
      return level.jobName;
    case "component":
      return `${level.jobName} › ${level.componentName}`;
  }
}

/** The level's kind as a chip word, e.g. for `CopilotScope.kindLabel`. */
export function topologyLevelKindLabel(level: TopologyLevel): string {
  switch (level.kind) {
    case "project":
      return "Landscape";
    case "integration":
      return "Integration";
    case "group":
      return "Group";
    case "job":
      return "Job";
    case "component":
      return "Component";
  }
}

/**
 * Validate the payload's shape. Never throws and never partially trusts: an
 * entry that fails validation is counted in `rejected` and dropped, so one
 * malformed target cannot take down the rest of the answer.
 */
export function parseTopologyTargets(payload: unknown): TopologyTargetParse {
  const list = TopologyTargetListSchema.safeParse(payload);
  if (list.success) return { targets: list.data, rejected: 0 };
  if (!Array.isArray(payload)) return { targets: [], rejected: 0 };

  const targets: TopologyTarget[] = [];
  let rejected = 0;
  for (const entry of payload) {
    const parsed = TopologyTargetListSchema.safeParse([entry]);
    if (parsed.success) targets.push(parsed.data[0]);
    else rejected += 1;
  }
  return { targets, rejected };
}

function classifySystemId(id: string, graph: TopologyGraphData): UnresolvableReason | null {
  if (graph.systemIds.has(id)) return null;
  if (graph.inactiveSystemIds.has(id)) return "inactive-id-space";
  // Shape fallback: the inactive collection is not always loaded, but the id
  // prefixes are unambiguous. Either way the id is reported, never rewritten.
  const looksOverlay = id.startsWith(OVERLAY_SYSTEM_PREFIX);
  if (looksOverlay !== (graph.idSpace === "business-overlay")) return "inactive-id-space";
  return "unknown-system";
}

function classifyEdgeKey(key: string, graph: TopologyGraphData): UnresolvableReason | null {
  if (graph.edgeKeys.has(key)) return null;
  if (graph.inactiveEdgeKeys.has(key)) return "inactive-id-space";
  const looksOverlay = key.startsWith(OVERLAY_EDGE_PREFIX);
  if (looksOverlay !== (graph.idSpace === "business-overlay")) return "inactive-id-space";
  return "unknown-edge";
}

function resolveIntegration(
  level: Extract<TopologyLevel, { kind: "integration" }>,
  graph: TopologyGraphData,
): UnresolvableReason | null {
  const selection = level.selection;
  switch (selection.kind) {
    case "application":
      return graph.applicationIds.has(selection.applicationId) ? null : "unknown-application";
    case "system":
      return classifySystemId(selection.systemId, graph);
    case "edge":
      return classifyEdgeKey(selection.edgeKey, graph);
    case "relationship": {
      // An aggregated system-pair edge. Both endpoints must be on the canvas,
      // and every constituent stream must resolve -- a partially resolvable
      // aggregate means our graph is not the one the answer was computed from.
      const endpoint =
        classifySystemId(selection.source, graph) ?? classifySystemId(selection.target, graph);
      if (endpoint) return endpoint === "unknown-system" ? "unknown-relationship" : endpoint;
      for (const key of selection.streamKeys) {
        const stream = classifyEdgeKey(key, graph);
        if (stream) return stream === "unknown-edge" ? "unknown-relationship" : stream;
      }
      return null;
    }
  }
}

function sourceRootMatches(
  declared: string | undefined,
  known: string | undefined,
): boolean {
  // The backend omits sourceRoot on flat (non-composite) scans. A declared
  // root that the manifest does not confirm would filter the canvas to
  // nothing, so it is a mismatch rather than something to ignore.
  if (declared === undefined) return true;
  return declared === known;
}

/** Validate one target against the loaded graph. Pure; never navigates. */
export function resolveTopologyTarget(
  target: TopologyTarget,
  graph: TopologyGraphData | null,
): ResolvedTopologyTarget {
  const base = { key: targetKey(target), label: target.label, target };
  const unresolvable = (reason: UnresolvableReason): ResolvedTopologyTarget => ({
    ...base,
    status: "unresolvable",
    reason,
  });
  const resolved = (confirmed: "graph" | "assumed" = "graph"): ResolvedTopologyTarget => ({
    ...base,
    status: "resolved",
    confirmed,
  });

  if (!graph || (!graph.hasLandscape && !graph.hasManifest)) return unresolvable("no-graph-data");

  const level = target.level;
  switch (level.kind) {
    case "project":
      // The root is always reachable when anything at all is loaded.
      return resolved();
    case "integration": {
      if (!graph.hasLandscape) return unresolvable("no-graph-data");
      const reason = resolveIntegration(level, graph);
      return reason ? unresolvable(reason) : resolved();
    }
    case "group": {
      if (!graph.hasManifest) return unresolvable("no-graph-data");
      if (!graph.groupNames.has(level.groupName)) return unresolvable("unknown-group");
      const roots = graph.sourceRootsByGroup.get(level.groupName);
      if (level.sourceRoot !== undefined && !roots?.has(level.sourceRoot)) {
        return unresolvable("source-root-mismatch");
      }
      return resolved();
    }
    case "job": {
      if (!graph.hasManifest) return unresolvable("no-graph-data");
      if (!graph.jobNames.has(level.jobName)) return unresolvable("unknown-job");
      if (!sourceRootMatches(level.sourceRoot, graph.sourceRootByJob.get(level.jobName))) {
        return unresolvable("source-root-mismatch");
      }
      return resolved();
    }
    case "component": {
      if (!graph.hasManifest) return unresolvable("no-graph-data");
      if (!graph.jobNames.has(level.jobName)) return unresolvable("unknown-job");
      if (!sourceRootMatches(level.sourceRoot, graph.sourceRootByJob.get(level.jobName))) {
        return unresolvable("source-root-mismatch");
      }
      const components = graph.componentNamesByJob.get(level.jobName);
      // Job graphs load lazily. When this job's graph is loaded we check the
      // component strictly; when it is not, the job itself resolves and the
      // click descends into a real (non-empty) canvas, so the chip stays live
      // and is marked `assumed` rather than made inert on missing data.
      if (!components) return resolved("assumed");
      return components.has(level.componentName) ? resolved() : unresolvable("unknown-component");
    }
  }
}

/**
 * Whether the loaded graph can actually place `level`.
 *
 * Used by the URL restore path, which has a level but no target: a shared link
 * may name a job from a scan this project no longer has, and dropping the user
 * on an empty canvas is worse than dropping them at the landscape. Reuses the
 * target resolver rather than duplicating its rules, so a link is held to the
 * same standard as a chip -- source-root matching included.
 */
export function isTopologyLevelResolvable(
  level: TopologyLevel,
  graph: TopologyGraphData | null,
): boolean {
  const target: TopologyTarget = {
    label: describeTopologyLevel(level),
    level,
    intent: "reference",
  };
  return resolveTopologyTarget(target, graph).status === "resolved";
}

export function resolveTopologyTargets(
  payload: unknown,
  graph: TopologyGraphData | null,
): ResolvedTopologyTarget[] {
  return parseTopologyTargets(payload).targets.map((target) =>
    resolveTopologyTarget(target, graph),
  );
}

/**
 * The single target an answer is allowed to move the canvas for, or null.
 *
 * ONE per answer, deliberately. A model that marks three places `navigate`
 * would otherwise walk the user through a slideshow they cannot follow, and
 * only the last stop would remain -- so the first is taken and the rest stay
 * as ordinary clickable chips.
 *
 * Unresolvable entries are skipped rather than consuming the one slot: they
 * are inert by contract and navigating them is not something this function
 * could do anyway. An `assumed` component target IS eligible, matching what a
 * click on the same chip already does.
 */
export function pickAutoNavigationTarget(
  resolutions: readonly ResolvedTopologyTarget[],
): ResolvedTopologyTarget | null {
  for (const resolution of resolutions) {
    if (resolution.status !== "resolved") continue;
    if (resolution.target.intent !== "navigate") continue;
    return resolution;
  }
  return null;
}

/**
 * The first exact server-authored primary target eligible for Follow mode.
 * Assumed component leaves are intentionally excluded: a human may still use
 * their chip, but the assistant never moves on an identity the loaded graph
 * has not confirmed.
 */
export function pickFollowTarget(
  resolutions: readonly ResolvedTopologyTarget[],
): ResolvedTopologyTarget | null {
  for (const resolution of resolutions) {
    if (resolution.status !== "resolved" || resolution.confirmed !== "graph") continue;
    if (resolution.target.visual_role === "primary") return resolution;
  }
  return null;
}

/**
 * Dispatch a resolved target onto the canvas's navigation object. Returns
 * false without touching navigation when the target is unresolvable.
 *
 * The navigation object is passed in rather than hooked here so this stays
 * pure and testable, and so the host keeps ownership of the stack.
 */
export function navigateToTarget(
  resolution: ResolvedTopologyTarget,
  navigation: TopologyNavigation,
): boolean {
  if (resolution.status !== "resolved") return false;

  const level = resolution.target.level;
  switch (level.kind) {
    case "project":
      navigation.reset();
      return true;
    case "integration":
      navigation.descendToIntegration(level.selection);
      return true;
    case "group":
      navigation.descendToGroup(level.groupName, level.sourceRoot);
      return true;
    case "job":
      navigation.descendToJob(level.jobName, level.sourceRoot);
      return true;
    case "component":
      // `descendToComponent` takes only the component name: it derives the job
      // from the top of the stack and is a no-op anywhere else. So the job
      // level is pushed first. Both calls are queued state updaters applied in
      // order, so the second one sees the job pushed by the first.
      navigation.descendToJob(level.jobName, level.sourceRoot);
      navigation.descendToComponent(level.componentName);
      return true;
  }
}

/** Flatten a resolution into the panel's page-agnostic chip descriptor. */
export function toCopilotTarget(resolution: ResolvedTopologyTarget): CopilotTarget {
  return {
    key: resolution.key,
    label: resolution.label,
    detail: describeTopologyLevel(resolution.target.level),
    resolvable: resolution.status === "resolved",
    unresolvableReason: resolution.reason
      ? UNRESOLVABLE_REASON_TEXT[resolution.reason]
      : undefined,
  };
}

/**
 * Pull targets out of a streamed agent event, so the canvas can highlight as
 * tool results arrive instead of waiting for the finished answer.
 *
 * The event's `public_payload` is an untyped, server-sanitised record: the
 * `targets` key is read defensively and ignored when absent, so this keeps
 * working on deployments whose event payloads do not carry targets yet.
 */
export function extractTargetsFromAgentEvent(event: PublicAgentEvent): TopologyTarget[] {
  if (!event.type.startsWith("tool.") && event.type !== "runtime.update") return [];
  const payload: unknown = event.public_payload?.targets;
  return parseTopologyTargets(payload).targets;
}
