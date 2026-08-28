import type { Persona } from "@/types/onboarding";

/**
 * What a step needs before it is worth showing.
 *
 * The old tour walked every user through /documents, /docs and /topology
 * unconditionally, so a fresh deployment spotlit three empty pages and a
 * developer without project access was shown controls they could not use. A
 * step now declares its own preconditions and is dropped when they fail.
 */
export interface StepGuard {
  /** Permission codenames the user must hold (all of them). */
  permissions?: string[];
  /**
   * Skip the step when its target element never appears. Default true — a step
   * whose anchor is missing teaches nothing, and spotlighting nothing is worse
   * than one fewer step.
   */
  skipIfMissing?: boolean;
}

export type SpotlightSide = "top" | "bottom" | "left" | "right" | "center";

export interface TourStep {
  /**
   * Stable id, persisted as tour progress. Never reuse or renumber these: they
   * are how a partially-finished tour resumes on the right step after the step
   * list changes shape for a different persona.
   */
  id: string;
  /** CSS selector for the element to spotlight. Omit for a centered card. */
  target?: string;
  /** Route to visit before this step. The engine waits for the target after. */
  route?: string;
  /** Short chapter label shown above the title, e.g. "Asking questions". */
  chapter: string;
  title: string;
  /** One or two sentences. Longer than that and nobody reads it. */
  body: string;
  /**
   * Optional concrete thing the user can do right now. Rendered as a secondary
   * action, never required to advance — a tour that blocks on interaction traps
   * anyone whose data does not match the script.
   */
  tryIt?: { label: string; hint: string };
  side?: SpotlightSide;
  guard?: StepGuard;
  /** Extra padding around the cutout, for targets that sit tight to content. */
  padding?: number;
}

export interface TourDefinition {
  /** Persisted as `tour_variant`, so we know which walkthrough was completed. */
  variant: Persona;
  label: string;
  /** One line shown on the launcher card before the tour starts. */
  pitch: string;
  steps: TourStep[];
}

/** Signals the engine needs to decide whether a guarded step applies. */
export interface TourCapabilities {
  hasPermission: (codename: string) => boolean;
}

/**
 * Drop steps whose preconditions cannot be met.
 *
 * Element presence is not checked here — it is only knowable once the step's
 * route has been visited, so the engine handles that at runtime.
 */
export function applicableSteps(
  steps: TourStep[],
  capabilities: TourCapabilities,
): TourStep[] {
  return steps.filter((step) => {
    const required = step.guard?.permissions;
    if (!required?.length) return true;
    return required.every((codename) => capabilities.hasPermission(codename));
  });
}
