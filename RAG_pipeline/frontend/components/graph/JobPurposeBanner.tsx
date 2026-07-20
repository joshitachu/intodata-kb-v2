"use client";

import { useState } from "react";
import { ChevronUp, Info } from "lucide-react";
import { useJobDetail } from "@/hooks/useJobDetail";

interface JobPurposeBannerProps {
  projectId: string;
  jobName: string;
}

// Compact, collapsible overlay pinned top-left of the level-2 job component
// canvas. Surfaces the job's human-readable purpose/summary that the backend
// already computes (cached AI summary -> structured **Purpose** line ->
// paragraph scrape -> heuristic fallback), so the graph reads as more than a
// wall of component names.
//
// Anti-fabrication posture (mirrors the doc-generation validators): when the
// backend has nothing truthful to say it returns an empty summary, and this
// banner renders NOTHING rather than inventing filler. Loading and error are
// both silent - this is a nice-to-have overlay and must never block the graph
// or surface an alarming error over it. Reuses `useJobDetail`, the same
// (cached) fetch ComponentInspector/JobDetailPanel already share.
export function JobPurposeBanner({ projectId, jobName }: JobPurposeBannerProps) {
  const { data, isLoading, error } = useJobDetail(projectId, jobName);
  const [collapsed, setCollapsed] = useState(false);

  const summary = data?.summary?.trim() ?? "";

  // Silent on loading / error / empty summary: never fabricate, never alarm.
  if (isLoading || error || !summary) return null;

  if (collapsed) {
    return (
      <button
        type="button"
        onClick={() => setCollapsed(false)}
        className="absolute top-3 left-3 z-10 inline-flex items-center gap-1.5 rounded-full border bg-card/95 px-3 py-1.5 shadow-sm text-[11px] font-medium text-muted-foreground hover:bg-muted"
        title="Show job purpose"
        aria-label="Show job purpose"
        data-testid="job-purpose-banner-pill"
      >
        <Info className="h-3.5 w-3.5" />
        Purpose
      </button>
    );
  }

  return (
    <div
      className="absolute top-3 left-3 z-10 max-w-sm rounded-lg border bg-card/95 backdrop-blur-sm shadow-sm px-3 py-2"
      data-testid="job-purpose-banner"
    >
      <div className="flex items-start gap-2">
        <div className="min-w-0 space-y-0.5">
          <div className="flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
            <Info className="h-3 w-3 shrink-0" aria-hidden="true" />
            <span className="truncate">{jobName}</span>
          </div>
          <p
            className="text-xs leading-snug text-foreground"
            data-testid="job-purpose-text"
          >
            {summary}
          </p>
        </div>
        <button
          type="button"
          onClick={() => setCollapsed(true)}
          className="shrink-0 rounded p-1 text-muted-foreground hover:bg-muted hover:text-foreground"
          title="Collapse job purpose"
          aria-label="Collapse job purpose"
          data-testid="job-purpose-banner-collapse"
        >
          <ChevronUp className="h-3.5 w-3.5" />
        </button>
      </div>
    </div>
  );
}
