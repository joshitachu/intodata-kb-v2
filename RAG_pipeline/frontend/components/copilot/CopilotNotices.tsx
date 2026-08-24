"use client";

import { AlertTriangle, CloudOff, Info } from "lucide-react";
import { cn } from "@/lib/utils";
import type { CopilotNotice, CopilotNoticeKind } from "@/types/copilot";

interface NoticePreset {
  title: string;
  detail: string;
  tone: "warning" | "error";
  icon: typeof Info;
}

/**
 * Default copy per degraded condition. These are first-class panel states,
 * not error handling: each one tells the user exactly which part of the answer
 * is weaker and what would fix it, and none of them implies the page itself
 * is broken.
 */
const PRESETS: Record<CopilotNoticeKind, NoticePreset> = {
  "stale-overlay": {
    title: "Names are scanner-derived",
    detail:
      "The business overlay has not been validated (or is out of date), so labels come from the scan rather than a confirmed model.",
    tone: "warning",
    icon: Info,
  },
  "missing-artifact": {
    title: "Nothing published for this project",
    detail: "This project needs a rescan before I can answer from the diagram.",
    tone: "warning",
    icon: AlertTriangle,
  },
  "service-unreachable": {
    title: "I cannot reach the topology service",
    detail:
      "Answers are unavailable until it responds. The diagram on this page is unaffected -- it is already loaded and stays usable.",
    tone: "error",
    icon: CloudOff,
  },
};

const TONE_CLASS: Record<NoticePreset["tone"], string> = {
  warning: "border-amber-400/50 bg-amber-400/10 text-amber-800 dark:text-amber-300",
  error: "border-destructive/50 bg-destructive/10 text-destructive",
};

/** Build a notice with the preset copy for `kind`. */
export function copilotNotice(kind: CopilotNoticeKind): CopilotNotice {
  return { kind };
}

interface CopilotNoticesProps {
  notices: readonly CopilotNotice[];
}

export function CopilotNotices({ notices }: CopilotNoticesProps) {
  if (notices.length === 0) return null;

  return (
    <div className="space-y-1.5 px-3 pt-2" aria-label="Copilot limitations">
      {notices.map((notice) => {
        const preset = PRESETS[notice.kind];
        const Icon = preset.icon;
        return (
          <div
            key={notice.kind}
            role="status"
            data-notice={notice.kind}
            className={cn(
              "flex items-start gap-2 rounded-md border px-2.5 py-2 text-[11px] leading-snug",
              TONE_CLASS[preset.tone],
            )}
          >
            <Icon className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />
            <div className="min-w-0">
              <p className="font-semibold">{notice.title ?? preset.title}</p>
              <p className="opacity-90">{notice.detail ?? preset.detail}</p>
            </div>
          </div>
        );
      })}
    </div>
  );
}
