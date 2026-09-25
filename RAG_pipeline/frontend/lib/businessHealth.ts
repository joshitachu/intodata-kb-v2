// Runtime health for the BUSINESS layer (systems + data streams), as opposed
// to lib/jobHealth.ts + lib/healthPropagation.ts which serve the L3 technical
// job graph. Pure, deterministic, no React, no I/O.
//
// The join chain, verified end to end on a published topology:
//
//   BusinessDataStream.artifact_ids   ->  "artifact:<20-hex>", opaque digests
//     -> EITHER topology.artifact_annotations[] (analyst-authored topologies)
//        OR topology_evidence.artifacts[]      (generated topologies)
//       -> the plain DESIGN name
//         -> an exact runtime health row (lib/jobHealth.ts joinHealthToJobs)
//
// BOTH sources are required, and using either alone renders the whole overlay
// grey on real data: the analyst-authored Pluripharm topology resolves 445/445
// through annotations and carries no evidence catalogue, while every GENERATED
// topology currently served has ZERO annotations and resolves only through the
// catalogue (werkorder 193 entries / 212 citations, schepenhuizen-talend 85/63).
//
// The interesting failure is therefore the LAST hop: a design that exists in the
// scan but is not deployed under that name in the runtime mirror. That case is
// reported as UNMEASURED WITH A REASON, never as health.

import type { BusinessDataStream, BusinessTopology, TopologyEvidence } from "@/types/landscape";
import type { RuntimeJobHealth } from "@/types/runtime";
import { deriveStatusFromHealth, joinHealthToJobs, worstStatus, type JobStatus } from "./jobHealth";
import type { EffectiveStatus } from "./healthPropagation";
import { worstEffectiveStatus } from "./healthStatusMeta";

/* ------------------------------------------------------------------ *
 * Why something is not measured
 * ------------------------------------------------------------------ */

/**
 * Absence of data is not health. Every unmeasured thing carries the reason it
 * is unmeasured, so the UI can render grey WITH a sentence instead of green.
 */
export type UnmeasuredReason =
  | "no_runtime_mirror"
  | "no_artifact_citations"
  | "unresolved_artifact_citations"
  | "designs_not_in_runtime"
  | "no_streams";

export const UNMEASURED_REASON_TEXT: Record<UnmeasuredReason, string> = {
  no_runtime_mirror:
    "The runtime mirror returned no rows for this project, so nothing on this canvas is measured. Check that a runtime source is connected and enabled.",
  no_artifact_citations:
    "This flow cites no scanner artifacts, so there is no design to look up in the runtime mirror.",
  unresolved_artifact_citations:
    "This flow's artifact citations do not resolve to a design in the published topology.",
  designs_not_in_runtime:
    "This flow's designs are not deployed under a name the runtime mirror reports.",
  no_streams:
    "No data streams are published for this system, so there is nothing to measure.",
};

/* ------------------------------------------------------------------ *
 * Per-stream health
 * ------------------------------------------------------------------ */

/** One design a stream is implemented by, and what the mirror says about it. */
export interface StreamDesignBinding {
  /** The plain design name resolved from an `artifact:` citation. */
  designName: string;
  /** The `artifact:<hash>` citation it was resolved from. */
  artifactId: string;
  /** Runtime rows bound to that design; empty when the mirror does not know it. */
  rows: RuntimeJobHealth[];
  /** `unknown` when `rows` is empty -- never a fabricated verdict. */
  status: JobStatus;
}

export interface StreamHealth {
  streamId: string;
  sourceSystemId: string;
  destinationSystemId: string;
  /** Worst status across BOUND designs; `unknown` when nothing is bound. */
  status: JobStatus;
  /** True only when at least one design resolved to at least one runtime row. */
  measured: boolean;
  /** Populated exactly when `measured` is false. */
  unmeasuredReason: UnmeasuredReason | null;
  /** Human sentence for `unmeasuredReason`; empty when measured. */
  unmeasuredDetail: string;
  designs: StreamDesignBinding[];
  /** Designs the artifact citations resolved to. */
  resolvedDesignCount: number;
  /** Of those, how many are bound to at least one runtime row. */
  boundDesignCount: number;
  /** Resolved designs the mirror does not know (the honest coverage gap). */
  unboundDesignNames: string[];
  /** Citations that resolved to no annotation at all. */
  unresolvedArtifactIds: string[];
  latestRunAt: string | null;
}

