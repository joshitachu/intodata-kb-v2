"use client";

import { Crosshair, Slash } from "lucide-react";
import { cn } from "@/lib/utils";
import type { CopilotTarget } from "@/types/copilot";

interface TargetChipsProps {
  targets: readonly CopilotTarget[];
  /** Section heading, e.g. "On the diagram" or "Found so far". */
  heading: string;
  onTargetHover?: (target: CopilotTarget | null) => void;
  onTargetClick?: (target: CopilotTarget) => void;
  /** Live (streaming) chips read as provisional. */
  provisional?: boolean;
  /** Keep dense page references to one quiet, horizontally scrollable row. */
  compact?: boolean;
}

/**
 * The places an answer refers to. Clicking navigates the page; hovering
 * re-highlights just that one. An unresolvable target renders as a non-button
 * so it cannot be clicked, focused or activated by keyboard -- it would
 * navigate into an empty canvas.
 */
export function TargetChips({
  targets,
  heading,
  onTargetHover,
  onTargetClick,
  provisional,
  compact = false,
}: TargetChipsProps) {
  if (targets.length === 0) return null;

  return (
    <section
      className={cn(
        "border-t px-3",
        compact ? "py-1.5" : "py-2",
        provisional && "opacity-90",
      )}
      aria-label={heading}
    >
      <div className={cn(compact && "flex min-w-0 items-center gap-2")}>
        <p className={cn(
          "flex shrink-0 items-center gap-1 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground/65",
          !compact && "mb-1.5",
        )}>
          <Crosshair className="h-3 w-3" aria-hidden />
          {heading}
        </p>
        <div className={cn(
          "flex gap-1.5",
          compact ? "min-w-0 flex-1 overflow-x-auto py-0.5" : "flex-wrap",
        )}>
        {targets.map((target) =>
          target.resolvable ? (
            <button
              key={target.key}
              type="button"
              data-target-key={target.key}
              onMouseEnter={() => onTargetHover?.(target)}
              onMouseLeave={() => onTargetHover?.(null)}
              onFocus={() => onTargetHover?.(target)}
              onBlur={() => onTargetHover?.(null)}
              onClick={() => onTargetClick?.(target)}
              title={target.detail}
              className={cn(
                "inline-flex max-w-full shrink-0 items-center gap-1 rounded-full border font-medium",
                compact ? "px-1.5 py-0.5 text-[10px]" : "px-2 py-1 text-[11px]",
                "border-primary/40 bg-primary/[0.06] text-foreground transition-colors",
                "hover:border-primary hover:bg-primary/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/50",
              )}
            >
              <span className="truncate">{target.label}</span>
              {target.detail && (
                <span className="shrink-0 text-muted-foreground/80">{target.detail}</span>
              )}
            </button>
          ) : (
            <span
              key={target.key}
              data-target-key={target.key}
              data-inert="true"
              aria-disabled="true"
              title={target.unresolvableReason}
              className={cn(
                "inline-flex max-w-full shrink-0 cursor-not-allowed items-center gap-1 rounded-full border border-dashed border-muted-foreground/40 text-muted-foreground/70",
                compact ? "px-1.5 py-0.5 text-[10px]" : "px-2 py-1 text-[11px]",
              )}
            >
              <Slash className="h-3 w-3 shrink-0" aria-hidden />
              <span className="truncate line-through decoration-muted-foreground/40">
                {target.label}
              </span>
              <span className="sr-only">
                {target.unresolvableReason ?? "Not available on this page."}
              </span>
            </span>
          ),
        )}
        </div>
      </div>
    </section>
  );
}
