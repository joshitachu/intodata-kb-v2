import {
  TopologyPageScopeSchema,
  type IntegrationSelection,
  type TopologyLevel,
  type TopologyPageScope,
  type TopologyVisualRole,
} from "@/types/topology";
import type {
  AssistantCanvasEmphasis,
  CopilotAnswerSource,
  CopilotScope,
} from "@/types/copilot";
import { aggregatedRelationshipId } from "@/lib/landscapeTransform";
import { architecturePairId, architectureSystemNodeId } from "@/lib/architectureViewModel";
import { tableNodeId } from "@/lib/componentGraphTransform";
import {
  describeTopologyLevel,
  topologyLevelKindLabel,
  type ResolvedTopologyTarget,
} from "@/lib/topologyTargets";
import type { MessageResponse } from "@/types";

/**
 * Host-side glue between the topology canvas and the docked copilot panel:
 * the outbound scope envelope, the per-level starter prompts, and the mapping
 * from resolved targets back onto design-canvas node ids.
 *
 * Everything here is pure so it can be unit-tested without mounting the page,
 * which is the whole reason the panel itself (`components/PageCopilot.tsx`)
 * takes flat descriptors instead of domain objects.
 */

/**
 * Strip keys whose value is `undefined` so the level round-trips byte-identical
 * through the backend and the database.
 *
 * `sourceRoot` is `.optional()` (not `.nullable()`) in both the wire contract
 * and the backend model: a serialised `null` is rejected as malformed. The
 * canvas's own union, however, carries `sourceRoot: undefined` on flat scans,
 * and `{ sourceRoot: undefined }` is not the same object as `{}` for anything
 * that compares recorded scopes. Rebuilding the level explicitly is the only
 * way to guarantee the key is absent rather than present-and-undefined.
 */
function omitUndefinedKeys(level: TopologyLevel): TopologyLevel {
  switch (level.kind) {
    case "project":
    case "integration":
      // Neither level has optional fields; `selection` is passed through
      // untouched because its ids must never be rewritten.
      return level;
    case "group":
      return level.sourceRoot === undefined
        ? { kind: "group", groupName: level.groupName }
        : { kind: "group", groupName: level.groupName, sourceRoot: level.sourceRoot };
    case "job":
      return level.sourceRoot === undefined
        ? { kind: "job", jobName: level.jobName }
        : { kind: "job", jobName: level.jobName, sourceRoot: level.sourceRoot };
    case "component":
      return level.sourceRoot === undefined
        ? {
            kind: "component",
            jobName: level.jobName,
            componentName: level.componentName,
          }
        : {
            kind: "component",
            jobName: level.jobName,
            componentName: level.componentName,
            sourceRoot: level.sourceRoot,
          };
  }
}

/**
 * The outbound `page_scope` for a message asked from the topology canvas.
 *
 * Returns null when there is nothing truthful to send -- no project selected,
 * or a level the strict wire schema rejects (e.g. a name past the 500-char
 * token bound). `page_scope` is a prompt hint, never a filter or a grant, so
 * omitting it degrades the answer's context and nothing else. Sending a
 * half-valid envelope instead would be the harmful option.
 */
export function buildTopologyPageScope(
  projectId: string,
  level: TopologyLevel,
  context?: Pick<TopologyPageScope, "selected_entity" | "active_detail_tab" | "filters">,
): TopologyPageScope | null {
  if (!projectId) return null;
  const parsed = TopologyPageScopeSchema.safeParse({
    surface: "topology",
    project_id: projectId,
    level: omitUndefinedKeys(level),
    ...(context?.selected_entity ? { selected_entity: context.selected_entity } : {}),
    ...(context?.active_detail_tab ? { active_detail_tab: context.active_detail_tab } : {}),
    ...(context?.filters && context.filters.length > 0 ? { filters: context.filters } : {}),
  });
  return parsed.success ? parsed.data : null;
}

/**
 * The identifier a topology READ can match for an integration selection, or
 * undefined when the selection is not one entity.
 *
 * Deliberately not `topologyLevelKey`. That builds a client-side render key
 * (`integration:edge:<key>`), and the prefix is exactly what made a selected
 * connection unresolvable server-side: the real key was already inside the
 * string, one prefix away from working, and the answer that followed was
 * improvised from the display label without disclosing the lookup had failed.
 *
 * A `relationship` yields nothing on purpose -- it names TWO systems, and half
 * of a pair is the wrong endpoint rather than a partial answer. The scope's
 * `level.selection` carries both, which is what the backend reads for that case.
 */