export interface StreamHealthIndex {
  byStream: Map<string, StreamHealth>;
  measuredStreamCount: number;
  unmeasuredStreamCount: number;
  /** Rows the mirror returned. Zero means "nothing is being measured". */
  runtimeRowCount: number;
  /** Freshest latest_run_at across every bound row, or null. */
  latestRunAt: string | null;
}

/**
 * `artifact:<hash>` -> design name, from BOTH sources the payload can carry.
 *
 * Two of them, because which one is populated depends on how the topology was
 * produced, and assuming either alone renders the whole overlay grey:
 *
 * * `artifact_annotations[]` -- an annotation's `artifact_id` IS the design
 *   name, and its `evidence_ids` carry the hashes. Analyst-authored topologies
 *   are rich here (one measured file has 445 of them, resolving 445/445).
 * * `topology_evidence.artifacts[]` -- the scanner evidence catalogue the
 *   landscape endpoint projects as `{id, name, job_id}`. This is what GENERATED
 *   topologies carry, and measured on the four projects currently serving a
 *   validated topology it is the ONLY source: all four have zero annotations,
 *   while the catalogue holds 193 (werkorder) and 85 (schepenhuizen-talend)
 *   entries against 212 and 63 stream citations respectively.
 *
 * Only `artifact:`-prefixed ids are indexed. An annotation also cites `channel:`
 * and other evidence kinds, and a stream only ever looks up its own
 * `artifact_ids`, so indexing the rest can add nothing but a collision.
 */
export function buildDesignNameIndex(
  topology: BusinessTopology,
  evidence?: TopologyEvidence | null,
): Map<string, string[]> {
  const index = new Map<string, string[]>();
  const add = (artifactId: string, design: string) => {
    if (!artifactId.startsWith("artifact:") || !design) return;
    const bucket = index.get(artifactId);
    if (bucket) {
      if (!bucket.includes(design)) bucket.push(design);
    } else {
      index.set(artifactId, [design]);
    }
  };
  for (const annotation of topology.artifact_annotations) {
    const design = annotation.artifact_id.trim();
    if (!design) continue;
    for (const evidenceId of annotation.evidence_ids) add(evidenceId, design);
  }
  for (const artifact of evidence?.artifacts ?? []) {
    // `name` is the design as the scanner published it; `job_id` is the
    // fallback for a scan that assigned no separate name.
    add(artifact.id, (artifact.name || artifact.job_id || "").trim());
  }
  return index;
}

function maxIso(a: string | null, b: string | null | undefined): string | null {
  if (!b) return a;
  if (!a) return b;
  return b > a ? b : a;
}

/**
 * Per-stream runtime health: which designs implement it, how many of those the
 * mirror knows, the worst status across the known ones, and -- when nothing is
 * known -- why.
 */
