import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useViewport } from "@/hooks/useViewport";

type Listener = (event: { matches: boolean }) => void;

interface MqlMock {
  matches: boolean;
  media: string;
  addEventListener: (type: "change", listener: Listener) => void;
  removeEventListener: (type: "change", listener: Listener) => void;
  addListener: (listener: Listener) => void;
  removeListener: (listener: Listener) => void;
}

const mqlRegistry: { mql: MqlMock; listeners: Set<Listener> }[] = [];

function installMatchMedia(getMatches: (media: string) => boolean) {
  mqlRegistry.length = 0;
  Object.defineProperty(window, "matchMedia", {
    writable: true,
    configurable: true,
    value: vi.fn((media: string) => {
      const listeners = new Set<Listener>();
      const mql: MqlMock = {
        media,
        get matches() {
          return getMatches(media);
        },
        addEventListener: (_type, listener) => listeners.add(listener),
        removeEventListener: (_type, listener) => listeners.delete(listener),
        addListener: (listener) => listeners.add(listener),
        removeListener: (listener) => listeners.delete(listener),
      };
      mqlRegistry.push({ mql, listeners });
      return mql;
    }),
  });
}

function fireChange(media: string, matches: boolean) {
  for (const entry of mqlRegistry) {
    if (entry.mql.media !== media) continue;
    Object.defineProperty(entry.mql, "matches", { value: matches, configurable: true });
    entry.listeners.forEach((l) => l({ matches }));
  }
}

describe("useViewport", () => {
  const originalMatchMedia = window.matchMedia;
  const originalInnerWidth = Object.getOwnPropertyDescriptor(window, "innerWidth");

  beforeEach(() => {
    if (originalInnerWidth) {
      Object.defineProperty(window, "innerWidth", { ...originalInnerWidth, configurable: true });
    } else {
      Object.defineProperty(window, "innerWidth", { configurable: true, value: 1024 });
    }
  });

  afterEach(() => {
    window.matchMedia = originalMatchMedia;
    if (originalInnerWidth) {
      Object.defineProperty(window, "innerWidth", originalInnerWidth);
    }
  });

  it("reports isMobile when inner width is below 640", () => {
    Object.defineProperty(window, "innerWidth", { configurable: true, value: 375 });
    installMatchMedia(() => false);
    const { result } = renderHook(() => useViewport());
    expect(result.current).toEqual({ isMobile: true, isTablet: false, isDesktop: false });
  });

  it("reports isTablet for widths 640-1023", () => {
    Object.defineProperty(window, "innerWidth", { configurable: true, value: 768 });
    installMatchMedia(() => true);
    const { result } = renderHook(() => useViewport());
    expect(result.current).toEqual({ isMobile: false, isTablet: true, isDesktop: false });
  });

  it("reports isDesktop for widths >= 1024", () => {
    Object.defineProperty(window, "innerWidth", { configurable: true, value: 1440 });
    installMatchMedia(() => true);
    const { result } = renderHook(() => useViewport());
    expect(result.current).toEqual({ isMobile: false, isTablet: false, isDesktop: true });
  });

  it("updates when a matchMedia change fires", () => {
    Object.defineProperty(window, "innerWidth", { configurable: true, value: 500 });
    installMatchMedia(() => false);
    const { result } = renderHook(() => useViewport());
    expect(result.current.isMobile).toBe(true);

    Object.defineProperty(window, "innerWidth", { configurable: true, value: 1200 });
    act(() => {
      fireChange("(min-width: 640px)", true);
      fireChange("(min-width: 1024px)", true);
    });
    expect(result.current).toEqual({ isMobile: false, isTablet: false, isDesktop: true });
  });
});
