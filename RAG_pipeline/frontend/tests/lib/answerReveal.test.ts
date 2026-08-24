import { describe, expect, it } from "vitest";
import {
  STREAM_REVEAL_KEY,
  answerRevealSource,
  messageRevealKey,
} from "@/lib/answerReveal";
import type { MessageResponse } from "@/types";

function assistant(id: number, content: string, structured?: string): MessageResponse {
  return {
    id,
    role: "assistant",
    content,
    sources: null,
    latency_ms: 120,
    status: "completed",
    created_at: "2026-08-21T10:00:00Z",
    ...(structured
      ? {
          structured_answer: {
            response_type: "agent_response_v1",
            kind: "answer",
            content: structured,
            synthesis_source: "agent_plan",
            facts: [],
            artifact_links: [],
            evidence_refs: [],
            workflow_cards: [],
            stop_reason: "answered",
            factual_fingerprint: "a".repeat(64),
          } as MessageResponse["structured_answer"],
        }
      : {}),
  };
}

function user(id: number, content: string): MessageResponse {
  return {
    id,
    role: "user",
    content,
    sources: null,
    latency_ms: null,
    status: "completed",
    created_at: "2026-08-21T10:00:00Z",
  };
}

describe("answerRevealSource", () => {
  it("follows the live buffer while a turn is on screen", () => {
    const source = answerRevealSource({
      streamState: { isStreaming: true, content: "SAP ERP receives" },
      messages: [assistant(1, "An older answer.")],
    });

    expect(source).toEqual({
      key: STREAM_REVEAL_KEY,
      text: "SAP ERP receives",
      isLive: true,
    });
  });

  it("still names the buffer after the stream ends, until it is dropped", () => {
    const source = answerRevealSource({
      streamState: { isStreaming: false, content: "Finished text." },
      messages: [],
    });

    expect(source).toEqual({
      key: STREAM_REVEAL_KEY,
      text: "Finished text.",
      isLive: false,
    });
  });

  it("names the same answer by its message once the buffer is cleared", () => {
    // The hand-off: same text, new key. `useProgressiveReveal` carries an
    // in-flight reveal across it.
    const source = answerRevealSource({
      streamState: { isStreaming: false, content: "" },
      messages: [user(9, "Which systems feed SAP?"), assistant(10, "SAP ERP receives orders.")],
    });

    expect(source).toEqual({
      key: messageRevealKey(10),
      text: "SAP ERP receives orders.",
      isLive: false,
    });
  });

  it("prefers the typed response, which is what the bubble renders", () => {
    expect(
      answerRevealSource({
        streamState: { isStreaming: true, content: "raw", typedResponse: { content: "typed" } },
      })?.text,
    ).toBe("typed");

    expect(
      answerRevealSource({ messages: [assistant(11, "raw", "typed")] })?.text,
    ).toBe("typed");
  });

  it("has nothing to pace for an empty thread or a trailing question", () => {
    expect(answerRevealSource({})).toBeNull();
    expect(answerRevealSource({ messages: [] })).toBeNull();
    expect(answerRevealSource({ messages: [assistant(1, "Answer."), user(2, "Follow-up?")] })).toBeNull();
    expect(answerRevealSource({ messages: [assistant(3, "")] })).toBeNull();
  });
});
