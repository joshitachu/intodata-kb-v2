"use client";

import { useEffect, useId, useRef, useState } from "react";
import {
  AlertTriangle,
  Ban,
  Brain,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Circle,
  ListChecks,
  ListTree,
  Loader2,
  Square,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import {
  formatDuration,
  type LabsActivityStatus,
  type LabsPlanStep,
  type LabsToolActivity,
  type LabsTurnRecord,
} from "./labsTurn";

/**
 * The labs lane's turn instrument: what the agent plans to do, what it is
 * thinking, what it is calling, and what the turn cost -- none of which the chat
 * bubble shows.
 *
 * It sits above the thread rather than inside it on purpose. The answer body is
 * rendered by the production `ChatWindow`, unforked, so the two lanes stay
 * comparable; anything added inside that body would be a difference in the
 * renderer rather than in the agent.
 *
 * Four channels, and the separation between them is the design:
 *
 *   plan          a card, collapsed, the steps the agent published
 *   status line   ONE line, replaced as work proceeds, gone when the turn ends
 *   reasoning     collapsed, the model narrating to itself
 *   tool activity collapsed, one row per call
 *
 * All four used to be one: the agent was asked to plan out loud, so it narrated
 * every step into the answer stream and a turn arrived as thirteen paragraphs of
 * commentary -- component ids and all -- above its own answer. None of it is
 * merged into the answer now, and reasoning in particular never will be: it is
 * unvalidated, ungrounded, and not what the lane is being judged on.
 */
export interface LabsTurnPanelProps {
  /** The live turn, or the last one once it finished. Null before the first. */
  record: LabsTurnRecord | null;
  /**
   * Stops reading the stream.
   *
   * Client-side only: the labs lane exposes no cancel endpoint and the proxy
   * does not forward the abort, so the run may continue on the server. The
   * button is worded accordingly.
   */
  onStop: () => void;
}

const STATUS_LABEL: Readonly<Record<LabsTurnRecord["status"], string>> = {
  streaming: "Running",
  completed: "Answered",
  failed: "Failed",
  ended: "Ended",
};

function TurnStatusIcon({ status }: { status: LabsTurnRecord["status"] }) {
  if (status === "streaming") {
    return <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin" aria-hidden />;
  }
  if (status === "completed") {
    return (
      <CheckCircle2 className="h-3.5 w-3.5 shrink-0 text-emerald-600" aria-hidden />
    );
  }
  if (status === "failed") {
    return (
      <AlertTriangle className="h-3.5 w-3.5 shrink-0 text-amber-600" aria-hidden />
    );
  }
  return <Circle className="h-3.5 w-3.5 shrink-0" aria-hidden />;
}

function ActivityIcon({ status }: { status: LabsActivityStatus }) {
  if (status === "running") {
    return <Loader2 className="h-3 w-3 shrink-0 animate-spin" aria-hidden />;
  }
  if (status === "succeeded") {
    return <CheckCircle2 className="h-3 w-3 shrink-0 text-emerald-600" aria-hidden />;
  }
  if (status === "failed") {
    return <AlertTriangle className="h-3 w-3 shrink-0 text-amber-600" aria-hidden />;
  }
  if (status === "denied") {
    return <Ban className="h-3 w-3 shrink-0 text-destructive" aria-hidden />;
  }
  return <Circle className="h-3 w-3 shrink-0" aria-hidden />;
}

const ACTIVITY_STATUS_LABEL: Readonly<Record<LabsActivityStatus, string>> = {
  running: "Running",
  succeeded: "Done",
  failed: "Failed",
  denied: "Denied",
  pending: "Waiting",
};

/** Wall-clock time of a frame, or null when the timestamp is unusable. */
function formatClock(iso: string): string | null {
  const parsed = new Date(iso);
  return Number.isNaN(parsed.getTime()) ? null : parsed.toLocaleTimeString();
}

/**
 * Step statuses this client renders with a mark of their own.
 *
 * Anything else the lane sends -- the vocabulary is OpenCode's and may grow --
 * falls through to the neutral mark. An unrecognised status is not evidence that
 * a step is finished, cancelled or under way, so it may not be drawn as any of
 * them.
 */
const PLAN_STEP_DONE = "completed";
const PLAN_STEP_ACTIVE = "in_progress";
const PLAN_STEP_CANCELLED = "cancelled";

function PlanStepIcon({ status }: { status: string }) {
  if (status === PLAN_STEP_ACTIVE) {
    return <Loader2 className="h-3 w-3 shrink-0 animate-spin text-primary" aria-hidden />;
  }
  if (status === PLAN_STEP_DONE) {
    return <CheckCircle2 className="h-3 w-3 shrink-0 text-emerald-600" aria-hidden />;
  }
  if (status === PLAN_STEP_CANCELLED) {
    return <Ban className="h-3 w-3 shrink-0 text-muted-foreground" aria-hidden />;
  }
  return <Circle className="h-3 w-3 shrink-0" aria-hidden />;
}

function PlanStepRow({ step }: { step: LabsPlanStep }) {
  return (
    <li className="flex min-w-0 items-start gap-2 py-0.5">
      <span className="mt-0.5">
        <PlanStepIcon status={step.status} />
      </span>
      <span
        className={cn(
          "min-w-0 flex-1 break-words",
          step.status === PLAN_STEP_ACTIVE && "font-medium text-foreground",
          step.status === PLAN_STEP_DONE && "text-muted-foreground/70 line-through",
          step.status === PLAN_STEP_CANCELLED && "text-muted-foreground/70 line-through",
        )}
      >
        {step.content}
      </span>
    </li>
  );
}

function ToolActivityRow({ activity }: { activity: LabsToolActivity }) {
  const clock = formatClock(activity.at);
  return (
    <li className="flex min-w-0 items-start gap-2 py-1">
      <span className="mt-0.5">
        <ActivityIcon status={activity.status} />
      </span>
      <span className="min-w-0 flex-1">
        <span className="flex min-w-0 flex-wrap items-baseline gap-x-2">
          <span className="min-w-0 break-all font-medium text-foreground">
            {activity.label}
          </span>
          <span className="shrink-0 text-muted-foreground/80">
            {ACTIVITY_STATUS_LABEL[activity.status]}
          </span>
          {activity.evidenceCount !== null && activity.evidenceCount > 0 && (
            <span className="shrink-0 text-muted-foreground/80">
              {activity.evidenceCount}{" "}
              {activity.evidenceCount === 1 ? "evidence ref" : "evidence refs"}
            </span>
          )}
          {clock && (
            <span className="shrink-0 tabular-nums text-muted-foreground/60">
              {clock}
            </span>
          )}
        </span>
        {activity.detail && (
          <span className="mt-0.5 block break-words text-muted-foreground">
            {activity.detail}
          </span>
        )}
      </span>
    </li>
  );
}

export function LabsTurnPanel({ record, onStop }: LabsTurnPanelProps) {
  const planId = useId();
  const reasoningId = useId();
  const activityId = useId();
  // Collapsed by default, all three. The plan is a reference the reader opens
  // when they want to know where the turn is going; the status line below is
  // what they read while it gets there, and it costs one line.
  const [showPlan, setShowPlan] = useState(false);
  const [showReasoning, setShowReasoning] = useState(false);
  const [showActivity, setShowActivity] = useState(false);
  // Which turn the user asked to stop. Keyed by run id so the notice does not
  // carry over to the next turn; a turn that has not announced a run id yet is
  // still identifiable, because only one turn is ever in flight.
  const [stoppedTurn, setStoppedTurn] = useState<string | null>(null);
  const reasoningRef = useRef<HTMLDivElement>(null);

  const isStreaming = record?.status === "streaming";
  const reasoning = record?.reasoning ?? "";

  // Follow the reasoning as it streams, but only while it is open: scrolling a
  // panel the user cannot see is pointless, and fighting their scroll position
  // once the turn is over is worse.
  useEffect(() => {
    if (!showReasoning || !isStreaming) return;
    const node = reasoningRef.current;
    if (node) node.scrollTop = node.scrollHeight;
  }, [showReasoning, isStreaming, reasoning]);

  if (!record) {
    return (
      <p className="shrink-0 border-b bg-muted/30 px-4 py-1.5 text-xs text-muted-foreground">
        No turn yet. The plan, reasoning, tool activity and reported usage
        appear here while the lane answers.
      </p>
    );
  }

  const turnKey = record.runId ?? "pending";
  const stopped = stoppedTurn === turnKey;
  const latency = formatDuration(record.latencyMs);
  const toolCount = record.activities.length;
  const hasReasoning = reasoning.length > 0;
  const plan = record.plan;
  const planDone = plan.filter(
    (step) => step.status === PLAN_STEP_DONE || step.status === PLAN_STEP_CANCELLED,
  ).length;

  function handleStop() {
    setStoppedTurn(turnKey);
    onStop();
  }

  return (
    <section
      aria-label="Labs turn detail"
      className="shrink-0 border-b bg-muted/30 px-4 py-1.5 text-xs text-muted-foreground"
    >
      <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1">
        <span className="flex min-w-0 items-center gap-1.5" aria-live="polite">
          <TurnStatusIcon status={record.status} />
          <span className="font-medium text-foreground">
            {stopped && !isStreaming ? "Stopped" : STATUS_LABEL[record.status]}
          </span>
        </span>

        {/* One entry per CALL: `collapseActivities` folds a call's live frame
            and its late completion into a single row. The lane reports a second,
            smaller number of its own (`capability_calls`, rendered as a labelled
            badge below) covering only the Nexus capabilities among these. Two
            true numbers, so both are named -- the pair used to appear unlabelled
            and disagreeing, "81 tool calls" beside "Tool calls 40". */}
        {toolCount > 0 && (
          <span className="shrink-0">
            {toolCount} {toolCount === 1 ? "tool call" : "tool calls"}
          </span>
        )}
        {latency && <span className="shrink-0 tabular-nums">{latency}</span>}
        {record.stopReason && (
          <span className="shrink-0 text-muted-foreground/80">
            {record.stopReason}
          </span>
        )}

        {/* Reported usage, verbatim and LABELLED with the lane's own key. It
            sends `capability_calls` today and may send tokens or cost later;
            whatever arrives is rendered, and what does not arrive is simply
            absent. The key matters: this number and the tool-call count above
            are different measurements, and the badge is what says so. */}
        {record.usage.map((entry) => (
          <Badge key={entry.key} variant="muted" title={`Reported by the lane: ${entry.key}`}>
            <span className="text-muted-foreground">{entry.label}</span>
            <span className="tabular-nums font-semibold text-foreground">
              {entry.value}
            </span>
          </Badge>
        ))}

        {record.error && (
          <span className="min-w-0 flex-1 truncate text-destructive" title={record.error}>
            {record.error}
          </span>
        )}

        {isStreaming && (
          <span className="ml-auto shrink-0">
            <Button
              variant="secondary"
              size="sm"
              onClick={handleStop}
              title="Stops showing this turn. The lane has no cancel endpoint, so the run may keep going on the server."
            >
              <Square className="h-3 w-3" aria-hidden />
              Stop showing
            </Button>
          </span>
        )}
      </div>

      {stopped && !isStreaming && (
        <p className="mt-1 text-muted-foreground/90">
          Stopped reading this turn. Anything already received is kept; the lane
          may still be finishing the run on the server.
        </p>
      )}

      {/* The plan the agent published, as a card of its own. It is the whole
          reason the answer no longer opens with a list of intentions: the steps
          have somewhere structured to go. */}
      {plan.length > 0 && (
        <div className="mt-1.5 rounded-md border bg-background/60 px-3 py-1.5">
          <button
            type="button"
            onClick={() => setShowPlan((value) => !value)}
            aria-expanded={showPlan}
            aria-controls={planId}
            className="flex w-full min-w-0 items-center gap-1.5 transition-colors hover:text-foreground"
          >
            {showPlan ? (
              <ChevronDown className="h-3.5 w-3.5 shrink-0" aria-hidden />
            ) : (
              <ChevronRight className="h-3.5 w-3.5 shrink-0" aria-hidden />
            )}
            <ListChecks className="h-3.5 w-3.5 shrink-0" aria-hidden />
            <span className="font-medium text-foreground">Plan</span>
            <span className="tabular-nums text-muted-foreground/80">
              {planDone}/{plan.length}
            </span>
          </button>
          {showPlan && (
            <ol id={planId} className="mt-1 list-none space-y-0">
              {plan.map((step) => (
                <PlanStepRow key={step.id} step={step} />
              ))}
            </ol>
          )}
        </div>
      )}

      {/* The replaceable status line. One line, never a log: it is whichever
          step the agent says it is on, or failing that the call it has open, and
          it disappears when the turn settles rather than freezing on a stale
          claim. `aria-live` announces the replacement, not an accumulation. */}
      {record.statusLine && (
        <p className="mt-1 flex min-w-0 items-center gap-1.5" aria-live="polite">
          <Loader2 className="h-3 w-3 shrink-0 animate-spin" aria-hidden />
          <span className="min-w-0 truncate" title={record.statusLine}>
            {record.statusLine}
          </span>
        </p>
      )}

      {(hasReasoning || toolCount > 0) && (
        <div className="mt-1 flex flex-wrap items-center gap-x-4 gap-y-1">
          {hasReasoning && (
            <button
              type="button"
              onClick={() => setShowReasoning((value) => !value)}
              aria-expanded={showReasoning}
              aria-controls={reasoningId}
              className="flex items-center gap-1.5 transition-colors hover:text-foreground"
            >
              {showReasoning ? (
                <ChevronDown className="h-3.5 w-3.5 shrink-0" aria-hidden />
              ) : (
                <ChevronRight className="h-3.5 w-3.5 shrink-0" aria-hidden />
              )}
              <Brain className="h-3.5 w-3.5 shrink-0" aria-hidden />
              <span>{isStreaming ? "Thinking" : "Reasoning"}</span>
              {isStreaming && (
                <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-primary" aria-hidden />
              )}
            </button>
          )}

          {toolCount > 0 && (
            <button
              type="button"
              onClick={() => setShowActivity((value) => !value)}
              aria-expanded={showActivity}
              aria-controls={activityId}
              className="flex items-center gap-1.5 transition-colors hover:text-foreground"
            >
              {showActivity ? (
                <ChevronDown className="h-3.5 w-3.5 shrink-0" aria-hidden />
              ) : (
                <ChevronRight className="h-3.5 w-3.5 shrink-0" aria-hidden />
              )}
              <ListTree className="h-3.5 w-3.5 shrink-0" aria-hidden />
              <span>Tool activity ({toolCount})</span>
            </button>
          )}
        </div>
      )}

      {showReasoning && hasReasoning && (
        <div
          id={reasoningId}
          ref={reasoningRef}
          className={cn(
            "mt-1.5 max-h-48 overflow-y-auto whitespace-pre-wrap break-words",
            "rounded-md border bg-background/60 px-3 py-2 font-mono text-[11px] leading-relaxed",
          )}
        >
          {reasoning}
        </div>
      )}

      {showActivity && toolCount > 0 && (
        <ul id={activityId} className="mt-1.5 divide-y border-l pl-3">
          {record.activities.map((activity) => (
            <ToolActivityRow key={activity.id} activity={activity} />
          ))}
        </ul>
      )}
    </section>
  );
}
