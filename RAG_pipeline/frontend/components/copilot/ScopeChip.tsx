"use client";

import type { LucideIcon } from "lucide-react";
import { cn } from "@/lib/utils";

interface ScopeChipProps {
  /** Ordered path segments; empty/blank segments are dropped. */
  parts: readonly (string | undefined)[];
  /** Rendered between segments. "·" for the panel indicator, "›" for a trail. */
  separator?: string;
  icon?: LucideIcon;
  className?: string;
  /** Accessible name for the chip as a whole. */
  label?: string;
}

/**
 * One-line "where am I" chip. Shared by the copilot's scope indicator and the
 * per-message scope chip so a question asked on the canvas reads the same way
 * in both places.
 */
export function ScopeChip({
  parts,
  separator = "·",
  icon: Icon,
  className,
  label,
}: ScopeChipProps) {
  const segments = parts.filter((part): part is string => Boolean(part?.trim()));
  if (segments.length === 0) return null;

  return (
    <span
      className={cn(
        "inline-flex max-w-full items-center gap-1 rounded-full border bg-muted/40 px-2 py-0.5",
        "text-[11px] font-medium text-muted-foreground",
        className,
      )}
      aria-label={label}
      title={segments.join(` ${separator} `)}
    >
      {Icon && <Icon className="h-3 w-3 shrink-0 text-primary/70" aria-hidden />}
      {segments.map((segment, index) => (
        <span key={`${index}-${segment}`} className="inline-flex min-w-0 items-center gap-1">
          {index > 0 && (
            <span aria-hidden className="text-muted-foreground/50">
              {separator}
            </span>
          )}
          <span className={cn("truncate", index === segments.length - 1 && "text-foreground")}>
            {segment}
          </span>
        </span>
      ))}
    </span>
  );
}
