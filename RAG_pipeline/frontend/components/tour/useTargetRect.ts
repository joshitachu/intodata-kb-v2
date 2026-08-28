"use client";

import { useEffect, useRef, useState } from "react";
import { sameRect, type TargetRect } from "./geometry";

export type { TargetRect };

/**
 * Resolution result, tagged with the selector it belongs to.
 *
 * The tag is load-bearing. State updates land a render after the selector
 * changes, so a consumer that reads `status` alone sees the *previous* step's
 * verdict attached to the *current* step — which cascaded a single missing
 * anchor into skipping every remaining step. Callers must check `selector`
 * before acting on `status`.
 */
export type TargetState =
  | { status: "pending"; selector: string | undefined }
  | { status: "found"; selector: string; rect: TargetRect }
  | { status: "missing"; selector: string | undefined };

/**
 * How long to wait for a target before treating the step as unshowable.
 *
 * Generous enough for a route change plus data fetch, short enough that a tour
 * with several missing anchors does not leave the user staring at a dimmed
 * screen — each skipped step costs this long before the next one is tried.
 */
const RESOLVE_TIMEOUT_MS = 2500;

/**
 * Frames the box must hold still before the cutout is allowed to open on it.
 *
 * A freshly navigated page settles over a few frames (fonts, images, a scroll
 * correction). Opening the cutout on the first measurement and then tracking
 * produced a highlight that visibly slid into place after the fact.
 */
const SETTLE_FRAMES = 2;

/**
 * Upper bound on settling. An anchor inside something permanently animating
 * would otherwise never publish and the tour would sit on a dimmed screen for
 * ever — a slightly-off cutout beats a frozen walkthrough.
 */
const MAX_SETTLE_FRAMES = 30;

function readRect(element: Element, padding: number): TargetRect {
  const box = element.getBoundingClientRect();
  return {
    top: box.top - padding,
    left: box.left - padding,
    width: box.width + padding * 2,
    height: box.height + padding * 2,
  };
}

/** Whether the raw box is fully on screen, so scrolling can be skipped. */
function isInView(box: DOMRect): boolean {
  const height = typeof window === "undefined" ? 0 : window.innerHeight;
  // No layout information (jsdom, SSR): never scroll on a guess.
  if (!height) return true;
  return box.top >= 0 && box.bottom <= height;
}

interface Resolution {
  element: Element;
  /** The single selector that matched, used for cheap re-querying. */
  selector: string;
}

/**
 * Resolve a comma-separated preference list, best candidate first.
 *
 * Several anchors only exist once a project is selected or data has arrived, so
 * a step declares the specific control it means *and* an always-rendered
 * fallback. `querySelector` on the whole list would pick by document order
 * instead of preference, and would happily return an element inside a
 * `display:none` subtree — which measures 0x0 and collapses the spotlight to a
 * dot. This walks the list in order and prefers a candidate that has a box.
 *
 * A zero-size match is still returned when nothing better exists, because an
 * environment without layout (jsdom) measures everything as zero and must not
 * be treated as "nothing rendered".
 */
export function resolveTarget(selector: string): Resolution | null {
  let fallback: Resolution | null = null;
  for (const part of selector.split(",")) {
    const candidate = part.trim();
    if (!candidate) continue;
    const element = document.querySelector(candidate);
    if (!element) continue;
    const box = element.getBoundingClientRect();
    if (box.width > 0 && box.height > 0) return { element, selector: candidate };
    if (!fallback) fallback = { element, selector: candidate };
  }
  return fallback;
}

/**
 * Track the on-screen box of a tour target, waiting for it to exist.
 *
 * The previous tour navigated with `router.push()` and then guessed that the
 * next page had mounted after a fixed 350ms, which spotlit the wrong element on
 * a slow page and nothing at all on a slower one. This observes the DOM until
 * the target actually appears, then keeps the rect current as the page scrolls,
 * resizes, or re-lays-out around it.
 *
 * Returns `missing` after a timeout so the engine can skip the step rather than
 * leaving the user in front of a frozen overlay.
 */
