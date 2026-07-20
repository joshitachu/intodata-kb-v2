// Pure "Focus mode" neighbourhood computation for the project-level topology.
//
// Focus mode is the click-to-centre exploration pattern used by every serious
// lineage/catalog tool (Dataiku, DataHub, Alation, Atlan): pick a focal job and
// show only its immediate dependency neighbourhood — ancestors (callers) and
// descendants (callees) up to N hops, plus the systems those jobs talk to —
// hiding everything else so the canvas is a small, readable subgraph instead of
// the whole flattened topology.
//
// This module is the pure core: no React, no I/O, cycle-safe, unit-tested in
// isolation (root CLAUDE.md rule 11: a pure-function module over threading graph
// logic through component state). It is driven entirely by the scan manifest's
// already-present dependency maps:
//   - job_calls[X]       = jobs X calls        (X's descendants, one hop down)
//   - job_called_by[X]   = jobs that call X    (X's ancestors,   one hop up)
//   - job_connections_used[X] = external systems X talks to
//
// The UI hides every node outside the returned in-focus set (see app/topology),
// so the "dimmed set" is simply the complement and is left for the caller to
// derive by filtering — there is no universe of node ids at this layer.

/** How many more immediate neighbours a boundary job has hidden beyond the limit. */
export interface FocusBoundary {
  /** Callers (upstream) of this job that fell outside the focus horizon. */
  upstream: number;
  /** Callees (downstream) of this job that fell outside the focus horizon. */
  downstream: number;
}

export interface FocusResult {
  /** The job the neighbourhood is centred on. */
  focal: string;
  /** Job names in focus: focal + ancestors(≤hops) + descendants(≤hops). */
  inFocusJobs: Set<string>;
  /**
   * Node ids in focus (`job:*` and `conn:*`) — the exact set the canvas keeps
   * visible. Connection ids are every system used by any in-focus job.
   */
  inFocus: Set<string>;
  /**
   * Boundary jobs: in-focus jobs sitting at the horizon that still have hidden
   * neighbours, keyed by job name, with per-direction counts of what is hidden.
   * Only jobs with at least one hidden neighbour appear here, so the UI can
   * render a "+N upstream" / "+N downstream" expand affordance exactly there.
   */
  boundaries: Map<string, FocusBoundary>;
}

/**
 * Cycle-safe breadth-first walk from `start` along `adjacency`, returning each
 * reachable node's minimum hop distance (start = 0), capped at `hops`. The
 * `visited`/distance map guarantees termination on cyclic graphs (e.g. tRunJob
 * cycles) and gives the caller the horizon distance needed for boundary
 * detection.
 */
function walkDistances(
  start: string,
  adjacency: Record<string, string[]>,
  hops: number,
): Map<string, number> {
  const distance = new Map<string, number>([[start, 0]]);
  let frontier = [start];
  for (let hop = 0; hop < hops; hop++) {
    const next: string[] = [];
    for (const node of frontier) {
      for (const neighbour of adjacency[node] ?? []) {
        if (distance.has(neighbour)) continue;
        distance.set(neighbour, hop + 1);
        next.push(neighbour);
      }
    }
    if (next.length === 0) break;
    frontier = next;
  }
  return distance;
}

/**
 * Compute the in-focus neighbourhood of `focalJob`.
 *
 * @param focalJob            job name to centre on
 * @param jobCalls            job -> jobs it calls (descendants)
 * @param jobCalledBy         job -> jobs that call it (ancestors)
 * @param jobConnectionsUsed  job -> external systems it uses
 * @param hops                horizon in each direction (default 2)
 */
export function computeFocus(
  focalJob: string,
  jobCalls: Record<string, string[]>,
  jobCalledBy: Record<string, string[]>,
  jobConnectionsUsed: Record<string, string[]>,
  hops = 2,
): FocusResult {
  // Descendants (things the focal job calls) and ancestors (things that call
  // it), each up to `hops` steps, keyed by their hop distance from the focal.
  const descendantDist = walkDistances(focalJob, jobCalls, hops);
  const ancestorDist = walkDistances(focalJob, jobCalledBy, hops);

  // A job that is both an ancestor and a descendant (a cycle) is included once.
  const inFocusJobs = new Set<string>([...descendantDist.keys(), ...ancestorDist.keys()]);

  // Boundary detection tied to what a hop increase would actually reveal:
  //   - an ANCESTOR sitting exactly at the upstream horizon (ancestorDist ===
  //     hops) has callers at distance hops+1 that are hidden; bumping the
  //     horizon would surface them -> "+N upstream".
  //   - a DESCENDANT exactly at the downstream horizon (descendantDist === hops)
  //     has callees at hops+1 hidden -> "+N downstream".
  // A job that is merely INTERIOR (e.g. a 1-hop parent whose OTHER children are
  // the focal's cousins) is deliberately NOT a boundary: those cousins lie off
  // any pure ancestor/descendant chain from the focal and expanding the horizon
  // never reveals them, so advertising them would be dishonest.
  const boundaries = new Map<string, FocusBoundary>();
  for (const job of inFocusJobs) {
    let upstream = 0;
    let downstream = 0;
    if (ancestorDist.get(job) === hops) {
      upstream = (jobCalledBy[job] ?? []).filter((j) => !inFocusJobs.has(j)).length;
    }
    if (descendantDist.get(job) === hops) {
      downstream = (jobCalls[job] ?? []).filter((j) => !inFocusJobs.has(j)).length;
    }
    if (upstream > 0 || downstream > 0) {
      boundaries.set(job, { upstream, downstream });
    }
  }

  // Node-id view: job nodes for every in-focus job, plus the connection nodes
  // for the systems those jobs talk to (directly-connected external systems).
  const inFocus = new Set<string>();
  for (const job of inFocusJobs) {
    inFocus.add(`job:${job}`);
    for (const conn of jobConnectionsUsed[job] ?? []) {
      inFocus.add(`conn:${conn}`);
    }
  }

  return { focal: focalJob, inFocusJobs, inFocus, boundaries };
}
