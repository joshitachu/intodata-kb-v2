import { describe, it, expect } from "vitest";
import { act, renderHook } from "@testing-library/react";
import { ReactFlowProvider, useStoreApi } from "@xyflow/react";
import type { ReactNode } from "react";
import { useZoomBelow } from "@/hooks/useZoomBelow";

function wrapper({ children }: { children: ReactNode }) {
  return <ReactFlowProvider>{children}</ReactFlowProvider>;
}

// Writes the viewport zoom straight into the React Flow store — the hook
// under test subscribes to that store, so this exercises exactly its
// subscription + hysteresis logic without needing a mounted pane (d3-zoom
// ignores setViewport when no canvas is rendered in jsdom).
function useHarness(threshold: number, hysteresis?: number) {
  const store = useStoreApi();
  const below = useZoomBelow(threshold, hysteresis);
  return {
    below,
    setZoom: (zoom: number) => store.setState({ transform: [0, 0, zoom] }),
  };
}

describe("useZoomBelow", () => {
  it("is false at the default zoom (1) and true once zoomed out past the threshold", () => {
    const { result } = renderHook(() => useHarness(0.45), { wrapper });
    expect(result.current.below).toBe(false);

    act(() => result.current.setZoom(0.2));
    expect(result.current.below).toBe(true);

    act(() => result.current.setZoom(1));
    expect(result.current.below).toBe(false);
  });

  it("applies hysteresis: inside the band the previous state is kept", () => {
    const { result } = renderHook(() => useHarness(0.45, 0.05), { wrapper });

    // Zoom into the band from above (0.47 < 0.45 + 0.05 but > 0.45): stays false.
    act(() => result.current.setZoom(0.47));
    expect(result.current.below).toBe(false);

    // Cross below the threshold: flips.
    act(() => result.current.setZoom(0.4));
    expect(result.current.below).toBe(true);

    // Back into the band from below: stays true until clearly above.
    act(() => result.current.setZoom(0.47));
    expect(result.current.below).toBe(true);

    act(() => result.current.setZoom(0.6));
    expect(result.current.below).toBe(false);
  });
});
