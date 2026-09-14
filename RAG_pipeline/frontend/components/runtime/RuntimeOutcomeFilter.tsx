"use client";

import {
  AlarmClockOff,
  AlertTriangle,
  CalendarX,
  CheckCircle2,
  HelpCircle,
  ListFilter,
  OctagonX,
  Play,
  type LucideIcon,
} from "lucide-react";
import { SelectMenu } from "@/components/ui/select-menu";
import { cn } from "@/lib/utils";
import type { RuntimeExecutionState } from "@/types";

// Shared outcome vocabulary for every runs table in the console (executions,
// job runs, plan runs, deployments). TMC reports six real execution outcomes;
// this is the one place that turns them into filter options and legible
// badges so every view agrees on the wording -- previously the filter lived
// only inside the Executions view (RuntimeConsole ~line 788) and covered four
// of the six.

/**
 * "Misfired" is the customer's own term for a scheduled run that never
 * started (their management asked for it by name) even though the badge
 * below reads "never started" -- the filter option is what gets typed into a
 * support ticket, the badge is what gets read on a screen.
 */
export const RUNTIME_OUTCOME_OPTIONS: Array<{ value: "" | RuntimeExecutionState; label: string }> = [
  { value: "", label: "All outcomes" },
  { value: "failed", label: "Failed" },
  { value: "misfired", label: "Misfired" },
  { value: "timed_out", label: "Timed out" },
  { value: "terminated", label: "Terminated" },
  { value: "success", label: "Succeeded" },
  { value: "running", label: "Running" },
  { value: "unknown", label: "Unknown" },
];

export function RuntimeOutcomeFilter({
  value,
  onChange,
  className,
  ariaLabel = "Outcome",
}: {
  value: "" | RuntimeExecutionState;
  onChange: (value: "" | RuntimeExecutionState) => void;
  className?: string;
  ariaLabel?: string;
}) {
  return (
    <SelectMenu
      value={value}
      onChange={(next) => onChange(next as "" | RuntimeExecutionState)}
      options={RUNTIME_OUTCOME_OPTIONS.map((option) => ({ value: option.value, label: option.label }))}
      placeholder="All outcomes"
      ariaLabel={ariaLabel}
      icon={ListFilter}
      variant="field"
      align="end"
      className={className}
    />
  );
}

// --- misfire reasons ---------------------------------------------------

const MISFIRE_REASON_LABELS: Record<string, string> = {
  PARALLEL_EXECUTION_FORBIDDEN: "previous run still busy",
  TASK_IS_PAUSED: "task is paused",
  MAX_DEPLOYMENT_ATTEMPTS_REACHED: "could not reach an engine",
  UNKNOWN: "unknown reason",
};

export function misfireReasonLabel(reason: string | null | undefined): string | null {
  if (!reason) return null;
  return MISFIRE_REASON_LABELS[reason] ?? reason.toLowerCase().replaceAll("_", " ");
}

// --- execution type ---------------------------------------------------------
// MANUAL / SCHEDULED / PLAN -- populated on every run record. This is the
// customer's "per view" axis: which trigger produced this run.

const EXECUTION_TYPE_LABELS: Record<string, string> = {
  MANUAL: "Manual",
  SCHEDULED: "Scheduled",
  PLAN: "Plan",
};

export function executionTypeLabel(value: string | null | undefined): string {
  if (!value) return "-";
  return EXECUTION_TYPE_LABELS[value] ?? value;
}

export function ExecutionTypeBadge({ value }: { value: string | null | undefined }) {
  if (!value) return <span className="text-muted-foreground">-</span>;
  return (
    <span className="inline-flex items-center rounded border border-border bg-muted/40 px-1.5 py-0.5 text-xs font-medium text-muted-foreground">
      {executionTypeLabel(value)}
    </span>
  );
}

// --- outcome badge -----------------------------------------------------------

type OutcomeTone = "neutral" | "good" | "warning" | "danger";

const OUTCOME_TONE_CLASSES: Record<OutcomeTone, string> = {
  neutral: "text-muted-foreground",
  good: "text-emerald-600 dark:text-emerald-400",
  warning: "text-amber-600 dark:text-amber-400",
  danger: "text-destructive",
};

interface OutcomeVisual {
  icon: LucideIcon;
  label: string;
  tone: OutcomeTone;
}

const OUTCOME_VISUALS: Record<string, OutcomeVisual> = {
  failed: { icon: AlertTriangle, label: "failed", tone: "danger" },
  // A misfire never started -- it must never read as a failure, which is why
  // both the icon and the wording are unrelated to the "failed" row.
  misfired: { icon: CalendarX, label: "never started", tone: "warning" },
  timed_out: { icon: AlarmClockOff, label: "timed out", tone: "warning" },
  terminated: { icon: OctagonX, label: "terminated", tone: "danger" },
  running: { icon: Play, label: "running", tone: "neutral" },
  success: { icon: CheckCircle2, label: "success", tone: "good" },
  unknown: { icon: HelpCircle, label: "unknown", tone: "neutral" },
};

/**
 * Outcome is state: icon + label, never colour alone. Shared by every runs
 * table (executions, job runs, plan runs) so a reader learns one vocabulary
 * instead of a different one per view.
 */
export function RuntimeOutcomeBadge({
  state,
  hasEnded = true,
  misfireReason,
}: {
  state: string | null | undefined;
  /** False when the record has no end time yet. Layered on top of whichever
   * base outcome the backend reported -- it never overrides a terminal state
   * like "misfired", which by definition has no end time either. */
  hasEnded?: boolean;
  misfireReason?: string | null;
}) {
  const key = state ?? "unknown";
  const visual: OutcomeVisual = OUTCOME_VISUALS[key] ?? { icon: HelpCircle, label: key, tone: "neutral" };
  const Icon = visual.icon;
  const unfinished = !hasEnded && key !== "running" && key !== "misfired";
  const label = unfinished ? `${visual.label} · no end` : visual.label;
  const tone: OutcomeTone = unfinished && visual.tone === "good" ? "warning" : visual.tone;
  const reason = key === "misfired" ? misfireReasonLabel(misfireReason) : null;

  return (
    <span className="inline-flex flex-col gap-0.5">
      <span className={cn("inline-flex items-center gap-1", OUTCOME_TONE_CLASSES[tone])}>
        <Icon className="h-3.5 w-3.5 shrink-0" aria-hidden />
        {label}
      </span>
      {reason && <span className="text-[11px] text-muted-foreground">{reason}</span>}
    </span>
  );
}
