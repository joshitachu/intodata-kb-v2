"use client";

import { useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";

/**
 * The component names of every job graph this browser has actually fetched,
 * for the given project.
 *
 * Job graphs load lazily -- only the level-2 canvas fetches one, and only for
 * the job the user descended into (`hooks/useJobGraph.ts`). So this is the
 * honest answer to "which component names can be checked strictly right now",
 * and it is read from the query cache rather than re-fetched: a validator that
 * triggered network requests would turn a chip's tooltip into a fan-out of job
 * graph loads.
 *
 * Absence is meaningful. A job missing from this map has genuinely not been
 * loaded, which is what lets `resolveTopologyTarget` mark a component target
 * `assumed` instead of wrongly declaring it unknown.
 */

const EMPTY: Readonly<Record<string, readonly string[]>> = Object.freeze({});

/** Component identities out of a cached job graph, or null if it holds none. */
function componentNames(data: unknown): string[] | null {
  if (typeof data !== "object" || data === null) return null;
  const nodes = (data as { nodes?: unknown }).nodes;
  // The cache only ever holds `JobGraphResponseSchema` output (useJobGraph
  // parses before returning), but it is typed `unknown` here, and a shape
  // check is cheaper than re-running the schema on every cache event.
  if (!Array.isArray(nodes)) return null;
  const names: string[] = [];
  for (const node of nodes) {
    if (typeof node !== "object" || node === null) continue;
    // `unique_name` is the component identity the canvas navigates with
    // (`jobGraphTransform`'s `technicalName`), not the display label.
    const uniqueName = (node as { unique_name?: unknown }).unique_name;
    if (typeof uniqueName === "string" && uniqueName.length > 0) names.push(uniqueName);
  }
  return names;
}

export function useLoadedJobComponents(
  projectId: string,
): Readonly<Record<string, readonly string[]>> {
  const queryClient = useQueryClient();
  const [snapshot, setSnapshot] = useState<Readonly<Record<string, readonly string[]>>>(EMPTY);
  // Signature of the last published snapshot. Cache events fire for every
  // query in the app, so without this the object identity would change
  // constantly and re-derive every target resolution on the page.
  const signatureRef = useRef<string>("");

  useEffect(() => {
    if (!projectId) {
      signatureRef.current = "";
      setSnapshot(EMPTY);
      return;
    }
    // A project switch invalidates the signature: the same job name and count
    // can occur in two projects, and matching signatures would keep the
    // previous project's components in play.
    signatureRef.current = "";

    const cache = queryClient.getQueryCache();
    const read = () => {
      const next: Record<string, readonly string[]> = {};
      for (const query of cache.findAll({ queryKey: ["job-graph", projectId] })) {
        const jobName = query.queryKey[2];
        if (typeof jobName !== "string") continue;
        const names = componentNames(query.state.data);
        if (names !== null) next[jobName] = names;
      }
      const signature = Object.keys(next)
        .sort()
        .map((job) => `${job}=${next[job].length}`)
        .join("|");
      if (signature === signatureRef.current) return;
      signatureRef.current = signature;
      setSnapshot(Object.keys(next).length > 0 ? next : EMPTY);
    };

    read();
    return cache.subscribe(read);
  }, [queryClient, projectId]);

  return snapshot;
}
