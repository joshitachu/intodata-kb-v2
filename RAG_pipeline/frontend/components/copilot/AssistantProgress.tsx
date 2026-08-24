"use client";

import { Loader2, CheckCircle2, AlertTriangle, Wrench } from "lucide-react";
import { cn } from "@/lib/utils";
import {
  formatElapsed,
  latestToolActivity,
  progressPhaseLabel,
  type StreamProgress,
  type ToolActivityStatus,
} from "@/lib/agentProgress";
import type { PublicAgentEvent } from "@/types";

/**
 * The waiting state, before any answer text exists.
 *
 * Everything shown here is server-authored: the `assistant.progress` summary,
 * and the last `tool.*` event. There is deliberately no percentage and no
 * progress bar -- the harness cannot say how much of a turn is left, so a bar
 * would be an invented number.
 *
 * `summary` is the user-facing line. `phase` is an OPEN set -- slugs are added
 * as capabilities gain source labels -- so nothing here switches on it or
 * carries per-phase copy; it serves only as the animation key that restarts the
 * enter transition when the turn moves on. The summary already ends in its own
 * elapsed suffix ("<label>... 4s"), so the elapsed badge appears only when the
 * server sent no summary at all.
 *
 * Degrades in steps, because a payload can be thinner than the current
 * contract: summary, then the humanised phase, then status, then the caller's
 * fallback label when nothing has arrived yet.
 */
export interface AssistantProgressProps {
  progress?: StreamProgress | null;
  /** The turn's retained agent events; the latest `tool.*` one is summarised. */
  events?: readonly PublicAgentEvent[];
  /** Used until the server reports a phase of its own. */
  fallbackLabel: string;
  className?: string;
}

function ToolIcon({ status }: { status: ToolActivityStatus }) {
  if (status === "completed") {
    return <CheckCircle2 className="h-3 w-3 shrink-0 text-emerald-600" aria-hidden />;
  }
  if (status === "failed") {
    return <AlertTriangle className="h-3 w-3 shrink-0 text-amber-600" aria-hidden />;
  }
  return <Wrench className="h-3 w-3 shrink-0" aria-hidden />;
}

export function AssistantProgress({
  progress,
  events,
  fallbackLabel,
  className,
}: AssistantProgressProps) {
  const detail = progress ?? null;
  const headline = detail?.summary ?? progressPhaseLabel(detail) ?? fallbackLabel;
  const elapsed = detail?.summary ? null : formatElapsed(detail?.elapsedMs ?? null);
  // The tool line answers "doing what, exactly" beside the phase. Dropped when
  // it just repeats the headline, which happens when the phase IS the tool.
  const latestTool = latestToolActivity(events);
  const tool = latestTool && latestTool.label !== headline ? latestTool : null;

  return (
    <div
      role="status"
      aria-live="polite"
      aria-label="Assistant progress"
      className={cn("flex min-w-0 flex-col gap-1 text-sm text-muted-foreground", className)}
    >
      <span className="flex min-w-0 items-center gap-2">
        <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin" aria-hidden />
        <span
          // Phase as animation key: a new phase re-runs the enter transition,
          // which is the only thing this component does with it.
          key={detail?.phase ?? "phase"}
          className="min-w-0 animate-message-enter truncate font-medium text-foreground"
        >
          {headline}
        </span>
        {elapsed && (
          <span className="shrink-0 text-xs tabular-nums text-muted-foreground/80">
            {elapsed}
          </span>
        )}
      </span>
      {tool && (
        <p className="flex min-w-0 items-center gap-1.5 text-xs leading-snug">
          <ToolIcon status={tool.status} />
          <span className="min-w-0 truncate">{tool.label}</span>
        </p>
      )}
    </div>
  );
}