export function buildStreamHealth(
  topology: BusinessTopology,
  healthRows: readonly RuntimeJobHealth[],
  evidence?: TopologyEvidence | null,
): StreamHealthIndex {
  const designIndex = buildDesignNameIndex(topology, evidence);

  // Resolve every stream's citations first so the name->row join runs once for
  // the whole topology rather than once per stream.
  const resolved = new Map<string, { designs: { designName: string; artifactId: string }[]; unresolved: string[] }>();
  const allDesignNames = new Set<string>();
  for (const stream of topology.data_streams) {
    const designs: { designName: string; artifactId: string }[] = [];
    const unresolved: string[] = [];
    const seen = new Set<string>();
    for (const artifactId of stream.artifact_ids) {
      const names = designIndex.get(artifactId);
      if (!names || names.length === 0) {
        unresolved.push(artifactId);
        continue;
      }
      for (const designName of names) {
        if (seen.has(designName)) continue;
        seen.add(designName);
        designs.push({ designName, artifactId });
        allDesignNames.add(designName);
      }
    }
    resolved.set(stream.id, { designs, unresolved });
  }

  const join = joinHealthToJobs([...allDesignNames], healthRows);

  const byStream = new Map<string, StreamHealth>();
  let measuredStreamCount = 0;
  let latestRunAt: string | null = null;

  for (const stream of topology.data_streams) {
    const { designs: resolvedDesigns, unresolved } = resolved.get(stream.id)!;
    const designs: StreamDesignBinding[] = resolvedDesigns.map(({ designName, artifactId }) => {
      const match = join.byJob.get(designName);
      const rows = match?.matched ?? [];
      return {
        designName,
        artifactId,
        rows,
        status: rows.length > 0 ? worstStatus(rows.map(deriveStatusFromHealth)) : "unknown",
      };
    });

    const bound = designs.filter((design) => design.rows.length > 0);
    const measured = bound.length > 0;
    let streamLatest: string | null = null;
    for (const design of bound) {
      for (const row of design.rows) streamLatest = maxIso(streamLatest, row.latest_run_at ?? null);
    }
    latestRunAt = maxIso(latestRunAt, streamLatest);

    const unmeasuredReason: UnmeasuredReason | null = measured
      ? null
      : healthRows.length === 0
        ? "no_runtime_mirror"
        : stream.artifact_ids.length === 0
          ? "no_artifact_citations"
          : resolvedDesigns.length === 0
            ? "unresolved_artifact_citations"
            : "designs_not_in_runtime";

    if (measured) measuredStreamCount += 1;
    byStream.set(stream.id, {
      streamId: stream.id,
      sourceSystemId: stream.source_system_id,
      destinationSystemId: stream.destination_system_id,
      status: measured ? worstStatus(bound.map((design) => design.status)) : "unknown",
      measured,
      unmeasuredReason,
      unmeasuredDetail: unmeasuredReason ? UNMEASURED_REASON_TEXT[unmeasuredReason] : "",
      designs,
      resolvedDesignCount: resolvedDesigns.length,
      boundDesignCount: bound.length,
      unboundDesignNames: designs.filter((design) => design.rows.length === 0).map((design) => design.designName),
      unresolvedArtifactIds: unresolved,
      latestRunAt: streamLatest,
    });
  }

  return {
    byStream,
    measuredStreamCount,
    unmeasuredStreamCount: byStream.size - measuredStreamCount,
    runtimeRowCount: healthRows.length,
    latestRunAt,
  };
}

/* ------------------------------------------------------------------ *
 * Downstream impact
 * ------------------------------------------------------------------ */

export interface ImpactChain {
  /** 1 = directly downstream of a failing stream. */
  hopDistance: number;
  /** System ids, failing source first, this system last. */
  systemPath: string[];
  /** Stream ids traversed; length === systemPath.length - 1. */
  streamPath: string[];
  /** The stream whose own measured status seeded this impact. */
  rootStreamId: string;
}

export interface StreamImpactOptions {
  /**
   * How far a failure is allowed to travel, counted in systems. Default 1 --
   * the direct consumers of a failing flow and nothing else. The bound is not
   * cosmetic: measured on Pluripharm, a 4-hop walk out of the `JD Edwards` hub
   * reaches 13 of 27 systems, which paints most of the canvas amber on the
   * strength of one failure.
   */
  maxHops?: number;
}

export interface StreamImpactResult {
  /** System id -> how it was reached. BFS order, so the chain is a shortest path. */
  impactedSystems: Map<string, ImpactChain>;
  /** Streams that carry the starvation onward (they leave an impacted system). */
  impactedStreams: Map<string, ImpactChain>;
  /** `down` streams that were actually allowed to propagate (see eligibility). */
  seedStreamIds: Set<string>;
}

/**
 * Whether a failure may travel along this stream.
 *
 * An `optional` flow, by its own declaration, is not something the destination
 * depends on; a stream that is not `active` is not currently delivering
 * anything to starve. An UNSET lifecycle is deliberately not treated as active:
 * under-reporting impact is recoverable, inventing it is not.
 * (Real distributions on Pluripharm: requiredness required 21 / optional 5 /
 * not_established 2; lifecycle active 24 / reference_only 4.)
 */
export function canCarryImpact(stream: BusinessDataStream): boolean {
  return stream.requiredness !== "optional" && stream.lifecycle === "active";
}

/**
 * Bounded downstream propagation over the business data streams.
 *
 * DIRECTION -- this is the opposite of lib/healthPropagation.ts, and the
 * difference is not a detail. That file walks a CALL graph: `job_calls[X] = [Y]`
 * means X calls Y, so a broken Y impacts its CALLERS and the walk runs
 * callee -> caller. A business data stream is DATA FLOW: `A -> D` means A
 * delivers to D, so a broken A->D starves D, and the walk runs
 * `source_system_id -> destination_system_id`. Reusing the call-graph
 * traversal here would blame every supplier for its customer's outage.
 */
