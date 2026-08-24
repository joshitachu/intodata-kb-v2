import type { TopologyTarget } from "@/types/topology";
import {
  dominantRelationshipDirection,
  pickAutoNavigationTarget,
  pickFollowTarget,
  relationshipStreamKeys,
  resolveTopologyTarget,
  type ResolvedTopologyTarget,
  type TopologyGraphData,
} from "@/lib/topologyTargets";

/**
 * What Follow mode is allowed to move the canvas to, and what to call the move.
 *
 * The failure this module exists to prevent: asked "what flows from A to B",
 * the model returned A as its one `primary` target, Follow mode dutifully
 * followed A, and the banner announced a move to A while the canvas still
 * showed the landscape with the A<->B pair the user had selected. Every part of
 * that was individually defensible and the whole was wrong -- the subject of
 * the conversation was the RELATIONSHIP, which no single target named.
 *
 * So relationship-shape is decided here, from three grounded signals in
 * priority order (what the user has selected, what they typed, what the answer
 * confirmed), and a relationship target is derived from the loaded graph rather
 * than hoped for from the model. When the request is relationship-shaped and no
 * relationship can be grounded, Follow moves NOTHING: highlighting the answer
 * in place is honest, and following one of the two endpoints is not.
 */

/**
 * How the view changed, which is what the banner is allowed to claim.
 *
 * Three, not two, because "the stack changed" and "the stack changed to a
 * DETAIL of the thing we were already looking at" read differently to someone
 * watching the canvas: `opened` keeps the subject and changes the depth,
 * `navigated` changes the subject. `focused` changes neither -- it reframes the
 * level already on screen, and must never be described as movement.
 */
export type AutoMovementKind = "focused" | "opened" | "navigated";

export interface RelationshipEndpoints {
  source: string;
  target: string;
}

/** The `selected_entity` half of a page scope, in either of its two shapes. */
export interface ScopedEntity {
  kind: string;
  label: string;
  entity_id?: string;
}

export interface RelationshipShape {
  endpoints: RelationshipEndpoints;
  /** Which signal produced it. `scope` is the strongest: the user selected it. */
  source: "scope" | "query" | "answer";
}

/**
 * Every prefix a relationship entity id is minted with today: the aggregated
 * edge id (`aggregatedRelationshipId`), the architecture canvas's unordered
 * pair id, and the level key the page itself records once the relationship
 * level is open (`topologyLevelKey`).
 */
const PAIR_PREFIXES = ["integration:relationship:", "relationship:", "pair:"];
const UNDIRECTED_SEPARATOR = "<->";
const DIRECTED_SEPARATOR = "->";
/**
 * Canvas node-id prefix for a system. The architecture canvas renders systems
 * as `system:<systemId>` nodes, so its pair ids carry the prefix while the
 * artifact's own ids do not -- and the scanner id space uses ids that THEMSELVES
 * start with `system:`. The prefix is therefore only ever stripped as a
 * fallback, after the raw id has failed to match (see `resolveSystemId`).
 */
const SYSTEM_NODE_PREFIX = "system:";

/**
 * An entity id from a canvas, mapped back to an id the loaded graph knows.
 *
 * Raw first, de-prefixed second, never both: `system:connection:0:x` is a real
 * scanner system id AND looks prefixed, so trying the prefix first would
 * silently resolve it to `connection:0:x` -- a different (or absent) system.
 */
function resolveSystemId(candidate: string, graph: TopologyGraphData): string | null {
  const trimmed = candidate.trim();
  if (!trimmed) return null;
  if (graph.systemIds.has(trimmed)) return trimmed;
  if (trimmed.startsWith(SYSTEM_NODE_PREFIX)) {
    const bare = trimmed.slice(SYSTEM_NODE_PREFIX.length);
    if (graph.systemIds.has(bare)) return bare;
  }
  return null;
}

function splitPairId(entityId: string): [string, string] | null {
  const prefix = PAIR_PREFIXES.find((candidate) => entityId.startsWith(candidate));
  const body = prefix ? entityId.slice(prefix.length) : entityId;
  // `<->` first: it contains `->`, so the directed split would mangle it.
  for (const separator of [UNDIRECTED_SEPARATOR, DIRECTED_SEPARATOR]) {
    const index = body.indexOf(separator);
    if (index <= 0) continue;
    const left = body.slice(0, index);
    const right = body.slice(index + separator.length);
    if (left && right) return [left, right];
  }
  return null;
}

