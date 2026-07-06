import { describe, it, expect, vi, afterEach } from "vitest";
import { renderHook, act, waitFor } from "@testing-library/react";
import { useVisualViewport } from "@/hooks/useVisualViewport";

interface VisualViewportMock {
  height: number;
  listeners: { resize: Set<() => void>; scroll: Set<() => void> };
  addEventListener: (type: "resize" | "scroll", cb: () => void) => void;
  removeEventListener: (type: "resize" | "scroll", cb: () => void) => void;
}

function makeVisualViewport(initialHeight: number): VisualViewportMock {
  const listeners = { resize: new Set<() => void>(), scroll: new Set<() => void>() };
  return {
    height: initialHeight,
    listeners,
    addEventListener: (type, cb) => listeners[type].add(cb),
    removeEventListener: (type, cb) => listeners[type].delete(cb),
  };
}

describe("useVisualViewport", () => {
  const original = Object.getOwnPropertyDescriptor(window, "visualViewport");

  afterEach(() => {
    if (original) {
      Object.defineProperty(window, "visualViewport", original);
    } else {
      // @ts-expect-error cleanup when no original
      delete window.visualViewport;
    }
    vi.restoreAllMocks();
  });

  it("returns zeros when visualViewport is unsupported", () => {
    // @ts-expect-error simulate missing API
    delete window.visualViewport;
    const { result } = renderHook(() => useVisualViewport());
    expect(result.current).toEqual({ keyboardHeight: 0, viewportHeight: 0 });
  });

  it("reports the initial viewport height", async () => {
    const vv = makeVisualViewport(800);
    Object.defineProperty(window, "visualViewport", { configurable: true, value: vv });

    const { result } = renderHook(() => useVisualViewport());
    await waitFor(() => {
      expect(result.current.viewportHeight).toBe(800);
    });
    expect(result.current.keyboardHeight).toBe(0);
  });

  it("computes keyboard height when the viewport shrinks", async () => {
    const vv = makeVisualViewport(800);
    Object.defineProperty(window, "visualViewport", { configurable: true, value: vv });

    const { result } = renderHook(() => useVisualViewport());
    await waitFor(() => {
      expect(result.current.viewportHeight).toBe(800);
    });

    act(() => {
      vv.height = 400;
      vv.listeners.resize.forEach((cb) => cb());
    });

    await waitFor(() => {
      expect(result.current.keyboardHeight).toBe(400);
    });
    expect(result.current.viewportHeight).toBe(400);
  });
});
