import type { PublicAgentEvent } from "@/types";
import type { StreamState } from "@/hooks/useStreamMessage";

/**
 * Turn-level derivation for the labs lane instrument panel.
 *
 * Everything here is a projection of frames the server sent. Nothing is
 * estimated: a number that did not arrive is `null` and renders as nothing at
 * all, because an invented token count or cost on an experimental lane would be
 * worse than an empty field. Every payload read is defensive for the same
 * reason -- the lane's event contract is still being written, and a payload
 * thinner than expected must degrade to what is there.
 */

export type LabsActivityStatus =
  | "running"
  | "succeeded"
  | "failed"
  | "denied"
  | "pending";

export interface LabsToolActivity {
  /** The frame's `event_id`; stable across a resumed stream, so it is the key. */
  id: string;
  /** Server-authored name of the tool. Never composed from arguments. */
  label: string;
  /** The server's own sentence about the call, when it adds to the label. */
  detail: string | null;
  status: LabsActivityStatus;
  /** How many evidence refs the call returned, when it reported any. */
  evidenceCount: number | null;
  /** ISO timestamp from the frame. */
  at: string;
}

export interface LabsUsageEntry {
  key: string;
  label: string;
  value: string;
}

/**
 * One step of the agent's plan, as the lane reported it on `plan.updated`.
 *
 * `status` is the model's own word, passed through by the backend rather than
 * mapped to a closed set, because the vocabulary is OpenCode's: today it is
 * `pending` / `in_progress` / `completed` / `cancelled`, and a value this client
 * has not seen is rendered neutrally instead of being recoded into a claim.
 */
export interface LabsPlanStep {
  id: string;
  content: string;
  status: string;
}

export type LabsTurnStatus = "streaming" | "completed" | "failed" | "ended";

export interface LabsTurnRecord {
  runId: string | null;
  status: LabsTurnStatus;
  /** Accumulated reasoning deltas. Empty when the lane emits none. */
  reasoning: string;
  /**
   * The agent's plan, as of the last `plan.updated`. Empty when it published none.
   *
   * Replaced by each frame, never merged: `todowrite` sends the WHOLE list every
   * time it is called, so the newest frame is the plan and an older one is a
   * superseded copy of it. Merging them would resurrect steps the agent dropped.
   */
  plan: LabsPlanStep[];
  /**
   * One line about what is happening now, or null. Replaces itself; never a log.
   *
   * Only while the turn is live. Once it has settled, the answer is on screen and
   * a line still claiming to be "reading the components" would be false.
   */
  statusLine: string | null;
  /**
   * Tool calls in the order the server announced them -- one entry per CALL, not
   * per frame. Each call produces a live `tool.running` frame and, for a Nexus
   * capability, a late `tool.completed` frame; `collapseActivities` folds the
   * pair into one entry, which is what makes `activities.length` a count of
   * calls. It was not always: while the two frames labelled the same call
   * differently, one turn's 40 calls listed as 81 entries and the header
   * reported that number next to the lane's own count of 40.
   */
  activities: LabsToolActivity[];
  /** Whatever `run.completed` put in `usage`, verbatim. */
  usage: LabsUsageEntry[];
  latencyMs: number | null;
  stopReason: string | null;
  error: string | null;
}

const TERMINAL_TYPES: ReadonlySet<string> = new Set([
  "run.completed",
  "run.failed",
  "run.interrupted",
]);

/** Payload `status` values, which are the server's own word for the outcome. */
const STATUS_BY_VALUE: Readonly<Record<string, LabsActivityStatus>> = {
  running: "running",
  started: "running",
  in_progress: "running",
  succeeded: "succeeded",
  success: "succeeded",
  completed: "succeeded",
  ok: "succeeded",
  failed: "failed",
  error: "failed",
  denied: "denied",
  rejected: "denied",
  proposed: "pending",
  awaiting_approval: "pending",
  pending: "pending",
};

/** Fallback: the `tool.<suffix>` frame name, when the payload said nothing. */
const STATUS_BY_SUFFIX: Readonly<Record<string, LabsActivityStatus>> = {
  proposed: "pending",
  awaiting_approval: "pending",
  running: "running",
  started: "running",
  completed: "succeeded",
  denied: "denied",
  failed: "failed",
};

function readText(payload: Record<string, unknown>, key: string): string | null {
  const value = payload[key];
  if (typeof value !== "string") return null;
  const trimmed = value.trim();
  return trimmed.length > 0 ? trimmed : null;
}

function readMillis(payload: Record<string, unknown>, key: string): number | null {
  const value = payload[key];
  if (typeof value !== "number" || !Number.isFinite(value) || value < 0) return null;
  return value;
}

