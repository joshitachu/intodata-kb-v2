"use client";

import { useId, useState } from "react";
import { Lock, MessageSquare, Network } from "lucide-react";
import { useChatContext } from "@/contexts/ChatContext";
import { cn } from "@/lib/utils";

export type ChatMode = "chat" | "deep-dive";

/**
 * Why Deep Dive is unavailable, in the two situations it can be.
 *
 * Only V2 honours a per-turn mode. V1 is the production LangGraph harness and
 * has no second evidence menu to widen -- `backend/src/api/routes/
 * conversations.py` never reads the field -- so a `deep_dive` turn sent there
 * was accepted, discarded, and answered exactly as a chat turn would be, with
 * no error and nothing in the answer to show for it. That silence cost
 * debugging time twice, which is why the toggle now refuses the choice instead
 * of forwarding it.
 *
 * Both sentences name the lane and the remedy, because "disabled" on its own
 * is the same failure in a quieter form: the user needs to know that the
 * setting exists, that this thread cannot have it, and what to do instead.
 */
const DEEP_DIVE_LOCK_REASON = {
  /** A thread is open and its lane is settled for life. */
  openThread:
    "Deep Dive needs V2. This thread answers on V1, which ignores it — start a new V2 chat to use Deep Dive.",
  /** No thread yet, so the lane is still the user's to pick. */
  newThread:
    "Deep Dive needs V2. Switch the answering engine above to V2 before starting this chat.",
} as const;

/**
 * Why Deep Dive cannot be used right now, or `null` when it can.
 *
 * Whether the lane honours a per-turn mode at all is decided by the provider
 * (`ChatContext.isDeepDiveAvailable`), which is also what clamps the mode it
 * actually sends -- so the control and the request can never disagree about
 * what is going to happen. This hook only turns that fact into the sentence
 * the user reads, and `isLaneLocked` picks which remedy is true: a settled
 * thread can only be replaced, an unstarted one can still change lane.
 */
export function useDeepDiveLockReason(): string | null {
  const { isDeepDiveAvailable, isLaneLocked } = useChatContext();
  if (isDeepDiveAvailable) return null;
  return isLaneLocked
    ? DEEP_DIVE_LOCK_REASON.openThread
    : DEEP_DIVE_LOCK_REASON.newThread;
}

interface ModeToggleProps {
  mode: ChatMode;
  onModeChange: (mode: ChatMode) => void;
  /**
   * Why Deep Dive is unavailable, or `null`/absent when it is available.
   *
   * A string rather than a boolean on purpose: there is no state in which the
   * option is refused without a reason to show for it.
   */
  deepDiveLockReason?: string | null;
}

const MODES = [
  { id: "chat" as const, label: "Chat", icon: MessageSquare },
  { id: "deep-dive" as const, label: "Deep Dive", icon: Network },
];

/**
 * Picks which evidence menu THIS turn runs against. Freely switchable
 * mid-thread on V2, which is the whole difference between this control and the
 * lane switch beside it: the lane is settled once, the mode is per turn.
 *
 * On V1 the Deep Dive option is refused rather than removed. Removing it would
 * hide a capability the product has; forwarding it would be the silent discard
 * this control exists to stop. `aria-disabled` rather than `disabled` is
 * deliberate -- a dead button explains nothing, and on a touch device there is
 * no hover to reveal a `title` -- so the click is still received and answered
 * with the reason. The padlock is the same signal the locked lane switch uses.
 */
export function ModeToggle({
  mode,
  onModeChange,
  deepDiveLockReason = null,
}: ModeToggleProps) {
  const reasonId = useId();
  // The reason the user has asked to see, held as the reason itself rather than
  // a flag: if the lane changes underneath, the stale sentence disappears with
  // it instead of being shown against a lock that no longer applies.
  const [revealedReason, setRevealedReason] = useState<string | null>(null);
  const reasonIsVisible =
    deepDiveLockReason !== null && revealedReason === deepDiveLockReason;
  // A locked option cannot also be the selected one. A caller still holding
  // "deep-dive" from before the lane was settled must not see Deep Dive lit up
  // while every turn it sends runs as chat.
  const effectiveMode: ChatMode = deepDiveLockReason ? "chat" : mode;

  return (
    <div className="flex min-w-0 flex-wrap items-center gap-1.5">
      <div
        role="radiogroup"
        aria-label="Chat mode"
        className="inline-flex h-7 shrink-0 items-center gap-0.5 rounded-full border border-border bg-muted/40 p-0.5"
        data-tour="mode-toggle"
      >
        {MODES.map(({ id, label, icon: Icon }) => {
          const lockReason = id === "deep-dive" ? deepDiveLockReason : null;
          const isLocked = lockReason !== null;
          const isActive = effectiveMode === id;
          const OptionIcon = isLocked ? Lock : Icon;
          return (
            <button
              key={id}
              type="button"
              role="radio"
              aria-checked={isActive}
              aria-disabled={isLocked || undefined}
              // The reason is the option's *description*, never its name: a
              // name that grew a sentence would stop matching the option it
              // names, for a screen reader and for anything else that looks
              // controls up by label.
              aria-describedby={isLocked ? reasonId : undefined}
              data-mode={id}
              data-locked={isLocked || undefined}
              title={lockReason ?? undefined}
              onClick={() => {
                if (lockReason) {
                  setRevealedReason(lockReason);
                  return;
                }
                setRevealedReason(null);
                onModeChange(id);
              }}
              className={cn(
                "inline-flex h-6 items-center gap-1.5 whitespace-nowrap rounded-full px-2.5",
                "text-xs font-medium transition-colors",
                isLocked
                  ? "cursor-not-allowed text-muted-foreground/60"
                  : isActive
                    ? "bg-background text-foreground shadow-elevation-xs"
                    : "text-muted-foreground hover:text-foreground",
              )}
            >
              <OptionIcon className="h-3.5 w-3.5" aria-hidden />
              {label}
            </button>
          );
        })}
      </div>
      {/* Present for as long as the lock is, so the option always has a
          description to point at; shown in the row once the user has asked,
          which is the only channel a touch device has -- it never sees the
          `title`. */}
      {deepDiveLockReason !== null && (
        <span
          id={reasonId}
          data-testid="mode-toggle-lock-reason"
          data-revealed={reasonIsVisible || undefined}
          className={cn(
            "min-w-0 text-[11px] leading-snug text-muted-foreground",
            !reasonIsVisible && "sr-only",
          )}
        >
          {deepDiveLockReason}
        </span>
      )}
    </div>
  );
}

/**
 * The composer's mode control, with the lane wired in from context.
 *
 * Split the same way `LaneSwitch` / `ComposerLaneControls` is split: the
 * presentational half stays testable without a provider, and exactly one place
 * knows how the lane reaches this control.
 */
export function ComposerModeToggle({
  mode,
  onModeChange,
}: Pick<ModeToggleProps, "mode" | "onModeChange">) {
  const deepDiveLockReason = useDeepDiveLockReason();

  return (
    <ModeToggle
      mode={mode}
      onModeChange={onModeChange}
      deepDiveLockReason={deepDiveLockReason}
    />
  );
}
