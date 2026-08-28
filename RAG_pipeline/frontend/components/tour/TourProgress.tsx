"use client";

import { cn } from "@/lib/utils";

/**
 * Indeterminate bar shown while a step is waiting on a route or an anchor.
 *
 * Without it, a cross-page step is ~400ms of dimmed screen with no card and no
 * explanation, which reads as frozen rather than as deliberate movement. A
 * 2px bar at the top of the viewport is the same affordance the browser itself
 * uses for navigation, so it needs no learning — and unlike a spinner in the
 * card it does not fight the card's own fade.
 *
 * Hidden from assistive tech: the step change is already announced by the
 * tour's aria-live region, and a second announcement for "still loading" is
 * noise.
 */
export function TourProgress({ active }: { active: boolean }) {
  return (
    <div
      aria-hidden
      data-tour-progress
      data-active={active ? "true" : "false"}
      className={cn(
        "pointer-events-none fixed inset-x-0 top-0 z-[102] h-[2px] overflow-hidden bg-primary/15",
        "transition-opacity duration-200",
        active ? "opacity-100" : "opacity-0",
      )}
    >
      <span className="tour-progress-segment bg-primary" data-spotlight-animated />
    </div>
  );
}
