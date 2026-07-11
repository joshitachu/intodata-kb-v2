"use client";

import { useState } from "react";
import {
  AlertTriangle,
  Check,
  ChevronDown,
  ChevronRight,
  Circle,
  Loader2,
} from "lucide-react";
import type { HarnessActivityEvent } from "@/types";

const HIDDEN_DETAIL_KEY =
  /(authorization|credential|password|secret|token|api[_-]?key|raw|payload|prompt|reasoning|log)/i;

const HIDDEN_DETAIL_VALUE =
  /(bearer\s+[a-z0-9._-]+|sk-[a-z0-9_-]{12,}|api[_-]?key=|access[_-]?token=|password=|secret=|token=)/i;

function formatDetailValue(value: HarnessActivityEvent["safe_details"][string]) {
  const text = Array.isArray(value) ? value.join(", ") : String(value);
  return text.length > 160 ? `${text.slice(0, 157)}...` : text;
}

function safeDetailEntries(activity: HarnessActivityEvent) {
  return Object.entries(activity.safe_details ?? {}).filter(
    ([key, value]) => !HIDDEN_DETAIL_KEY.test(key) && isSafeDetailValue(value),
  );
}

function statusText(activity: HarnessActivityEvent) {
  return activity.label;
}

function isSafeDetailValue(value: HarnessActivityEvent["safe_details"][string]) {
  const values = Array.isArray(value) ? value : [value];
  return values.every((item) => {
    if (typeof item !== "string") return true;
    return !HIDDEN_DETAIL_VALUE.test(item);
  });
}

interface HarnessActivityStatusProps {
  activities: HarnessActivityEvent[];
}

export function HarnessActivityStatus({
  activities,
}: HarnessActivityStatusProps) {
  const [expanded, setExpanded] = useState(false);

  if (activities.length === 0) return null;

  const latest = activities.at(-1)!;
  const detailRows = activities.flatMap((activity, index) => {
    const rows = safeDetailEntries(activity);
    if (rows.length === 0 && !activity.summary) return [];
    return [{ activity, index, rows }];
  });
  const hasDetails = detailRows.length > 0;

  return (
    <div className="pb-1.5 text-xs text-muted-foreground">
      <button
        type="button"
        onClick={() => hasDetails && setExpanded((value) => !value)}
        className="flex items-center gap-1.5 text-left transition-colors hover:text-foreground disabled:hover:text-muted-foreground"
        disabled={!hasDetails}
        aria-expanded={expanded}
      >
        {hasDetails ? (
          expanded ? (
            <ChevronDown className="h-3 w-3 shrink-0" />
          ) : (
            <ChevronRight className="h-3 w-3 shrink-0" />
          )
        ) : null}
        {latest.status === "running" && (
          <Loader2 className="h-3 w-3 shrink-0 animate-spin" />
        )}
        {latest.status === "completed" && (
          <Check className="h-3 w-3 shrink-0 text-green-600" />
        )}
        {latest.status === "failed" && (
          <AlertTriangle className="h-3 w-3 shrink-0 text-amber-600" />
        )}
        {latest.status === "skipped" && (
          <Circle className="h-3 w-3 shrink-0" />
        )}
        <span>{statusText(latest)}</span>
        {activities.length > 1 && (
          <span className="text-muted-foreground/70">
            ({activities.length} updates)
          </span>
        )}
      </button>

      {expanded && hasDetails && (
        <div className="mt-1.5 space-y-1 border-l pl-3">
          {detailRows.map(({ activity, index, rows }) => (
            <div key={`${activity.event_type}-${index}`} className="space-y-0.5">
              <div className="font-medium text-muted-foreground">
                {activity.label}
              </div>
              {activity.summary && <div>{activity.summary}</div>}
              {rows.map(([key, value]) => (
                <div key={key} className="flex flex-wrap gap-1">
                  <span className="text-muted-foreground/75">{key}:</span>
                  <span>{formatDetailValue(value)}</span>
                </div>
              ))}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
