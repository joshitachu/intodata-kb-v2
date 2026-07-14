"use client";

import { Check } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import type { DocumentationPack } from "@/types";

interface DocumentationPackSelectorProps {
  packs: DocumentationPack[];
  selectedPluginPackId: number | null;
  onChange: (pluginPackId: number | null) => void;
}

export function DocumentationPackSelector({
  packs,
  selectedPluginPackId,
  onChange,
}: DocumentationPackSelectorProps) {
  return (
    <section aria-label="Documentation swap packs" className="space-y-3">
      <div>
        <h2 className="text-sm font-semibold">Documentation pack</h2>
      </div>
      <div className="space-y-3">
        <article className={cn("border p-4", selectedPluginPackId === null ? "border-primary bg-primary/5" : "border-border")}>
          <div className="flex items-center justify-between gap-3">
            <div><h3 className="text-sm font-medium">No documentation pack</h3></div>
            <Button variant={selectedPluginPackId === null ? "primary" : "secondary"} size="sm" onClick={() => onChange(null)} aria-pressed={selectedPluginPackId === null}>
              {selectedPluginPackId === null && <Check className="h-3.5 w-3.5" aria-hidden />}
              {selectedPluginPackId === null ? "Selected" : "Select"}
            </Button>
          </div>
        </article>
        {packs.map((pack) => {
          const selected = pack.id === selectedPluginPackId;
          return <article key={pack.id} className={cn("border p-4", selected ? "border-primary bg-primary/5" : "border-border")}>
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <h3 className="text-sm font-medium">{pack.name}</h3>
                <p className="mt-1 text-xs text-muted-foreground">Version {pack.version}</p>
                {pack.description && <p className="mt-2 text-sm text-muted-foreground">{pack.description}</p>}
              </div>
              <Button variant={selected ? "primary" : "secondary"} size="sm" onClick={() => onChange(pack.id)} aria-pressed={selected}>
                {selected && <Check className="h-3.5 w-3.5" aria-hidden />}
                {selected ? "Selected" : "Select"}
              </Button>
            </div>
          </article>;
        })}
      </div>
    </section>
  );
}
