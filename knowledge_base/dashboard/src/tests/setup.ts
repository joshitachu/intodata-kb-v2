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
