import { act, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useFitOnContainerResize } from "@/hooks/useFitOnContainerResize";

/**
 * jsdom implements no ResizeObserver and never lays anything out, so the real
 * one would never fire. This stub records the observed element and lets a test
 * drive the callback with explicit widths -- which is the only way to assert the
 * debounce and the first-observation rule at all.
 */
class StubResizeObserver {
  static instances: StubResizeObserver[] = [];
  readonly callback: ResizeObserverCallback;
  observed: Element[] = [];
  disconnected = false;

  constructor(callback: ResizeObserverCallback) {
    this.callback = callback;
    StubResizeObserver.instances.push(this);
  }

  observe(element: Element) {
    this.observed.push(element);
  }

  disconnect() {
    this.disconnected = true;
  }

  unobserve() {}

  emit(width: number) {
    this.callback(
      [{ contentRect: { width } } as unknown as ResizeObserverEntry],
      this as unknown as ResizeObserver,
    );
  }
}

function Host({
  fit,
  enabled = true,
}: {
  fit: () => void;
  enabled?: boolean;
}) {
  const ref = useFitOnContainerResize(fit, { enabled });
  return <div ref={ref} data-testid="column" />;
}

function latest() {
  const observer = StubResizeObserver.instances.at(-1);
  if (!observer) throw new Error("nothing observed the element");
  return observer;
}

describe("useFitOnContainerResize", () => {
  beforeEach(() => {
    StubResizeObserver.instances = [];
    vi.stubGlobal("ResizeObserver", StubResizeObserver);
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("observes the element the ref lands on", () => {
    const { getByTestId } = render(<Host fit={vi.fn()} />);

    expect(latest().observed).toEqual([getByTestId("column")]);
  });

  it("does not fit on the first observation", () => {
    // ResizeObserver fires once on observe with the current size. Treating that
    // as a change refits on mount, competing with the fitView ReactFlow already
    // ran and producing a visible double-fit on every navigation.
    const fit = vi.fn();
    render(<Host fit={fit} />);

    act(() => latest().emit(900));
    act(() => void vi.advanceTimersByTime(1000));

    expect(fit).not.toHaveBeenCalled();
  });

  it("fits once the width settles", () => {
    const fit = vi.fn();
    render(<Host fit={fit} />);

    act(() => latest().emit(900));
    act(() => latest().emit(700));
    act(() => void vi.advanceTimersByTime(1000));

    expect(fit).toHaveBeenCalledTimes(1);
  });

  it("collapses a whole drag into a single fit", () => {
    // A drag emits a resize per frame. Fitting per frame animates against itself
    // and makes the diagram crawl while the pointer is still moving.
    const fit = vi.fn();
    render(<Host fit={fit} />);

    act(() => latest().emit(900));
    for (const width of [860, 820, 780, 740, 700]) {
      act(() => latest().emit(width));
      act(() => void vi.advanceTimersByTime(20));
    }
    act(() => void vi.advanceTimersByTime(1000));

    expect(fit).toHaveBeenCalledTimes(1);
  });

  it("ignores sub-pixel and scrollbar-sized noise", () => {
    const fit = vi.fn();
    render(<Host fit={fit} />);

    act(() => latest().emit(900));
    act(() => latest().emit(903));
    act(() => void vi.advanceTimersByTime(1000));

    expect(fit).not.toHaveBeenCalled();
  });

  it("does not fit while disabled", () => {
    // Focus mode: the framing is the user's own, and a full-graph fit would
    // discard the neighbourhood they resized the panel in order to see.
    const fit = vi.fn();
    render(<Host fit={fit} enabled={false} />);

    act(() => latest().emit(900));
    act(() => latest().emit(700));
    act(() => void vi.advanceTimersByTime(1000));

    expect(fit).not.toHaveBeenCalled();
  });

  it("re-checks enabled when the timer fires, not when it is scheduled", () => {
    // A resize can start before the host enters focus mode and land after it.
    const fit = vi.fn();
    const { rerender } = render(<Host fit={fit} enabled />);

    act(() => latest().emit(900));
    act(() => latest().emit(700));
    rerender(<Host fit={fit} enabled={false} />);
    act(() => void vi.advanceTimersByTime(1000));

    expect(fit).not.toHaveBeenCalled();
  });

  it("keeps the width baseline when the host rebuilds the fit callback", () => {
    // `fit` closes over fitView and is rebuilt every render. Tearing the
    // observer down for that would lose the baseline and make the next resize
    // look like a first observation -- i.e. silently stop fitting.
    const { rerender } = render(<Host fit={vi.fn()} />);
    const observer = latest();
    act(() => observer.emit(900));

    const fit = vi.fn();
    rerender(<Host fit={fit} />);

    expect(StubResizeObserver.instances).toHaveLength(1);
    act(() => observer.emit(700));
    act(() => void vi.advanceTimersByTime(1000));
    expect(fit).toHaveBeenCalledTimes(1);
  });

  it("stops observing when the host unmounts", () => {
    const { unmount } = render(<Host fit={vi.fn()} />);
    const observer = latest();

    unmount();

    expect(observer.disconnected).toBe(true);
  });

  it("does not fit after unmount when a resize was already pending", () => {
    const fit = vi.fn();
    const { unmount } = render(<Host fit={fit} />);
    const observer = latest();

    act(() => observer.emit(900));
    act(() => observer.emit(700));
    unmount();
    act(() => void vi.advanceTimersByTime(1000));

    expect(fit).not.toHaveBeenCalled();
  });
});
