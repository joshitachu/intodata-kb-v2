"use client";

import { cn } from "@/lib/utils";

/**
 * The one distinction this whole feature turns on, in words a reader can act on.
 *
 * Some text is put in front of the assistant before *every* question, and some
 * text only gets opened when a question needs it. That is the cost model, and
 * it is the difference between a rule that is always in force and a file that
 * is merely available. Saying it as "always loaded" / "read on demand" names
 * the mechanism; saying it as "sent with every question" / "opened only when
 * needed" names the consequence, which is the thing the reader is deciding
 * about.
 *
 * The strings live here rather than in each component so the tree, the level
 * headers and the file viewer cannot drift into three different vocabularies
 * for the same fact -- which is exactly how the panel became unreadable.
 */

/** Full wording. Used wherever there is room for a sentence fragment. */
export const ALWAYS_LOADED_LABEL = "Sent with every question";
export const ON_DEMAND_LABEL = "Opened only when needed";

/** Tight wording for a narrow row, where the long form would crowd the name. */
export const ALWAYS_LOADED_SHORT = "Every question";
export const ON_DEMAND_SHORT = "Only when needed";

/** The consequence, offered on hover and in the file viewer. */
export const ALWAYS_LOADED_DETAIL =
  "Re-read before every single answer, so it takes up room in every question. Keep it short.";
export const ON_DEMAND_DETAIL =
  "Costs nothing until a question actually needs it, so length matters far less here.";

export function deliveryLabel(alwaysLoaded: boolean): string {
  return alwaysLoaded ? ALWAYS_LOADED_LABEL : ON_DEMAND_LABEL;
}

export function deliveryDetail(alwaysLoaded: boolean): string {
  return alwaysLoaded ? ALWAYS_LOADED_DETAIL : ON_DEMAND_DETAIL;
}

interface DeliveryChipProps {
  alwaysLoaded: boolean;
  /** Use the tight wording, for rows where the full label would not fit. */
  short?: boolean;
  className?: string;
}

/**
 * The badge that carries the distinction.
 *
 * Colour is never the only carrier: the words say it, the colour reinforces it.
 * A greyscale screenshot of this panel still has to be readable, because that
 * is how it gets pasted into a ticket.
 */
export function DeliveryChip({ alwaysLoaded, short = false, className }: DeliveryChipProps) {
  const text = alwaysLoaded
    ? short
      ? ALWAYS_LOADED_SHORT
      : ALWAYS_LOADED_LABEL
    : short
      ? ON_DEMAND_SHORT
      : ON_DEMAND_LABEL;

  return (
    <span
      title={deliveryDetail(alwaysLoaded)}
      className={cn(
        "inline-flex shrink-0 items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] leading-tight",
        alwaysLoaded
          ? "border-amber-300 bg-amber-50 text-amber-900 dark:border-amber-900/60 dark:bg-amber-950/30 dark:text-amber-200"
          : "border-border bg-muted/60 text-muted-foreground",
        className,
      )}
    >
      <span
        aria-hidden
        className={cn(
          "h-1.5 w-1.5 rounded-full",
          alwaysLoaded ? "bg-amber-500" : "border border-muted-foreground/60",
        )}
      />
      {text}
    </span>
  );
}
