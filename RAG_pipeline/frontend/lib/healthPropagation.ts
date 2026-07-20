// Pure dependency-failure propagation for the topology overlay (S3).
//
// Structural job->job dependencies come from the scan manifest's `job_calls`:
// `job_calls[X] = [Y, ...]` means X CALLS Y, so X depends on Y. A down Y
// therefore impacts X and every transitive ancestor (caller-of-caller...).
//
// The algorithm is a cycle-safe, fully-transitive reverse BFS from the set of
// `down` jobs, walking child->parent (callee->caller) edges. A non-down job
// reached this way is flagged `impacted` (its own status is preserved
// separately). Every edge on a down->impacted path is a "failing" edge.
//
// No React, no I/O - unit-tested in isolation and shared verbatim by a future
// server-side implementation (plan D1/D3).

import type { JobStatus } from "./jobHealth";

export type EffectiveStatus = JobStatus | "impacted";

export interface PropagatedJob {
  /** The job's own derived status (never overwritten by propagation). */
  ownStatus: JobStatus;
  /** Own status, except a non-down job reached from a down seed becomes `impacted`. */
  effectiveStatus: EffectiveStatus;
  /** Min hops from the nearest down seed; 0 for a down seed, null if unaffected. */
  hopDistance: number | null;
  /**
   * Chain of job names from this job's downstream neighbour to the down seed,
   * for a tooltip like "impacted via Y -> Z". Empty for seeds and unaffected jobs.
   */
  impactedVia: string[];
}

export interface PropagationResult {
  jobs: Map<string, PropagatedJob>;
  /** Edge keys `${caller}->${callee}` lying on a down->impacted path. */
  failingEdges: Set<string>;
}

/** Canonical key for a caller->callee dependency edge. */
export function callEdgeKey(caller: string, callee: string): string {
  return `${caller}->${callee}`;
}

export function propagateHealth(
  jobCalls: Record<string, readonly string[]>,
  statusByJob: ReadonlyMap<string, JobStatus>,
): PropagationResult {
  // 1. Collect the full node set and build the child -> parents adjacency.
  //    parent = caller (depends on the child), child = callee.
  const allJobs = new Set<string>(statusByJob.keys());
  const childToParents = new Map<string, Set<string>>();
  for (const [caller, callees] of Object.entries(jobCalls)) {
    allJobs.add(caller);
    for (const callee of callees) {
      allJobs.add(callee);
      let parents = childToParents.get(callee);
      if (!parents) {
        parents = new Set<string>();
        childToParents.set(callee, parents);
      }
      parents.add(caller);
    }
  }

  // 2. Seed every job with its own status.
  const jobs = new Map<string, PropagatedJob>();
  const seeds: string[] = [];
  for (const job of allJobs) {
    const ownStatus = statusByJob.get(job) ?? "unknown";
    const isDown = ownStatus === "down";
    jobs.set(job, {
      ownStatus,
      effectiveStatus: ownStatus,
      hopDistance: isDown ? 0 : null,
      impactedVia: [],
    });
    if (isDown) seeds.push(job);
  }

  // 3. Multi-source reverse BFS from down seeds. Visited-set makes it cycle
  //    safe; BFS order guarantees each impacted job's min hop distance.
  const failingEdges = new Set<string>();
  const visited = new Set<string>(seeds);
  let queue = [...seeds];

  while (queue.length > 0) {
    const next: string[] = [];
    for (const child of queue) {
      const childNode = jobs.get(child)!;
      const parents = childToParents.get(child);
      if (!parents) continue;
      for (const parent of parents) {
        // The parent depends on an affected child -> this edge is failing.
        failingEdges.add(callEdgeKey(parent, child));
        if (visited.has(parent)) continue;
        visited.add(parent);
        const parentNode = jobs.get(parent)!;
        const chain = [child, ...childNode.impactedVia];
        // A parent that is itself down stays down; otherwise it is impacted.
        if (parentNode.ownStatus !== "down") {
          parentNode.effectiveStatus = "impacted";
        }
        parentNode.hopDistance = chain.length;
        parentNode.impactedVia = chain;
        next.push(parent);
      }
    }
    queue = next;
  }

  return { jobs, failingEdges };
}
