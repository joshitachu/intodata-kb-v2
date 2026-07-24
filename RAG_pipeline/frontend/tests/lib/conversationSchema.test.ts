import { describe, expect, it } from "vitest";

import { ConversationDetailResponseSchema } from "@/types";

const groundedAnswer = {
  response_type: "agent_response_v2_grounded",
  content: "Hello from the standard agent runtime.",
  answer_status: "grounded",
  citations: [],
  disclosures: [],
  attribution: {
    runtime_release: "slice-3-readonly-primary-v1",
    manifest_snapshot_hash: "a".repeat(64),
    model_profile: "kimi/kimi-k2.5",
    prompt_version: "slice-3-readonly-primary-v2",
  },
} as const;

describe("ConversationDetailResponseSchema", () => {
  it("accepts persisted grounded standard-runtime responses", () => {
    const detail = ConversationDetailResponseSchema.parse({
      id: 403,
      title: "hi",
      messages: [
        {
          id: 1384,
          role: "assistant",
          content: groundedAnswer.content,
          sources: null,
          latency_ms: 18_657,
          structured_answer: groundedAnswer,
          created_at: "2026-07-21T20:44:20Z",
        },
      ],
      agent_plan: null,
      created_at: "2026-07-21T20:44:01Z",
      updated_at: "2026-07-21T20:44:20Z",
    });

    expect(detail.messages[0].structured_answer?.response_type).toBe(
      "agent_response_v2_grounded",
    );
  });

  it("still rejects unknown structured response contracts", () => {
    expect(() => ConversationDetailResponseSchema.parse({
      id: 403,
      title: "unknown",
      messages: [
        {
          id: 1384,
          role: "assistant",
          content: "Unknown response.",
          sources: null,
          latency_ms: 1,
          structured_answer: {
            response_type: "agent_response_v999",
            content: "Unknown response.",
          },
          created_at: "2026-07-21T20:44:20Z",
        },
      ],
      created_at: "2026-07-21T20:44:01Z",
      updated_at: "2026-07-21T20:44:20Z",
    })).toThrow();
  });
});
