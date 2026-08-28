"use client";

import { useEffect, useState } from "react";
import type { Viewport } from "./geometry";

function measure(): Viewport {
  if (typeof window === "undefined") return { width: 0, height: 0 };
  return { width: window.innerWidth, height: window.innerHeight };
}

/**
 * Viewport size, measured eagerly.
 *
 * Measured in the initialiser rather than in an effect: the overlay cannot
 * paint before it knows the screen size, and deferring the first measurement to
 * an effect left one frame with no scrim — the exact undimmed flash the
 * walkthrough is supposed to avoid.
 */
export function useViewport(): Viewport {
  const [viewport, setViewport] = useState<Viewport>(measure);

  useEffect(() => {
    const update = () => setViewport(measure());
    update();
    window.addEventListener("resize", update);
    return () => window.removeEventListener("resize", update);
  }, []);

  return viewport;
}

/**
 * Whether the user asked for reduced motion.
 *
 * The CSS media query already disables the transitions, but the choreography
 * itself has to change too: with transitions off, a "fade out, move, fade in"
 * sequence degrades into the card blinking off and on, which is worse than an
 * instant move. The engine reads this to skip the choreography entirely.
 *
 * Starts as `false` so server and first client render agree.
 */
export function usePrefersReducedMotion(): boolean {
  const [reduced, setReduced] = useState(false);

  useEffect(() => {
    if (typeof window === "undefined" || !window.matchMedia) return;
    const query = window.matchMedia("(prefers-reduced-motion: reduce)");
    setReduced(query.matches);
    const onChange = (event: MediaQueryListEvent) => setReduced(event.matches);
    query.addEventListener?.("change", onChange);
    return () => query.removeEventListener?.("change", onChange);
  }, []);

  return reduced;
}
