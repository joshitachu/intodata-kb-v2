"use client";

import { ChevronLeft, ChevronRight } from "lucide-react";
import type { TopologyLevel } from "@/hooks/useTopologyNavigation";

interface TopologyBreadcrumbProps {
  projectLabel: string;
  stack: TopologyLevel[];
  onNavigate: (index: number) => void;
}

function levelLabel(level: TopologyLevel, projectLabel: string): string {
  switch (level.kind) {
    case "project":
      return projectLabel;
    case "integration":
      // IDs are intentionally opaque. The detail canvas renders a human label
      // from the authoritative landscape record; this crumb simply names its
      // level without exposing or reconstructing that identity.
      return "Integration";
    case "group":
      return level.groupName.split("/").filter(Boolean).at(-1) || level.groupName;
    case "job":
      return level.jobName;
    case "component":
      return level.componentName;
  }
}

export function TopologyBreadcrumb({
  projectLabel,
  stack,
  onNavigate,
}: TopologyBreadcrumbProps) {
  return (
    <nav
      aria-label="Topology breadcrumb"
      className="flex items-center gap-1 min-w-0 text-xs"
    >
      {stack.length > 1 && (
        <button
          type="button"
          onClick={() => onNavigate(stack.length - 2)}
          className="mr-1 flex shrink-0 items-center gap-1 rounded-md border border-slate-200 bg-white px-2 py-1 font-semibold text-slate-600 shadow-sm transition-colors hover:bg-slate-50 hover:text-slate-900"
          aria-label={`Back to ${levelLabel(stack[stack.length - 2], projectLabel)}`}
          title={`Back to ${levelLabel(stack[stack.length - 2], projectLabel)}`}
        >
          <ChevronLeft className="h-3.5 w-3.5" />
          Back
        </button>
      )}
      {stack.map((level, index) => {
        const isLast = index === stack.length - 1;
        const label = levelLabel(level, projectLabel);
        return (
          <span key={`${level.kind}:${index}`} className="flex items-center gap-1 min-w-0">
            {index > 0 && (
              <ChevronRight className="h-3 w-3 shrink-0 text-muted-foreground" />
            )}
            {isLast ? (
              <span
                className="font-semibold text-foreground truncate"
                aria-current="page"
                title={label}
              >
                {label}
              </span>
            ) : (
              <button
                type="button"
                onClick={() => onNavigate(index)}
                className="text-muted-foreground hover:text-foreground hover:underline truncate"
                title={label}
              >
                {label}
              </button>
            )}
          </span>
        );
      })}
    </nav>
  );
}
