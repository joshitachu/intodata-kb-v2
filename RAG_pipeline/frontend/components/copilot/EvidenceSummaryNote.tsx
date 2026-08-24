"use client";

import {
  Activity,
  AlertTriangle,
  Database,
  FileQuestion,
  FileText,
  MessageSquareText,
  Network,
  Plug,
} from "lucide-react";
import { cn } from "@/lib/utils";
import type {
  EvidenceSourceFamily,
  EvidenceSummary,
  MessageStructuredAnswer,
} from "@/types";

const FAMILY_PRESENTATION: Record<
  EvidenceSourceFamily,
  { label: string; icon: typeof Network }
> = {
  topology: { label: "Topology", icon: Network },
  documentation: { label: "Documentation", icon: FileText },
  runtime: { label: "Runtime", icon: Activity },
  metadata: { label: "Project metadata", icon: Database },
  conversation: { label: "Conversation context", icon: MessageSquareText },
  connector: { label: "Connected source", icon: Plug },
  other: { label: "Other evidence", icon: FileQuestion },
};

type EvidenceState =
  | "zero"
  | "partial"
  | "fused"
  | "topology"
  | "documentation"
  | "runtime"
  | "supported";

function evidenceState(summary: EvidenceSummary): EvidenceState {
  if (summary.zero_evidence) return "zero";
  if (summary.failed_sources.length > 0) return "partial";

  const families = new Set(summary.successful_sources.map((source) => source.family));
  if (families.size > 1) return "fused";
  const family = summary.successful_sources[0]?.family;
  if (family === "topology" || family === "documentation" || family === "runtime") {
    return family;
  }
  return "supported";
}

function stateLabel(state: EvidenceState): string {
  switch (state) {
    case "zero":
      return "No current-turn evidence";
    case "partial":
      return "Partially supported";
    case "fused":
      return "Combined evidence";
    default:
      return "Supported by project evidence";
  }
}

function familyCounts(summary: EvidenceSummary) {
  const counts = new Map<EvidenceSourceFamily, number>();
  for (const source of summary.successful_sources) {
    counts.set(source.family, (counts.get(source.family) ?? 0) + source.evidence_count);
  }
  return [...counts.entries()];
}

export function readEvidenceSummary(
  answer: MessageStructuredAnswer | null | undefined,
): EvidenceSummary | null {
  return answer?.evidence_summary ?? null;
}

/**
 * Server-authored, current-turn provenance for one assistant answer.
 *
 * This sits on the shared message bubble, so main chat and every embedded
 * copilot render the same evidence truth. Document citations and topology
 * targets remain separate, more detailed affordances immediately below it.
 */
export function EvidenceSummaryNote({ summary }: { summary: EvidenceSummary }) {
  const state = evidenceState(summary);
  const counts = familyCounts(summary);
  const failedFamilies = [...new Set(summary.failed_sources.map((source) => source.family))];
  const provider = summary.documentation.provider_used;

  return (
    <div
      role={state === "zero" ? "alert" : "status"}
      aria-label="Answer evidence"
      data-evidence-state={state}
      className={cn(
        "mt-2 rounded-md border px-2.5 py-2 text-[11px] leading-snug",
        state === "zero"
          ? "border-amber-500/60 bg-amber-500/10 text-amber-900 dark:text-amber-300"
          : state === "partial"
            ? "border-amber-400/50 bg-amber-400/10 text-amber-800 dark:text-amber-300"
            : "border-border bg-muted/40 text-muted-foreground",
      )}
    >
      <div className="flex items-start gap-2">
        {state === "zero" || state === "partial" ? (
          <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />
        ) : (
          <Network className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />
        )}
        <div className="min-w-0 flex-1">
          <p className="font-medium">{stateLabel(state)}</p>
          {state === "zero" && (
            <p className="opacity-90">
              No validated project source supported this answer.
            </p>
          )}

          {counts.length > 0 && (
            <div className="mt-1 flex flex-wrap gap-1" aria-label="Sources used">
              {counts.map(([family, count]) => {
                const { label, icon: Icon } = FAMILY_PRESENTATION[family];
                const providerDetail =
                  family === "documentation" && provider
                    ? ` via ${provider}${summary.documentation.fallback_used ? " (fallback)" : ""}`
                    : "";
                return (
                  <span
                    key={family}
                    className="inline-flex items-center gap-1 rounded-full border border-current/20 bg-background/60 px-1.5 py-0.5"
                  >
                    <Icon className="h-3 w-3" aria-hidden />
                    {label} · {count}{providerDetail}
                  </span>
                );
              })}
            </div>
          )}

          {failedFamilies.length > 0 && (
            <p className="mt-1 opacity-90">
              Unavailable: {failedFamilies.map((family) => FAMILY_PRESENTATION[family].label).join(", ")}
            </p>
          )}

          {!summary.zero_evidence && (
            <p className="mt-1 opacity-80">
              {summary.evidence_step_count} evidence {summary.evidence_step_count === 1 ? "step" : "steps"}
            </p>
          )}
        </div>
      </div>
    </div>
  );
}
