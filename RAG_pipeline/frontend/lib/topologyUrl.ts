import { TopologyLevelSchema, type TopologyLevel } from "@/types/topology";

/**
 * The shareable URL form of a topology drill-down level.
 *
 * Why readable params rather than one encoded blob. These links are pasted
 * into tickets and chat messages by people, and read by people before they are
 * clicked -- `?level=job&job=tJob_OrderSync` says where it goes, a base64 blob
 * does not. The cost is one param per field, which is bounded: the widest
 * variant (an aggregated relationship) is source + target + N stream keys.
 *
 * The contract:
 *
 *   ?level=project
 *   ?level=group&group=<name>[&root=<sourceRoot>]
 *   ?level=job&job=<name>[&root=<sourceRoot>]
 *   ?level=component&job=<name>&component=<name>[&root=<sourceRoot>]
 *   ?level=integration&sel=application&app=<applicationId>
 *   ?level=integration&sel=system&system=<systemId>
 *   ?level=integration&sel=edge&edge=<edgeKey>
 *   ?level=integration&sel=relationship&source=<id>&target=<id>[&stream=<key>...]
 *
 * plus two page-level params that are not part of the level itself:
 *
 *   &project=<projectId>   which project's topology to open
 *   &c=<conversationId>    the thread to restore (same param the chat page uses)
 *
 * Every value is written verbatim -- ids are never re-prefixed or normalised
 * (see the two-id-space note in `lib/topologyTargets.ts`) -- and every parse
 * goes through the strict `TopologyLevelSchema`, so a hand-edited or truncated
 * link fails as a whole and the caller falls back to the project level rather
 * than opening a plausible-looking wrong view.
 */

export const TOPOLOGY_PROJECT_PARAM = "project";
export const TOPOLOGY_CONVERSATION_PARAM = "c";

/** Every param this module owns, for callers that need to clear them. */
export const TOPOLOGY_LEVEL_PARAMS = [
  "level",
  "group",
  "job",
  "component",
  "root",
  "sel",
  "app",
  "system",
  "edge",
  "source",
  "target",
  "stream",
] as const;

function put(params: URLSearchParams, key: string, value: string | undefined): void {
  // An absent optional field must stay absent: `root=` (empty) would parse as
  // a declared-but-blank source root, which is a different claim.
  if (value === undefined) return;
  params.set(key, value);
}

/** The level as URL params. Page-level params are added by `topologyHref`. */
export function topologyLevelToParams(level: TopologyLevel): URLSearchParams {
  const params = new URLSearchParams();
  params.set("level", level.kind);
  switch (level.kind) {
    case "project":
      break;
    case "group":
      params.set("group", level.groupName);
      put(params, "root", level.sourceRoot);
      break;
    case "job":
      params.set("job", level.jobName);
      put(params, "root", level.sourceRoot);
      break;
    case "component":
      params.set("job", level.jobName);
      params.set("component", level.componentName);
      put(params, "root", level.sourceRoot);
      break;
    case "integration": {
      const selection = level.selection;
      params.set("sel", selection.kind);
      if (selection.kind === "application") params.set("app", selection.applicationId);
      else if (selection.kind === "system") params.set("system", selection.systemId);
      else if (selection.kind === "edge") params.set("edge", selection.edgeKey);
      else {
        params.set("source", selection.source);
        params.set("target", selection.target);
        // Repeated rather than joined: a stream key contains unit separators
        // of its own, so any joining character would be ambiguous.
        for (const key of selection.streamKeys) params.append("stream", key);
      }
      break;
    }
  }
  return params;
}

/** Build the candidate object for `TopologyLevelSchema`, or null if unreadable. */
function readLevelShape(params: URLSearchParams): unknown {
  const kind = params.get("level");
  const root = params.get("root");
  // `sourceRoot` is `.optional()`, never `.nullable()`: omit the key entirely.
  const withRoot = <T extends object>(base: T): T => (root === null ? base : { ...base, sourceRoot: root });

  switch (kind) {
    case "project":
      return { kind: "project" };
    case "group":
      return withRoot({ kind: "group", groupName: params.get("group") });
    case "job":
      return withRoot({ kind: "job", jobName: params.get("job") });
    case "component":
      return withRoot({
        kind: "component",
        jobName: params.get("job"),
        componentName: params.get("component"),
      });
    case "integration": {
      const sel = params.get("sel");
      if (sel === "application") {
        return { kind: "integration", selection: { kind: "application", applicationId: params.get("app") } };
      }
      if (sel === "system") {
        return { kind: "integration", selection: { kind: "system", systemId: params.get("system") } };
      }
      if (sel === "edge") {
        return { kind: "integration", selection: { kind: "edge", edgeKey: params.get("edge") } };
      }
      if (sel === "relationship") {
        return {
          kind: "integration",
          selection: {
            kind: "relationship",
            source: params.get("source"),
            target: params.get("target"),
            streamKeys: params.getAll("stream"),
          },
        };
      }
      return null;
    }
    default:
      return null;
  }
}

/**
 * The level a URL asks for, or null when there is none or it is malformed.
 *
 * Null is not an error state for the caller to report -- it is the signal to
 * stay at the project level. A missing `level` param and a corrupt one are
 * deliberately indistinguishable here: both mean "this URL cannot be trusted
 * to place the user", and both fail the same safe way.
 */
export function parseTopologyLevelFromParams(
  params: URLSearchParams,
): TopologyLevel | null {
  const shape = readLevelShape(params);
  if (shape === null) return null;
  const parsed = TopologyLevelSchema.safeParse(shape);
  return parsed.success ? parsed.data : null;
}

export interface TopologyHrefOptions {
  level: TopologyLevel;
  /** Which project's topology to open. Omitted leaves the page's own choice. */
  projectId?: string | null;
  /** Thread to restore alongside the level, so a shared link keeps its context. */
  conversationId?: number | null;
}

/** A `/topology` link that opens at `level`. */
export function topologyHref({
  level,
  projectId,
  conversationId,
}: TopologyHrefOptions): string {
  const params = topologyLevelToParams(level);
  if (projectId) params.set(TOPOLOGY_PROJECT_PARAM, projectId);
  if (conversationId !== null && conversationId !== undefined && conversationId > 0) {
    params.set(TOPOLOGY_CONVERSATION_PARAM, String(conversationId));
  }
  return `/topology?${params.toString()}`;
}

/** The project a topology URL asks for, or null. */
export function parseTopologyProjectFromParams(params: URLSearchParams): string | null {
  const value = params.get(TOPOLOGY_PROJECT_PARAM);
  return value && value.length > 0 && value.length <= 255 ? value : null;
}

/** The thread a topology URL asks for, or null. Mirrors `useUrlConversationId`. */
export function parseTopologyConversationFromParams(params: URLSearchParams): number | null {
  const raw = params.get(TOPOLOGY_CONVERSATION_PARAM);
  if (raw === null) return null;
  const parsed = Number.parseInt(raw, 10);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : null;
}
