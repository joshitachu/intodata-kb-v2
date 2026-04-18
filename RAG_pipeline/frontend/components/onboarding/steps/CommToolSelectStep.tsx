"use client";

import { useState } from "react";
import { Check, Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";
import {
  useOnboardingConfig,
  useSaveCommTools,
} from "@/hooks/useOnboarding";

interface Props {
  initial: string[];
  onBack: () => void;
  onNext: () => void;
}

export function CommToolSelectStep({ initial, onBack, onNext }: Props) {
  const { data: config } = useOnboardingConfig();
  const [selected, setSelected] = useState<Set<string>>(new Set(initial));
  const save = useSaveCommTools();

  const categories = config?.tool_categories ?? [];

  function toggle(id: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  async function handleNext() {
    await save.mutateAsync(Array.from(selected));
    onNext();
  }

  return (
    <div className="max-w-2xl mx-auto px-6 py-8">
      <h2 className="text-2xl font-semibold mb-2">
        Which tools does your team use?
      </h2>
      <p className="text-sm text-muted-foreground mb-6">
        Pick the chat, ticketing, and docs platforms your team already works in.
        Integrations marked &ldquo;coming soon&rdquo; save your preference so
        we&rsquo;ll enable them automatically when they ship.
      </p>

      <div className="space-y-6 mb-8">
        {categories.map((cat) => (
          <div key={cat.id}>
            <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground mb-2">
              {cat.label}
            </h3>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              {cat.tools.map((opt) => {
                const active = selected.has(opt.id);
                const comingSoon = opt.status === "coming_soon";
                return (
                  <button
                    key={opt.id}
                    onClick={() => toggle(opt.id)}
                    className={cn(
                      "relative text-left p-4 border rounded-lg transition-all",
                      active
                        ? "border-primary bg-primary/5 ring-2 ring-primary/20"
                        : "border-border hover:border-primary/40 hover:bg-muted/30",
                      comingSoon && "opacity-70",
                    )}
                  >
                    {active && (
                      <span className="absolute top-3 right-3 flex items-center justify-center w-5 h-5 rounded-full bg-primary text-primary-foreground">
                        <Check className="h-3 w-3" />
                      </span>
                    )}
                    <div className="flex items-center gap-2">
                      <span className="font-medium text-sm">{opt.label}</span>
                      {comingSoon && (
                        <span className="text-[10px] font-medium px-1.5 py-0.5 rounded bg-muted text-muted-foreground uppercase tracking-wide">
                          Coming soon
                        </span>
                      )}
                    </div>
                    <div className="text-xs text-muted-foreground mt-1">
                      {opt.description}
                    </div>
                  </button>
                );
              })}
            </div>
          </div>
        ))}
      </div>

      {save.isError && (
        <p className="text-sm text-red-600 mb-3">
          Failed to save — please try again.
        </p>
      )}

      <div className="flex justify-between items-center">
        <button
          onClick={onBack}
          className="px-4 py-2 text-sm text-muted-foreground hover:text-foreground transition-colors"
        >
          Back
        </button>
        <button
          onClick={handleNext}
          disabled={save.isPending}
          className="px-6 py-2 bg-primary text-primary-foreground rounded-md text-sm font-medium hover:bg-primary/90 transition-colors disabled:opacity-50 flex items-center gap-2"
        >
          {save.isPending && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
          Continue
        </button>
      </div>
    </div>
  );
}