export function integrationSelectionEntityId(
  selection: IntegrationSelection,
): string | undefined {
  switch (selection.kind) {
    case "application":
      return selection.applicationId;
    case "system":
      return selection.systemId;
    case "edge":
      return selection.edgeKey;
    case "relationship":
      return undefined;
  }
}

const ROLE_PRIORITY: Record<TopologyVisualRole, number> = {
  supporting: 0,
  path: 1,
  primary: 2,
};

function setVisualRole(
  roles: Map<string, TopologyVisualRole>,
  id: string,
  role: TopologyVisualRole,
) {
  const current = roles.get(id);
  if (!current || ROLE_PRIORITY[role] > ROLE_PRIORITY[current]) roles.set(id, role);
}

function canvasEmphasis(): AssistantCanvasEmphasis {
  return { nodeRoles: new Map(), edgeRoles: new Map(), columnRoles: new Map() };
}

/** Group/job skeleton roles used by the legacy project-level design canvas. */
export function designAssistantEmphasis(
  resolutions: readonly ResolvedTopologyTarget[],
): AssistantCanvasEmphasis {
  const emphasis = canvasEmphasis();
  for (const resolution of resolutions) {
    if (resolution.status !== "resolved" || resolution.confirmed !== "graph") continue;
    const level = resolution.target.level;
    const role = resolution.target.visual_role ?? "supporting";
    if (level.kind === "group") {
      setVisualRole(emphasis.nodeRoles as Map<string, TopologyVisualRole>, `group:${level.groupName}`, role);
    } else if (level.kind === "job" || level.kind === "component") {
      setVisualRole(emphasis.nodeRoles as Map<string, TopologyVisualRole>, `job:${level.jobName}`, role);
    }
  }
  return emphasis;
}

/**
 * Every id the two project-level canvases could draw one artifact system as.
 *
 * `TechnicalLandscapeCanvas` keys nodes by the artifact id; the architecture
 * canvas keys them by `system:<id>` (or the collapsed supporting group). An
 * emphasis map is a lookup table, so emitting both is free and removes the class
 * of bug where a correct emphasis matched nothing and dimmed the whole diagram.
 * Ids are ALIASED here, never rewritten: the target itself keeps the id it came
 * with, and navigation still uses that one.
 */
function systemNodeAliases(systemId: string): string[] {
  const nodeId = architectureSystemNodeId(systemId);
  return nodeId === systemId ? [systemId] : [systemId, nodeId];
}

/**
 * Every id those canvases could draw the aggregated edge for a system pair as:
 * both directed `pair:a->b` forms plus the architecture canvas's unordered
 * `pair:a<->b`, in both id spaces.
 */
function relationshipEdgeAliases(source: string, target: string): string[] {
  const sourceNode = architectureSystemNodeId(source);
  const targetNode = architectureSystemNodeId(target);
  return [
    ...new Set([
      aggregatedRelationshipId(source, target),
      aggregatedRelationshipId(target, source),
      aggregatedRelationshipId(sourceNode, targetNode),
      aggregatedRelationshipId(targetNode, sourceNode),
      architecturePairId(source, target),
      architecturePairId(sourceNode, targetNode),
    ]),
  ];
}

/** Exact L1 entities and relationships named by resolved topology targets. */
export function landscapeAssistantEmphasis(
  resolutions: readonly ResolvedTopologyTarget[],
): AssistantCanvasEmphasis {
  const emphasis = canvasEmphasis();
  const nodeRoles = emphasis.nodeRoles as Map<string, TopologyVisualRole>;
  const edgeRoles = emphasis.edgeRoles as Map<string, TopologyVisualRole>;
  for (const resolution of resolutions) {
    if (resolution.status !== "resolved" || resolution.confirmed !== "graph") continue;
    const { level } = resolution.target;
    const role = resolution.target.visual_role ?? "supporting";
    if (level.kind !== "integration") continue;
    const selection = level.selection;
    if (selection.kind === "application") {
      setVisualRole(nodeRoles, selection.applicationId, role);
    } else if (selection.kind === "system") {
      for (const id of systemNodeAliases(selection.systemId)) setVisualRole(nodeRoles, id, role);
    } else if (selection.kind === "edge") {
      setVisualRole(edgeRoles, selection.edgeKey, role);
    } else {
      // Both endpoints AND the connecting edge: a relationship the user can see
      // is a line between two boxes, so emphasising one of the three would show
      // them something other than what the answer is about.
      for (const id of systemNodeAliases(selection.source)) setVisualRole(nodeRoles, id, role);
      for (const id of systemNodeAliases(selection.target)) setVisualRole(nodeRoles, id, role);
      for (const id of relationshipEdgeAliases(selection.source, selection.target)) {
        setVisualRole(edgeRoles, id, role);
      }
      // Narrow the highlight to the constituent streams when the answer named
      // them, so a pair with twelve flows does not light up as one thick line.
      for (const key of selection.streamKeys) setVisualRole(edgeRoles, key, role);
    }
  }
  return emphasis;
}

