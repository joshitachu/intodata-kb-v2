"use client";

import { useEffect, useMemo, useRef } from "react";
import {
  extractTargetsFromAgentEvent,
  resolveTopologyTarget,
  type ResolvedTopologyTarget,
  type TopologyGraphData,
} from "@/lib/topologyTargets";
import type { PublicAgentEvent } from "@/types";

export interface UseProgressiveTargetsOptions {
  /**
   * Public agent events for the turn in progress, oldest first. The host owns
   * the buffer and clears it per turn; this hook derives from it and keeps no
   * state of its own, so a replayed or reordered buffer cannot desynchronise.
   */
  events: readonly PublicAgentEvent[];
  graph: TopologyGraphData | null;
  /**
   * Called with the cumulative RESOLVED targets each time the set grows.
   *
   * The host must highlight only -- emphasis, no zoom, no pan, no fitView.
   * Moving the viewport while prose is still streaming is disorienting, and
   * this fires mid-stream by design.
   */
  onHighlight?: (targets: ResolvedTopologyTarget[]) => void;
}

/**
 * Surfaces navigable targets from `tool.*` / `runtime.update` events as they
 * arrive, so the canvas reacts within about a second instead of waiting for
 * the finished answer. The answer's own `targets` remain the authoritative
 * clickable chips; this path is highlight-only.
 */
export function useProgressiveTargets({
  events,
  graph,
  onHighlight,
}: UseProgressiveTargetsOptions): ResolvedTopologyTarget[] {
  const resolutions = useMemo(() => {
    const byKey = new Map<string, ResolvedTopologyTarget>();
    for (const event of events) {
      for (const target of extractTargetsFromAgentEvent(event)) {
        const resolution = resolveTopologyTarget(target, graph);
        // First mention wins: a later event repeating the same node must not
        // make an already-highlighted node flicker.
        if (!byKey.has(resolution.key)) byKey.set(resolution.key, resolution);
      }
    }
    return [...byKey.values()];
  }, [events, graph]);

  const lastSignatureRef = useRef<string | null>(null);
  useEffect(() => {
    const highlightable = resolutions.filter((item) => item.status === "resolved");
    const signature = highlightable.map((item) => item.key).join("|");
    if (signature === lastSignatureRef.current) return;
    lastSignatureRef.current = signature;
    if (highlightable.length > 0) onHighlight?.(highlightable);
  }, [resolutions, onHighlight]);

  return resolutions;
}