/**
 * The pair a relationship-shaped selection names, in graph id space.
 *
 * Returns null for any selection that is not a relationship, whose ids the
 * loaded graph cannot place, or whose pair has no flow here -- an ungrounded
 * pair must degrade to "no relationship in scope" rather than to a guess.
 */
export function relationshipScopeEndpoints(
  entity: ScopedEntity | null | undefined,
  graph: TopologyGraphData | null,
): RelationshipEndpoints | null {
  if (!graph || entity?.kind !== "relationship" || !entity.entity_id) return null;
  const split = splitPairId(entity.entity_id);
  if (!split) return null;
  const source = resolveSystemId(split[0], graph);
  const target = resolveSystemId(split[1], graph);
  if (!source || !target || source === target) return null;
  return dominantRelationshipDirection(graph, source, target);
}

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/**
 * The systems the user's own words named, in the order they named them.
 *
 * Matched against the names the loaded graph actually carries, with word
 * boundaries, so this reports what is on the canvas rather than parsing intent.
 */
export function namedSystemIdsInQuery(
  query: string | null | undefined,
  graph: TopologyGraphData | null,
): string[] {
  if (!graph || !query) return [];
  const haystack = query.toLocaleLowerCase();
  const hits: Array<{ index: number; id: string }> = [];
  for (const [name, id] of graph.systemIdByName) {
    const match = new RegExp(`(?:^|[^a-z0-9])${escapeRegExp(name)}(?:[^a-z0-9]|$)`).exec(haystack);
    if (!match) continue;
    if (hits.some((hit) => hit.id === id)) continue;
    hits.push({ index: match.index, id });
  }
  return hits.sort((a, b) => a.index - b.index).map((hit) => hit.id);
}

/**
 * The pair the user named, when they named exactly two systems the graph
 * connects.
 *
 * Three or more names is not a relationship question and yields null: it is
 * better to fall back to the ordinary primary-target rule than to pick two of
 * three systems by position.
 */
export function relationshipEndpointsFromQuery(
  query: string | null | undefined,
  graph: TopologyGraphData | null,
): RelationshipEndpoints | null {
  const named = namedSystemIdsInQuery(query, graph);
  if (named.length !== 2) return null;
  return dominantRelationshipDirection(graph, named[0], named[1]);
}

function targetSystemIds(resolution: ResolvedTopologyTarget): string[] {
  const level = resolution.target.level;
  if (level.kind !== "integration") return [];
  const selection = level.selection;
  if (selection.kind === "system") return [selection.systemId];
  if (selection.kind === "relationship") return [selection.source, selection.target];
  return [];
}

function confirmed(resolution: ResolvedTopologyTarget): boolean {
  return resolution.status === "resolved" && resolution.confirmed === "graph";
}

/**
 * The pair the ANSWER named, when its confirmed targets name exactly two
 * related systems.
 *
 * A relationship target in the answer wins outright -- the model already said
 * the subject is a pair. Otherwise two system targets the graph connects are
 * the same statement made in two pieces, but ONLY when both were marked
 * `primary` or `path`: "which systems have no runtime data" legitimately returns
 * a list of unrelated systems as `supporting`, and inferring a data flow between
 * two of them would invent a fact. This is the weakest of the three signals and
 * is gated accordingly.
 */
export function relationshipEndpointsFromTargets(
  resolutions: readonly ResolvedTopologyTarget[],
  graph: TopologyGraphData | null,
): RelationshipEndpoints | null {
  if (!graph) return null;
  const systems: string[] = [];
  for (const resolution of resolutions) {
    if (!confirmed(resolution)) continue;
    const level = resolution.target.level;
    if (level.kind === "integration" && level.selection.kind === "relationship") {
      return dominantRelationshipDirection(
        graph,
        level.selection.source,
        level.selection.target,
      );
    }
    const role = resolution.target.visual_role ?? "supporting";
    if (role !== "primary" && role !== "path") continue;
    for (const id of targetSystemIds(resolution)) {
      if (!systems.includes(id)) systems.push(id);
    }
  }
  if (systems.length !== 2) return null;
  return dominantRelationshipDirection(graph, systems[0], systems[1]);
}

/**
 * Whether this turn is about a relationship, and which one.
 *
 * Priority is by strength of evidence, not convenience: a selection the user
 * made outranks words they typed, which outrank what the model chose to return.
 */
