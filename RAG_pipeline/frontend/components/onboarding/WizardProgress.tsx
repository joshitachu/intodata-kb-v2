"use client";

import { cn } from "@/lib/utils";

interface Props {
  steps: string[];
  currentStep: number;
}

export function WizardProgress({ steps, currentStep }: Props) {
  return (
    <div className="flex items-center justify-center gap-2 py-6">
      {steps.map((label, i) => {
        const active = i === currentStep;
        const complete = i < currentStep;
        return (
          <div key={label} className="flex items-center">
            <div
              className={cn(
                "flex items-center justify-center w-8 h-8 rounded-full text-xs font-medium transition-colors",
                complete && "bg-green-500 text-white",
                active && "bg-primary text-primary-foreground ring-4 ring-primary/20",
                !active && !complete && "bg-muted text-muted-foreground",
              )}
            >
              {complete ? <span aria-hidden>✓</span> : i + 1}
            </div>
            <span
              className={cn(
                "ml-2 text-xs hidden sm:inline",
                active ? "text-foreground font-medium" : "text-muted-foreground",
              )}
            >
              {label}
            </span>
            {i < steps.length - 1 && (
              <div
                className={cn(
                  "w-8 h-px mx-2 transition-colors",
                  complete ? "bg-green-500" : "bg-border",
                )}
              />
            )}
          </div>
        );
      })}
    </div>
  );
}
