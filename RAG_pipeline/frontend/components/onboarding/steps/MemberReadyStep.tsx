"use client";

import { useMemo } from "react";
import { ArrowLeft, ArrowRight, Compass, PlayCircle } from "lucide-react";
import { Button } from "@/components/ui/button";
import { usePermissions } from "@/hooks/usePermissions";
import { tourFor } from "@/components/tour/tours";
import { applicableSteps } from "@/components/tour/types";
import type { Persona } from "@/types/onboarding";
import { profileFor } from "../personaContent";

interface Props {
  persona: Persona;
  isSaving: boolean;
  onBack: () => void;
  onStartTour: () => void;
  onSkipTour: () => void;
}

/**
 * Final wizard screen: choose the guided walkthrough or go straight to work.
 *
 * Both exits are first-class. The old flow ended on a single "Let's go" button
 * that dropped the user on an empty chat screen with no suggested next action,
 * which is the moment most people decide a tool is not for them.
 */
export function MemberReadyStep({
  persona,
  isSaving,
  onBack,
  onStartTour,
  onSkipTour,
}: Props) {
  const profile = profileFor(persona);
  const { hasPermission } = usePermissions();
  const tour = useMemo(() => tourFor(persona), [persona]);

  // Promise the number of steps this user will really see.
  const stepCount = useMemo(
    () =>
      applicableSteps(tour.steps, {
        hasPermission: (codename) =>
          hasPermission(codename as Parameters<typeof hasPermission>[0]),
      }).length,
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [tour, persona],
  );

  return (
    <div className="animate-step-enter mx-auto w-full max-w-xl px-6">
      <div className="mb-8 text-center">
        <h1 className="text-2xl font-semibold tracking-tight text-foreground sm:text-3xl">
          You are ready
        </h1>
        <p className="mx-auto mt-3 max-w-md text-sm leading-6 text-muted-foreground">
          {tour.pitch}
        </p>
      </div>

      <button
        type="button"
        onClick={onStartTour}
        disabled={isSaving}
        className="group w-full rounded-xl border border-primary/30 bg-primary/[0.06] p-5 text-left transition-colors hover:bg-primary/[0.1] focus:outline-none focus-visible:ring-2 focus-visible:ring-ring/40 disabled:opacity-60"
      >
        <div className="flex items-start gap-4">
          <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-primary text-primary-foreground">
            <PlayCircle className="h-5 w-5" aria-hidden />
          </span>
          <div className="min-w-0">
            <p className="font-semibold text-foreground">
              {`Take the ${stepCount}-step walkthrough`}
            </p>
            <p className="mt-1 text-sm leading-6 text-muted-foreground">
              Highlights the real controls in the real app. You can leave at any
              point and pick it back up from Help.
            </p>
          </div>
          <ArrowRight
            className="mt-2.5 h-4 w-4 shrink-0 text-primary transition-transform group-hover:translate-x-0.5"
            aria-hidden
          />
        </div>
      </button>

      <div className="mt-3 rounded-xl border border-border bg-card p-5">
        <div className="flex items-start gap-4">
          <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-muted text-muted-foreground">
            <Compass className="h-5 w-5" aria-hidden />
          </span>
          <div className="min-w-0">
            <p className="font-semibold text-foreground">
              {profile.firstMove.label}
            </p>
            <p className="mt-1 text-sm leading-6 text-muted-foreground">
              {profile.firstMove.hint}
            </p>
          </div>
        </div>
        <Button
          variant="secondary"
          size="md"
          className="mt-4 w-full"
          onClick={onSkipTour}
          isLoading={isSaving}
        >
          Skip the walkthrough and start
        </Button>
      </div>

      <div className="mt-6 flex justify-start">
        <Button variant="ghost" size="sm" onClick={onBack} disabled={isSaving}>
          <ArrowLeft className="h-3.5 w-3.5" aria-hidden />
          Back
        </Button>
      </div>
    </div>
  );
}
