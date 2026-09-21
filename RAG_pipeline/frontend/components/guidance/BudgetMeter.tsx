"use client";

import { cn } from "@/lib/utils";

interface BudgetMeterProps {
  /** Human name of the level, used in the accessible label. */
  layerLabel: string;
  /** Tokens this level currently renders to, or null when unknown. */
  used: number | null;
  /** The ceiling enforced on write. */
  ceiling: number;
}

/**
 * How much room is left here, in words before numbers.
 *
 * Everything at a level is re-read before every answer, so each level has a
 * hard size limit and a write that would breach it is refused. The point of
 * showing it is to turn that refusal into something the user saw coming -- so
 * the primary line is "almost full" or "room to spare", and the token counts
 * are a smaller second line for whoever wants them.
 *
 * Nothing renders when nothing is stored or nothing is known. A bar drawn at
 * zero beside a ceiling was the old behaviour and it was worse than silence: it
 * looked like a measurement, invited the reader to compare it to something, and
 * said nothing either way.
 */
export function BudgetMeter({ layerLabel, used, ceiling }: BudgetMeterProps) {
  if (used === null || used <= 0 || ceiling <= 0) return null;

  const ratio = Math.min(used / ceiling, 1);
  const percentLeft = Math.max(0, Math.round((1 - used / ceiling) * 100));
  const full = used >= ceiling;
  const tight = !full && ratio >= 0.8;

  const sentence = full
    ? "Full - retire something here before adding more"
    : tight
      ? `Almost full - about ${percentLeft}% of the space left`
      : `Room to spare - about ${percentLeft}% of the space left`;

  return (
    <div className="flex flex-col items-end gap-1 text-[11px]">
      <span
        className="h-1.5 w-24 overflow-hidden rounded-full bg-muted"
        role="img"
        aria-label={`${layerLabel}: ${sentence}`}
      >
        <span
          className={cn(
            "block h-full rounded-full transition-[width]",
            full ? "bg-destructive" : tight ? "bg-amber-500" : "bg-primary/60",
          )}
          style={{ width: `${Math.round(ratio * 100)}%` }}
        />
      </span>
      <span className={cn("text-right", full ? "text-destructive" : "text-muted-foreground")}>
        {sentence}
      </span>
      <span className="text-right text-muted-foreground/70">
        {used} of {ceiling} tokens
      </span>
    </div>
  );
}
