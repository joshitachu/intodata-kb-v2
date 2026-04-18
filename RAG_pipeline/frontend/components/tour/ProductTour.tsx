"use client";

import { useEffect, useRef } from "react";
import { usePathname, useRouter } from "next/navigation";
import { driver, type Driver } from "driver.js";
import "driver.js/dist/driver.css";

import { useAuthContext } from "@/components/AuthProvider";
import { useOnboardingContext } from "@/contexts/OnboardingContext";
import { useCompleteTour } from "@/hooks/useOnboarding";
import { tourSteps } from "./tourSteps";

/**
 * Driver.js wrapper. Mounts in the app shell and starts automatically for
 * users whose tour is incomplete (right after they finish the wizard). It
 * also obeys `startTour()` from OnboardingContext for the "Replay tour" button.
 */
export function ProductTour() {
  const router = useRouter();
  const pathname = usePathname();
  const { user } = useAuthContext();
  const { status, tourActive, stopTour, startTour } = useOnboardingContext();
  const completeTour = useCompleteTour();
  const driverRef = useRef<Driver | null>(null);

  // Auto-start right after wizard completes (first time only)
  useEffect(() => {
    if (!user) return;
    if (!status?.wizard_completed) return;
    if (status.tour_completed) return;
    if (user.tour_completed) return;
    if (tourActive) return;
    startTour();
  }, [user, status, tourActive, startTour]);

  useEffect(() => {
    if (!tourActive) return;

    const d = driver({
      showProgress: true,
      animate: true,
      allowClose: true,
      overlayOpacity: 0.6,
      steps: tourSteps,
      onNextClick: (_el, step, ctx) => {
        const idx = ctx.state.activeIndex ?? 0;
        const nextStep = tourSteps[idx + 1];
        if (nextStep?.navigateTo && nextStep.navigateTo !== pathname) {
          router.push(nextStep.navigateTo);
          // Give the next page a moment to mount before driver moves on
          setTimeout(() => d.moveNext(), 350);
        } else {
          d.moveNext();
        }
      },
      onPrevClick: (_el, step, ctx) => {
        const idx = ctx.state.activeIndex ?? 0;
        const prevStep = tourSteps[idx - 1];
        if (prevStep?.navigateTo && prevStep.navigateTo !== pathname) {
          router.push(prevStep.navigateTo);
          setTimeout(() => d.movePrevious(), 350);
        } else {
          d.movePrevious();
        }
      },
      onDestroyed: () => {
        stopTour();
        completeTour.mutate();
      },
    });

    driverRef.current = d;

    // Navigate to the first step's page before starting so the target exists
    const first = tourSteps[0];
    if (first?.navigateTo && first.navigateTo !== pathname) {
      router.push(first.navigateTo);
      setTimeout(() => d.drive(), 400);
    } else {
      d.drive();
    }

    return () => {
      d.destroy();
      driverRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tourActive]);

  return null;
}
