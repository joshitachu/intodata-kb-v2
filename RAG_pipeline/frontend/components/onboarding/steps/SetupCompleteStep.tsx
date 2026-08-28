"use client";

import { useEffect, useMemo } from "react";
import { ArrowRight, Check, PlayCircle } from "lucide-react";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Card, CardBody } from "@/components/ui/card";
import { useOnboardingConfig, useCompleteTour } from "@/hooks/useOnboarding";
import { useOnboardingContext } from "@/contexts/OnboardingContext";
import { usePermissions } from "@/hooks/usePermissions";
import { tourFor } from "@/components/tour/tours";
import { applicableSteps } from "@/components/tour/types";
import { trackWizardEvent } from "@/lib/telemetry";

interface Props {
  workspaceName: string | null | undefined;
  selectedPlatformIds: string[];
  selectedToolIds: string[];
  onStartTour: () => void;
  onSkipTour: () => void;
}

export function SetupCompleteStep({
  workspaceName,
  selectedPlatformIds,
  selectedToolIds,
  onStartTour,
  onSkipTour,
}: Props) {
  const { data: config } = useOnboardingConfig();
  const completeTour = useCompleteTour();
  const { status } = useOnboardingContext();

  const persona = status?.persona ?? "developer";
  const { hasPermission } = usePermissions();
  const tour = useMemo(() => tourFor(persona), [persona]);
  // Count the steps this user will actually be shown, not the full list. An
  // "8 steps" promise that turns into 5 undermines the walkthrough on its
  // first screen.
  const stepCount = useMemo(
    () =>
      applicableSteps(tour.steps, {
        hasPermission: (codename) =>
          hasPermission(codename as Parameters<typeof hasPermission>[0]),
      }).length,
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [tour, persona],
  );

  useEffect(() => {
    trackWizardEvent("wizard_completed", 6);
  }, []);

  const platformLabels = useMemo(() => {
    if (!config) return selectedPlatformIds;
    const byId = Object.fromEntries(config.platforms.map((p) => [p.id, p.label]));
    return selectedPlatformIds.map((id) => byId[id] ?? id);
  }, [config, selectedPlatformIds]);

  const toolLabels = useMemo(() => {
    if (!config) return selectedToolIds;
    const byId: Record<string, string> = {};
    for (const cat of config.tool_categories) {
      for (const t of cat.tools) byId[t.id] = t.label;
    }
    return selectedToolIds.map((id) => byId[id] ?? id);
  }, [config, selectedToolIds]);

  function handleSkip() {
    // Mark the tour as complete so we don't nag the user on next login. The
    // variant records *which* walkthrough was declined, so a later promotion
    // can still offer the one that matches the new authority.
    completeTour.mutate(persona);
    onSkipTour();
  }

  return (
    <div className="w-full max-w-xl mx-auto px-6 animate-step-enter">
      {/* Success glyph with soft halo */}
      <div className="flex flex-col items-center text-center mb-10">
        <div className="relative mb-8">
          <span
            className="absolute inset-0 rounded-full bg-emerald-400/25 blur-3xl scale-125"
            aria-hidden
          />
          <div className="relative flex h-20 w-20 items-center justify-center rounded-2xl bg-emerald-500 text-white shadow-elevation-lg ring-8 ring-emerald-500/10">
            <Check className="h-10 w-10" strokeWidth={3} aria-hidden />
          </div>
        </div>

        <h1 className="text-3xl font-semibold tracking-tight text-foreground mb-3">
          {workspaceName ? `${workspaceName} is ready` : "Your workspace is ready"}
        </h1>
        <p className="text-base text-muted-foreground max-w-sm leading-relaxed">
          Everything is provisioned and live. {tour.pitch}
        </p>
      </div>

      {/* Summary card */}
      <Card className="mb-8">
        <CardBody className="space-y-3">
          <h3 className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
            What we set up
          </h3>
          <dl className="divide-y divide-border/60 -mx-2">
            <SummaryRow
              label="Workspace"
              value={workspaceName || "Your workspace"}
            />
            <SummaryRow
              label="Platforms"
              value={
                platformLabels.length > 0
                  ? platformLabels.join(", ")
                  : "None — add later"
              }
              muted={platformLabels.length === 0}
            />
            <SummaryRow
              label="Tools"
              value={
                toolLabels.length > 0
                  ? toolLabels.join(", ")
                  : "None — add later"
              }
              muted={toolLabels.length === 0}
            />
          </dl>
        </CardBody>
      </Card>

      {/* Primary CTAs */}
      <div className="flex flex-col sm:flex-row gap-3">
        <Button
          size="lg"
          onClick={onStartTour}
          className="flex-1"
        >
          <PlayCircle className="h-4 w-4" />
          {`Start the ${stepCount}-step walkthrough`}
          <ArrowRight className="h-4 w-4" />
        </Button>
        <Button
          size="lg"
          variant="secondary"
          onClick={handleSkip}
          isLoading={completeTour.isPending}
          className="flex-1"
        >
          Skip and explore
        </Button>
      </div>

    </div>
  );
}

function SummaryRow({
  label,
  value,
  muted = false,
}: {
  label: string;
  value: string;
  muted?: boolean;
}) {
  return (
    <div className="flex gap-4 px-2 py-2">
      <dt className="w-24 shrink-0 text-sm text-muted-foreground">{label}</dt>
      <dd
        className={cn(
          "text-sm font-medium",
          muted ? "text-muted-foreground" : "text-foreground",
        )}
      >
        {value}
      </dd>
    </div>
  );
}
