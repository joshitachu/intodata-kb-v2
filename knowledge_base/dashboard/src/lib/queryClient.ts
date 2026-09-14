import { QueryClient } from "@tanstack/react-query";
import { httpStatusFromError } from "./errors";

/**
 * Whether a failed query is worth attempting again.
 *
 * A 4xx is an answer, not a fault: the resource is missing, the caller is not
 * allowed to see it, or the request is malformed. Repeating it verbatim cannot
 * change the outcome, so it is never retried. (401 does not reach here --
 * `api/client.ts` redirects to login on it.) Anything else -- a 5xx from a
 * restarting upstream, a dropped connection -- gets a bounded retry.
 */
export function shouldRetryQuery(failureCount: number, error: unknown): boolean {
  const status = httpStatusFromError(error);
  if (status !== null && status >= 400 && status < 500) return false;
  return failureCount < 2;
}

/**
 * Bounds refetch-on-mount and refetch-on-focus for queries that do not state
 * their own rate. The previous value was 2s, which was effectively no cache.
 */
export const DEFAULT_STALE_MS = 30_000;

/**
 * The app's single QueryClient.
 *
 * There is deliberately NO global `refetchInterval`. A blanket 5s poll made
 * every mounted query re-fetch continuously: measured over ~2h of partial use
 * it produced 339 calls to `/mcp/platforms`, 336 to `/mcp/models` and 334 to
 * `/mcp/plugins` -- catalogues that change when an operator installs something,
 * not every five seconds -- 343 to `/mcp/logs/{project}`, and it turned a
 * single 404 in the doc viewer into 81 identical failed requests.
 *
 * Polling is now a per-query decision, made where the data's real change rate
 * is known: the conditional 10s intervals in `hooks/useProjects.ts` (live only
 * while a scan or generation is in flight) and the 30s interval in
 * `hooks/useFleet.ts`. Everything else is invalidated at the event that changes
 * it -- see the run-completion effect in `App.tsx` -- because an invalidation is
 * exact and a poll is a guess.
 */
export function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: DEFAULT_STALE_MS,
        retry: shouldRetryQuery,
      },
    },
  });
}
