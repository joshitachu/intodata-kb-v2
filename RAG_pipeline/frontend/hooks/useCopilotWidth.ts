"use client";

import { useCallback, useState } from "react";

/** Narrow enough to still read an answer; below this the composer wraps badly. */
export const MIN_COPILOT_WIDTH = 300;

/** The panel's width when nobody has dragged it. */
export const DEFAULT_COPILOT_WIDTH = 360;

/**
 * What the page beside the panel must keep. The panel is docked to a diagram it
 * describes, so a width that hides the diagram defeats the panel -- this is the
 * guard that makes the drag handle safe to pull all the way across.
 */
export const MIN_HOST_WIDTH = 420;

/** Absolute ceiling, for very wide displays where the host guard alone is slack. */
export const MAX_COPILOT_WIDTH = 900;

/**
 * Clamp a requested width against the viewport.
 *
 * `viewportWidth` is passed rather than read from `window` so this stays pure
 * and testable, and so a caller mid-drag clamps against the viewport the drag
 * started in rather than re-reading layout on every pointer event.
 */
export function clampCopilotWidth(width: number, viewportWidth: number): number {
  if (!Number.isFinite(width)) return DEFAULT_COPILOT_WIDTH;
  // On a viewport too narrow to honour both minimums the panel's own minimum
  // wins: a panel below `MIN_COPILOT_WIDTH` is unusable, whereas a squeezed
  // diagram is merely cramped, and the user can collapse the panel entirely.
  const ceiling = Math.max(
    MIN_COPILOT_WIDTH,
    Math.min(MAX_COPILOT_WIDTH, viewportWidth - MIN_HOST_WIDTH),
  );
  return Math.round(Math.min(ceiling, Math.max(MIN_COPILOT_WIDTH, width)));
}

function readStored(storageKey: string): number | null {
  // localStorage throws (not returns null) in some privacy modes, and is absent
  // during SSR -- neither is a reason to fail rendering the panel.
  try {
    if (typeof window === "undefined") return null;
    const stored = window.localStorage.getItem(storageKey);
    if (stored === null) return null;
    const parsed = Number.parseInt(stored, 10);
    return Number.isFinite(parsed) ? parsed : null;
  } catch {
    return null;
  }
}

/**
 * Width of a docked copilot panel, remembered across visits.
 *
 * Sibling of `useCopilotCollapsed` and deliberately separate from it: collapsing
 * is "I do not want this now", resizing is "this is how much room it should have
 * when I do". Folding them into one value would lose the chosen width every time
 * the panel was collapsed, which is the state a user collapses it FROM.
 *
 * The stored value is re-clamped on read, so a width saved on a wide monitor
 * cannot swallow the diagram when the same user opens the page on a laptop.
 */
export function useCopilotWidth(
  storageKey: string,
  viewportWidth: () => number,
): [number, (width: number) => void] {
  const [width, setWidth] = useState<number>(() =>
    clampCopilotWidth(readStored(storageKey) ?? DEFAULT_COPILOT_WIDTH, viewportWidth()),
  );

  const persist = useCallback(
    (next: number) => {
      const clamped = clampCopilotWidth(next, viewportWidth());
      setWidth(clamped);
      try {
        window.localStorage.setItem(storageKey, String(clamped));
      } catch {
        // A rejected write only costs the preference, not the interaction.
      }
    },
    [storageKey, viewportWidth],
  );

  return [width, persist];
}
