import type { PublicAgentEvent } from "@/types";

/**
 * The live phase of a turn in progress, as reported by the server's
 * `assistant.progress` heartbeat.
 *
 * Every field is nullable on purpose. The payload is authored by the harness
 * and gains fields over time, so this must degrade to whatever arrived: a
 * heartbeat with only `status` still tells the user the turn is alive, and a
 * missing `summary` must not blank the phase it belongs to.
 */
export interface StreamProgress {
  status: string | null;
  phase: string | null;
  summary: string | null;
  elapsedMs: number | null;
}

/** Trimmed string, or null for absent/blank/non-string values. */
function readText(payload: Record<string, unknown>, key: string): string | null {
  const value = payload[key];
  if (typeof value !== "string") return null;
  const trimmed = value.trim();
  return trimmed.length > 0 ? trimmed : null;
}

/** Finite non-negative number, or null. A negative elapsed time is not a fact. */
function readMillis(payload: Record<string, unknown>, key: string): number | null {
  const value = payload[key];
  if (typeof value !== "number" || !Number.isFinite(value) || value < 0) return null;
  return value;
}

/**
 * Reads an `assistant.progress` payload. Returns null when the payload carries
 * no usable signal at all, so the caller keeps the last known phase instead of
 * replacing it with an empty one.
 */
export function parseAgentProgress(
  payload: Record<string, unknown>,
): StreamProgress | null {
  const progress: StreamProgress = {
    status: readText(payload, "status"),
    phase: readText(payload, "phase"),
    summary: readText(payload, "summary"),
    elapsedMs: readMillis(payload, "elapsed_ms"),
  };
  const hasSignal =
    progress.status !== null ||
    progress.phase !== null ||
    progress.summary !== null ||
    progress.elapsedMs !== null;
  return hasSignal ? progress : null;
}

/** "retrieving_evidence" -> "Retrieving evidence". Server wording, not ours. */
function humanize(value: string): string {
  const spaced = value.replace(/[_-]+/g, " ").trim();
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

/**
 * The one line that names where the turn is. `phase` is the server's own
 * statement of position and wins; `status` is the coarser fallback.
 */
export function progressPhaseLabel(progress: StreamProgress | null): string | null {
  if (!progress) return null;
  const value = progress.phase ?? progress.status;
  return value ? humanize(value) : null;
}

/** Whole seconds up to a minute, then "1m 5s". Never a percentage. */
export function formatElapsed(elapsedMs: number | null): string | null {
  if (elapsedMs === null || elapsedMs < 1000) return null;
  const seconds = Math.floor(elapsedMs / 1000);
  if (seconds < 60) return `${seconds}s`;
  return `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
}

export type ToolActivityStatus = "running" | "completed" | "failed";

export interface ToolActivity {
  label: string;
  status: ToolActivityStatus;
}

const TOOL_STATUS_BY_SUFFIX: Readonly<Record<string, ToolActivityStatus>> = {
  started: "running",
  running: "running",
  completed: "completed",
  failed: "failed",
};

/**
 * The most recent `tool.*` event, as a single line.
 *
 * This is text only. The canvas already reacts to the same events by
 * highlighting nodes (`useProgressiveTargets`); duplicating that here would be
 * two answers to one question, so nothing in this module resolves targets.
 */
export function latestToolActivity(
  events: readonly PublicAgentEvent[] | undefined,
): ToolActivity | null {
  if (!events || events.length === 0) return null;
  for (let index = events.length - 1; index >= 0; index -= 1) {
    const event = events[index];
    if (!event.type.startsWith("tool.")) continue;
    const payload = event.public_payload;
    const label =
      readText(payload, "summary") ??
      readText(payload, "label") ??
      readText(payload, "tool") ??
      readText(payload, "tool_name") ??
      humanize(event.type.slice("tool.".length));
    const status = TOOL_STATUS_BY_SUFFIX[event.type.slice("tool.".length)] ?? "running";
    return { label, status };
  }
  return null;
}
