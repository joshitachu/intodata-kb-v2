import "@testing-library/jest-dom/vitest";

// matchMedia is referenced by useViewport (used by MessageBubble,
// ChatLayout, NotificationBell, and the mobile components). jsdom does
// not implement it, so provide a minimal stub that reports desktop
// (no matchMedia queries match by default). Tests that need to assert
// mobile behavior can override the stub via vi.spyOn.
if (typeof window !== "undefined" && !window.matchMedia) {
  Object.defineProperty(window, "matchMedia", {
    writable: true,
    configurable: true,
    value: (query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    }),
  });
}