export function propagateStreamImpact(
  topology: BusinessTopology,
  streamHealth: ReadonlyMap<string, StreamHealth>,
  options: StreamImpactOptions = {},
): StreamImpactResult {
  const maxHops = Math.max(0, options.maxHops ?? 1);
  const impactedSystems = new Map<string, ImpactChain>();
  const impactedStreams = new Map<string, ImpactChain>();
  const seedStreamIds = new Set<string>();

  const outgoing = new Map<string, BusinessDataStream[]>();
  for (const stream of topology.data_streams) {
    if (!canCarryImpact(stream)) continue;
    const bucket = outgoing.get(stream.source_system_id);
    if (bucket) bucket.push(stream);
    else outgoing.set(stream.source_system_id, [stream]);
  }

  if (maxHops === 0) return { impactedSystems, impactedStreams, seedStreamIds };

  // Hop 1: the direct consumers of a failing flow.
  let frontier: ImpactChain[] = [];
  for (const stream of topology.data_streams) {
    if (streamHealth.get(stream.id)?.status !== "down") continue;
    if (!canCarryImpact(stream)) continue;
    if (stream.source_system_id === stream.destination_system_id) continue;
    seedStreamIds.add(stream.id);
    if (impactedSystems.has(stream.destination_system_id)) continue;
    const chain: ImpactChain = {
      hopDistance: 1,
      systemPath: [stream.source_system_id, stream.destination_system_id],
      streamPath: [stream.id],
      rootStreamId: stream.id,
    };
    impactedSystems.set(stream.destination_system_id, chain);
    frontier.push(chain);
  }

  // Hop 2..maxHops. The visited sets (impactedSystems / impactedStreams) make
  // this cycle-safe; BFS order makes every recorded chain a shortest path.
  while (frontier.length > 0) {
    const next: ImpactChain[] = [];
    for (const chain of frontier) {
      if (chain.hopDistance >= maxHops) continue;
      const systemId = chain.systemPath[chain.systemPath.length - 1];
      for (const stream of outgoing.get(systemId) ?? []) {
        if (impactedStreams.has(stream.id)) continue;
        const destination = stream.destination_system_id;
        const onward: ImpactChain = {
          hopDistance: chain.hopDistance + 1,
          systemPath: [...chain.systemPath, destination],
          streamPath: [...chain.streamPath, stream.id],
          rootStreamId: chain.rootStreamId,
        };
        impactedStreams.set(stream.id, onward);
        if (impactedSystems.has(destination)) continue;
        impactedSystems.set(destination, onward);
        next.push(onward);
      }
    }
    frontier = next;
  }

  return { impactedSystems, impactedStreams, seedStreamIds };
}

/* ------------------------------------------------------------------ *
 * Rendering-ready projections
 * ------------------------------------------------------------------ */

/**
 * A stream's status as the canvas should paint it: its own measured status,
 * upgraded to `impacted` only when it is not already carrying something worse.
 */
export function streamEffectiveStatus(
  streamId: string,
  index: ReadonlyMap<string, StreamHealth>,
  impact: StreamImpactResult,
): EffectiveStatus {
  const own = index.get(streamId)?.status ?? "unknown";
  if (own === "down" || own === "degraded") return own;
  return impact.impactedStreams.has(streamId) ? "impacted" : own;
}

export interface SystemHealth {
  systemId: string;
  status: EffectiveStatus;
  measured: boolean;
  unmeasuredReason: UnmeasuredReason | null;
  unmeasuredDetail: string;
  /** How this system was reached by a failure elsewhere, when it was. */
  impact: ImpactChain | null;
  outboundStreamIds: string[];
  inboundStreamIds: string[];
  measuredStreamCount: number;
  streamCount: number;
}

/**
 * System-level rollup.
 *
 * A system is painted from the flows it takes part in, with one asymmetry that
 * matters: an OUTBOUND flow that is down contributes `down` (this system's
 * delivery is broken), while an INBOUND flow that is down contributes
 * `impacted` (this system is the victim, not the fault). Without that
 * distinction a single broken delivery paints both ends identically and the
 * canvas stops saying where to look.
 */
