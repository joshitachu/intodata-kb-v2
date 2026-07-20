"use client";

import { ChevronRight } from "lucide-react";
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
