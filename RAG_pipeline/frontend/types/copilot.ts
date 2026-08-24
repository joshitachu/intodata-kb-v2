/**
 * Presentation contract for the docked copilot panel (`PageCopilot`).
 *
 * These types are deliberately page-agnostic: the panel renders targets,
 * a scope chip and notices without knowing what a landscape, a job or an
 * id space is. The host page resolves domain objects (see
 * `lib/topologyTargets.ts` for the topology surface) and hands the panel
 * these flat descriptors, so the same panel can be mounted on other pages
 * unchanged.
 */

/** Scope indicator, rendered as "kindLabel · name" (e.g. "Job · tJob_OrderSync"). */
export interface CopilotScope {
  /** What kind of thing is in view -- "Landscape", "Job", "Component". */
  kindLabel: string;
  /** Its name, when the level has one. Omitted at the root level. */
  name?: string;
}

/**
 * A place on the page an answer refers to. `key` is the identity used for
 * hover/click callbacks and React keys; the host maps it back to a real
 * navigation action.
 *
 * `resolvable: false` means the host could not match the target against the
 * data actually loaded on the page. Such a chip renders inert -- it must never
 * navigate, because it would navigate into an empty canvas.
 */
export interface CopilotTarget {
  key: string;
  label: string;
  /** Short level description shown under the label, e.g. "Job · tX". */
  detail?: string;
  resolvable: boolean;
  /** User-facing explanation, shown as the tooltip on an inert chip. */
  unresolvableReason?: string;
}

export type AssistantVisualRole = "primary" | "supporting" | "path";

/** Grounded emphasis projected into one currently mounted canvas. */
export interface AssistantCanvasEmphasis {
  nodeRoles: ReadonlyMap<string, AssistantVisualRole>;
  edgeRoles: ReadonlyMap<string, AssistantVisualRole>;
  /** `table::column`, used only by the mapping canvas. */
  columnRoles?: ReadonlyMap<string, AssistantVisualRole>;
}

/** One completed answer's exact primary focus request. */
export interface AssistantFocusRequest {
  key: string;
  nodeIds: readonly string[];
  edgeIds?: readonly string[];
}

export interface CopilotViewContext {
  selectedEntity?: {
    kind: "application" | "system" | "relationship" | "edge" | "group" | "job" | "component" | "table" | "column" | "mapping";
    label: string;
    entity_id?: string;
  };
  activeDetailTab?: string;
  filters?: Array<{ name: string; value: string }>;
}

/**
 * What actually backed the last answer.
 *
 * The panel is dressed in the chrome of the page it is docked to, which is an
 * implicit claim that the page's own artifact answered. When something else did
 * -- generated documentation, runtime data, or nothing at all -- that claim is
 * false and the answer has to say so where the answer is, not in a footnote.
 *
 * `attribution: "surface"` means the page's artifact backed it and the chrome
 * is honest. `"other"` means a different source answered. `"none"` means
 * nothing identifiable did, which is the case worth being loudest about.
 */
export interface CopilotAnswerSource {
  attribution: "surface" | "other" | "none";
  /** One short line, e.g. "Answered from documents, not the diagram". */
  label: string;
  /** Optional second line naming the source, e.g. "3 documents cited". */
  detail?: string;
}

/**
 * Degraded conditions that are part of the panel's normal vocabulary rather
 * than error handling bolted on afterwards. Copy has a sensible default per
 * kind and can be overridden per host.
 */
export type CopilotNoticeKind =
  /** Business overlay exists but is unvalidated or stale: names are scanner-derived. */
  | "stale-overlay"
  /** No published artifact for this project: the page has nothing to answer from. */
  | "missing-artifact"
  /** The answering service is unreachable. The page's own rendering is unaffected. */
  | "service-unreachable";

export interface CopilotNotice {
  kind: CopilotNoticeKind;
  /** Overrides the built-in title for this kind. */
  title?: string;
  /** Overrides the built-in detail line for this kind. */
  detail?: string;
}
