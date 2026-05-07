"use client";

import { useState } from "react";
import { Check, Layers } from "lucide-react";
import { cn } from "@/lib/utils";
import {
  useOnboardingConfig,
  useSavePlatforms,
} from "@/hooks/useOnboarding";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Card, CardBody, CardFooter, CardHeader } from "@/components/ui/card";
import { trackWizardEvent } from "@/lib/telemetry";

interface Props {
  initial: string[];
  onBack: () => void;
  onNext: () => void;
}

export function PlatformSelectStep({ initial, onBack, onNext }: Props) {
  const { data: config } = useOnboardingConfig();
  const [selected, setSelected] = useState<Set<string>>(new Set(initial));
  const save = useSavePlatforms();

  const platforms = config?.platforms ?? [];

  function toggle(id: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
        trackWizardEvent("wizard_platform_deselected", 2, { platform: id });
      } else {
        next.add(id);
        trackWizardEvent("wizard_platform_selected", 2, { platform: id });
      }
      return next;
    });
  }

  async function handleNext() {
    await save.mutateAsync(Array.from(selected));
    onNext();
  }

  return (
    <div className="w-full max-w-3xl mx-auto px-6">
      <Card>
        <CardHeader>
          <div className="flex items-center gap-3">
            <span className="flex h-10 w-10 items-center justify-center rounded-lg bg-primary/10 text-primary">
              <Layers className="h-5 w-5" aria-hidden />
            </span>
            <div>
              <h2 className="text-xl font-semibold text-foreground">
                Which platforms does your team integrate with?
              </h2>
              <p className="mt-0.5 text-sm text-muted-foreground">
                Pick all that apply — we&rsquo;ll tailor scanners and doc
                templates to match.
              </p>
            </div>
          </div>
        </CardHeader>

        <CardBody>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            {platforms.map((opt) => {
              const active = selected.has(opt.id);
              const comingSoon = opt.status === "coming_soon";
              return (
                <button
                  key={opt.id}
                  type="button"
                  onClick={() => toggle(opt.id)}
                  aria-pressed={active}
                  className={cn(
                    "group relative text-left rounded-lg border p-4 transition-all",
                    "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/30",
                    active
                      ? "border-primary bg-primary/5 shadow-elevation-sm"
                      : "border-border bg-card hover:border-primary/40 hover:shadow-elevation-sm",
                    comingSoon && "opacity-80",
                  )}
                >
                  <span
                    className={cn(
                      "absolute top-3 right-3 flex h-5 w-5 items-center justify-center rounded-full transition-all",
                      active
                        ? "bg-primary text-primary-foreground scale-100"
                        : "border border-border bg-background text-transparent scale-90 group-hover:scale-100",
                    )}
                    aria-hidden
                  >
                    <Check className="h-3 w-3" strokeWidth={3} />
                  </span>
                  <div className="flex items-center gap-2 pr-8">
                    <span className="font-medium text-sm text-foreground">
                      {opt.label}
                    </span>
                    {comingSoon && <Badge variant="soon">Coming soon</Badge>}
                  </div>
                  <div className="mt-1 text-xs text-muted-foreground">
                    {opt.description}
                  </div>
                </button>
              );
            })}
          </div>

          {save.isError && (
            <p className="mt-4 text-sm text-destructive">
              Failed to save — please try again.
            </p>
          )}
        </CardBody>

        <CardFooter>
          <Button variant="ghost" onClick={onBack}>
            Back
          </Button>
          <Button onClick={handleNext} isLoading={save.isPending}>
            Continue
          </Button>
        </CardFooter>
      </Card>
    </div>
  );
}