export function rollUpSystemHealth(
  topology: BusinessTopology,
  index: StreamHealthIndex,
  impact: StreamImpactResult,
): Map<string, SystemHealth> {
  const contributions = new Map<string, EffectiveStatus[]>();
  const outbound = new Map<string, string[]>();
  const inbound = new Map<string, string[]>();
  const measuredCount = new Map<string, number>();

  const add = <T,>(map: Map<string, T[]>, key: string, value: T): void => {
    const bucket = map.get(key);
    if (bucket) bucket.push(value);
    else map.set(key, [value]);
  };

  for (const stream of topology.data_streams) {
    const health = index.byStream.get(stream.id);
    const own = streamEffectiveStatus(stream.id, index.byStream, impact);
    add(outbound, stream.source_system_id, stream.id);
    add(inbound, stream.destination_system_id, stream.id);
    add(contributions, stream.source_system_id, own);
    add(contributions, stream.destination_system_id, own === "down" ? "impacted" : own);
    if (health?.measured) {
      for (const systemId of [stream.source_system_id, stream.destination_system_id]) {
        measuredCount.set(systemId, (measuredCount.get(systemId) ?? 0) + 1);
      }
    }
  }

  const bySystem = new Map<string, SystemHealth>();
  for (const system of topology.systems) {
    const outboundIds = outbound.get(system.id) ?? [];
    const inboundIds = inbound.get(system.id) ?? [];
    const streamIds = [...outboundIds, ...inboundIds];
    const chain = impact.impactedSystems.get(system.id) ?? null;
    const statuses = [...(contributions.get(system.id) ?? [])];
    if (chain) statuses.push("impacted");
    const measured = (measuredCount.get(system.id) ?? 0) > 0;

    const unmeasuredReason: UnmeasuredReason | null = measured
      ? null
      : index.runtimeRowCount === 0
        ? "no_runtime_mirror"
        : streamIds.length === 0
          ? "no_streams"
          : (index.byStream.get(streamIds[0])?.unmeasuredReason ?? "designs_not_in_runtime");

    bySystem.set(system.id, {
      systemId: system.id,
      // `worstEffectiveStatus` of an empty list is `unknown`, which is exactly
      // what an unmeasured system must render as: grey, with the reason below.
      status: worstEffectiveStatus(statuses),
      measured,
      unmeasuredReason,
      unmeasuredDetail: unmeasuredReason ? UNMEASURED_REASON_TEXT[unmeasuredReason] : "",
      impact: chain,
      outboundStreamIds: outboundIds,
      inboundStreamIds: inboundIds,
      measuredStreamCount: measuredCount.get(system.id) ?? 0,
      streamCount: streamIds.length,
    });
  }
  return bySystem;
}

/* ------------------------------------------------------------------ *
 * The feed the canvas is handed
 * ------------------------------------------------------------------ */

/**
 * Everything the architecture canvas needs about the runtime mirror, resolved
 * once by the page that owns the poll. Type-only: no React in this module.
 */
export interface BusinessHealthFeed {
  /** The user's overlay toggle, shared with the L3 job-graph overlay. */
  enabled: boolean;
  onToggle: () => void;
  rows: readonly RuntimeJobHealth[];
  /** react-query `dataUpdatedAt`; 0 before the first successful poll. */
  dataUpdatedAt: number;
  /** A poll completed, whatever it returned. Distinguishes "empty" from "pending". */
  loaded: boolean;
}

/** The whole overlay, derived from a feed in one place. */
export interface BusinessHealthOverlay {
  index: StreamHealthIndex;
  impact: StreamImpactResult;
  bySystem: Map<string, SystemHealth>;
  /** At least one stream is actually measured. */
  active: boolean;
}

export function buildBusinessHealthOverlay(
  topology: BusinessTopology,
  healthRows: readonly RuntimeJobHealth[],
  options: StreamImpactOptions = {},
  evidence?: TopologyEvidence | null,
): BusinessHealthOverlay {
  const index = buildStreamHealth(topology, healthRows, evidence);
  const impact = propagateStreamImpact(topology, index.byStream, options);
  return {
    index,
    impact,
    bySystem: rollUpSystemHealth(topology, index, impact),
    active: index.measuredStreamCount > 0,
  };
}
