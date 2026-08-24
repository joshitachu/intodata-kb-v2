import type { MessageResponse } from "@/types";

/**
 * Which answer the progressive reveal is currently pacing.
 *
 * There are two sources for the SAME text over one turn: the stream buffer
 * while the turn runs, and the persisted message once the host refetches the
 * thread and drops the buffer (see `usePageCopilotChat`). The hand-off happens
 * milliseconds after the last delta, so a reveal keyed to the buffer alone
 * would be cut off almost immediately. Naming both sources with one key lets
 * the reveal carry across the hand-off instead (`useProgressiveReveal`).
 */
export const STREAM_REVEAL_KEY = "stream";

export function messageRevealKey(messageId: number): string {
  return `msg:${messageId}`;
}

/**
 * Minimal structural view of `StreamState`. Kept structural so this module does
 * not depend on the hook that produces it.
 */
export interface RevealStream {
  isStreaming: boolean;
  content: string;
  typedResponse?: { content: string } | null;
}

export interface AnswerRevealSource {
  /** `STREAM_REVEAL_KEY`, or `messageRevealKey(id)` for a persisted answer. */
  key: string;
  /** Everything received so far. The reveal never shows more than this. */
  text: string;
  /** True while more text may still arrive. */
  isLive: boolean;
}

function structuredContent(value: MessageResponse["structured_answer"]): string | null {
  if (!value || typeof value !== "object") return null;
  const content = (value as { content?: unknown }).content;
  return typeof content === "string" && content.length > 0 ? content : null;
}

/**
 * Picks the answer to reveal: the live stream buffer if a turn is on screen,
 * otherwise the thread's trailing assistant message.
 *
 * Returning the trailing message unconditionally is safe -- and required for
 * the hand-off -- because the reveal only animates a source it first saw empty
 * and live. A thread loaded from history therefore renders in full at once.
 */
export function answerRevealSource(args: {
  streamState?: RevealStream | null;
  messages?: readonly MessageResponse[];
}): AnswerRevealSource | null {
  const stream = args.streamState;
  if (stream && (stream.isStreaming || stream.content.length > 0)) {
    return {
      key: STREAM_REVEAL_KEY,
      // The typed response replaces `content` in the same state update, so
      // these agree; preferring it keeps this aligned with what the bubble
      // actually renders.
      text: stream.typedResponse?.content ?? stream.content,
      isLive: stream.isStreaming,
    };
  }

  const messages = args.messages;
  const last = messages && messages.length > 0 ? messages[messages.length - 1] : null;
  if (!last || last.role !== "assistant") return null;
  const text = structuredContent(last.structured_answer) ?? last.content;
  if (text.length === 0) return null;
  return { key: messageRevealKey(last.id), text, isLive: false };
}
