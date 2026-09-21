"use client";

import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import { LANE_DESCRIPTION, LANE_LABEL } from "./lane";
import type { ChatLane } from "@/types";

interface LaneBadgeProps {
  lane: ChatLane;
  className?: string;
}

/**
 * The lane marker on a thread-list row.
 *
 * Both lanes are badged, not just V2. A badge that only appeared on the
 * exceptional row would read as a warning; the lane is a property every thread
 * has, and the user needs to be able to tell which runtime answered a thread
 * they are about to reopen -- it cannot be changed once they are in it.
 */
export function LaneBadge({ lane, className }: LaneBadgeProps) {
  return (
    <Badge
      variant={lane === "v2" ? "default" : "muted"}
      title={LANE_DESCRIPTION[lane]}
      aria-label={LANE_DESCRIPTION[lane]}
      className={cn("shrink-0 px-1.5 py-0 font-mono leading-4", className)}
    >
      {LANE_LABEL[lane]}
    </Badge>
  );
}
