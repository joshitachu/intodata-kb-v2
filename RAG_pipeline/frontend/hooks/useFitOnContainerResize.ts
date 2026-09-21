"use client";

import { useEffect, useRef, useState } from "react";

/** How long the container must hold still before refitting. */
const DEFAULT_SETTLE_MS = 160;

/**
 * Below this, a size change is layout noise (scrollbars, sub-pixel reflow)
 * rather than the user resizing something, and refitting for it would move the
 * diagram under a user who did nothing.
 */
const MIN_SIGNIFICANT_DELTA = 8;

export interface FitOnContainerResizeOptions {
  /**
   * When false the observer keeps watching but never fits. Hosts use this to
   * protect a view the user deliberately composed -- a focused neighbourhood is
   * the whole point of focus mode, and refitting to the full graph would discard
   * it on a resize the user made in order to SEE that neighbourhood better.
   */
  enabled?: boolean;
  settleMs?: number;
}

/**
 * Refit a diagram when the element it lives in changes size.
 *
 * Returns a ref callback to put on the element that actually resizes -- usually
 * the flex column beside a docked panel, not the ReactFlow wrapper itself.
 *
 * `fit` is supplied by the host rather than derived here because every canvas
 * fits differently: the landscape uses its own padding and a minimum zoom that
 * keeps labels legible, the job graph does not. A hook that called `fitView()`
 * with defaults would silently override those.
 *
 * Two behaviours matter and are easy to get wrong:
 *
 * The first observation is ignored. `ResizeObserver` fires once on observe with
 * the current size, and treating that as a change refits immediately on mount --
 * competing with the `fitView` prop ReactFlow already ran, which produces a
 * visible double-fit on every navigation.
 *
 * The fit is debounced to the END of the gesture. A drag emits a resize per
 * frame, and fitting per frame both animates against itself and makes the
 * diagram appear to crawl while the pointer is still moving. One fit once the
 * width settles is what reads as the diagram following the panel.
 */
export function useFitOnContainerResize(
  fit: (() => void) | null | undefined,
  { enabled = true, settleMs = DEFAULT_SETTLE_MS }: FitOnContainerResizeOptions = {},
): (element: HTMLElement | null) => void {
  const [element, setElement] = useState<HTMLElement | null>(null);

  // Read through refs so a host that rebuilds `fit` every render (the common
  // case, since it closes over fitView) does not tear down the observer and
  // lose the width baseline with it.
  const fitRef = useRef(fit);
  const enabledRef = useRef(enabled);
  useEffect(() => {
    fitRef.current = fit;
    enabledRef.current = enabled;
  }, [fit, enabled]);

  useEffect(() => {
    if (element === null || typeof ResizeObserver === "undefined") return;

    let lastWidth: number | null = null;
    let timer: ReturnType<typeof setTimeout> | null = null;

    const observer = new ResizeObserver((entries) => {
      const width = entries[0]?.contentRect.width;
      if (width === undefined) return;
      if (lastWidth === null) {
        lastWidth = width;
        return;
      }
      if (Math.abs(width - lastWidth) < MIN_SIGNIFICANT_DELTA) return;
      lastWidth = width;
      if (timer !== null) clearTimeout(timer);
      timer = setTimeout(() => {
        timer = null;
        // Re-checked at fire time, not at schedule time: a resize can start
        // before the host enters focus mode and land after it.
        if (enabledRef.current) fitRef.current?.();
      }, settleMs);
    });

    observer.observe(element);
    return () => {
      if (timer !== null) clearTimeout(timer);
      observer.disconnect();
    };
  }, [element, settleMs]);

  return setElement;
}
