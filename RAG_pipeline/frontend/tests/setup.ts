import "@testing-library/jest-dom/vitest";

// matchMedia is referenced by useViewport (used by MessageBubble,
// ChatLayout, NotificationBell, and the mobile components). jsdom does
// not implement it, so provide a minimal stub that reports desktop
// (no matchMedia queries match by default). Tests that need to assert
// mobile behavior can override the stub via vi.spyOn.
// Radix popper-based primitives (SelectMenu, tooltips) observe their trigger.
// jsdom has no ResizeObserver, so provide an inert stub.
if (typeof globalThis !== "undefined" && !("ResizeObserver" in globalThis)) {
  class ResizeObserverStub {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
  (globalThis as { ResizeObserver?: unknown }).ResizeObserver = ResizeObserverStub;
}

// Node 26 declares a `localStorage` global of its own, but it evaluates to
// `undefined` unless the process was started with `--localstorage-file`, and it
// takes precedence over the one jsdom installs. The result is that
// `window.localStorage` is undefined, so every `window.localStorage.clear()` in
// a `beforeEach` throws before a single component renders -- 59 tests across 6
// files, none of which are about storage. Restore a working in-memory Storage
// when the environment has not provided one. Same intent as the ResizeObserver
// and matchMedia stubs: fill a gap the DOM environment is meant to cover, never
// change what a test observes.
if (typeof window !== "undefined" && !window.localStorage) {
  const entries = new Map<string, string>();
  const storage: Storage = {
    get length() {
      return entries.size;
    },
    key: (index: number) => Array.from(entries.keys())[index] ?? null,
    getItem: (key: string) => (entries.has(String(key)) ? entries.get(String(key))! : null),
    setItem: (key: string, value: string) => {
      entries.set(String(key), String(value));
    },
    removeItem: (key: string) => {
      entries.delete(String(key));
    },
    clear: () => {
      entries.clear();
    },
  };
  for (const target of [window, globalThis]) {
    Object.defineProperty(target, "localStorage", {
      writable: true,
      configurable: true,
      value: storage,
    });
  }
}

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
