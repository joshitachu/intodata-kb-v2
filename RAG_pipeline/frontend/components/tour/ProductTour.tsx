"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { usePathname, useRouter } from "next/navigation";

import { useOnboardingContext } from "@/contexts/OnboardingContext";
import { useCompleteTour, useSaveTourStep } from "@/hooks/useOnboarding";
import { usePermissions } from "@/hooks/usePermissions";
import { trackWizardEvent } from "@/lib/telemetry";
import { isOversize } from "./geometry";
import { SpotlightOverlay } from "./SpotlightOverlay";
import { TourCard } from "./TourCard";
import { TourProgress } from "./TourProgress";
import { tourFor } from "./tours";
import { applicableSteps } from "./types";
import { usePrefersReducedMotion, useViewport } from "./useEnvironment";
import { useTargetRect } from "./useTargetRect";
import { useTourStage } from "./useTourStage";

/**
 * Role-aware spotlight walkthrough.
 *
 * Replaces the driver.js tour, which showed one hardcoded step list to every
 * persona, guessed at navigation timing with a fixed `setTimeout`, and never
 * persisted progress despite the API existing for it.
 *
 * Behaviour worth knowing:
 * - Steps are filtered by permission before the tour starts, and again at
 *   runtime when a target never appears, so nobody is shown an empty page.
 * - Progress is saved per step id, so closing the tab resumes on the right step
 *   even though each persona's tour is a different length.
 * - Navigation waits for the target element rather than a timer.
 * - The overlay is mounted for the whole tour and the *presentation* is a
 *   separate state machine (`useTourStage`) from the *resolution* of the next
 *   anchor. Rendering nothing while an anchor resolved is what made the tour
 *   flash undimmed between every pair of steps.
 */
