"use client";

import { Check, Loader2, Undo2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import type { GuidanceRule } from "@/types/guidance";
import { KIND_ROW_LABELS } from "./kindLabels";
import { isLearned, ruleSummary } from "./ruleSummary";

interface RuleRowProps {
  rule: GuidanceRule;
  /** Whether this user may change this level. */
  canManage: boolean;
  /** Why not, shown on the disabled control. Required when `canManage` is false. */
  manageDisabledReason: string;
  onApprove?: (ruleId: number) => void;
  onRetire?: (ruleId: number) => void;
  isBusy?: boolean;
}

/**
 * One entry, and whether it is actually in force.
 *
 * A suggested entry is rendered distinctly rather than mixed into the list: it
 * came out of a conversation, it is *not* being applied, and a reader who
 * cannot tell the two apart will mis-explain the assistant's behaviour in both
 * directions. Approving it is what puts it into effect -- and what starts it
 * taking up room in every question, which is why approval can itself be
 * refused when the level is full.
 */
export function RuleRow({
  rule,
  canManage,
  manageDisabledReason,
  onApprove,
  onRetire,
  isBusy = false,
}: RuleRowProps) {
  const proposed = rule.status === "proposed";
  const hasId = rule.id !== null;

  return (
    <li
      data-testid={`guidance-rule-${rule.id ?? "new"}`}
      data-status={rule.status}
      className={cn(
        "flex items-start gap-2 rounded-md border px-3 py-2 text-sm",
        proposed
          ? "border-amber-300 bg-amber-50/60 dark:border-amber-900/60 dark:bg-amber-950/20"
          : "border-border bg-card",
      )}
    >
      <div className="min-w-0 flex-1">
        <p className="break-words text-foreground">{ruleSummary(rule)}</p>
        <div className="mt-1 flex flex-wrap items-center gap-1.5">
          <Badge variant="muted">{KIND_ROW_LABELS[rule.kind]}</Badge>
          {proposed && <Badge variant="warning">Waiting for approval</Badge>}
          {isLearned(rule) && <Badge variant="muted">Suggested from a conversation</Badge>}
        </div>
        {proposed && (
          <p className="mt-1 text-xs text-amber-700 dark:text-amber-400">
            Not being used yet. Approve it to apply it to everyone on this project.
          </p>
        )}
      </div>

      <div className="flex shrink-0 items-center gap-1">
        {proposed && onApprove && (
          <button
            type="button"
            onClick={() => hasId && onApprove(rule.id as number)}
            disabled={!canManage || !hasId || isBusy}
            title={
              canManage
                ? "Start applying this to everyone on this project"
                : manageDisabledReason
            }
            aria-label={`Approve rule ${rule.id ?? ""}`}
            className={cn(
              "inline-flex h-7 items-center gap-1 rounded-md border border-border px-2",
              "text-xs font-medium transition-colors",
              "hover:bg-muted disabled:cursor-not-allowed disabled:opacity-50",
            )}
          >
            {isBusy ? <Loader2 className="h-3 w-3 animate-spin" /> : <Check className="h-3 w-3" />}
            Approve
          </button>
        )}

        {!proposed && onRetire && (
          <button
            type="button"
            onClick={() => hasId && onRetire(rule.id as number)}
            disabled={!canManage || !hasId || isBusy}
            title={
              canManage
                ? "Stop using this. It stops affecting answers; the record of it is kept."
                : manageDisabledReason
            }
            aria-label={`Retire rule ${rule.id ?? ""}`}
            className={cn(
              "inline-flex h-7 items-center gap-1 rounded-md border border-border px-2",
              "text-xs font-medium text-muted-foreground transition-colors",
              "hover:bg-muted hover:text-foreground",
              "disabled:cursor-not-allowed disabled:opacity-50",
            )}
          >
            {isBusy ? <Loader2 className="h-3 w-3 animate-spin" /> : <Undo2 className="h-3 w-3" />}
            Stop using
          </button>
        )}
      </div>
    </li>
  );
}
