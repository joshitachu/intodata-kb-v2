"use client";

import { AlertCircle, Loader2, Lock } from "lucide-react";
import { cn } from "@/lib/utils";
import { budgetConflictMessage, guidanceErrorMessage } from "@/hooks/useGuidance";
import type { GuidanceRule, GuidanceRuleCreateRequest } from "@/types/guidance";
import { AddRuleForm } from "./AddRuleForm";
import { RuleRow } from "./RuleRow";
import type { EditableKind } from "./kindLabels";

interface RulesFileEditorProps {
  /** What this file is, in the reader's words. */
  title: string;
  /** Who the file affects. The first thing said about it, and the only thing
   *  that differs between the two rule files. */
  affects: string;
  /** The kinds this file can hold, in the order the picker offers them. */
  kinds: readonly EditableKind[];
  /** What belongs in each kind, with one instance, shown under the picker. */
  hints: Partial<Record<EditableKind, string>>;
  /** Stable prefix for the form's input ids. */
  idPrefix: string;
  rules: GuidanceRule[];
  /** Entries a conversation suggested. Absent for the organization file. */
  proposed?: GuidanceRule[];
  isLoading: boolean;
  loadError: unknown;
  loadErrorFallback: string;
  /** The last write that failed: a refusal, a full level, or a lost request. */
  writeError: unknown;
  /** Why this user may not write here, or null when they may. */
  restriction: string | null;
  isAdding: boolean;
  onAdd: (draft: GuidanceRuleCreateRequest) => Promise<boolean>;
  onRetire: (ruleId: number) => void;
  onApprove?: (ruleId: number) => void;
  /** The single rule a write is in flight for, so only its control spins. */
  busyRuleId?: number;
  /** What to say when nothing is stored here yet. */
  emptyNote: string;
}

/**
 * Everything one rules file holds, edited where it is read.
 *
 * This is the whole "Edit the levels" region collapsed into the pane that was
 * already showing the file. Selecting `.nexus/org/RULES.md` shows its text and
 * this, directly under it, so reading what is in force and changing it are the
 * same act rather than two regions of the page that happen to describe each
 * other.
 *
 * One list, not one list per kind. Each row already carries what kind it is,
 * and four headed sub-lists over four short lists was the arrangement that made
 * a reader count boxes instead of reading rules. The taxonomy now lives in the
 * one place where it is a decision: the picker in the add form.
 *
 * A user who cannot edit here is told so, in place, with the reason, and the
 * controls stay visible and disabled -- a capability nobody can see is a
 * capability nobody asks for.
 */
export function RulesFileEditor({
  title,
  affects,
  kinds,
  hints,
  idPrefix,
  rules,
  proposed,
  isLoading,
  loadError,
  loadErrorFallback,
  writeError,
  restriction,
  isAdding,
  onAdd,
  onRetire,
  onApprove,
  busyRuleId,
  emptyNote,
}: RulesFileEditorProps) {
  const canManage = restriction === null;
  const waiting = proposed ?? [];

  return (
    <section
      data-testid="guidance-pane-editor"
      className="space-y-3 border-t border-border px-3 py-3"
    >
      <div>
        <h4 className="text-sm font-semibold text-foreground">{title}</h4>
        <p className="mt-0.5 text-xs text-muted-foreground">{affects}</p>
      </div>

      {restriction && (
        <p className="flex items-start gap-1.5 rounded-md border border-border bg-muted/50 px-2 py-1.5 text-[11px] text-muted-foreground">
          <Lock className="mt-0.5 h-3 w-3 shrink-0" aria-hidden />
          <span>
            <strong className="font-medium text-foreground">
              You can read this, but not change it.
            </strong>{" "}
            {restriction}
          </span>
        </p>
      )}

      {isLoading ? (
        <p className="flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" />
          Loading...
        </p>
      ) : loadError != null ? (
        <p className="text-sm text-destructive" role="alert">
          {guidanceErrorMessage(loadError, loadErrorFallback)}
        </p>
      ) : (
        <>
          {waiting.length > 0 && (
            <div className="space-y-1.5">
              <h5 className="text-xs font-semibold text-foreground">
                Waiting for your approval ({waiting.length})
              </h5>
              <p className="text-xs text-muted-foreground">
                These came out of conversations with the assistant. They change
                nothing until someone approves them.
              </p>
              <ul className="space-y-1.5">
                {waiting.map((rule) => (
                  <RuleRow
                    key={rule.id ?? `proposed-${rule.kind}-${rule.content ?? ""}`}
                    rule={rule}
                    canManage={canManage}
                    manageDisabledReason={restriction ?? ""}
                    onApprove={onApprove}
                    isBusy={busyRuleId !== undefined && busyRuleId === rule.id}
                  />
                ))}
              </ul>
            </div>
          )}

          <div className="space-y-1.5">
            <h5 className="text-xs font-semibold text-foreground">
              What is in this file now
            </h5>
            {rules.length === 0 ? (
              <p
                data-testid="pane-rules-empty"
                className="rounded-md border border-dashed border-border bg-muted/30 px-3 py-2 text-xs text-muted-foreground"
              >
                {emptyNote}
              </p>
            ) : (
              <ul className="space-y-1.5">
                {rules.map((rule) => (
                  <RuleRow
                    key={rule.id ?? `${rule.kind}-${rule.content ?? ""}`}
                    rule={rule}
                    canManage={canManage}
                    manageDisabledReason={restriction ?? ""}
                    onRetire={onRetire}
                    isBusy={busyRuleId !== undefined && busyRuleId === rule.id}
                  />
                ))}
              </ul>
            )}
          </div>
        </>
      )}

      <div className="space-y-2">
        <h5 className="text-xs font-semibold text-foreground">Add to this file</h5>
        <WriteFailureNotice error={writeError} />
        <AddRuleForm
          kinds={kinds}
          idPrefix={idPrefix}
          hints={hints}
          disabledReason={restriction}
          isPending={isAdding}
          onSubmit={onAdd}
        />
      </div>
    </section>
  );
}

/**
 * A write that was refused, with "this file is full" told apart from the rest.
 *
 * Running out of room is not a generic failure: the message names the level and
 * how far over it went, and the thing to do is stop using something before
 * adding more. Rendering it as "something went wrong" would leave the user
 * retrying a write that can never succeed. It sits beside the form that caused
 * it, because the refusal is about what was just typed.
 */
export function WriteFailureNotice({ error }: { error: unknown }) {
  if (error == null) return null;
  const budget = budgetConflictMessage(error);

  return (
    <p
      role="alert"
      className={cn(
        "flex items-start gap-2 rounded-md border px-3 py-2 text-xs",
        budget
          ? "border-amber-300 bg-amber-50 text-amber-900 dark:border-amber-900/60 dark:bg-amber-950/30 dark:text-amber-200"
          : "border-destructive/40 bg-destructive/5 text-destructive",
      )}
    >
      <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
      <span>
        {budget ? (
          <>
            <strong className="font-medium">No room left here.</strong> {budget} Stop
            using one of the entries above before adding another.
          </>
        ) : (
          guidanceErrorMessage(error, "The change was refused.")
        )}
      </span>
    </p>
  );
}