/** "read_runtime_evidence" -> "Read runtime evidence". Server wording, not ours. */
function humanize(value: string): string {
  const spaced = value.replace(/[_.-]+/g, " ").trim();
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

function activityStatus(
  reported: string | null,
  suffix: string,
): LabsActivityStatus {
  if (reported) {
    const mapped = STATUS_BY_VALUE[reported.toLowerCase()];
    if (mapped) return mapped;
  }
  return STATUS_BY_SUFFIX[suffix] ?? "running";
}

function isOpen(status: LabsActivityStatus): boolean {
  return status === "running" || status === "pending";
}

/**
 * The three of the agent's own tools whose raw names say nothing to a reader.
 *
 * Deliberately not a general relabelling table: `humanize` turning
 * `read_runtime_evidence` into "Read runtime evidence" is already the server's
 * wording and is already readable. These three are not. "Todowrite" and "Task"
 * name a mechanism, and they are exactly the frames a user watching a long turn
 * most needs to understand -- they are the turn saying "here is my plan" and
 * "I am checking what I wrote before I show it to you".
 */
const WORKSPACE_TOOL_LABELS: Record<string, string> = {
  todowrite: "Planning the steps",
  todoread: "Reviewing the plan",
  task: "Checking the answer against the evidence",
};

function toolActivity(event: PublicAgentEvent): LabsToolActivity | null {
  if (!event.type.startsWith("tool.")) return null;
  const payload = event.public_payload;
  const suffix = event.type.slice("tool.".length);
  const summary = readText(payload, "summary");
  // `tool_label` is the tool's name and the only field guaranteed to be one.
  // `summary` is a sentence about the call; it is the label only when there is
  // nothing better, and is otherwise shown as the detail line.
  const reportedLabel = readText(payload, "tool_label");
  const label =
    (reportedLabel && WORKSPACE_TOOL_LABELS[reportedLabel.toLowerCase()]) ??
    reportedLabel ??
    summary ??
    humanize(suffix);
  const evidenceRefs = payload.evidence_refs;
  return {
    id: event.event_id,
    label,
    detail: summary && summary !== label ? summary : null,
    status: activityStatus(readText(payload, "status"), suffix),
    evidenceCount: Array.isArray(evidenceRefs) ? evidenceRefs.length : null,
    at: event.created_at,
  };
}

/**
 * Collapses the `tool.*` frames into one line per call.
 *
 * A terminal frame closes the OLDEST still-open entry with the same label
 * instead of appending a second line. Oldest, not newest: both frame families
 * are emitted in call order, so a turn that called one tool twice closes the
 * first call with the first completion. Matching the newest would pair each
 * completion with the wrong call and attach its summary and evidence count to it.
 *
 * The two families still do not line up one for one -- the bus announces every
 * OpenCode tool (`read`, `todowrite`, `task`, ...) while the late frames cover
 * only Nexus capabilities -- so a completion matching no open entry is appended
 * as its own line rather than guessed at. What they do share is the label: the
 * backend emits both frames of one call under the same string
 * (`src/labs/runtime.py:labs_tool_label`). Before it did, nothing here could tell
 * that `nexus_read_topology_structure` and `topology.landscape.read_structure`
 * were one call, and every call was listed -- and counted -- twice.
 */
function collapseActivities(
  events: readonly PublicAgentEvent[],
): LabsToolActivity[] {
  const activities: LabsToolActivity[] = [];
  for (const event of events) {
    const activity = toolActivity(event);
    if (!activity) continue;
    if (!isOpen(activity.status)) {
      let matched = false;
      for (let index = 0; index < activities.length; index += 1) {
        const open = activities[index];
        if (!isOpen(open.status) || open.label !== activity.label) continue;
        activities[index] = {
          ...open,
          status: activity.status,
          detail: activity.detail ?? open.detail,
          evidenceCount: activity.evidenceCount ?? open.evidenceCount,
          at: activity.at,
        };
        matched = true;
        break;
      }
      if (matched) continue;
    }
    activities.push(activity);
  }
  return activities;
}

/** The step a plan reports as under way, when exactly that is what it reports. */
const PLAN_STEP_IN_PROGRESS = "in_progress";

/**
 * The agent's plan as of the last frame that carried one.
 *
 * Every field is checked rather than trusted: the frame is projected from a
 * model-authored tool argument, and a step with no readable content is dropped
 * instead of rendered as a blank row.
 */
function latestPlan(events: readonly PublicAgentEvent[]): LabsPlanStep[] {
  for (let index = events.length - 1; index >= 0; index -= 1) {
    const event = events[index];
    if (event.type !== "plan.updated") continue;
    const todos = event.public_payload.todos;
    if (!Array.isArray(todos)) continue;
    const steps: LabsPlanStep[] = [];
    todos.forEach((entry, position) => {
      if (typeof entry !== "object" || entry === null) return;
      const step = entry as Record<string, unknown>;
      const content = readText(step, "content");
      if (content === null) return;
      steps.push({
        id: readText(step, "id") ?? String(position),
        content,
        status: readText(step, "status") ?? "pending",
      });
    });
    if (steps.length > 0) return steps;
  }
  return [];
}

/**
 * The one line that says what is happening now.
 *
 * Precedence is "what the agent said it is doing" over "what it is calling": a
 * plan step is written for the reader, a tool label is the name of a mechanism.
 * The server's own sentence about the call (`detail`) beats the bare label for
 * the same reason.
 *
 * Null once the turn has settled. The answer is on screen by then, and a line
 * that still claims work is under way would be the panel asserting something it
 * cannot know.
 */
function statusLineFor(
  status: LabsTurnStatus,
  plan: readonly LabsPlanStep[],
  activities: readonly LabsToolActivity[],
): string | null {
  if (status !== "streaming") return null;
  const current = plan.find((step) => step.status === PLAN_STEP_IN_PROGRESS);
  if (current) return current.content;
  for (let index = activities.length - 1; index >= 0; index -= 1) {
    const activity = activities[index];
    if (isOpen(activity.status)) return activity.detail ?? activity.label;
  }
  return null;
}

/**
 * One `usage` value as text, or null when it cannot be rendered honestly.
 *
 * Every key is optional and unknown keys are expected: the lane reports
 * `capability_calls` today and may report tokens and cost later, so this formats
 * by the value's shape rather than by a list of field names. Nested objects are
 * skipped instead of stringified -- "[object Object]" is not a fact.
 */
function formatUsageValue(value: unknown): string | null {
  if (typeof value === "number") {
    if (!Number.isFinite(value)) return null;
    return Number.isInteger(value) ? value.toLocaleString("en-US") : String(value);
  }
  if (typeof value === "string") {
    const trimmed = value.trim();
    return trimmed.length > 0 ? trimmed : null;
  }
  if (typeof value === "boolean") return value ? "yes" : "no";
  return null;
}

function usageEntries(payload: Record<string, unknown>): LabsUsageEntry[] {
  const usage = payload.usage;
  if (typeof usage !== "object" || usage === null || Array.isArray(usage)) {
    return [];
  }
  const entries: LabsUsageEntry[] = [];
  for (const [key, value] of Object.entries(usage as Record<string, unknown>)) {
    const formatted = formatUsageValue(value);
    if (formatted === null) continue;
    entries.push({ key, label: humanize(key), value: formatted });
  }
  return entries;
}

/** "840 ms", "12.4s", "2m 5s". Never a rate, never an estimate. */
export function formatDuration(milliseconds: number | null): string | null {
  if (milliseconds === null || !Number.isFinite(milliseconds) || milliseconds < 0) {
    return null;
  }
  if (milliseconds < 1000) return `${Math.round(milliseconds)} ms`;
  const seconds = milliseconds / 1000;
  if (seconds < 60) return `${seconds.toFixed(1)}s`;
  const whole = Math.floor(seconds);
  return `${Math.floor(whole / 60)}m ${whole % 60}s`;
}

/**
 * Projects the live stream state into the panel's view of one turn.
 *
 * Returns null when the state carries nothing at all, which is what the host
 * sees after the chat context resets a finished turn. The panel keeps showing
 * the last record in that case: the tool sequence and the usage line are worth
 * more after the turn than during it, and they would otherwise flash and
 * vanish the moment the answer was saved.
 */
export function deriveLabsTurn(state: StreamState): LabsTurnRecord | null {
  const events = state.agentEvents ?? [];
  const reasoning = state.reasoning ?? "";
  const hasSignal =
    state.isStreaming ||
    Boolean(state.runId) ||
    state.content.length > 0 ||
    Boolean(state.error) ||
    state.messageId !== null ||
    events.length > 0 ||
    reasoning.length > 0;
  if (!hasSignal) return null;

  let terminal: PublicAgentEvent | null = null;
  for (let index = events.length - 1; index >= 0; index -= 1) {
    if (TERMINAL_TYPES.has(events[index].type)) {
      terminal = events[index];
      break;
    }
  }
  const terminalPayload = terminal?.public_payload ?? null;

  let status: LabsTurnStatus;
  if (state.error || terminal?.type === "run.failed") {
    status = "failed";
  } else if (terminal?.type === "run.completed" || state.messageId !== null) {
    status = "completed";
  } else if (state.isStreaming) {
    status = "streaming";
  } else {
    // The socket closed, or the reader was stopped, without a terminal frame.
    // Not a failure and not a completion -- only what is known.
    status = "ended";
  }

  const plan = latestPlan(events);
  const activities = collapseActivities(events);

  return {
    runId: state.runId ?? null,
    status,
    reasoning,
    plan,
    statusLine: statusLineFor(status, plan, activities),
    activities,
    usage: terminalPayload ? usageEntries(terminalPayload) : [],
    latencyMs:
      state.latencyMs ??
      (terminalPayload ? readMillis(terminalPayload, "latency_ms") : null),
    stopReason: terminalPayload ? readText(terminalPayload, "stop_reason") : null,
    error:
      state.error ??
      (terminal?.type === "run.failed" && terminalPayload
        ? readText(terminalPayload, "summary")
        : null),
  };
}
