"use client";

import { cn } from "@/lib/utils";
import {
  LANE_FILTERS,
  LANE_FILTER_LABEL,
  type LaneFilter,
} from "./lane";

interface LaneFilterControlProps {
  value: LaneFilter;
  onChange: (filter: LaneFilter) => void;
  className?: string;
}

/**
 * Narrows the thread list to one lane.
 *
 * It filters what is *shown*, never what is loaded: `ChatProvider` keeps the
 * full cross-lane list because the URL recovery in `ChatLayout` asks it whether
 * an id exists, and answering that through the filter would bounce an open
 * thread to `/?c=new` the moment the user narrowed the list past it.
 */
export function LaneFilterControl({
  value,
  onChange,
  className,
}: LaneFilterControlProps) {
  return (
    <div
      role="radiogroup"
      aria-label="Filter conversations by lane"
      className={cn(
        "inline-flex items-center gap-0.5 rounded-full border border-border bg-muted/40 p-0.5",
        className,
      )}
    >
      {LANE_FILTERS.map((option) => {
        const isActive = option === value;
        return (
          <button
            key={option}
            type="button"
            role="radio"
            aria-checked={isActive}
            onClick={() => onChange(option)}
            className={cn(
              "inline-flex h-5 flex-1 items-center justify-center whitespace-nowrap rounded-full px-2",
              "text-[11px] font-medium transition-colors",
              isActive
                ? "bg-background text-foreground shadow-elevation-xs"
                : "text-muted-foreground hover:text-foreground",
            )}
          >
            {LANE_FILTER_LABEL[option]}
          </button>
        );
      })}
    </div>
  );
}
