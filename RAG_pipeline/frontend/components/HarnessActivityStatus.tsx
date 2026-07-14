"use client";

import { useEffect, useId, useMemo, useRef, useState } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Circle,
  Loader2,
} from "lucide-react";
import type { HarnessActivityEvent } from "@/types";

const HIDDEN_DETAIL_KEY =
  /(authorization|credential|password|secret|token|api[_-]?key|raw|payload|prompt|reasoning|log|capability|provider|tool(?:_|$)|runtime[_-]?id)/i;
const HIDDEN_DETAIL_VALUE =
  /(bearer\s+[a-z0-9._-]+|sk-[a-z0-9_-]{12,}|api[_-]?key=|access[_-]?token=|password=|secret=|token=)/i;

type DetailValue = HarnessActivityEvent["safe_details"][string];
type DetailCategory = "Systems" | "Searches" | "Evidence" | "Retries" | "Artifacts" | "Details";

function isSafeDetailValue(value: DetailValue) {
  const values = Array.isArray(value) ? value : [value];
  return values.every((item) => typeof item !== "string" || !HIDDEN_DETAIL_VALUE.test(item));
}

function safeDetailEntries(activity: HarnessActivityEvent) {
  return Object.entries(activity.safe_details ?? {}).filter(
    ([key, value]) => !HIDDEN_DETAIL_KEY.test(key) && isSafeDetailValue(value),
  );
}

function categoryFor(key: string): DetailCategory {
  const normalized = key.toLowerCase();
  if (/(system|connector|service|source)/.test(normalized)) return "Systems";
  if (/(search|query|lookup)/.test(normalized)) return "Searches";
  if (/(evidence|document|citation|result)/.test(normalized)) return "Evidence";
  if (/(retry|attempt|warning)/.test(normalized)) return "Retries";
  if (/(artifact|file|runbook)/.test(normalized)) return "Artifacts";
  return "Details";
}

function formatDetailKey(key: string) {
  return key.replace(/[_-]+/g, " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function formatDetailValue(value: DetailValue) {
  const text = Array.isArray(value) ? value.join(", ") : String(value);
  return text.length > 160 ? `${text.slice(0, 157)}...` : text;
}

function resultCount(activities: HarnessActivityEvent[]) {
  let count = 0;
  for (const activity of activities) {
    for (const [key, value] of safeDetailEntries(activity)) {
      if (!/(count|results?|evidence|documents?|artifacts?)/i.test(key)) continue;
      if (typeof value === "number") count += value;
      else if (Array.isArray(value)) count += value.length;
    }
  }
  return count;
}

function activityStart(activities: HarnessActivityEvent[], fallback: number) {
  const timestamps = activities
    .map((activity) => activity.timestamp ? Date.parse(activity.timestamp) : Number.NaN)
    .filter(Number.isFinite);
  return timestamps.length > 0 ? Math.min(...timestamps) : fallback;
}

function formatElapsed(milliseconds: number) {
  const seconds = Math.max(0, Math.floor(milliseconds / 1000));
  return seconds < 60 ? `${seconds}s` : `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
}

function StatusIcon({ status }: { status: HarnessActivityEvent["status"] }) {
  if (status === "running") return <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin" />;
  if (status === "completed") return <CheckCircle2 className="h-3.5 w-3.5 shrink-0 text-emerald-600" />;
  if (status === "failed") return <AlertTriangle className="h-3.5 w-3.5 shrink-0 text-amber-600" />;
  return <Circle className="h-3.5 w-3.5 shrink-0" />;
}

function statusText(status: HarnessActivityEvent["status"]) {
  return { running: "In progress", completed: "Complete", failed: "Needs attention", skipped: "Skipped" }[status];
}

interface HarnessActivityStatusProps {
  activities: HarnessActivityEvent[];
}

export function HarnessActivityStatus({ activities }: HarnessActivityStatusProps) {
  const detailsId = useId();
  const [expanded, setExpanded] = useState(false);
  const [now, setNow] = useState(() => Date.now());
  const firstSeenAt = useRef(Date.now());
  const latest = activities.at(-1);
  const running = activities.some((activity) => activity.status === "running");

  useEffect(() => {
    if (!running) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1_000);
    return () => window.clearInterval(timer);
  }, [running]);

  const details = useMemo(() => activities.map((activity) => ({
    activity,
    entries: safeDetailEntries(activity),
  })), [activities]);

  if (!latest) return null;

  const count = resultCount(activities);
  const elapsed = formatElapsed(now - activityStart(activities, firstSeenAt.current));
  const actionCount = activities.length;

  return (
    <section className="pb-1.5 text-xs text-muted-foreground" aria-label="Workflow activity">
      <button
        type="button"
        onClick={() => setExpanded((value) => !value)}
        className="flex min-h-8 w-full items-center gap-1.5 text-left transition-colors hover:text-foreground"
        aria-expanded={expanded}
        aria-controls={detailsId}
      >
        {expanded ? <ChevronDown className="h-3.5 w-3.5 shrink-0" /> : <ChevronRight className="h-3.5 w-3.5 shrink-0" />}
        <StatusIcon status={latest.status} />
        <span className="min-w-0 flex-1 truncate font-medium text-foreground">{latest.label}</span>
        <span className="shrink-0">{statusText(latest.status)}</span>
        <span className="shrink-0 text-muted-foreground/70">{elapsed}</span>
        <span className="shrink-0 text-muted-foreground/70">
          {count > 0 ? `${count} ${count === 1 ? "result" : "results"}` : `${actionCount} ${actionCount === 1 ? "action" : "actions"}`}
        </span>
      </button>

      {expanded && (
        <div id={detailsId} className="mt-1.5 space-y-3 border-l pl-3">
          {details.map(({ activity, entries }, index) => {
            const byCategory = entries.reduce<Partial<Record<DetailCategory, [string, DetailValue][]>>>((groups, entry) => {
              const category = categoryFor(entry[0]);
              (groups[category] ??= []).push(entry);
              return groups;
            }, {});
            return (
              <div key={`${activity.workflow_run_id ?? "stream"}-${activity.event_type}-${activity.label}-${index}`} className="space-y-1">
                <div className="flex items-center gap-1.5 font-medium text-foreground">
                  <StatusIcon status={activity.status} />
                  <span>{activity.label}</span>
                </div>
                {activity.summary && <p>{activity.summary}</p>}
                {Object.entries(byCategory).map(([category, rows]) => (
                  <div key={category} className="space-y-0.5">
                    <div className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground/75">{category}</div>
                    {rows!.map(([key, value]) => (
                      <div key={key} className="flex flex-wrap gap-x-1.5">
                        <span className="text-muted-foreground/75">{formatDetailKey(key)}</span>
                        <span>{formatDetailValue(value)}</span>
                      </div>
                    ))}
                  </div>
                ))}
              </div>
            );
          })}
        </div>
      )}
    </section>
  );
}