export function useTargetRect(
  selector: string | undefined,
  { padding = 8, enabled = true }: { padding?: number; enabled?: boolean } = {},
): TargetState {
  const [state, setState] = useState<TargetState>(
    selector ? { status: "pending", selector } : { status: "missing", selector },
  );
  const lastRect = useRef<TargetRect | null>(null);

  // Publish the pending state during render rather than in an effect, so a
  // consumer never observes the previous selector's verdict alongside the new
  // selector. Effects run too late for that guarantee.
  if (state.selector !== selector) {
    lastRect.current = null;
    setState(
      selector
        ? { status: "pending", selector }
        : { status: "missing", selector },
    );
  }

  useEffect(() => {
    if (!enabled || !selector) {
      lastRect.current = null;
      setState({ status: "missing", selector });
      return;
    }

    let frame = 0;
    let mutationObserver: MutationObserver | null = null;
    let timeout: ReturnType<typeof setTimeout> | null = null;
    let started = false;
    let published = false;
    let settleRect: TargetRect | null = null;
    let matches = 0;
    let attempts = 0;
    /** The winning member of the preference list, re-queried each frame. */
    let winner: string | null = null;

    const consider = (element: Element) => {
      const rect = readRect(element, padding);
      if (published) {
        if (sameRect(lastRect.current, rect)) return;
        lastRect.current = rect;
        setState({ status: "found", selector, rect });
        return;
      }
      attempts += 1;
      if (sameRect(settleRect, rect)) {
        matches += 1;
      } else {
        settleRect = rect;
        matches = 0;
      }
      if (matches < SETTLE_FRAMES && attempts < MAX_SETTLE_FRAMES) return;
      published = true;
      lastRect.current = rect;
      setState({ status: "found", selector, rect });
    };

    /**
     * Re-measure every frame, re-resolving when the anchor goes away.
     *
     * Re-querying rather than trusting a captured node is deliberate: React can
     * swap the element on re-render, and a tab change can unmount it entirely.
     * The previous version stopped scheduling frames in that case, which froze
     * the spotlight on a stale box for the rest of the step.
     */
    const tick = () => {
      let element = winner ? document.querySelector(winner) : null;
      if (!element) {
        const next = resolveTarget(selector);
        if (next) {
          winner = next.selector;
          element = next.element;
        }
      }
      if (element) consider(element);
      frame = requestAnimationFrame(tick);
    };

    const start = () => {
      const initial = resolveTarget(selector);
      if (!initial) return false;

      started = true;
      if (timeout) {
        clearTimeout(timeout);
        timeout = null;
      }
      mutationObserver?.disconnect();
      mutationObserver = null;
      winner = initial.selector;

      // Bring the anchor on screen *before* the cutout opens.
      //
      // This used to be a smooth scroll, which fought the per-frame tracking:
      // the cutout chased the element down the page and settled late. The
      // engine now performs this while the previous cutout is closed and the
      // card is faded out, so an instant jump is not visible as movement — and
      // the first measurement is already the final one.
      //
      // Optional call on purpose: an environment without `scrollIntoView` must
      // still get a working spotlight rather than an exception mid-step.
      if (!isInView(initial.element.getBoundingClientRect())) {
        initial.element.scrollIntoView?.({ block: "center", behavior: "auto" });
      }

      tick();
      return true;
    };

    if (!start()) {
      // Wait for the element instead of guessing at a navigation delay.
      mutationObserver = new MutationObserver(() => {
        if (!started) start();
      });
      mutationObserver.observe(document.body, { childList: true, subtree: true });

      timeout = setTimeout(() => {
        if (started) return;
        mutationObserver?.disconnect();
        mutationObserver = null;
        setState({ status: "missing", selector });
      }, RESOLVE_TIMEOUT_MS);
    }

    return () => {
      if (frame) cancelAnimationFrame(frame);
      if (timeout) clearTimeout(timeout);
      mutationObserver?.disconnect();
    };
  }, [selector, padding, enabled]);

  return state;
}
