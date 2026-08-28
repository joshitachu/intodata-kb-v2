"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { TargetRect } from "./geometry";

/**
 * Presentation phase of the walkthrough, independent of target resolution.
 *
 * The tour used to render nothing at all while the next step's anchor was
 * resolving: the overlay unmounted, the page flashed back to full brightness,
 * and the card reappeared somewhere else. That is the "jumps from page to page"
 * complaint. The overlay now stays mounted for the whole tour and this machine
 * decides what it should be *doing* in between two steps.
 *
 * - `hold`    the previous cutout is still open. Most same-page moves resolve
 *             inside this window and can glide straight to the new box.
 * - `closing` the aperture is shutting, because the move is not a quick one.
 * - `shut`    closed and waiting. Navigation, data fetch and the anchor's
 *             scroll-into-view all happen here, under a dim screen, unseen.
 * - `open`    settled on a step.
 */
export type TourPhase = "idle" | "hold" | "closing" | "shut" | "open";

/** How long a move may take before it stops being treated as a small hop. */
const GRACE_MS = 140;
/** Iris-close duration. Must match the cutout transition in SpotlightOverlay. */
const CLOSE_MS = 240;
/** Silence allowed before admitting the tour is waiting on something. */
const PENDING_AFTER_MS = 300;

export interface TourStageInput {
  active: boolean;
  /** Step the engine wants to show next. */
  stepId: string | null;
  /** True once this step's anchor question is answered (or it has no anchor). */
  resolved: boolean;
  /** Live box for the requested step; null while unresolved or unanchored. */
  rect: TargetRect | null;
  reducedMotion: boolean;
}

export interface TourStage {
  phase: TourPhase;
  /** Step the card is presenting. Lags `stepId` during a transition. */
  committedId: string | null;
  /** Geometry the overlay should paint, or null for "no cutout". */
  cutoutRect: TargetRect | null;
  /** False collapses the cutout to a point and hides the ring. */
  cutoutOpen: boolean;
  cardVisible: boolean;
  /** The card may animate its position instead of being repositioned. */
  glide: boolean;
  /** Resolution is slow enough that the user deserves an affordance. */
  pending: boolean;
}

interface Internal {
  requestedId: string | null;
  committedId: string | null;
  phase: TourPhase;
  glide: boolean;
  pending: boolean;
}

const IDLE: Internal = {
  requestedId: null,
  committedId: null,
  phase: "idle",
  glide: false,
  pending: false,
};

export function useTourStage({
  active,
  stepId,
  resolved,
  rect,
  reducedMotion,
}: TourStageInput): TourStage {
  const [state, setState] = useState<Internal>(IDLE);

  /** Newest good geometry, kept so a transition has something to close from. */
  const lastRect = useRef<TargetRect | null>(null);
  const graceTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const closeTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pendingTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Declared before the machine so the machine always sees the newest box.
  useEffect(() => {
    if (rect) lastRect.current = rect;
  }, [rect]);

  const clearTimers = useCallback(() => {
    for (const timer of [graceTimer, closeTimer, pendingTimer]) {
      if (timer.current) clearTimeout(timer.current);
      timer.current = null;
    }
  }, []);

  useEffect(() => clearTimers, [clearTimers]);

  useEffect(() => {
    if (!active) {
      clearTimers();
      lastRect.current = null;
      setState((prev) => (prev.phase === "idle" ? prev : IDLE));
      return;
    }
    if (!stepId) return;
    if (state.committedId === stepId && state.requestedId === stepId) return;

    const commit = (glide: boolean) => {
      clearTimers();
      setState({
        requestedId: stepId,
        committedId: stepId,
        phase: "open",
        glide,
        pending: false,
      });
    };

    const armPending = () => {
      if (state.pending || pendingTimer.current) return;
      pendingTimer.current = setTimeout(() => {
        pendingTimer.current = null;
        setState((prev) =>
          prev.committedId === prev.requestedId ? prev : { ...prev, pending: true },
        );
      }, PENDING_AFTER_MS);
    };

    // ── a different step is being requested ─────────────────────────────────
    if (state.requestedId !== stepId) {
      clearTimers();

      // Already on screen (same page, element mounted): no choreography, just
      // move. This is the common case for two steps on one page.
      if (resolved) {
        commit(state.phase === "open" && !reducedMotion);
        return;
      }

      // Holding needs something to hold: a settled previous step, a known box,
      // and a user who has not asked for less movement.
      const canHold =
        !reducedMotion && state.phase === "open" && lastRect.current !== null;

      setState((prev) => ({
        ...prev,
        requestedId: stepId,
        phase: canHold ? "hold" : "shut",
        glide: false,
        pending: false,
      }));

      if (canHold) {
        graceTimer.current = setTimeout(() => {
          graceTimer.current = null;
          setState((prev) =>
            prev.phase === "hold" ? { ...prev, phase: "closing" } : prev,
          );
        }, GRACE_MS);
      }
      armPending();
      return;
    }

    // ── same step, waiting on resolution or on the aperture ─────────────────
    if (state.phase === "closing" && !closeTimer.current) {
      closeTimer.current = setTimeout(
        () => {
          closeTimer.current = null;
          setState((prev) =>
            prev.phase === "closing" ? { ...prev, phase: "shut" } : prev,
          );
        },
        reducedMotion ? 0 : CLOSE_MS,
      );
    }

    if (!resolved) {
      armPending();
      return;
    }
    if (state.phase === "hold") {
      // Resolved inside the grace window: glide, do not blink.
      commit(true);
      return;
    }
    if (state.phase === "shut") {
      commit(false);
    }
    // "closing": let the aperture finish. Opening from a half-closed cutout
    // drags the highlight across the screen, which is the glitch we are fixing.
  }, [active, stepId, resolved, reducedMotion, state, clearTimers]);

  const settledOnStep = state.phase === "open";
  const cutoutOpen = settledOnStep || state.phase === "hold";

  return {
    phase: state.phase,
    committedId: state.committedId,
    // While holding or closing, paint the geometry we last knew to be real.
    cutoutRect: settledOnStep ? rect : lastRect.current,
    cutoutOpen,
    cardVisible: cutoutOpen && state.committedId !== null,
    glide: state.glide,
    pending: state.pending,
  };
}
