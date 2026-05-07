"use client";

import { cn } from "@/lib/utils";
import { TenantMark } from "@/components/TenantMark";

interface Props {
  steps: string[];
  currentStep: number;
  workspaceName?: string | null;
}

export function WizardProgress({ steps, currentStep, workspaceName }: Props) {
  const total = steps.length;
  const current = Math.min(Math.max(currentStep, 0), total - 1);
  const activeLabel = steps[current] ?? "";
  const progressPct = ((current + 1) / total) * 100;

  return (
    <div className="border-b border-border/60 bg-background/95 backdrop-blur-sm">
      <div className="mx-auto max-w-5xl px-6 py-4">
        {/* Top row — brand lockup + step indicator */}
        <div className="flex items-center justify-between gap-4">
          <TenantMark
            size="md"
            subtitle={workspaceName || "Workspace setup"}
          />

          <div className="flex items-center gap-2">
            <span className="text-xs tabular-nums text-muted-foreground">
              Step {current + 1} of {total}
            </span>
            <span className="hidden sm:inline h-3 w-px bg-border" aria-hidden />
            <span className="hidden sm:inline text-xs font-medium text-foreground">
              {activeLabel}
            </span>
          </div>
        </div>

        {/* Progress rail — segmented */}
        <div
          className="mt-4 flex items-center gap-1.5"
          role="progressbar"
          aria-valuemin={0}
          aria-valuemax={total}
          aria-valuenow={current + 1}
          aria-valuetext={activeLabel}
        >
          {steps.map((label, i) => {
            const reached = i <= current;
            return (
              <div
                key={label}
                className={cn(
                  "h-1 flex-1 rounded-full transition-colors duration-300",
                  reached ? "bg-primary" : "bg-border",
                )}
                aria-hidden
              />
            );
          })}
        </div>

        {/* Mobile label row */}
        <div className="mt-2 sm:hidden">
          <span className="text-xs font-medium text-foreground">{activeLabel}</span>
        </div>

        {/* Visually-hidden rail that keeps width consistent */}
        <span className="sr-only">Overall progress: {Math.round(progressPct)}%</span>
      </div>
    </div>
  );
}
