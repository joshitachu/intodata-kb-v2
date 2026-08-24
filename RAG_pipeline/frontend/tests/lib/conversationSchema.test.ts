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

  it("round-trips the additive current-turn evidence summary", () => {
    const detail = ConversationDetailResponseSchema.parse({
      id: 404,
      title: "evidence",
      messages: [
        {
          id: 1385,
          role: "assistant",
          content: groundedAnswer.content,
          sources: null,
          latency_ms: 900,
          structured_answer: {
            ...groundedAnswer,
            evidence_summary: {
              version: "1.0",
              evidence_step_count: 2,
              successful_sources: [
                { family: "topology", role: "structure", evidence_count: 1 },
                { family: "documentation", role: "explanation", evidence_count: 1 },
              ],
              failed_sources: [
                {
                  family: "runtime",
                  status: "unavailable",
                  reason_code: null,
                  message: "Runtime evidence is not configured.",
                },
              ],
              zero_evidence: false,
              documentation: {
                provider_used: "kimi",
                fallback_used: false,
                attempts: [{ provider: "kimi", status: "succeeded" }],
              },
            },
          },
          created_at: "2026-07-21T20:44:20Z",
        },
      ],
      agent_plan: null,
      created_at: "2026-07-21T20:44:01Z",
      updated_at: "2026-07-21T20:44:20Z",
    });

    expect(detail.messages[0].structured_answer?.evidence_summary?.successful_sources)
      .toHaveLength(2);
    expect(detail.messages[0].structured_answer?.evidence_summary?.failed_sources[0].reason_code)
      .toBeNull();
  });

  it("accepts persisted grounded answers with topology targets", () => {
    const detail = ConversationDetailResponseSchema.parse({
      id: 405,
      title: "topology flow",
      messages: [
        {
          id: 1386,
          role: "assistant",
          content: groundedAnswer.content,
          sources: null,
          latency_ms: 1200,
          structured_answer: {
            ...groundedAnswer,
            topology_targets: [
              {
                label: "4PS",
                level: {
                  kind: "integration",
                  selection: { kind: "system", systemId: "sys-4ps" },
                },
                intent: "reference",
                visual_role: "primary",
              },
            ],
          },
          created_at: "2026-08-21T19:32:08Z",
        },
      ],
      created_at: "2026-08-21T19:31:56Z",
      updated_at: "2026-08-21T19:32:08Z",
    });

    const structuredAnswer = detail.messages[0].structured_answer;
    expect(structuredAnswer?.response_type).toBe("agent_response_v2_grounded");
    if (structuredAnswer?.response_type !== "agent_response_v2_grounded") {
      throw new Error("Expected a grounded topology answer");
    }
    expect(structuredAnswer.topology_targets).toHaveLength(1);
  });

  it("normalizes nullable persisted topology scope details", () => {
    const detail = ConversationDetailResponseSchema.parse({
      id: 406,
      title: "topology follow-up",
      messages: [
        {
          id: 1387,
          role: "user",
          content: "What flows to 4PS?",
          sources: null,
          latency_ms: null,
          page_scope: {
            surface: "topology",
            project_id: "werkorder",
            level: { kind: "project" },
            selected_entity: null,
            active_detail_tab: null,
            filters: null,
          },
          created_at: "2026-08-21T20:04:02Z",
        },
      ],
      created_at: "2026-08-21T20:03:14Z",
      updated_at: "2026-08-21T20:04:20Z",
    });

    expect(detail.messages[0].page_scope).toEqual({
      surface: "topology",
      project_id: "werkorder",
      level: { kind: "project" },
    });
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
