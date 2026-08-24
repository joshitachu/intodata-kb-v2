import { describe, expect, it } from "vitest";
import { isCopilotTurnHandoffPending } from "@/hooks/usePageCopilotChat";
import type { MessageResponse } from "@/types";

function message(id: number): MessageResponse {
  return {
    id,
    role: "assistant",
    content: `answer ${id}`,
    sources: null,
    latency_ms: null,
    status: "completed",
    created_at: "2026-08-21T00:00:00Z",
  };
}

describe("page copilot turn hand-off", () => {
  it("keeps the composer locked while the completed stream is not in the thread", () => {
    expect(isCopilotTurnHandoffPending(42, [message(40)])).toBe(true);
  });

  it("releases the composer once the exact saved message is in the thread", () => {
    expect(isCopilotTurnHandoffPending(42, [message(40), message(42)])).toBe(false);
  });

  it("does not report a hand-off while no completed stream is waiting", () => {
    expect(isCopilotTurnHandoffPending(null, [message(42)])).toBe(false);
  });

  it("never locks the composer when the saved thread failed to reload", () => {
    expect(isCopilotTurnHandoffPending(42, [message(40)], true)).toBe(false);
  });
});
