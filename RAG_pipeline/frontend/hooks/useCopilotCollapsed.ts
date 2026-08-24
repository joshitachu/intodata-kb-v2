"use client";

import { useCallback, useState } from "react";

function readStored(storageKey: string): boolean | null {
  // localStorage throws (not returns null) in some privacy modes, and is absent
  // during SSR -- neither is a reason to fail rendering the panel.
  try {
    if (typeof window === "undefined") return null;
    const stored = window.localStorage.getItem(storageKey);
    if (stored === "true") return true;
    if (stored === "false") return false;
    return null;
  } catch {
    return null;
  }
}

/**
 * Collapsed state for a docked copilot panel, remembered across visits.
 *
 * The panel competes for horizontal space with the page's own detail panels, so
 * whether it is open is a real preference rather than incidental UI state: a
 * user who collapsed it should not have to collapse it again after every
 * navigation. `collapsedByDefault` applies only until the user decides once
 * (viewports too narrow for three columns start collapsed).
 */
export function useCopilotCollapsed(
  storageKey: string,
  collapsedByDefault: boolean,
): [boolean, (collapsed: boolean) => void] {
  const [collapsed, setCollapsed] = useState<boolean>(
    () => readStored(storageKey) ?? collapsedByDefault,
  );

  const persist = useCallback(
    (next: boolean) => {
      setCollapsed(next);
      try {
        window.localStorage.setItem(storageKey, String(next));
      } catch {
        // A rejected write only costs the preference, not the interaction.
      }
    },
    [storageKey],
  );

  return [collapsed, persist];
}
