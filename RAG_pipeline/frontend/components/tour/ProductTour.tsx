"use client";

import { useEffect, useRef } from "react";
import { usePathname, useRouter } from "next/navigation";
import { driver, type Driver } from "driver.js";
import "driver.js/dist/driver.css";

import { useOnboardingContext } from "@/contexts/OnboardingContext";
import { useCompleteTour } from "@/hooks/useOnboarding";
import { tourSteps } from "./tourSteps";

/**
 * Driver.js wrapper. Mounts in the app shell and responds to `startTour()`
 * from OnboardingContext. The tour no longer auto-starts right after the
 * wizard — that transition was jarring; the Ready step owns kick-off
 * via its explicit "Take the quick tour" CTA.
 */
export function ProductTour() {
  const router = useRouter();
  const pathname = usePathname();
  const { tourActive, stopTour } = useOnboardingContext();
  const completeTour = useCompleteTour();
  const driverRef = useRef<Driver | null>(null);

  useEffect(() => {
    if (!tourActive) return;

    const d = driver({
      showProgress: true,
      animate: true,
      allowClose: true,
      overlayOpacity: 0.55,
      popoverClass: "intodata-tour",
      nextBtnText: "Next",
      prevBtnText: "Back",
      doneBtnText: "Finish tour",
      steps: tourSteps,
      onNextClick: (_el, _step, ctx) => {
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
      onPrevClick: (_el, _step, ctx) => {
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
