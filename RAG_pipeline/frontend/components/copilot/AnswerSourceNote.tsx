"use client";

import { FileQuestion, FileText, Network } from "lucide-react";
import { cn } from "@/lib/utils";
import type { CopilotAnswerSource } from "@/types/copilot";

interface AnswerSourceNoteProps {
  source: CopilotAnswerSource;
}

const PRESENTATION: Record<
  CopilotAnswerSource["attribution"],
  { icon: typeof Network; className: string }
> = {
  // Neutral: the chrome around the panel is already making this claim, so
  // repeating it loudly would just add noise.
  surface: {
    icon: Network,
    className: "border-border bg-muted/40 text-muted-foreground",
  },
  // Something else answered. Amber rather than red -- the answer may well be
  // correct, it just is not the diagram's word.
  other: {
    icon: FileText,
    className: "border-amber-400/50 bg-amber-400/10 text-amber-800 dark:text-amber-300",
  },
  none: {
    icon: FileQuestion,
    className: "border-amber-500/60 bg-amber-500/10 text-amber-900 dark:text-amber-300",
  },
};

/**
 * Says what backed the answer, right where the answer is.
 *
 * Sits in the panel footer beside the target chips rather than at the top with
 * the page-level notices: this is a property of one answer, and an attribution
 * the user has to scroll away from the answer to find is one they will not read.
 */
export function AnswerSourceNote({ source }: AnswerSourceNoteProps) {
  const { icon: Icon, className } = PRESENTATION[source.attribution];
  return (
    <div
      role="status"
      data-answer-source={source.attribution}
      className={cn(
        "mx-3 mt-2 flex items-start gap-2 rounded-md border px-2.5 py-1.5 text-[11px] leading-snug",
        className,
      )}
    >
      <Icon className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />
      <div className="min-w-0">
        <p className="font-medium">{source.label}</p>
        {source.detail && <p className="opacity-90">{source.detail}</p>}
      </div>
    </div>
  );
}