export function relationshipShape(input: {
  resolutions: readonly ResolvedTopologyTarget[];
  selectedEntity?: ScopedEntity | null;
  query?: string | null;
  graph: TopologyGraphData | null;
}): RelationshipShape | null {
  const fromScope = relationshipScopeEndpoints(input.selectedEntity, input.graph);
  if (fromScope) return { endpoints: fromScope, source: "scope" };
  const fromQuery = relationshipEndpointsFromQuery(input.query, input.graph);
  if (fromQuery) return { endpoints: fromQuery, source: "query" };
  const fromAnswer = relationshipEndpointsFromTargets(input.resolutions, input.graph);
  return fromAnswer ? { endpoints: fromAnswer, source: "answer" } : null;
}

/** `A -> B` in the names the loaded graph carries, falling back to raw ids. */
export function describeRelationship(
  endpoints: RelationshipEndpoints,
  graph: TopologyGraphData | null,
): string {
  const name = (id: string) => graph?.systemNameById.get(id) ?? id;
  return `${name(endpoints.source)} → ${name(endpoints.target)}`;
}

/**
 * The relationship target for a pair, built from the loaded graph.
 *
 * Deliberately synthesized rather than requested from the model: the pair, its
 * direction and its constituent stream keys are all facts the client already
 * holds, and a target derived from them cannot be wrong about the graph in the
 * way a generated one can. It is then put through the ordinary resolver, so a
 * synthesized target is held to exactly the same standard as a server-authored
 * one -- no privileged path onto the canvas.
 */
export function synthesizeRelationshipTarget(
  endpoints: RelationshipEndpoints,
  graph: TopologyGraphData | null,
  label?: string,
): ResolvedTopologyTarget | null {
  const streamKeys = relationshipStreamKeys(graph, endpoints.source, endpoints.target);
  if (streamKeys.length === 0) return null;
  const target: TopologyTarget = {
    label: label || describeRelationship(endpoints, graph),
    level: {
      kind: "integration",
      selection: {
        kind: "relationship",
        source: endpoints.source,
        target: endpoints.target,
        streamKeys,
      },
    },
    intent: "reference",
    visual_role: "primary",
  };
  const resolution = resolveTopologyTarget(target, graph);
  return confirmed(resolution) ? resolution : null;
}

/** A relationship target already in the answer for this exact pair, if any. */
export function findRelationshipTarget(
  resolutions: readonly ResolvedTopologyTarget[],
  endpoints: RelationshipEndpoints,
): ResolvedTopologyTarget | null {
  for (const resolution of resolutions) {
    if (!confirmed(resolution)) continue;
    const level = resolution.target.level;
    if (level.kind !== "integration" || level.selection.kind !== "relationship") continue;
    const { source, target } = level.selection;
    if (
      (source === endpoints.source && target === endpoints.target) ||
      (source === endpoints.target && target === endpoints.source)
    ) {
      return resolution;
    }
  }
  return null;
}

/**
 * Whether the user asked about a PAIR, whether or not the loaded graph can
 * ground one.
 *
 * Deliberately weaker than `relationshipShape`: a selected relationship whose
 * flows this scan does not carry, or two systems the graph does not connect, is
 * still a two-thing question. Following one of the two things is the exact
 * failure being fixed, so the guard has to fire even when the constructive path
 * cannot.
 */
export function isRelationshipRequest(input: {
  selectedEntity?: ScopedEntity | null;
  query?: string | null;
  graph: TopologyGraphData | null;
}): boolean {
  if (input.selectedEntity?.kind === "relationship") return true;
  return namedSystemIdsInQuery(input.query, input.graph).length === 2;
}

/** A target that is one system on its own -- never the answer to a pair question. */
function isLoneSystemTarget(resolution: ResolvedTopologyTarget): boolean {
  const level = resolution.target.level;
  return level.kind === "integration" && level.selection.kind === "system";
}

export interface MovementDecision {
  target: ResolvedTopologyTarget;
  reason: "explicit" | "follow";
  /** The relationship this move is about, when it is about one. */
  relationship?: RelationshipShape;
  /** True when the target was derived here rather than returned by the answer. */
  synthesized: boolean;
}

/**
 * The one target this answer may move the canvas for, or null.
 *
 * Order of authority:
 *
 *  1. An explicit `navigate` target. The user asked to be taken somewhere; that
 *     outranks Follow, Stay Put and everything below.
 *  2. Nothing at all, when Follow is off.
 *  3. The relationship, when the turn is relationship-shaped -- adopted from the
 *     answer if it named one, synthesized from the graph otherwise.
 *  4. The ordinary first graph-confirmed primary target -- EXCEPT that a pair
 *     question excludes lone systems from that fallback. Following one endpoint
 *     of a pair the user is looking at is the specific bug this prevents; a job
 *     or group that implements the flow is still a defensible place to go.
 */
