"use client";

import { Loader2 } from "lucide-react";
import type { QueryPhase } from "@/lib/queryState";

/**
 * The two honest non-answers a toolbar selector can show.
 *
 * Shared by every project selector so the states cannot drift apart again:
 * the three selectors had three copies of the same branch ladder, and the
 * copy that mattered rendered "No projects available" for a query that had
 * simply not answered yet.
 */

/** Nothing known yet: in flight, waiting on a backoff, or paused. */
export function SelectorPending({ phase }: { phase: QueryPhase }) {
  // A paused query is waiting for the tab to regain focus before it retries
  // (React Query's retryer requires focus to continue). Saying "Projects..."
  // there would promise progress that is not happening.
  const label = phase === "paused" ? "Reconnecting…" : "Projects…";
  return (
    <div
      className="flex h-7 items-center gap-1.5 px-2 text-xs text-muted-foreground"
      role="status"
      aria-live="polite"
    >
      {phase === "paused" ? null : <Loader2 className="h-3 w-3 animate-spin" />}
      <span>{label}</span>
    </div>
  );
}

/**
 * The request failed. Distinct from empty on purpose: an account with no
 * projects and a backend that will not answer are different problems with
 * different fixes, and the user can only act on the difference if we show it.
 */
export function SelectorFailed({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div className="flex h-7 items-center gap-1.5 px-2 text-xs text-destructive">
      <span title={message}>Projects unavailable</span>
      {onRetry ? (
        <button
          type="button"
          onClick={onRetry}
          className="underline underline-offset-2 hover:no-underline"
        >
          Retry
        </button>
      ) : null}
    </div>
  );
}
