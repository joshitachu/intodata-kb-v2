import { ChatLaneSchema, type ChatLane } from "@/types";

/**
 * The chat lane vocabulary, in one place.
 *
 * There used to be two chat surfaces: production at `/` and a cloned
 * `/labs/chat` that mounted a second `ChatProvider` with `apiBase="/api/labs"`.
 * They are one surface now, and the lane is the only thing that differs -- so
 * the mapping from lane to backend base, the labels on the switch and the
 * badges, and the filter vocabulary all live here rather than being restated in
 * the composer, the sidebar and the provider.
 */

/** The lane a brand-new thread gets unless the user picks otherwise. */
export const DEFAULT_CHAT_LANE: ChatLane = "v1";

/** Display order for the switch and the filter. */
export const CHAT_LANES: readonly ChatLane[] = ["v1", "v2"] as const;

/**
 * Backend base per lane.
 *
 * `v1` is the production LangGraph harness; `v2` is the OpenCode lane, whose
 * routes live under `/api/labs`. Note that the *conversation list* is not
 * routed through this map -- see `ChatProvider`, which pins it to `/api`
 * because that endpoint returns both lanes.
 */
export const LANE_API_BASE: Readonly<Record<ChatLane, string>> = {
  v1: "/api",
  v2: "/api/labs",
};

/** Short label for the switch and the row badges. */
export const LANE_LABEL: Readonly<Record<ChatLane, string>> = {
  v1: "V1",
  v2: "V2",
};

/** One line of "what am I choosing", for tooltips and screen readers. */
export const LANE_DESCRIPTION: Readonly<Record<ChatLane, string>> = {
  v1: "V1 - production harness",
  v2: "V2 - OpenCode lane",
};

/** Thread-list filter: either every lane, or exactly one. */
export type LaneFilter = "all" | ChatLane;

export const LANE_FILTERS: readonly LaneFilter[] = ["all", "v1", "v2"] as const;

export const LANE_FILTER_LABEL: Readonly<Record<LaneFilter, string>> = {
  all: "All",
  v1: LANE_LABEL.v1,
  v2: LANE_LABEL.v2,
};

/**
 * Narrow an untrusted value (a URL parameter, a stored preference) to a lane.
 *
 * Returns `null` rather than the default so a caller can tell "the user asked
 * for nothing" apart from "the user asked for V1".
 */
export function parseLane(value: unknown): ChatLane | null {
  const parsed = ChatLaneSchema.safeParse(value);
  return parsed.success ? parsed.data : null;
}

/** Whether a conversation belongs in the list under the given filter. */
export function laneMatchesFilter(lane: ChatLane, filter: LaneFilter): boolean {
  return filter === "all" || filter === lane;
}
