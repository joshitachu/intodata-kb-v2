import { z } from "zod";

/**
 * The labs lane's health contract, and the banner's reading of it.
 *
 * Split out of `app/labs/chat/page.tsx` for the same reason `labsTurn.ts` is
 * split out of `LabsTurnPanel.tsx`: the page renders, this decides. A contract
 * that only exists inside a Next page module cannot be tested without mounting
 * the page, and this one had been wrong in production for long enough to prove
 * that matters.
 *
 * It was wrong in a way worth recording. The schema declared `status`, `healthy`
 * and `message` -- none of which the backend has ever sent -- and typed `detail`
 * as a string while `_probe_opencode` fills it with OpenCode's health OBJECT. The
 * banner therefore reported the exact opposite of the truth: a reachable lane
 * failed validation and rendered the raw Zod issue list in red on every 30-second
 * poll, while an unreachable one parsed cleanly, matched no branch, and rendered
 * "Lane responding" with the `error` explaining the outage never read.
 */

/**
 * Mirror of `LabsHealthResponse` in `backend/src/api/routes/labs.py`, field for
 * field. No field here that the backend does not send; no field there that the
 * banner cannot read. The backend test `test_lane_health.py` pins the same
 * shape from the other side.
 *
 * `opencode_available` is required rather than optional because it is the one
 * field the response model marks required, and a 200 without it is not this
 * contract. Guessing at a verdict from a payload that cannot support one is what
 * produced the inversion above. A non-2xx never reaches this parser: `fetchJson`
 * throws first and the banner renders that error instead.
 */
export const LabsHealthSchema = z.object({
  lane: z.string().optional(),
  opencode_available: z.boolean(),
  /** OpenCode's own probe body (`{"healthy": true}` today), for the tooltip. */
  detail: z.record(z.string(), z.unknown()).nullable().optional(),
  error: z.string().nullable().optional(),
  model: z.string().nullable().optional(),
});

export type LabsHealth = z.infer<typeof LabsHealthSchema>;

export type LaneTone = "ok" | "down" | "unknown";

/**
 * `opencode_available` is the verdict; everything else is how it is worded.
 *
 * There is no third branch. The field is present or the response was not this
 * contract, so `unknown` is left to the states where the client genuinely does
 * not know yet -- before the first poll answers, or when it threw.
 */
export function laneVerdict(health: LabsHealth): { tone: LaneTone; label: string } {
  if (!health.opencode_available) {
    return { tone: "down", label: health.error?.trim() || "Lane unreachable" };
  }
  const model = health.model?.trim();
  return { tone: "ok", label: model ? `Lane ready (${model})` : "Lane ready" };
}

/**
 * The probe body as one line of hover text, or null.
 *
 * Formatted by the shape of each value rather than stringified: `detail` is
 * whatever OpenCode's `/api/health` returned, so a nested object is skipped
 * instead of rendered as "[object Object]", which is not a fact about the lane.
 * Same rule `labsTurn.ts:formatUsageValue` follows for the usage badges.
 */
export function describeLaneDetail(
  detail: Record<string, unknown> | null | undefined,
): string | null {
  if (!detail) return null;
  const parts: string[] = [];
  for (const [key, value] of Object.entries(detail)) {
    if (typeof value === "boolean") parts.push(`${key}: ${value ? "yes" : "no"}`);
    else if (typeof value === "number" && Number.isFinite(value)) parts.push(`${key}: ${value}`);
    else if (typeof value === "string" && value.trim()) parts.push(`${key}: ${value.trim()}`);
  }
  return parts.length > 0 ? parts.join(", ") : null;
}
