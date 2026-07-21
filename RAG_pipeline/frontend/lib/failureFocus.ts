// Pure "Focus failures" set computation for the project-level topology view.
//
// Derives the set of project-level node ids that make up the active failure
// cascade so the canvas can dim everything else down to a low opacity, letting
// the down/impacted jobs and their red propagation edges dominate. No React,
// no I/O — unit-tested in isolation and driven entirely by the already-computed
// propagation result (never recomputed here).

import type { EffectiveStatus, PropagationResult } from "./healthPropagation";

/**
 * Node ids that must stay full-colour under failure-focus:
 *   - every job whose effective status is `down` or `impacted`
 *     (down = failed itself; impacted = downstream of a failed dependency)
 *   - both endpoints of every red failing edge (defensive: those jobs are
 *     already down/impacted, but a red edge must never have a dimmed endpoint)
 *   - every collapsed group whose worst member status is `down`/`impacted`,
 *     so the failure stays visible in Overview (collapsed) mode too
 *
 * Degraded (yellow) jobs are intentionally NOT included — they are warnings,
 * not part of the failure cascade, and keeping them out of the "keep" set means
 * they dim like any other healthy node when focus is on (kept simple per spec).
 *
 * Returns null when there is nothing to focus (no down/impacted jobs), so the
 * caller can treat focus mode as a no-op.
 */
export function computeFailureFocusSet(
  propagation: PropagationResult,
  groupWorst: ReadonlyMap<string, EffectiveStatus>,
): Set<string> | null {
  const ids = new Set<string>();

  for (const [job, p] of propagation.jobs) {
    if (p.effectiveStatus === "down" || p.effectiveStatus === "impacted") {
      ids.add(`job:${job}`);
    }
  }

  for (const key of propagation.failingEdges) {
    const arrow = key.indexOf("->");
    if (arrow === -1) continue;
    ids.add(`job:${key.slice(0, arrow)}`);
    ids.add(`job:${key.slice(arrow + 2)}`);
  }

  for (const [group, worst] of groupWorst) {
    if (worst === "down" || worst === "impacted") ids.add(`group:${group}`);
  }

  return ids.size > 0 ? ids : null;
}

/**
 * Which project-level nodes failure-focus is allowed to DIM.
 *
 * Only job-level nodes (`job:*`) participate. Group summary nodes (`group:*`)
 * and connection nodes (`conn:*`) are exempt and stay full-colour: at the
 * collapsed overview the down group already carries a red DOWN badge, so fading
 * the other groups to ~15% would blank out the screen and destroy the "what
 * does each group talk to" picture the overview exists to show. Focus dimming
 * is therefore only meaningful inside expanded groups (job detail). The raw
 * focus set may still contain `group:*` ids (see computeFailureFocusSet); they
 * are simply inert for dimming because those nodes are never dimmable.
 */
export function isFocusDimmableNode(nodeId: string): boolean {
  return nodeId.startsWith("job:");
}

/**
 * An edge is dimmable by failure-focus only when BOTH endpoints are dimmable
 * (i.e. job->job edges). Any edge touching a group summary or connection node —
 * including the aggregated group->system (`group-uses`) edges and inter-group
 * call edges — stays full opacity so the overview keeps its structure.
 */
export function isFocusDimmableEdge(source: string, target: string): boolean {
  return isFocusDimmableNode(source) && isFocusDimmableNode(target);
}
