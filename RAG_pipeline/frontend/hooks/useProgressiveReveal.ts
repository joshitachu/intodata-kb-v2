"use client";

import { useCallback, useEffect, useReducer, useRef, useState } from "react";

/**
 * Paces text that has ALREADY arrived, so an answer reads as arriving instead
 * of appearing as one block.
 *
 * Why client-side: the backend does not stream tokens. It validates the whole
 * answer against its grounding rules and only then splits it into chunks and
 * fires them in one tight loop, so every delta lands within milliseconds of the
 * first. Pacing on the server would mean holding validated text back -- real
 * added latency. Pacing here adds none: the reveal is presentation over content
 * the client already has, and it never gates the turn's completion, its target
 * chips, auto-navigation or cache invalidation, which all run off the stream
 * lifecycle in the host.
 */

/**
 * Timing.
 *
 * `MIN_REVEAL_MS + MS_PER_SQRT_CHAR * sqrt(chars)`, capped at MAX_REVEAL_MS:
 *
 *   120 chars  -> ~218ms      800 chars  -> ~375ms
 *   400 chars  -> ~300ms     2000 chars  -> ~522ms
 *                            3600+ chars ->  600ms (cap)
 *
 * A typical answer here is 400-800 characters, which lands at ~300-375ms: long
 * enough to be perceived as arriving, short enough that nobody is waiting on
 * it. sqrt rather than linear so a very long answer is not proportionally
 * slower, and the cap makes the worst case a stated number rather than a
 * function of answer length -- no answer, at any length, takes longer than
 * 600ms to finish revealing.
 */
const MIN_REVEAL_MS = 120;
const MAX_REVEAL_MS = 600;
const MS_PER_SQRT_CHAR = 9;

export function revealDurationMs(chars: number): number {
  if (chars <= 0) return 0;
  return Math.min(MAX_REVEAL_MS, MIN_REVEAL_MS + MS_PER_SQRT_CHAR * Math.sqrt(chars));
}

export interface ProgressiveRevealInput {
  /** Everything received so far. The reveal is always clamped to this. */
  text: string;
  /**
   * Identity of the answer being revealed. Null disables the reveal entirely
   * (text passes through). A reveal already in flight carries over to a new key
   * whose text extends what is on screen -- that is the stream-to-persisted
   * hand-off (see `lib/answerReveal.ts`).
   */
  turnKey: string | null;
  /** True while `text` may still grow. */
  isLive: boolean;
}

export interface ProgressiveReveal {
  /** The prefix of `text` cleared for display. Never longer than `text`. */
  text: string;
  /** True while there is received-but-unrevealed text left. */
  isRevealing: boolean;
  /** Reveal everything now. For user actions that must not be left mid-flight. */
  complete: () => void;
}

/**
 * Reveal progress lives in a ref, NOT in state, and is reconciled against
 * `turnKey`. A parent re-render therefore cannot restart an animation: replaying
 * an answer from the top would be worse than the block paint this fixes.
 */
interface RevealCursor {
  key: string | null;
  /** Characters cleared for display. Clamped to the text's length on read. */
  cursor: number;
  /** A reveal is in flight. Stays true after the stream ends, until it catches up. */
  active: boolean;
  /**
   * Clock origin for the pacing curve, or 0 until the first character arrives.
   * A turn can spend seconds in tool calls before any text exists; starting the
   * clock at the turn instead of at the text would spend the whole budget
   * waiting and then paint the block this exists to avoid.
   */
  startedAt: number;
  /** What was last put on screen, so a continuation can be recognised. */
  revealed: string;
}

const REVEAL_ALL = Number.MAX_SAFE_INTEGER;
const FALLBACK_FRAME_MS = 16;

function nowMs(): number {
  return typeof performance !== "undefined" && typeof performance.now === "function"
    ? performance.now()
    : Date.now();
}

function reducedMotionQuery(): MediaQueryList | null {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") return null;
  return window.matchMedia("(prefers-reduced-motion: reduce)");
}

function usePrefersReducedMotion(): boolean {
  const [reduced, setReduced] = useState(() => reducedMotionQuery()?.matches ?? false);

  useEffect(() => {
    const query = reducedMotionQuery();
    if (!query) return;
    const handleChange = () => setReduced(query.matches);
    handleChange();
    query.addEventListener?.("change", handleChange);
    return () => query.removeEventListener?.("change", handleChange);
  }, []);

  return reduced;
}

