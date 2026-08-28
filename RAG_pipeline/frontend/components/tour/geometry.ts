/**
 * Geometry shared by the walkthrough overlay, card and target tracker.
 *
 * Kept in one place because the spotlight, the card placement and the
 * "is this even worth spotlighting" verdict must agree on the same numbers —
 * three components deriving their own idea of the target box is how a cutout
 * ends up misaligned with the ring drawn over it.
 */

export interface TargetRect {
  top: number;
  left: number;
  width: number;
  height: number;
}

export interface Viewport {
  width: number;
  height: number;
}

/**
 * Fraction of the viewport a cutout may cover before it stops meaning anything.
 *
 * Several tour anchors are page-root containers, so their box is the whole
 * page. Dimming everything except 95% of the screen does not highlight — it
 * just draws a border around the app and reads as a broken popup. Past this
 * ratio the engine drops the cutout and centres the card instead, which at
 * least honestly says "this is about this page".
 */
export const OVERSIZE_AREA_RATIO = 0.55;

/** Area of the part of `rect` that is actually on screen. */
function visibleArea(rect: TargetRect, viewport: Viewport): number {
  const width = Math.max(0, Math.min(rect.width, viewport.width));
  const height = Math.max(0, Math.min(rect.height, viewport.height));
  return width * height;
}

/**
 * True when the target is too large for a cutout to communicate anything.
 *
 * Returns false for an unmeasured viewport: guessing "oversize" with no
 * dimensions would silently disable every spotlight in an environment without
 * layout (jsdom, SSR).
 */
export function isOversize(rect: TargetRect, viewport: Viewport): boolean {
  if (!viewport.width || !viewport.height) return false;
  const screen = viewport.width * viewport.height;
  return visibleArea(rect, viewport) / screen > OVERSIZE_AREA_RATIO;
}

/**
 * The same box with no size, centred where it was.
 *
 * Used as the closed state of the cutout: transitioning the mask geometry to a
 * zero-size rect at the old centre reads as an aperture closing, where
 * unmounting the rect would pop the highlight off in one frame.
 */
export function collapse(rect: TargetRect): TargetRect {
  return {
    top: rect.top + rect.height / 2,
    left: rect.left + rect.width / 2,
    width: 0,
    height: 0,
  };
}

/** Overlapping area of two boxes. Zero when they do not intersect. */
export function overlapArea(a: TargetRect, b: TargetRect): number {
  const width = Math.min(a.left + a.width, b.left + b.width) - Math.max(a.left, b.left);
  const height = Math.min(a.top + a.height, b.top + b.height) - Math.max(a.top, b.top);
  if (width <= 0 || height <= 0) return 0;
  return width * height;
}

/** Sub-pixel-tolerant comparison, so scroll jitter does not re-render. */
export function sameRect(a: TargetRect | null, b: TargetRect | null): boolean {
  if (!a || !b) return a === b;
  return (
    Math.abs(a.top - b.top) < 0.5 &&
    Math.abs(a.left - b.left) < 0.5 &&
    Math.abs(a.width - b.width) < 0.5 &&
    Math.abs(a.height - b.height) < 0.5
  );
}