export function ProductTour() {
  const router = useRouter();
  const pathname = usePathname();
  const { tourActive, stopTour, status } = useOnboardingContext();
  const { hasPermission } = usePermissions();
  const completeTour = useCompleteTour();
  const saveStep = useSaveTourStep();
  const viewport = useViewport();
  const reducedMotion = usePrefersReducedMotion();

  const persona = status?.persona ?? "developer";
  const definition = useMemo(() => tourFor(persona), [persona]);

  const steps = useMemo(
    () =>
      applicableSteps(definition.steps, {
        // Cast is safe: the tour declares codenames from the same RBAC list the
        // hook types, and an unknown codename correctly resolves to "denied".
        hasPermission: (codename) =>
          hasPermission(codename as Parameters<typeof hasPermission>[0]),
      }),
    // `hasPermission` closes over the auth user; permissions only change on a
    // fresh auth payload, and re-filtering mid-tour would renumber the steps
    // under the user. Persona is the intended trigger.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [definition, persona],
  );

  const [index, setIndex] = useState(0);
  /** Steps whose target never appeared — skipped and not retried. */
  const [unavailable, setUnavailable] = useState<Set<string>>(new Set());

  const visibleSteps = useMemo(
    () => steps.filter((step) => !unavailable.has(step.id)),
    [steps, unavailable],
  );

  const step = visibleSteps[Math.min(index, Math.max(visibleSteps.length - 1, 0))];

  // Resume where the user stopped. Only on tour start, so a save mid-tour never
  // yanks the user backwards.
  const resumed = useRef(false);
  useEffect(() => {
    if (!tourActive) {
      resumed.current = false;
      setIndex(0);
      setUnavailable(new Set());
      return;
    }
    if (resumed.current) return;
    resumed.current = true;

    const seen = new Set(status?.tour_seen_steps ?? []);
    // Resume only when the persisted progress belongs to *this* walkthrough.
    // After a promotion the admin tour is a different list, and resuming into
    // it by index would drop the new steps.
    if (!seen.size || (status?.tour_variant && status.tour_variant !== persona)) {
      setIndex(0);
      return;
    }
    const firstUnseen = steps.findIndex((candidate) => !seen.has(candidate.id));
    setIndex(firstUnseen === -1 ? 0 : firstUnseen);
  }, [tourActive, status?.tour_seen_steps, status?.tour_variant, persona, steps]);

  // Navigate before revealing. The route is pushed here; the target hook waits
  // for the element, so no timing guess is involved.
  useEffect(() => {
    if (!tourActive || !step?.route) return;
    const [routePath] = step.route.split("?");
    const target = step.route;
    if (routePath !== pathname || target.includes("?")) {
      const alreadyThere =
        routePath === pathname &&
        typeof window !== "undefined" &&
        window.location.search === `?${target.split("?")[1] ?? ""}`;
      if (!alreadyThere) router.push(target);
    }
  }, [tourActive, step?.route, step?.id, pathname, router]);

  const targetState = useTargetRect(step?.target, {
    padding: step?.padding ?? 8,
    enabled: tourActive && Boolean(step?.target),
  });

  /**
   * True only when the resolution result describes the step being rendered.
   *
   * Without this check the previous step's verdict is read against the current
   * step for one render, which turned a single missing anchor into a cascade
   * that skipped every remaining step of the tour.
   */
  const resolutionIsCurrent = targetState.selector === step?.target;
  const anchorRect =
    resolutionIsCurrent && targetState.status === "found" ? targetState.rect : null;

  /**
   * Drop the cutout when the anchor is effectively the whole page.
   *
   * Some anchors are page-root containers. Dimming everything except 90% of the
   * screen highlights nothing and reads as a broken popup; a centred card over
   * an evenly dimmed page at least says "this is about this page" honestly.
   */
  const spotlightRect =
    anchorRect && !isOversize(anchorRect, viewport) ? anchorRect : null;

  /**
   * Whether this step's anchor question has been answered either way — the
   * signal the presentation machine waits on before opening the aperture.
   */
  const resolved = !step?.target
    ? Boolean(step)
    : resolutionIsCurrent &&
      (targetState.status === "found" ||
        (targetState.status === "missing" && step.guard?.skipIfMissing === false));

  const stage = useTourStage({
    active: tourActive,
    stepId: step?.id ?? null,
    resolved,
    rect: spotlightRect,
    reducedMotion,
  });

  // A step whose anchor never mounts is dropped rather than shown over an empty
  // page. Recording it in state keeps the step count stable afterwards.
  useEffect(() => {
    if (!tourActive || !step?.target) return;
    if (!resolutionIsCurrent || targetState.status !== "missing") return;
    if (step.guard?.skipIfMissing === false) return;

    setUnavailable((prev) => {
      if (prev.has(step.id)) return prev;
      const next = new Set(prev);
      next.add(step.id);
      return next;
    });
    trackWizardEvent("tour_step_unavailable", index);
  }, [
    tourActive,
    resolutionIsCurrent,
    targetState.status,
    step?.id,
    step?.target,
    step?.guard,
    index,
  ]);

  /**
   * The step the card is actually presenting.
   *
   * This lags `step` during a transition: the card keeps the outgoing step's
   * copy while the next anchor resolves, so its controls always match what is
   * on screen. Advancing therefore works off this index, not the requested one,
   * and a click during a handoff re-requests the same step instead of skipping.
   */
  const cardIndex = stage.committedId
    ? visibleSteps.findIndex((candidate) => candidate.id === stage.committedId)
    : -1;
  const cardStep = cardIndex >= 0 ? visibleSteps[cardIndex] : null;
  const cardIsLast = cardIndex >= visibleSteps.length - 1;

  /** Contiguous runs of steps sharing a chapter label. */
  const chapters = useMemo(() => {
    const grouped: { label: string; from: number; count: number }[] = [];
    visibleSteps.forEach((candidate, position) => {
      const open = grouped[grouped.length - 1];
      if (open && open.label === candidate.chapter) open.count += 1;
      else grouped.push({ label: candidate.chapter, from: position, count: 1 });
    });
    return grouped;
  }, [visibleSteps]);

  const nextChapterStart = useMemo(() => {
    const current = chapters.find(
      (chapter) => cardIndex >= chapter.from && cardIndex < chapter.from + chapter.count,
    );
    return current ? current.from + current.count : -1;
  }, [chapters, cardIndex]);
  const canSkipChapter =
    nextChapterStart > 0 && nextChapterStart < visibleSteps.length;

  const persist = useCallback(
    (stepIndex: number, stepId: string) => {
      saveStep.mutate({
        tour_step: stepIndex,
        step_id: stepId,
        variant: persona,
      });
    },
    [saveStep, persona],
  );

  const finish = useCallback(
    (reason: "completed" | "dismissed") => {
      trackWizardEvent(
        reason === "completed" ? "tour_completed" : "tour_dismissed",
        cardIndex >= 0 ? cardIndex : index,
      );
      stopTour();
      // Mark complete on dismissal too: an unfinished tour that reappears on
      // every login reads as a bug. It stays replayable from Help.
      completeTour.mutate(persona);
    },
    [completeTour, cardIndex, index, persona, stopTour],
  );

  const next = useCallback(() => {
    if (!cardStep) return;
    persist(cardIndex, cardStep.id);
    if (cardIsLast) {
      finish("completed");
      return;
    }
    setIndex(cardIndex + 1);
  }, [cardStep, cardIndex, cardIsLast, persist, finish]);

  const prev = useCallback(() => {
    if (cardIndex < 0) return;
    setIndex(Math.max(0, cardIndex - 1));
  }, [cardIndex]);

  /**
   * Jump past the current chapter.
   *
   * The administrative chapters are long by design — they are the parts people
   * ask for in depth — but an owner who already runs the deployment should not
   * have to click through all of them. Progress for the skipped steps is not
   * persisted: they were not seen.
   */
  const skipChapter = useCallback(() => {
    if (!canSkipChapter) return;
    trackWizardEvent("tour_skipped", cardIndex >= 0 ? cardIndex : index);
    setIndex(nextChapterStart);
  }, [canSkipChapter, nextChapterStart, cardIndex, index]);

  // Keyboard control. A walkthrough that traps the user until they find the
  // close button is worse than no walkthrough.
  useEffect(() => {
    if (!tourActive) return;
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") {
        event.preventDefault();
        finish("dismissed");
      } else if (event.key === "ArrowRight") {
        event.preventDefault();
        next();
      } else if (event.key === "ArrowLeft") {
        event.preventDefault();
        prev();
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [tourActive, next, prev, finish]);

  if (!tourActive || !step) return null;

  return (
    <>
      {/* Mounted for the whole tour, never for a single step: the dim layer
          staying painted across a route change is what removes the flash. */}
      <SpotlightOverlay
        rect={stage.cutoutRect}
        open={stage.cutoutOpen}
        viewport={viewport}
        onDismiss={() => finish("dismissed")}
      />
      <TourProgress active={stage.pending} />
      {cardStep && (
        <TourCard
          step={cardStep}
          rect={stage.cutoutRect}
          index={cardIndex}
          total={visibleSteps.length}
          chapterLabel={cardStep.chapter}
          chapters={chapters}
          onSkipChapter={canSkipChapter ? skipChapter : undefined}
          visible={stage.cardVisible}
          glide={stage.glide}
          onNext={next}
          onPrev={prev}
          onSkip={() => finish("dismissed")}
          isLast={cardIsLast}
        />
      )}
      {/* Announce each step for screen readers; the card itself is not modal so
          the underlying page stays reachable. Announced from the committed step
          so nothing is read out for a step that ends up being skipped. */}
      <p aria-live="polite" className="sr-only">
        {cardStep
          ? `Step ${cardIndex + 1} of ${visibleSteps.length}. ${cardStep.title}. ${cardStep.body}`
          : ""}
      </p>
    </>
  );
}