/** Implementing design nodes named while the L2 stream canvas is mounted. */
export function integrationAssistantEmphasis(
  resolutions: readonly ResolvedTopologyTarget[],
  artifacts: readonly { artifact_id: string; qualified_artifact_id: string; source_root: string }[],
): AssistantCanvasEmphasis {
  const emphasis = canvasEmphasis();
  for (const resolution of resolutions) {
    if (resolution.status !== "resolved" || resolution.confirmed !== "graph") continue;
    const { level } = resolution.target;
    const role = resolution.target.visual_role ?? "supporting";
    if (level.kind !== "job" && level.kind !== "component") continue;
    for (const artifact of artifacts) {
      if (artifact.artifact_id !== level.jobName) continue;
      if (level.sourceRoot !== undefined && artifact.source_root !== level.sourceRoot) continue;
      setVisualRole(
        emphasis.nodeRoles as Map<string, TopologyVisualRole>,
        `artifact:${artifact.qualified_artifact_id}`,
        role,
      );
    }
  }
  return emphasis;
}

/** Exact component nodes named while a job graph is mounted. */
export function jobAssistantEmphasis(
  resolutions: readonly ResolvedTopologyTarget[],
  jobName: string,
): AssistantCanvasEmphasis {
  const emphasis = canvasEmphasis();
  for (const resolution of resolutions) {
    if (resolution.status !== "resolved" || resolution.confirmed !== "graph") continue;
    const { level } = resolution.target;
    const role = resolution.target.visual_role ?? "supporting";
    if (level.kind === "component" && level.jobName === jobName) {
      setVisualRole(
        emphasis.nodeRoles as Map<string, TopologyVisualRole>,
        `comp:${level.componentName}`,
        role,
      );
    }
  }
  return emphasis;
}

/** Exact tables, columns and mapping edges named by bounded visual_detail. */
export function mappingAssistantEmphasis(
  resolutions: readonly ResolvedTopologyTarget[],
  jobName: string,
  componentName: string,
): AssistantCanvasEmphasis {
  const emphasis = canvasEmphasis();
  for (const resolution of resolutions) {
    if (resolution.status !== "resolved" || resolution.confirmed !== "graph") continue;
    const target = resolution.target;
    const level = target.level;
    if (
      level.kind !== "component" ||
      level.jobName !== jobName ||
      level.componentName !== componentName
    ) continue;
    const detail = target.visual_detail;
    if (!detail) continue;
    for (const table of detail.tables) {
      setVisualRole(emphasis.nodeRoles as Map<string, TopologyVisualRole>, tableNodeId(table), target.visual_role ?? "supporting");
    }
    for (const column of detail.columns) {
      setVisualRole(
        emphasis.nodeRoles as Map<string, TopologyVisualRole>,
        tableNodeId(column.table),
        target.visual_role ?? "supporting",
      );
      setVisualRole(
        emphasis.columnRoles as Map<string, TopologyVisualRole>,
        `${column.table}::${column.column}`,
        target.visual_role ?? "supporting",
      );
    }
    for (const edge of detail.edges) {
      setVisualRole(
        emphasis.nodeRoles as Map<string, TopologyVisualRole>,
        tableNodeId(edge.target_table),
        target.visual_role ?? "supporting",
      );
      setVisualRole(
        emphasis.columnRoles as Map<string, TopologyVisualRole>,
        `${edge.target_table}::${edge.target_column}`,
        target.visual_role ?? "supporting",
      );
      setVisualRole(
        emphasis.edgeRoles as Map<string, TopologyVisualRole>,
        `${edge.target_table}::${edge.target_column}`,
        target.visual_role ?? "supporting",
      );
    }
  }
  return emphasis;
}

