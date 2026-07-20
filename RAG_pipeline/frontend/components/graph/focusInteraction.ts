import { createContext, useContext } from "react";

// Bridges the project-level Focus mode gestures from the topology page down into
// the JobNode custom node, WITHOUT putting callbacks on node `data` (which would
// break the reference-stability the topology relayout/poll optimisations depend
// on). The provider value is memoised in the page, so consuming JobNodes only
// re-render when focus identity actually changes.
export interface FocusInteraction {
  /** Whether Focus mode is currently active (a focal job is selected). */
  active: boolean;
  /** The focal job's name while active, else null. */
  focalJobName: string | null;
  /** Enter/re-centre Focus mode on a job (toggles off if already focal). */
  onFocusJob: (jobName: string) => void;
  /** Push the focus horizon out by one hop in both directions. */
  onExpandHops: () => void;
}

export const FocusInteractionContext = createContext<FocusInteraction | null>(null);

export function useFocusInteraction(): FocusInteraction | null {
  return useContext(FocusInteractionContext);
}
