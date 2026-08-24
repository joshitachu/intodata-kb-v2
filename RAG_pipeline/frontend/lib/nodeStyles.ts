import type { CSSProperties } from "react";
import type { AssistantVisualRole } from "@/types/copilot";

// ── Shared topology node/edge visual language ─────────────────────────────
//
// One calm card style for every node on the topology canvases: white card,
// neutral border, a single 3px left accent bar carrying the group/role color.
// No gradients, no glow blooms. Dimmed nodes stay readable (grayscale + 35%
// opacity) instead of the old near-invisible 0.12 ghosting.

export const CARD_BORDER = "1px solid #e2e8f0";
export const CARD_SHADOW = "0 1px 2px rgba(15, 23, 42, 0.06)";
export const TITLE_COLOR = "#0f172a"; // slate-900
export const SUBTLE_TEXT_COLOR = "#64748b"; // slate-500

// Dimmed (search/hover/focus/failure-focus non-matches): de-emphasized but
// still readable — the surrounding context must remain legible.
export const DIMMED_STYLE: CSSProperties = {
  opacity: 0.35,
  filter: "grayscale(0.8)",
};

// Highlighted (hover/search/lineage match): one crisp accent ring, no bloom.
export function cardShadow(highlighted: boolean, accentColor: string): string {
  return highlighted ? `0 0 0 2px ${accentColor}` : CARD_SHADOW;
}

/**
 * Server-authored assistant roles need a stable visual hierarchy across every
 * topology depth. Primary is deliberately the strongest treatment; path is
 * warm and directional; supporting stays neutral and quieter.
 */
export function assistantRoleShadow(
  role: AssistantVisualRole | string | undefined,
): string | undefined {
  if (role === "primary") {
    return "0 0 0 4px #2563eb, 0 6px 16px rgba(37, 99, 235, 0.22)";
  }
  if (role === "path") return "0 0 0 3px #f59e0b";
  if (role === "supporting") return "0 0 0 2px #94a3b8";
  return undefined;
}

// The 3px left accent bar that carries a node's group/role identity color.
export function accentBarStyle(color: string, radius = 8): CSSProperties {
  return {
    position: "absolute",
    left: 0,
    top: 0,
    bottom: 0,
    width: 3,
    background: color,
    borderTopLeftRadius: radius,
    borderBottomLeftRadius: radius,
  };
}

// ── Edge palette ──────────────────────────────────────────────────────────
// Neutral by default; color is reserved for meaning (failing = red, see
// healthStatusMeta). Animation is reserved for failing/highlighted edges.

export const CALL_EDGE_COLOR = "#94a3b8"; // slate-400 — job→job orchestration
export const USES_EDGE_COLOR = "#c4b5fd"; // muted violet-300 — connection usage
export const AGGREGATE_EDGE_COLOR = "#64748b"; // slate-500 — inter-group rollup
export const DATA_EDGE_COLOR = "#64748b"; // slate-500 — level-2 data flow
export const CONTROL_EDGE_COLOR = "#cbd5e1"; // slate-300 — level-2 triggers

export const CALL_EDGE_STYLE: CSSProperties = {
  stroke: CALL_EDGE_COLOR,
  strokeWidth: 1.5,
};

export const USES_EDGE_STYLE: CSSProperties = {
  stroke: USES_EDGE_COLOR,
  strokeWidth: 1,
  strokeDasharray: "6 3",
};

// ── Zoom-adaptive labels ──────────────────────────────────────────────────
// Below this zoom level compact nodes render as text-free pills — smeared
// 4px labels carry no information and turn big graphs into noise.
export const LABEL_MIN_ZOOM = 0.45;

// ── Middle-ellipsis truncation ────────────────────────────────────────────
// CSS `truncate` (text-overflow: ellipsis) always cuts the END of a string.
// Job/connection names in this domain follow "Area_Stage_Entity_Variant"
// conventions where the trailing variant/suffix (_v1, _Set, _Item, _DB) is
// exactly what disambiguates otherwise-identical-looking siblings — end
// truncation cuts that part first, making different jobs look the same on
// the canvas. This keeps the head AND tail, eliding the middle instead. It's
// a character-count budget rather than real text measurement (no canvas
// available in every caller) — the full name remains available via the
// existing `title` tooltip regardless, so an imprecise fit is harmless; the
// CSS `truncate` class stays on callers as a backstop for any edge case this
// under/over-estimates.
export function middleEllipsis(text: string, maxChars: number): string {
  if (text.length <= maxChars) return text;
  const keep = maxChars - 1; // reserve 1 char for the ellipsis itself
  const head = Math.floor(keep / 2);
  const tail = keep - head;
  return `${text.slice(0, head)}…${text.slice(text.length - tail)}`;
}
