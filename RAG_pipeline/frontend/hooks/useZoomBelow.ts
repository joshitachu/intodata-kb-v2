import { useEffect, useState } from "react";
import { useStoreApi } from "@xyflow/react";

/**
 * Zoom-adaptive label rendering: returns whether the canvas zoom is below
 * `threshold`. Subscribes to the React Flow store imperatively and applies
 * hysteresis, so node components re-render ONLY when the boolean actually
 * flips — never on every zoom frame (critical on graphs with 100+ nodes).
 */
export function useZoomBelow(threshold: number, hysteresis = 0.05): boolean {
  const store = useStoreApi();
  const [below, setBelow] = useState(() => store.getState().transform[2] < threshold);

  useEffect(() => {
    setBelow(store.getState().transform[2] < threshold);
    return store.subscribe((state) => {
      const zoom = state.transform[2];
      setBelow((prev) => {
        // Hysteresis band: once below, stay below until clearly back above
        // the threshold (and vice versa) — no flip-flopping at the boundary.
        if (prev) return zoom < threshold + hysteresis;
        return zoom < threshold;
      });
    });
  }, [store, threshold, hysteresis]);

  return below;
}
