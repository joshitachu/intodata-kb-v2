"use client";

import { Check, Lightbulb } from "lucide-react";
import { cn } from "@/lib/utils";
import type { HarnessPackRecommendation, HarnessPackView } from "@/types";
import { Button } from "@/components/ui/button";

interface PackSelectorProps {
  packs: HarnessPackView[];
  selectedPackIds: string[];
  recommendations: HarnessPackRecommendation[];
  onChange: (packIds: string[]) => void;
}

export function PackSelector({ packs, selectedPackIds, recommendations, onChange }: PackSelectorProps) {
  const selected = new Set(selectedPackIds);
  const byId = new Map(recommendations.map((item) => [item.pack_id, item]));

  function toggle(packId: string) {
    const next = new Set(selected);
    if (next.has(packId)) next.delete(packId);
    else next.add(packId);
    onChange([...next].sort());
  }

  return (
    <div className="space-y-3" aria-label="Available Packs">
      {packs.map((pack) => {
        const recommendation = byId.get(pack.id);
        const isSelected = selected.has(pack.id);
        return (
          <section key={pack.id} className={cn("border p-4", isSelected ? "border-primary bg-primary/5" : "border-border")}>
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <h3 className="text-sm font-semibold">{pack.name}</h3>
                <p className="mt-1 text-sm text-muted-foreground">{pack.workflow_descriptions[0] ?? "Configured project workflow support."}</p>
              </div>
              <Button variant={isSelected ? "primary" : "secondary"} size="sm" onClick={() => toggle(pack.id)} aria-pressed={isSelected}>
                {isSelected && <Check className="h-3.5 w-3.5" aria-hidden />}
                {isSelected ? "Selected" : "Select"}
              </Button>
            </div>
            {recommendation && (
              <div className="mt-3 flex gap-2 text-xs text-muted-foreground">
                <Lightbulb className="mt-0.5 h-3.5 w-3.5 shrink-0 text-amber-600" aria-hidden />
                <p>{recommendation.reasons.join(". ")} ({Math.round(recommendation.confidence * 100)}% match)</p>
              </div>
            )}
          </section>
        );
      })}
    </div>
  );
}
