"use client";

import { Crosshair, CornerUpLeft, MoveRight } from "lucide-react";
import { cn } from "@/lib/utils";
import type { AutoMovementKind } from "@/lib/topologyFollow";

export interface AutoNavigationBannerProps {
  /** The place the view moved to, e.g. "the Quintiq to Maximo integration". */
  label: string;
  /** Short level description, shown after the label when it adds anything. */
  detail?: string;
  /** Puts the previous view back in one action. */
  onUndo: () => void;
  reason?: "explicit" | "follow";
  /**
   * What actually happened to the view. Defaults to `navigated` so an
   * un-migrated caller still gets the strongest (and previously only) claim
   * rather than silently understating a real jump.
   */
  movement?: AutoMovementKind;
  className?: string;
}

/**
 * The verb has to match what the canvas did.
 *
 * "Moved to AFAS" while the landscape is still on screen and AFAS is one of two
 * highlighted boxes is not a small wording problem: the user looks for a view
 * that changed, finds the same view, and stops trusting the banner. `focused`
 * therefore says so plainly, and only a real navigation-stack change is allowed
 * to claim movement.
 */
const MOVEMENT_VERB: Record<AutoMovementKind, string> = {
  focused: "Focused on",
  opened: "Opened",
  navigated: "Moved to",
};

/**
 * Announces a view change the ANSWER made, and offers it back.
 *
 * An answer is allowed to move the canvas only when the user asked to be taken
 * somewhere, and even then being teleported silently is disorienting -- you
 * lose track of whether the diagram changed or you misread it. So the move is
 * stated, attributed to the request that caused it, and reversible in one
 * click. `role="status"` (not `alert`) because this follows an action the user
 * initiated; it should be announced, not interrupt.
 *
 * Undo restores the previous STACK rather than ascending one level: a component
 * jump grows the stack by two, and a single step back would leave the user on
 * a job they never asked for.
 */
export function AutoNavigationBanner({
  label,
  detail,
  onUndo,
  reason = "explicit",
  movement = "navigated",
  className,
}: AutoNavigationBannerProps) {
  const Icon = movement === "focused" ? Crosshair : MoveRight;
  return (
    <div
      role="status"
      data-testid="auto-navigation-banner"
      data-movement={movement}
      className={cn(
        "absolute inset-x-3 top-3 z-30 flex items-center gap-2 rounded-lg border border-primary/40",
        "bg-primary/[0.07] px-3 py-2 text-xs text-foreground shadow-sm backdrop-blur-sm",
        className,
      )}
    >
      <Icon className="h-3.5 w-3.5 shrink-0 text-primary" aria-hidden />
      <p className="min-w-0 flex-1 leading-snug">
        <span className="font-semibold">
          {MOVEMENT_VERB[movement]} {label}
        </span>
        {detail && <span className="text-muted-foreground"> · {detail}</span>}
        <span className="text-muted-foreground">
          {reason === "follow"
            ? " to follow the conversation."
            : " because you asked to be taken there."}
        </span>
      </p>
      <button
        type="button"
        onClick={onUndo}
        className="inline-flex shrink-0 items-center gap-1 rounded-md border border-primary/30 bg-background/70 px-2 py-1 font-medium transition-colors hover:bg-muted"
      >
        <CornerUpLeft className="h-3 w-3" aria-hidden />
        Undo
      </button>
    </div>
  );
}