/**
 * The scope indicator for the panel header.
 *
 * `displayName` lets the host substitute a human label for a level whose
 * identity is an opaque id -- integration levels carry hashed system/edge ids,
 * which the landscape artifact can resolve to a real name but this module
 * cannot. Without one, the level kind is shown rather than a hash.
 */
export function topologyCopilotScope(
  level: TopologyLevel,
  displayName?: string,
): CopilotScope {
  const kindLabel = topologyLevelKindLabel(level);
  if (level.kind === "project") return { kindLabel };
  if (level.kind === "integration") {
    // describeTopologyLevel yields the selection kind ("Flow", "System", ...),
    // which is the honest fallback when the host has no resolved label.
    return displayName
      ? { kindLabel: describeTopologyLevel(level), name: displayName }
      : { kindLabel: describeTopologyLevel(level) };
  }
  return { kindLabel, name: displayName ?? describeTopologyLevel(level) };
}

/**
 * Starter prompts for the level currently on screen.
 *
 * These are the discovery mechanism for the whole feature -- nobody reads a
 * placeholder, but people do click a chip -- so they are written per level and
 * name the thing in view. Generic prompts would waste the only slot the panel
 * has to explain what it can do.
 */
export function topologyCopilotSuggestions(level: TopologyLevel): readonly string[] {
  switch (level.kind) {
    case "project":
      return [
        "Which systems feed the most data into this landscape?",
        "Which external systems does this landscape depend on?",
        "Where are the busiest integration points?",
        "Which parts of the landscape have no runtime data?",
      ];
    case "integration": {
      const subject = describeTopologyLevel(level).toLowerCase();
      return [
        `What flows through this ${subject}?`,
        `Which jobs implement this ${subject}?`,
        `What breaks if this ${subject} goes down?`,
      ];
    }
    case "group":
      return [
        `What is ${level.groupName} responsible for?`,
        `Which systems does ${level.groupName} read from and write to?`,
        `Which jobs in ${level.groupName} fail most often?`,
        `What depends on ${level.groupName}?`,
      ];
    case "job":
      return [
        `What does ${level.jobName} do?`,
        `Which jobs does ${level.jobName} depend on?`,
        `What breaks if ${level.jobName} fails?`,
        `Is ${level.jobName} healthy right now?`,
      ];
    case "component":
      return [
        `What does ${level.componentName} map?`,
        `Where do ${level.componentName}'s input fields come from?`,
        `Which transformations in ${level.componentName} are non-trivial?`,
      ];
  }
}

/**
 * Design-canvas node ids to emphasise for a set of resolved targets.
 *
 * Only the level-0/1 canvas has an emphasis channel the host owns (`job:*` and
 * `group:*` node ids). Two levels therefore contribute nothing:
 *
 *  - `project`, because the whole canvas is the target -- dimming everything
 *    else would mean dimming nothing, and
 *  - `integration`, because those nodes belong to `LandscapeCanvas`, which owns
 *    its own selection state and has no highlight input.
 *
 * A `component` target maps to its JOB's node: the component itself only
 * exists on the level-2 canvas, and pointing at the job is the true statement
 * this canvas can make. Unresolvable targets are skipped -- they are inert by
 * contract and must not light anything up.
 */
export function highlightNodeIdsForTargets(
  resolutions: readonly ResolvedTopologyTarget[],
): Set<string> {
  const ids = new Set<string>();
  for (const resolution of resolutions) {
    if (resolution.status !== "resolved") continue;
    const level = resolution.target.level;
    switch (level.kind) {
      case "job":
      case "component":
        ids.add(`job:${level.jobName}`);
        break;
      case "group":
        ids.add(`group:${level.groupName}`);
        break;
      case "project":
      case "integration":
        break;
    }
  }
  return ids;
}

/**
 * Read an answer's raw `topology_targets` payload off a persisted
 * `structured_answer`, for `resolveTopologyTargets` to validate.
 *
 * This is the reload / non-streaming path; the live path is the `tool.completed`
 * public event. The field is read off the raw record rather than through
 * `MessageStructuredAnswerSchema` because both members of that union are
 * `.strict()` and neither declares `topology_targets` -- parsing through them
 * would reject the whole answer. The payload stays `unknown` on purpose: it is
 * server data whose shape is validated exactly once, by the resolver.
 */
