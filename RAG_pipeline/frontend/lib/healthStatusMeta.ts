// Presentation metadata for runtime-health statuses on the topology overlay.
// Kept separate from the pure logic in jobHealth.ts / healthPropagation.ts so
// those stay free of styling. Colours pair with a visible text label and dot
// so the overlay is never colour-only (accessibility).

import type { EffectiveStatus } from "./healthPropagation";

export interface StatusMeta {
  label: string;
  /** Short code shown inside the small node badge. */
  short: string;
  /** Primary colour (dot, ring, badge text). */
  color: string;
  description: string;
}

export const STATUS_META: Record<EffectiveStatus, StatusMeta> = {
  down: {
    label: "Down",
    short: "DOWN",
    color: "#dc2626",
    description: "Latest run failed",
  },
  impacted: {
    label: "Impacted",
    short: "IMPACT",
    color: "#f59e0b",
    description: "Downstream of a failed dependency",
  },
  degraded: {
    label: "Degraded",
    short: "DEGR",
    color: "#eab308",
    description: "Elevated failure rate",
  },
  running: {
    label: "Running",
    short: "RUN",
    color: "#3b82f6",
    description: "Currently executing",
  },
  healthy: {
    label: "Healthy",
    short: "OK",
    color: "#16a34a",
    description: "Latest run succeeded",
  },
  unknown: {
    label: "Unknown",
    short: "?",
    color: "#94a3b8",
    description: "No runtime match for this job",
  },
};

// Higher = more alarming. Used to roll a group up to its worst member.
const EFFECTIVE_SEVERITY: Record<EffectiveStatus, number> = {
  down: 6,
  impacted: 5,
  degraded: 4,
  running: 3,
  healthy: 2,
  unknown: 1,
};

export function worstEffectiveStatus(
  statuses: readonly EffectiveStatus[],
): EffectiveStatus {
  let worst: EffectiveStatus = "unknown";
  for (const s of statuses) {
    if (EFFECTIVE_SEVERITY[s] > EFFECTIVE_SEVERITY[worst]) worst = s;
  }
  return worst;
}

/** Statuses that warrant a visible node ring/badge (everything but unknown). */
export function isOverlayStatus(status: EffectiveStatus | undefined): boolean {
  return status !== undefined && status !== "unknown";
}

export const FAILING_EDGE_COLOR = "#dc2626";