export function pickMovementTarget(input: {
  resolutions: readonly ResolvedTopologyTarget[];
  graph: TopologyGraphData | null;
  followConversation: boolean;
  selectedEntity?: ScopedEntity | null;
  query?: string | null;
}): MovementDecision | null {
  const explicit = pickAutoNavigationTarget(input.resolutions);
  if (explicit) return { target: explicit, reason: "explicit", synthesized: false };
  if (!input.followConversation) return null;

  const shape = relationshipShape(input);
  if (shape) {
    const adopted = findRelationshipTarget(input.resolutions, shape.endpoints);
    if (adopted) {
      return { target: adopted, reason: "follow", relationship: shape, synthesized: false };
    }
    const synthesized = synthesizeRelationshipTarget(shape.endpoints, input.graph);
    if (synthesized) {
      return { target: synthesized, reason: "follow", relationship: shape, synthesized: true };
    }
  }

  // The pair could not be grounded. Highlights stay either way; what is refused
  // here is spending the one move on half of what was asked about.
  const candidates = isRelationshipRequest(input)
    ? input.resolutions.filter((resolution) => !isLoneSystemTarget(resolution))
    : input.resolutions;
  const follow = pickFollowTarget(candidates);
  return follow ? { target: follow, reason: "follow", synthesized: false } : null;
}

/** Total chips the panel shows for one answer. */
export const PANEL_TARGET_LIMIT = 8;
/** Of those, how many may be incidental `supporting` entries. */
export const PANEL_SUPPORTING_LIMIT = 4;

function isRelationshipLike(resolution: ResolvedTopologyTarget): boolean {
  const level = resolution.target.level;
  return (
    level.kind === "integration" &&
    (level.selection.kind === "relationship" || level.selection.kind === "edge")
  );
}

/**
 * Rank for panel display. Lower is more relevant.
 *
 * The subject first (the relationship or flow the question was about), then the
 * answer's own primary and path entities, then whatever else it touched.
 * Unresolvable entries are pushed behind their group rather than dropped: "this
 * is not in the diagram you have loaded" is information, but it is not what the
 * one visible slot should be spent on.
 */
function panelRank(resolution: ResolvedTopologyTarget): number {
  const role = resolution.target.visual_role ?? "supporting";
  const base = isRelationshipLike(resolution)
    ? 0
    : role === "primary"
      ? 1
      : role === "path"
        ? 2
        : 3;
  return resolution.status === "resolved" ? base : base + 4;
}

/**
 * The targets worth showing for one answer.
 *
 * A retrieval step that resolved thirty jobs produces thirty chips, and a panel
 * of thirty chips says nothing -- the two the answer was actually about are
 * indistinguishable from the twenty-eight that merely appeared in the evidence.
 * So supporting entries are capped separately from the total: the cap that
 * matters is the one on noise, not the one on length.
 *
 * Order is by relevance, not by the order the model emitted, because the
 * subject of the question belongs first.
 */
export function selectRelevantTargets(
  resolutions: readonly ResolvedTopologyTarget[],
  options?: { limit?: number; supportingLimit?: number },
): ResolvedTopologyTarget[] {
  const limit = options?.limit ?? PANEL_TARGET_LIMIT;
  const supportingLimit = options?.supportingLimit ?? PANEL_SUPPORTING_LIMIT;

  const seen = new Set<string>();
  const unique: ResolvedTopologyTarget[] = [];
  for (const resolution of resolutions) {
    if (seen.has(resolution.key)) continue;
    seen.add(resolution.key);
    unique.push(resolution);
  }

  const ranked = unique
    .map((resolution, index) => ({ resolution, index, rank: panelRank(resolution) }))
    .sort((a, b) => a.rank - b.rank || a.index - b.index);

  const kept: ResolvedTopologyTarget[] = [];
  let supporting = 0;
  for (const entry of ranked) {
    if (kept.length >= limit) break;
    const incidental =
      !isRelationshipLike(entry.resolution) &&
      (entry.resolution.target.visual_role ?? "supporting") === "supporting";
    if (incidental) {
      if (supporting >= supportingLimit) continue;
      supporting += 1;
    }
    kept.push(entry.resolution);
  }
  return kept;
}
