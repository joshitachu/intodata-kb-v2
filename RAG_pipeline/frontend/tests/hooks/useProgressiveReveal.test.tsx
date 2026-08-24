import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  revealDurationMs,
  useProgressiveReveal,
  type ProgressiveRevealInput,
} from "@/hooks/useProgressiveReveal";

/**
 * The reveal is driven by requestAnimationFrame and a monotonic clock, so every
 * test here fakes both -- otherwise "within 600ms" would be a race against the
 * machine running the suite.
 */
function useRevealTimers() {
  vi.useFakeTimers({
    toFake: [
      "requestAnimationFrame",
      "cancelAnimationFrame",
      "performance",
      "Date",
      "setTimeout",
      "clearTimeout",
    ],
  });
}

const ANSWER =
  "SAP ERP receives order data from the warehouse job, which reads the staging table first.";

function reducedMotion(matches: boolean) {
  vi.spyOn(window, "matchMedia").mockImplementation((query: string) => ({
    matches: query.includes("prefers-reduced-motion") ? matches : false,
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  }) as MediaQueryList);
}

describe("useProgressiveReveal", () => {
  beforeEach(() => {
    useRevealTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("prices a typical answer in a few hundred milliseconds and caps very long ones", () => {
    expect(revealDurationMs(0)).toBe(0);
    expect(revealDurationMs(400)).toBeLessThan(320);
    expect(revealDurationMs(800)).toBeLessThan(400);
    // Sub-linear: 5x the text is nowhere near 5x the time.
    expect(revealDurationMs(4000)).toBeLessThan(revealDurationMs(800) * 2);
    expect(revealDurationMs(100_000)).toBe(600);
  });

  it("reveals received text progressively and never more than has arrived", () => {
    const { result, rerender } = renderHook(
      (input: ProgressiveRevealInput) => useProgressiveReveal(input),
      { initialProps: { text: "", turnKey: "stream", isLive: true } },
    );

    // The whole answer lands at once -- the backend fires every chunk in one
    // tight loop -- so the reveal is what makes it progressive.
    rerender({ text: ANSWER, turnKey: "stream", isLive: true });
    expect(result.current.text).toBe("");

    act(() => vi.advanceTimersByTime(60));
    const partial = result.current.text;
    expect(partial.length).toBeGreaterThan(0);
    expect(partial.length).toBeLessThan(ANSWER.length);
    expect(ANSWER.startsWith(partial)).toBe(true);
    expect(result.current.isRevealing).toBe(true);
  });

  it("never reveals text that has not been received", () => {
    const { result, rerender } = renderHook(
      (input: ProgressiveRevealInput) => useProgressiveReveal(input),
      { initialProps: { text: "", turnKey: "stream", isLive: true } },
    );

    rerender({ text: "First half. ", turnKey: "stream", isLive: true });
    act(() => vi.advanceTimersByTime(1000));
    // Everything received, nothing invented.
    expect(result.current.text).toBe("First half. ");

    rerender({ text: "First half. Second half.", turnKey: "stream", isLive: true });
    expect(result.current.text.length).toBeLessThanOrEqual("First half. Second half.".length);
    expect("First half. Second half.".startsWith(result.current.text)).toBe(true);
  });

  it("finishes a long answer inside the stated bound and converges instead of queueing", () => {
    const long = "x".repeat(8000);
    const { result, rerender } = renderHook(
      (input: ProgressiveRevealInput) => useProgressiveReveal(input),
      { initialProps: { text: "", turnKey: "stream", isLive: true } },
    );

    rerender({ text: long.slice(0, 400), turnKey: "stream", isLive: true });
    // First frame: the reveal clock starts at the first character, not at the
    // turn, so a long tool phase does not eat the animation's budget.
    act(() => vi.advanceTimersByTime(16));
    // More arrives mid-reveal: the reveal converges on the new end rather than
    // animating each chunk in sequence.
    rerender({ text: long, turnKey: "stream", isLive: false });

    act(() => vi.advanceTimersByTime(300));
    expect(result.current.text.length).toBeLessThan(long.length);

    // The 600ms ceiling, plus two frames of scheduling slack.
    act(() => vi.advanceTimersByTime(332));
    expect(result.current.text).toBe(long);
    expect(result.current.isRevealing).toBe(false);
  });

  it("does not restart when a parent re-renders mid-reveal", () => {
    const { result, rerender } = renderHook(
      (input: ProgressiveRevealInput) => useProgressiveReveal(input),
      { initialProps: { text: "", turnKey: "stream", isLive: true } },
    );

    rerender({ text: ANSWER, turnKey: "stream", isLive: true });
    act(() => vi.advanceTimersByTime(100));
    const before = result.current.text;
    expect(before.length).toBeGreaterThan(0);

    // Three parent re-renders with identical input: progress lives in a ref
    // keyed to the turn, so none of them may rewind the reveal.
    rerender({ text: ANSWER, turnKey: "stream", isLive: true });
    rerender({ text: ANSWER, turnKey: "stream", isLive: true });
    rerender({ text: ANSWER, turnKey: "stream", isLive: true });
    expect(result.current.text.length).toBeGreaterThanOrEqual(before.length);
    expect(ANSWER.startsWith(result.current.text)).toBe(true);
  });

  it("carries a reveal across the stream-to-persisted hand-off", () => {
    const { result, rerender } = renderHook(
      (input: ProgressiveRevealInput) => useProgressiveReveal(input),
      { initialProps: { text: "", turnKey: "stream", isLive: true } },
    );

    rerender({ text: ANSWER, turnKey: "stream", isLive: true });
    act(() => vi.advanceTimersByTime(100));
    const midStream = result.current.text;
    expect(midStream.length).toBeLessThan(ANSWER.length);

    // The host refetched the thread and dropped the buffer: same answer, new
    // source. The reveal must continue, not restart and not jump to the end.
    rerender({ text: ANSWER, turnKey: "msg:900", isLive: false });
    expect(result.current.text.length).toBeGreaterThanOrEqual(midStream.length);
    expect(result.current.text.length).toBeLessThan(ANSWER.length);
    expect(result.current.isRevealing).toBe(true);

    act(() => vi.advanceTimersByTime(600));
    expect(result.current.text).toBe(ANSWER);
    expect(result.current.isRevealing).toBe(false);
  });

  it("survives the gap where the buffer is cleared before the message lands", () => {
    const initialProps: ProgressiveRevealInput = { text: "", turnKey: "stream", isLive: true };
    const { result, rerender } = renderHook(
      (input: ProgressiveRevealInput) => useProgressiveReveal(input),
      { initialProps },
    );

    rerender({ text: ANSWER, turnKey: "stream", isLive: true });
    act(() => vi.advanceTimersByTime(100));
    const midStream = result.current.text;
    expect(midStream.length).toBeLessThan(ANSWER.length);

    // The host reset the stream and the refetch has not returned yet: for a
    // beat there is no answer on screen at all.
    rerender({ text: "", turnKey: null, isLive: false });
    expect(result.current.text).toBe("");

    // The saved message arrives and the reveal picks up where it left off.
    rerender({ text: ANSWER, turnKey: "msg:900", isLive: false });
    expect(result.current.text.length).toBeGreaterThanOrEqual(midStream.length);
    expect(result.current.text.length).toBeLessThan(ANSWER.length);

    act(() => vi.advanceTimersByTime(600));
    expect(result.current.text).toBe(ANSWER);
  });

  it("renders history in full: only a live source first seen empty animates", () => {
    const { result } = renderHook(() =>
      useProgressiveReveal({ text: ANSWER, turnKey: "msg:12", isLive: false }),
    );

    expect(result.current.text).toBe(ANSWER);
    expect(result.current.isRevealing).toBe(false);
  });

  it("reveals instantly when prefers-reduced-motion is set", () => {
    reducedMotion(true);
    const { result, rerender } = renderHook(
      (input: ProgressiveRevealInput) => useProgressiveReveal(input),
      { initialProps: { text: "", turnKey: "stream", isLive: true } },
    );

    rerender({ text: ANSWER, turnKey: "stream", isLive: true });
    expect(result.current.text).toBe(ANSWER);
    expect(result.current.isRevealing).toBe(false);
  });

  it("completes instantly on demand and stays complete as more text arrives", () => {
    const { result, rerender } = renderHook(
      (input: ProgressiveRevealInput) => useProgressiveReveal(input),
      { initialProps: { text: "", turnKey: "stream", isLive: true } },
    );

    rerender({ text: ANSWER, turnKey: "stream", isLive: true });
    act(() => vi.advanceTimersByTime(32));
    expect(result.current.text.length).toBeLessThan(ANSWER.length);

    act(() => result.current.complete());
    expect(result.current.text).toBe(ANSWER);
    expect(result.current.isRevealing).toBe(false);

    rerender({ text: `${ANSWER} And one more sentence.`, turnKey: "stream", isLive: true });
    expect(result.current.text).toBe(`${ANSWER} And one more sentence.`);
  });

  it("treats a shorter text under the same key as the next turn", () => {
    const { result, rerender } = renderHook(
      (input: ProgressiveRevealInput) => useProgressiveReveal(input),
      { initialProps: { text: "", turnKey: "stream", isLive: true } },
    );

    rerender({ text: ANSWER, turnKey: "stream", isLive: true });
    act(() => vi.advanceTimersByTime(1000));
    expect(result.current.text).toBe(ANSWER);

    // The buffer is reused by the next question: nothing of the old answer may
    // survive into the new turn.
    rerender({ text: "", turnKey: "stream", isLive: true });
    expect(result.current.text).toBe("");

    rerender({ text: "A different answer entirely.", turnKey: "stream", isLive: true });
    act(() => vi.advanceTimersByTime(1000));
    expect(result.current.text).toBe("A different answer entirely.");
  });

  it("shows text that arrives after the reveal has finished, under the same key", () => {
    /**
     * The observed bug: the panel stopped applying updates MID-WORD -- three
     * rows of a nine-row table, ending at "(confir" -- and the rest of the
     * answer, already in the client, appeared only after a page reload.
     *
     * Only `active` schedules frames and only a frame advances the cursor, so
     * once the stream ends and the reveal catches up, `active` is cleared. The
     * stream key is a CONSTANT for the whole turn, so any text arriving after
     * that point hit the same-key early return and was frozen out permanently.
     */
    const { result, rerender } = renderHook(
      (input: ProgressiveRevealInput) => useProgressiveReveal(input),
      { initialProps: { text: "", turnKey: "stream", isLive: true } },
    );

    const head = "Six streams run from Maximo to 4PS: Work Orders (confir";

    // The deltas land in milliseconds and the `done` event arrives while the
    // reveal is still mid-flight, so the catch-up happens with isLive already
    // false -- which is the branch that CLEARS `active`.
    rerender({ text: head, turnKey: "stream", isLive: false });
    act(() => vi.advanceTimersByTime(1000));
    expect(result.current.text).toBe(head);
    expect(result.current.isRevealing).toBe(false);

    // A late authoritative payload extends the same buffer. With `active`
    // cleared no frame will ever be scheduled again, so this assertion holds
    // WITHOUT advancing the clock -- and before the fix it stayed truncated for
    // the life of the buffer, which is why a reload was the only way to see the
    // rest.
    const full = `${head}med), Invoices, Purchase Orders, Material Usage, Time Registrations.`;
    rerender({ text: full, turnKey: "stream", isLive: false });
    expect(result.current.text).toBe(full);
  });

  it("passes text through untouched when there is nothing to reveal", () => {
    const { result } = renderHook(() =>
      useProgressiveReveal({ text: ANSWER, turnKey: null, isLive: false }),
    );

    expect(result.current.text).toBe(ANSWER);
    expect(result.current.isRevealing).toBe(false);
  });
});