function reconcile(state: RevealCursor, input: ProgressiveRevealInput): RevealCursor {
  const length = input.text.length;

  // Text that no longer extends what is on screen is a different answer, even
  // under the same key -- the next turn reusing the stream buffer, or the
  // server's authoritative text replacing the provisional one.
  const continues = state.revealed.length === 0 || input.text.startsWith(state.revealed);

  if (state.key === input.turnKey && continues) {
    // INVARIANT: an inactive reveal shows everything it has.
    //
    // Only `active` schedules frames, and only a frame advances the cursor, so
    // text that arrives after the reveal has been deactivated is unreachable --
    // and the stream key is a CONSTANT for the whole turn, so this early return
    // would keep handing back the stale cursor for as long as the buffer lives.
    // The user saw an answer cut mid-word (three rows of a nine-row table,
    // ending at "(confir") whose full text was already in the client and only
    // appeared after a page reload. Holding back text nothing will ever pace is
    // pure loss, so snap to the end instead.
    if (!state.active && state.cursor < length) {
      return { ...state, cursor: length };
    }
    return state;
  }

  // Mid-flight hand-off: same answer, new source. Keeps the cursor AND the clock
  // origin, so the reveal neither restarts nor gets a fresh budget.
  if (continues && state.active && state.cursor < length) {
    return { ...state, key: input.turnKey };
  }

  // Only a live source first seen EMPTY animates. Anything else -- history, a
  // resumed stream that already carries text, a finished answer rendered fresh
  // -- appears in full immediately.
  const startsEmpty = length === 0;
  return {
    key: input.turnKey,
    cursor: startsEmpty ? 0 : length,
    active: input.isLive && startsEmpty,
    startedAt: 0,
    revealed: "",
  };
}

export function useProgressiveReveal(input: ProgressiveRevealInput): ProgressiveReveal {
  const { text, turnKey, isLive } = input;
  const reduced = usePrefersReducedMotion();
  const stateRef = useRef<RevealCursor>({
    key: null,
    cursor: 0,
    active: false,
    startedAt: 0,
    revealed: "",
  });
  // Frames advance the cursor in the ref; this only asks React to read it again.
  const [, tick] = useReducer((count: number) => count + 1, 0);

  // Nothing to pace: no identity to key progress to, or the user asked for no
  // motion. The ref is deliberately left untouched rather than reset, because a
  // null key is usually TRANSIENT -- the host clears the stream buffer the
  // moment the turn is saved, and the refetched message lands a beat later.
  // Cancelling there would abort a reveal that is about to continue under the
  // message's own key.
  const bypass = turnKey === null || reduced;
  const state = bypass
    ? stateRef.current
    : reconcile(stateRef.current, { text, turnKey, isLive });
  if (!bypass) stateRef.current = state;

  const cursor = bypass ? text.length : Math.min(state.cursor, text.length);
  const revealed = bypass ? text : text.slice(0, cursor);
  if (!bypass) state.revealed = revealed;
  const isRevealing = !bypass && state.active && cursor < text.length;

  useEffect(() => {
    if (!isRevealing) return;

    let handle = 0;
    let cancelled = false;
    const schedule =
      typeof requestAnimationFrame === "function"
        ? requestAnimationFrame
        : (callback: FrameRequestCallback) =>
            setTimeout(() => callback(nowMs()), FALLBACK_FRAME_MS) as unknown as number;
    const unschedule =
      typeof cancelAnimationFrame === "function"
        ? cancelAnimationFrame
        : (id: number) => clearTimeout(id);

    const step = () => {
      if (cancelled) return;
      const current = stateRef.current;
      const length = text.length;
      if (current.startedAt === 0) current.startedAt = nowMs();
      const duration = revealDurationMs(length);
      // Linear against a deadline derived from the CURRENT length: text that
      // lands late raises the target position instead of queueing a second
      // animation, and once the deadline passes the remainder appears at once.
      // The reveal can therefore never outlast `revealDurationMs`.
      const progress =
        duration <= 0 ? 1 : Math.min(1, (nowMs() - current.startedAt) / duration);
      const target = Math.min(length, Math.ceil(length * progress));
      if (target > current.cursor) {
        current.cursor = target;
        tick();
      }
      if (current.cursor < length) {
        handle = schedule(step);
        return;
      }
      // Caught up. A live source may still send more, so the reveal stays armed
      // until the stream ends -- otherwise a burst arriving after the first
      // catch-up would paint as a block.
      if (!isLive) {
        current.active = false;
        tick();
      }
    };

    handle = schedule(step);
    return () => {
      cancelled = true;
      unschedule(handle);
    };
  }, [isRevealing, isLive, text, tick]);

  const complete = useCallback(() => {
    const current = stateRef.current;
    if (!current.active && current.cursor === REVEAL_ALL) return;
    current.cursor = REVEAL_ALL;
    current.active = false;
    tick();
  }, [tick]);

  return { text: revealed, isRevealing, complete };
}
