"use client";

import { useEffect, useState } from "react";
import { AlertTriangle, CheckCircle2, Circle, Clock3, Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";
import type { WorkflowCard as WorkflowCardValue } from "@/types";

const TONE_CLASSES = {
  neutral: "border-border bg-muted/20",
  progress: "border-primary/30 bg-primary/5",
  success: "border-emerald-500/30 bg-emerald-500/5",
  danger: "border-destructive/40 bg-destructive/5",
  muted: "border-border bg-muted/30",
  warning: "border-amber-500/40 bg-amber-500/5",
} as const;

function formatElapsed(milliseconds: number) {
  const seconds = Math.max(0, Math.floor(milliseconds / 1_000));
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  return `${minutes}m ${seconds % 60}s`;
}

function StatusIcon({ card }: { card: WorkflowCardValue }) {
  if (card.is_active) return <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />;
  if (card.display_tone === "success") return <CheckCircle2 className="h-4 w-4" aria-hidden="true" />;
  if (card.display_tone === "danger" || card.display_tone === "warning") {
    return <AlertTriangle className="h-4 w-4" aria-hidden="true" />;
  }
  return <Circle className="h-4 w-4" aria-hidden="true" />;
}

export function WorkflowCard({ card }: { card: WorkflowCardValue }) {
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    if (!card.is_active) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1_000);
    return () => window.clearInterval(timer);
  }, [card.is_active]);

  const projectedAt = Date.parse(card.projected_at);
  const liveDelta = card.is_active && Number.isFinite(projectedAt)
    ? Math.max(0, now - projectedAt)
    : 0;
  const elapsed = card.timing_complete
    ? formatElapsed(card.elapsed_ms + liveDelta)
    : "Duration unavailable";

  return (
    <section
      className={cn("rounded-lg border p-3 text-sm", TONE_CLASSES[card.display_tone])}
      aria-label={`${card.label} workflow`}
    >
      <div className="flex items-start gap-2">
        <span className="mt-0.5"><StatusIcon card={card} /></span>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
            <span className="font-medium text-foreground">{card.label}</span>
            <span className="text-xs text-muted-foreground">{card.display_label}</span>
          </div>
          <div className="mt-1 flex items-center gap-1 text-xs text-muted-foreground">
            <Clock3 className="h-3 w-3" aria-hidden="true" />
            <span>{elapsed}</span>
          </div>
          {card.summary && <p className="mt-2 text-muted-foreground">{card.summary}</p>}
          {card.error_message && <p className="mt-2 text-destructive">{card.error_message}</p>}
          {card.children.length > 0 && (
            <ul className="mt-2 space-y-1 text-xs text-muted-foreground">
              {card.children.map((child, index) => (
                <li key={`${child.kind}-${index}`}>
                  {child.kind}: {child.status}{child.error_message ? ` — ${child.error_message}` : ""}
                </li>
              ))}
            </ul>
          )}
          {card.artifact_links.length > 0 && (
            <div className="mt-2 flex flex-wrap gap-2">
              {card.artifact_links.map((artifact, index) => (
                <a
                  key={`${artifact.url}-${index}`}
                  href={artifact.url}
                  className="text-xs font-medium text-primary underline-offset-2 hover:underline"
                  rel="noreferrer"
                  target={artifact.url.startsWith("http") ? "_blank" : undefined}
                >
                  {artifact.label}
                </a>
              ))}
            </div>
          )}
        </div>
      </div>
    </section>
  );
}
