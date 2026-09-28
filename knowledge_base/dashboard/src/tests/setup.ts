import "@testing-library/jest-dom/vitest";

// jsdom has no EventSource. Components that open a live log stream
// (LogPanel/useLogStream) only need a no-op
// implementation under test -- nothing here asserts on stream delivery.
if (typeof globalThis.EventSource === "undefined") {
  class NoopEventSource {
    onmessage: ((event: MessageEvent) => void) | null = null;
    onerror: ((event: Event) => void) | null = null;
    close(): void {}
  }
  // @ts-expect-error -- minimal test-only stand-in, not a spec-complete EventSource
  globalThis.EventSource = NoopEventSource;
}

// jsdom does not implement scrollIntoView (LogPanel's autoscroll-on-new-log
// effect calls it unconditionally).
if (!Element.prototype.scrollIntoView) {
  Element.prototype.scrollIntoView = () => {};
}

// Node 26 declares a `localStorage` global of its own that evaluates to
// `undefined` unless the process was started with `--localstorage-file`, and it
// shadows the one jsdom installs. Every component that reads a saved model
// choice (GenerateDialog) then throws before rendering. Restore a working
// in-memory Storage when the environment has not provided one -- the same stub
// RAG_pipeline/frontend/tests/setup.ts uses. It fills an environment gap and
// changes nothing a test observes.
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
