"use client";

import { cn } from "@/lib/utils";
import { useRuntimeStatus } from "@/hooks/useRuntimeStatus";

const DOT_CLASS_BY_STATUS = {
  disabled: "bg-muted-foreground/40",
  unavailable: "bg-amber-500",
  connected: "bg-green-500",
  unknown: "bg-muted-foreground/40",
} as const;

export function RuntimeStatusIndicator() {
  const { data, isLoading, isError } = useRuntimeStatus();

  let dotClass: string = DOT_CLASS_BY_STATUS.unknown;
  let label = "Live runtime status unknown";

  if (isLoading) {
    label = "Checking live runtime...";
  } else if (isError || !data) {
    label = "Live runtime status unknown";
  } else {
    dotClass = DOT_CLASS_BY_STATUS[data.status];
    label = data.message;
  }

  return (
    <div
      className="inline-flex h-7 shrink-0 items-center gap-1.5 rounded-full px-2 text-xs text-muted-foreground whitespace-nowrap"
      title={label}
    >
      <span className={cn("h-1.5 w-1.5 rounded-full shrink-0", dotClass)} />
      <span className="truncate max-w-[10rem]">{label}</span>
    </div>
  );
}
