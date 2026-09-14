/**
 * "I don't know yet" is a state, and it is not "empty".
 *
 * THE DEFECT THIS REPLACES
 *
 * Every selector branched in this order:
 *
 *   if (isLoading) -> spinner
 *   if (error)     -> "unavailable"
 *   if (!data || data.projects.length === 0) -> "No projects available"
 *
 * The third branch is a fall-through for *any* state that is neither loading
 * nor errored, and React Query v5 has one: `fetchStatus === "paused"`.
 * `data` is undefined, `error` is null (starting a fetch with no data resets
 * it), and `isLoading` is false — because `isLoading === isPending &&
 * isFetching`, and a paused retryer is not fetching. The user is then told
 * their account has no projects.
 *
 * A query pauses when a retry delay elapses while the tab is unfocused
 * (`retryer.canContinue()` requires `focusManager.isFocused()`), and a paused
 * query survives unmount/remount without restarting, so the lie persists for
 * as long as the tab stays hidden.
 *
 * `isPending` — not `isLoading` — is the honest "nothing known yet" signal:
 * it is `status === "pending"`, which is true exactly while there is neither
 * data nor an error, whether the request is in flight, waiting on a backoff,
 * or paused.
 */

export type QueryPhase = "fetching" | "paused" | "idle";

/** The three states a caller may actually render. */
export type AsyncOutcome<T> =
  | { kind: "unknown"; phase: QueryPhase }
  | { kind: "failed"; error: unknown; message: string }
  | { kind: "ready"; data: T };

/**
 * The subset of a `useQuery` result this needs.
 *
 * `isPending` is deliberately absent: `data === undefined && error == null` is
 * the same predicate and cannot drift out of step with it.
 */
export interface QueryLike<T> {
  data: T | undefined;
  error: unknown;
  fetchStatus: "fetching" | "paused" | "idle";
}

export function queryOutcome<T>(
  query: QueryLike<T>,
  fallbackMessage = "Could not load this right now",
): AsyncOutcome<T> {
  // Data wins over everything else. A background refetch — or a refetch that
  // failed while cached data is still on screen — must not blank the UI.
  if (query.data !== undefined) return { kind: "ready", data: query.data };

  // No data and an error: a failure, and it must look different from empty.
  if (query.error !== null && query.error !== undefined) {
    return {
      kind: "failed",
      error: query.error,
      message: query.error instanceof Error && query.error.message.trim()
        ? query.error.message
        : fallbackMessage,
    };
  }

  // Pending, paused, waiting on a backoff, or not started (`enabled: false`).
  // None of these are answers, so none of them may render as "empty".
  // `phase` is carried so a caller can word the affordance honestly: a paused
  // query is waiting for the tab to regain focus, not making progress.
  return { kind: "unknown", phase: query.fetchStatus };
}