export function readTopologyTargetsPayload(structuredAnswer: unknown): unknown {
  if (typeof structuredAnswer !== "object" || structuredAnswer === null) return undefined;
  return (structuredAnswer as Record<string, unknown>).topology_targets;
}

/**
 * The source the backend SAYS answered, when it says anything.
 *
 * Read off the raw record for the same reason as `topology_targets`: both
 * members of `MessageStructuredAnswerSchema` are `.strict()`, so parsing an
 * additive key through them would reject the whole answer. Only the four
 * values below are honoured; anything else is treated as absent, so the
 * derived fallback stays in charge rather than a typo silently claiming the
 * diagram vouched for an answer.
 */
export type DeclaredAnswerSource = "topology" | "documents" | "runtime" | "none";

const DECLARED_ANSWER_SOURCES: readonly DeclaredAnswerSource[] = [
  "topology",
  "documents",
  "runtime",
  "none",
];

export function readDeclaredAnswerSource(
  structuredAnswer: unknown,
): DeclaredAnswerSource | null {
  if (typeof structuredAnswer !== "object" || structuredAnswer === null) return null;
  const value = (structuredAnswer as Record<string, unknown>).answer_source;
  return typeof value === "string" &&
    (DECLARED_ANSWER_SOURCES as readonly string[]).includes(value)
    ? (value as DeclaredAnswerSource)
    : null;
}

const DECLARED_COPY: Record<DeclaredAnswerSource, CopilotAnswerSource> = {
  topology: { attribution: "surface", label: "Answered from the diagram" },
  documents: {
    attribution: "other",
    label: "Answered from documentation, not the diagram",
    detail: "The diagram was not the source. Check it against what is on screen.",
  },
  runtime: {
    attribution: "other",
    label: "Answered from runtime data, not the diagram",
    detail: "The diagram was not the source. Check it against what is on screen.",
  },
  none: {
    attribution: "none",
    label: "No source backed this answer",
    detail: "The topology artifact returned nothing for this question.",
  },
};

/**
 * What backed the answer now showing in the panel, or null when there is no
 * answer to attribute yet.
 *
 * Prefers the backend's own declaration and derives one otherwise, because the
 * failure this exists to prevent already happened once without it: a question
 * asked at an integration level was answered out of generated documentation,
 * the topology tools having had nothing at that depth, and the answer arrived
 * under diagram chrome and contradicted the diagram. Presenting an
 * unattributed answer as neutral is the part that made that mistake plausible.
 *
 * The derived path is deliberately conservative: `resolutions` being non-empty
 * is the only positive evidence available client-side that the topology
 * artifact was actually read, so everything else is reported as not-the-diagram
 * rather than assumed benign.
 */
export function topologyAnswerSource(
  message: Pick<MessageResponse, "structured_answer" | "sources" | "status"> | null,
  resolutions: readonly ResolvedTopologyTarget[],
): CopilotAnswerSource | null {
  if (!message) return null;
  // A turn that never produced an answer has no source to attribute; the
  // failure is already reported where it happened.
  if (message.status === "failed") return null;

  const declared = readDeclaredAnswerSource(message.structured_answer);
  if (declared) return DECLARED_COPY[declared];

  if (resolutions.length > 0) {
    // Targets at all means the topology artifact was read. Whether the CLIENT
    // can place them is a separate question -- the inert chips already say so
    // individually, but an answer whose every target is unplaceable should not
    // read as confirmed against what is on screen either.
    const placed = resolutions.filter((item) => item.status === "resolved").length;
    return {
      attribution: "surface",
      label: "Answered from the diagram",
      detail:
        placed > 0
          ? `${placed} ${placed === 1 ? "place" : "places"} on this page referenced`
          : "Nothing it named is in the topology loaded here",
    };
  }

  const documentCount = message.sources?.length ?? 0;
  if (documentCount > 0) {
    return {
      attribution: "other",
      label: "Answered from documents, not the diagram",
      detail: `${documentCount} ${documentCount === 1 ? "document" : "documents"} cited`,
    };
  }

  return {
    attribution: "none",
    label: "Not attributed to the diagram",
    detail: "Nothing on this page was cited. Verify before acting on it.",
  };
}
