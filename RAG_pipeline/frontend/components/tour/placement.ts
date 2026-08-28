import { overlapArea, type TargetRect, type Viewport } from "./geometry";
import type { SpotlightSide } from "./types";

export const CARD_WIDTH = 372;
/** Distance between the card and the element it describes. */
const GAP = 16;
/** Minimum distance from the card to the edge of the viewport. */
const EDGE = 12;

export interface Placement {
  top: number;
  left: number;
  side: SpotlightSide;
}

type AnchoredSide = Exclude<SpotlightSide, "center">;

function clamp(value: number, min: number, max: number): number {
  return Math.min(Math.max(value, min), Math.max(min, max));
}

/** Where the card lands on a given side, after clamping into the viewport. */
function boxFor(
  side: AnchoredSide,
  rect: TargetRect,
  cardHeight: number,
  view: Viewport,
): Placement {
  const clampX = (value: number) => clamp(value, EDGE, view.width - CARD_WIDTH - EDGE);
  const clampY = (value: number) => clamp(value, EDGE, view.height - cardHeight - EDGE);
  const centreX = clampX(rect.left + rect.width / 2 - CARD_WIDTH / 2);
  const centreY = clampY(rect.top + rect.height / 2 - cardHeight / 2);

  if (side === "top") {
    return { top: clampY(rect.top - cardHeight - GAP), left: centreX, side };
  }
  if (side === "bottom") {
    return { top: clampY(rect.top + rect.height + GAP), left: centreX, side };
  }
  if (side === "left") {
    return { top: centreY, left: clampX(rect.left - CARD_WIDTH - GAP), side };
  }
  return { top: centreY, left: clampX(rect.left + rect.width + GAP), side };
}

/** The card centred in the viewport, used when there is nothing to anchor to. */
export function centred(cardHeight: number, view: Viewport): Placement {
  return {
    top: Math.max(EDGE, (view.height - cardHeight) / 2),
    left: Math.max(EDGE, (view.width - CARD_WIDTH) / 2),
    side: "center",
  };
}

/**
 * Position the card next to the target, flipping to whichever side works.
 *
 * The requested side is a preference, not a guarantee, and there are two
 * separate reasons to override it:
 *
 * - Room. A target near the bottom of the viewport with `side: "bottom"` would
 *   push the card off screen, which is how tour cards end up unreachable on
 *   small displays.
 * - Occlusion. Clamping a card back into the viewport can park it on top of the
 *   very element the step is describing — a card explaining the cost tile
 *   landing across the cost tile. Sides are scored by how much of the target
 *   they cover and the first side that covers none of it wins, so the
 *   preference order still decides between equally good options.
 */
export function place(
  rect: TargetRect | null,
  preferred: AnchoredSide,
  cardHeight: number,
  view: Viewport,
): Placement {
  if (!rect || !view.width || !view.height) return centred(cardHeight, view);

  const room = {
    top: rect.top - GAP,
    bottom: view.height - (rect.top + rect.height) - GAP,
    left: rect.left - GAP,
    right: view.width - (rect.left + rect.width) - GAP,
  };
  const fits = (side: AnchoredSide) =>
    side === "top" || side === "bottom"
      ? room[side] >= cardHeight
      : room[side] >= CARD_WIDTH;

  const order: AnchoredSide[] = [preferred, "bottom", "top", "right", "left"];
  let best: Placement | null = null;
  let bestScore = Number.POSITIVE_INFINITY;

  for (const side of order) {
    const candidate = boxFor(side, rect, cardHeight, view);
    const covered = overlapArea(
      {
        top: candidate.top,
        left: candidate.left,
        width: CARD_WIDTH,
        height: cardHeight,
      },
      rect,
    );
    // Not fitting is heavily penalised but not disqualifying: a scored
    // fallback still beats rendering the card off screen.
    const score = (fits(side) ? 0 : view.width * view.height) + covered;
    if (score < bestScore) {
      bestScore = score;
      best = candidate;
    }
    if (score === 0) break;
  }

  return best ?? boxFor(preferred, rect, cardHeight, view);
}
