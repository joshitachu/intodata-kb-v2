import { describe, it, expect, vi, beforeEach } from "vitest";
import React from "react";
import { render, screen } from "@testing-library/react";
import { MessageList } from "@/components/MessageList";
import type { MessageResponse } from "@/types";
import type { StreamState } from "@/hooks/useStreamMessage";

/**
 * Which answers ran in Deep Dive, as far as the client can honestly tell.
 *
 * Nothing persists a turn's mode: there is no column on
 * `conversation_messages`, no field on the backend's `MessageResponse`, and no
 * SSE frame carries it (`_PUBLIC_EVENT_FIELDS` in
 * `backend/src/harness/agent_runs.py` is a whitelist and lists none). So the
 * badge renders from what this session watched happen, and from nothing else.
 * The reloaded-thread case below is the deliberate consequence, asserted so it
 * cannot be "fixed" later by guessing from tool calls or evidence shape.
 */

function assistantMessage(id: number, content: string): MessageResponse {
  return {
    id,
    role: "assistant",
    content,
    sources: null,
    latency_ms: 1200,
    status: "completed",
    created_at: "2026-09-17T10:00:00Z",
  };
}

function userMessage(id: number, content: string): MessageResponse {
  return { ...assistantMessage(id, content), role: "user", latency_ms: null };
}

function streaming(overrides: Partial<StreamState> = {}): StreamState {
  return {
    isStreaming: true,
    content: "Working on it",
    error: null,
    messageId: null,
    latencyMs: null,
    typedResponse: null,
    ...overrides,
  };
}

function badges(): HTMLElement[] {
  return screen.queryAllByTestId("deep-dive-badge");
}

describe("the Deep Dive badge on a turn", () => {
  beforeEach(() => {
    Element.prototype.scrollIntoView = vi.fn();
  });

  it("marks only the answers that ran in Deep Dive", () => {
    render(
      <MessageList
        messages={[
          userMessage(40, "How does the order job map its fields?"),
          assistantMessage(41, "From the raw scan: ..."),
          userMessage(42, "And who calls it?"),
          assistantMessage(43, "From the documentation: ..."),
        ]}
        deepDiveMessageIds={new Set([41])}
      />,
    );

    // One badge, on the one answer -- a "Chat" counterpart on everything else
    // would be noise on the overwhelming majority of a thread.
    expect(badges()).toHaveLength(1);
    expect(badges()[0]).toHaveAccessibleName("Answered in Deep Dive");
    expect(badges()[0]).toHaveTextContent("Deep Dive");
  });

  it("renders nothing on a thread the session knows nothing about", () => {
    // The reloaded thread: the ids are gone with the tab, and no server field
    // can restore them. An absent badge is recoverable; a wrong one is not.
    render(
      <MessageList
        messages={[
          userMessage(40, "How does the order job map its fields?"),
          assistantMessage(41, "From the raw scan: ..."),
        ]}
      />,
    );

    expect(badges()).toHaveLength(0);
  });

  it("does not mark the question, only the answer that came back", () => {
    render(
      <MessageList
        messages={[userMessage(41, "Ask something")]}
        deepDiveMessageIds={new Set([41])}
      />,
    );

    expect(badges()).toHaveLength(0);
  });

  it("badges the turn while it is still streaming", () => {
    render(
      <MessageList
        messages={[userMessage(40, "How does the order job map its fields?")]}
        streamingMessage={streaming()}
        isStreamingDeepDive
      />,
    );

    expect(badges()).toHaveLength(1);
  });

  it("carries the badge across the hand-off from stream to persisted answer", () => {
    // The completed stream is still rendered while its persisted twin is
    // hidden, so the badge has to hold through the swap or it would blink out
    // at the exact moment the answer finishes.
    const messages = [
      userMessage(40, "How does the order job map its fields?"),
      assistantMessage(41, "From the raw scan: ..."),
    ];
    const { rerender } = render(
      <MessageList
        messages={messages}
        streamingMessage={streaming({
          isStreaming: false,
          messageId: 41,
          content: "From the raw scan: ...",
        })}
        isStreamingDeepDive
        deepDiveMessageIds={new Set([41])}
      />,
    );

    expect(badges()).toHaveLength(1);

    rerender(
      <MessageList messages={messages} deepDiveMessageIds={new Set([41])} />,
    );

    expect(badges()).toHaveLength(1);
  });

  it("leaves an ordinary streaming turn unbadged", () => {
    render(
      <MessageList
        messages={[userMessage(40, "Ask something")]}
        streamingMessage={streaming()}
      />,
    );

    expect(badges()).toHaveLength(0);
  });
});
