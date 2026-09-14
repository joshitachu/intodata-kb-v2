/**
 * A value that may not be known yet.
 *
 * The defect this exists to prevent: a presentational component that receives a
 * bare `T[]` cannot distinguish "the server said there are none" from "the
 * query has not resolved". It therefore renders a confident `0`, `"Never"` or
 * "no results" for a state that is simply unknown, and contradicts itself a
 * moment later when the data arrives. That reads as data loss.
 *
 * An empty array is not evidence of emptiness. Making the unknown state a
 * distinct variant makes the mistake unrepresentable rather than merely
 * discouraged: a caller cannot hand a component an empty array while the query
 * is still pending, because the type has nowhere to put one.
 *
 * Reserve `0` and `"Never"` for a confirmed successful response; render
 * `UNKNOWN_DISPLAY` for anything else.
 */
export type Loadable<T> =
  | { readonly status: "loading" }
  | { readonly status: "error"; readonly error: unknown }
  | { readonly status: "ready"; readonly value: T };

/** Placeholder for a quantity that is not yet known. Never a confirmed zero. */
export const UNKNOWN_DISPLAY = "—"; // em dash

/**
 * Adapt a react-query result.
 *
 * Prefers `data` whenever it is present: react-query keeps the last successful
 * value through a background refetch and can report `isError` alongside it, and
 * a stale-but-real number beats a placeholder.
 */
export function loadableFromQuery<T>(query: {
  data: T | undefined;
  isError: boolean;
  error?: unknown;
}): Loadable<T> {
  if (query.data !== undefined) return { status: "ready", value: query.data };
  if (query.isError) return { status: "error", error: query.error };
  return { status: "loading" };
}

/** Project a ready value; loading and error pass through untouched. */
export function mapLoadable<T, U>(source: Loadable<T>, project: (value: T) => U): Loadable<U> {
  return source.status === "ready"
    ? { status: "ready", value: project(source.value) }
    : source;
}
