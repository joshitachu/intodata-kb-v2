"use client";

import { useId, useRef } from "react";
import { collapse, type TargetRect, type Viewport } from "./geometry";

interface Props {
  /** Null renders a plain scrim: a centered step, or a target too big to mean anything. */
  rect: TargetRect | null;
  /** False collapses the cutout to a point and hides the ring. */
  open: boolean;
  viewport: Viewport;
  onDismiss: () => void;
}

const CORNER_RADIUS = 10;
/** Must match CLOSE_MS in useTourStage, or the aperture opens before it shut. */
const MOVE_MS = 240;
const EASE = "cubic-bezier(0.32, 0.72, 0, 1)";

const GEOMETRY_TRANSITION = ["x", "y", "width", "height"]
  .map((property) => `${property} ${MOVE_MS}ms ${EASE}`)
  .join(", ");

/**
 * Dimming layer with a cutout over the current tour target.
 *
 * Implemented as a single SVG rect with a mask rather than the four-div
 * technique: one animating shape means the highlight glides between targets
 * instead of four edges independently snapping, and the cutout stays exact for
 * rounded elements.
 *
 * Two things here are structural, not cosmetic:
 *
 * 1. This component stays mounted for the entire tour, including across route
 *    changes. It used to unmount whenever the next step's anchor had not
 *    resolved yet, so the page flashed undimmed between every pair of steps.
 * 2. The cutout rect is always rendered, even with no target. Closing it means
 *    animating its geometry to zero size, not unmounting it — an unmounted rect
 *    pops the highlight off in a single frame, which reads as a glitch.
 *
 * The overlay is click-through over the cutout so the "try it" affordances stay
 * usable — a tour the user cannot interact with is a slideshow.
 */
export function SpotlightOverlay({ rect, open, viewport, onDismiss }: Props) {
  const maskId = useId();

  // Last real box, so the aperture has somewhere to close to when the target
  // goes away with the page it lived on.
  const painted = useRef<TargetRect | null>(null);
  if (rect) painted.current = rect;

  if (!viewport.width || !viewport.height) return null;

  const centre: TargetRect = painted.current ?? {
    top: viewport.height / 2,
    left: viewport.width / 2,
    width: 0,
    height: 0,
  };
  const highlighted = open && rect ? rect : collapse(centre);
  const showRing = open && Boolean(rect);

  return (
    <div className="tour-scrim pointer-events-none fixed inset-0 z-[100]">
      <svg
        width="100%"
        height="100%"
        className="pointer-events-none absolute inset-0"
        aria-hidden
      >
        <defs>
          <mask id={maskId}>
            <rect width="100%" height="100%" fill="white" />
            <rect
              x={highlighted.left}
              y={highlighted.top}
              width={Math.max(highlighted.width, 0)}
              height={Math.max(highlighted.height, 0)}
              rx={CORNER_RADIUS}
              fill="black"
              data-spotlight-animated
              // Transitioning geometry attributes (not transform) keeps the
              // cutout aligned to the target at every frame of the move.
              style={{ transition: GEOMETRY_TRANSITION }}
            />
          </mask>
        </defs>
        <rect
          width="100%"
          height="100%"
          // Deliberately a fixed dark scrim rather than a theme token: this
          // dims the app in both light and dark mode, where `background` would
          // invert and stop reading as "behind a veil" in one of them.
          fill="hsl(222 47% 8% / 0.62)"
          mask={`url(#${maskId})`}
        />
      </svg>

      {/* Ring drawn outside the mask so it is not clipped by the cutout. It is
          kept mounted and faded rather than conditionally rendered, so it can
          shrink away with the aperture instead of disappearing in one frame. */}
      <div
        data-spotlight-animated
        aria-hidden
        className="pointer-events-none absolute rounded-[10px] ring-2 ring-primary/70"
        style={{
          top: highlighted.top,
          left: highlighted.left,
          width: Math.max(highlighted.width, 0),
          height: Math.max(highlighted.height, 0),
          opacity: showRing ? 1 : 0,
          boxShadow: "0 0 0 6px hsl(var(--primary) / 0.14)",
          transition: `top ${MOVE_MS}ms ${EASE}, left ${MOVE_MS}ms ${EASE}, width ${MOVE_MS}ms ${EASE}, height ${MOVE_MS}ms ${EASE}, opacity 160ms linear`,
        }}
      />

      {/* Only the dimmed area dismisses; clicks inside the cutout reach the app
          so a "try it" step can actually be tried.

          Deliberately not a labelled button: the card's close control and the
          Escape key are the accessible ways out, and exposing this scrim as a
          second "Close walkthrough" button gives assistive tech two controls
          for one action with no way to tell them apart. */}
      <div
        aria-hidden
        onClick={onDismiss}
        className="pointer-events-auto absolute inset-0 cursor-default"
        style={
          showRing && rect
            ? {
                clipPath: `polygon(
                  0 0, 100% 0, 100% 100%, 0 100%, 0 0,
                  ${rect.left}px ${rect.top}px,
                  ${rect.left}px ${rect.top + rect.height}px,
                  ${rect.left + rect.width}px ${rect.top + rect.height}px,
                  ${rect.left + rect.width}px ${rect.top}px,
                  ${rect.left}px ${rect.top}px
                )`,
              }
            : undefined
        }
      />
    </div>
  );
}
