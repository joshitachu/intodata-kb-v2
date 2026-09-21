import { describe, expect, it } from "vitest";
import { deriveLabsTurn, formatDuration } from "@/components/labs/labsTurn";
import type { StreamState } from "@/hooks/useStreamMessage";
import type { PublicAgentEvent } from "@/types";

/**
 * The labs turn panel shows only what the lane reported. These cover the two
 * ways that can go wrong: inventing a number the lane never sent, and losing
 * one it did send because the key was not known in advance.
 *
 * They also pin the two projections added after a live turn rendered wrong: the
 * plan, which used to reach the user only by being narrated into the answer, and
 * the one-row-per-CALL collapse, which reported 40 tool calls as 81.
 */

function event(
  sequence: number,
  type: string,
  publicPayload: Record<string, unknown>,
): PublicAgentEvent {
  return {
    event_id: `run-fixture:evt:${String(sequence).padStart(6, "0")}`,
    run_id: "run-fixture",
    sequence,
    type,
    created_at: "2026-09-17T10:00:00Z",
    public_payload: publicPayload,
    replayable: false,
  };
}

function streamState(overrides: Partial<StreamState> = {}): StreamState {
  return {
    isStreaming: false,
    content: "",
    error: null,
    messageId: null,
    latencyMs: null,
    typedResponse: null,
    runId: null,
    progress: null,
    progressDetail: null,
    lastSequence: 0,
    agentEvents: [],
    deltaSource: null,
    reasoning: "",
    ...overrides,
  };
}

describe("deriveLabsTurn", () => {
  it("returns null for an idle state so the last turn stays on screen", () => {
    expect(deriveLabsTurn(streamState())).toBeNull();
  });

  it("closes a running tool call with its completion instead of listing it twice", () => {
    const record = deriveLabsTurn(
      streamState({
        isStreaming: true,
        runId: "run-fixture",
        agentEvents: [
          event(1, "tool.running", { tool_label: "read_docs", status: "running", summary: "read_docs" }),
          event(2, "tool.running", { tool_label: "bash", status: "running", summary: "bash" }),
          event(3, "tool.completed", {
            tool_label: "read_docs",
            status: "succeeded",
            summary: "Read 3 documents",
            evidence_refs: [{ id: "a" }, { id: "b" }],
          }),
        ],
      }),
    );

    expect(record?.activities).toHaveLength(2);
    expect(record?.activities[0]).toMatchObject({
      label: "read_docs",
      status: "succeeded",
      detail: "Read 3 documents",
      evidenceCount: 2,
    });
    expect(record?.activities[1]).toMatchObject({ label: "bash", status: "running" });
    expect(record?.status).toBe("streaming");
  });

  it("says what the plan and verification steps are, not what they are called", () => {
    // These are the two frames a user watching a long turn most needs to read:
    // the turn saying what it intends to do, and the turn checking what it wrote
    // before showing it. "Todowrite" and "Task" name a mechanism and tell them
    // nothing. Every other tool keeps the server's own wording.
    const record = deriveLabsTurn(
      streamState({
        agentEvents: [
          event(1, "tool.running", { tool_label: "todowrite", status: "running" }),
          event(2, "tool.running", { tool_label: "task", status: "running" }),
          event(3, "tool.running", { tool_label: "read_runtime_evidence", status: "running" }),
        ],
      }),
    );

    expect(record?.activities.map((activity) => activity.label)).toEqual([
      "Planning the steps",
      "Checking the answer against the evidence",
      "read_runtime_evidence",
    ]);
  });

  it("keeps an unmatched completion as its own line rather than guessing", () => {
    const record = deriveLabsTurn(
      streamState({
        agentEvents: [
          event(1, "tool.running", { tool_label: "bash", status: "running" }),
          event(2, "tool.completed", { tool_label: "nexus_read_evidence", status: "failed", summary: "Denied" }),
        ],
      }),
    );

    expect(record?.activities.map((activity) => [activity.label, activity.status])).toEqual([
      ["bash", "running"],
      ["nexus_read_evidence", "failed"],
    ]);
  });

  it("renders usage keys it has never seen and skips values it cannot state", () => {
    const record = deriveLabsTurn(
      streamState({
        messageId: 9,
        latencyMs: 12400,
        agentEvents: [
          event(1, "run.completed", {
            status: "completed",
            stop_reason: "answered",
            latency_ms: 12400,
            usage: {
              capability_calls: 3,
              input_tokens: 18420,
              cost_usd: 0.0142,
              model: "kimi-k2.6",
              cache: { read: 1 },
              output_tokens: null,
            },
          }),
        ],
      }),
    );

    expect(record?.status).toBe("completed");
    expect(record?.stopReason).toBe("answered");
    expect(record?.usage).toEqual([
      { key: "capability_calls", label: "Capability calls", value: "3" },
      { key: "input_tokens", label: "Input tokens", value: "18,420" },
      { key: "cost_usd", label: "Cost usd", value: "0.0142" },
      { key: "model", label: "Model", value: "kimi-k2.6" },
    ]);
    expect(formatDuration(record?.latencyMs ?? null)).toBe("12.4s");
  });

  it("reports a failed run with the server's own summary", () => {
    const record = deriveLabsTurn(
      streamState({
        agentEvents: [
          event(1, "run.failed", {
            status: "failed",
            stop_reason: "opencode_unavailable",
            summary: "The lane is not reachable",
            latency_ms: 0,
          }),
        ],
      }),
    );

    expect(record?.status).toBe("failed");
    expect(record?.error).toBe("The lane is not reachable");
    expect(record?.stopReason).toBe("opencode_unavailable");
  });

  it("calls a stream that stopped without a terminal frame ended, not answered", () => {
    const record = deriveLabsTurn(
      streamState({ runId: "run-fixture", content: "Half an ", reasoning: "Thinking..." }),
    );

    expect(record?.status).toBe("ended");
    expect(record?.reasoning).toBe("Thinking...");
    expect(record?.usage).toEqual([]);
    expect(record?.latencyMs).toBeNull();
  });

  it("counts one call, not two frames, when a completion closes a running call", () => {
    // The regression this exists for: the live frame named the call
    // `nexus_read_topology_structure` and the late one
    // `topology.landscape.read_structure`, nothing could pair them, and a turn
    // of 40 calls reported 81. The backend now emits both under one label.
    const record = deriveLabsTurn(
      streamState({
        messageId: 4,
        agentEvents: [
          event(1, "tool.running", {
            tool_label: "nexus_read_topology_structure",
            status: "running",
            summary: "nexus_read_topology_structure",
          }),
          event(2, "tool.running", { tool_label: "todowrite", status: "running" }),
          event(3, "tool.completed", {
            tool_label: "nexus_read_topology_structure",
            status: "succeeded",
            summary: "Read 3 components",
            evidence_refs: [{ id: "a" }],
          }),
          event(4, "run.completed", {
            status: "completed",
            stop_reason: "answered",
            usage: { capability_calls: 1 },
          }),
        ],
      }),
    );

    expect(record?.activities).toHaveLength(2);
    // The header count and the lane's own count now describe different things
    // and are labelled accordingly, instead of disagreeing about the same one.
    expect(record?.usage).toEqual([
      { key: "capability_calls", label: "Capability calls", value: "1" },
    ]);
  });

  it("pairs a repeated tool's completions with its calls in order", () => {
    // Both entries carry the same label, so "which open row does this close"
    // has a wrong answer: matching the newest would hang the first call's
    // summary on the second one.
    const record = deriveLabsTurn(
      streamState({
        agentEvents: [
          event(1, "tool.running", { tool_label: "nexus_read_full_document", status: "running" }),
          event(2, "tool.running", { tool_label: "nexus_read_full_document", status: "running" }),
          event(3, "tool.completed", {
            tool_label: "nexus_read_full_document",
            status: "succeeded",
            summary: "Read the interface document",
          }),
          event(4, "tool.completed", {
            tool_label: "nexus_read_full_document",
            status: "failed",
            summary: "Document not indexed",
          }),
        ],
      }),
    );

    expect(record?.activities.map((activity) => [activity.status, activity.detail])).toEqual([
      ["succeeded", "Read the interface document"],
      ["failed", "Document not indexed"],
    ]);
  });
});

describe("deriveLabsTurn plan projection", () => {
  it("reads the agent's steps off the newest plan frame", () => {
    // `todowrite` sends the whole list every time, so the newest frame IS the
    // plan. Merging the frames would resurrect a step the agent dropped.
    const record = deriveLabsTurn(
      streamState({
        isStreaming: true,
        agentEvents: [
          event(1, "plan.updated", {
            status: "running",
            summary: "Find the jobs in this chain",
            todos: [
              { id: "1", content: "Find the jobs in this chain", status: "in_progress" },
              { id: "2", content: "Read the interface document", status: "pending" },
              { id: "3", content: "Check the runtime records", status: "pending" },
            ],
          }),
          event(2, "plan.updated", {
            status: "running",
            summary: "Read the interface document",
            todos: [
              { id: "1", content: "Find the jobs in this chain", status: "completed" },
              { id: "2", content: "Read the interface document", status: "in_progress" },
            ],
          }),
        ],
      }),
    );

    expect(record?.plan).toEqual([
      { id: "1", content: "Find the jobs in this chain", status: "completed" },
      { id: "2", content: "Read the interface document", status: "in_progress" },
    ]);
  });

  it("puts the step in progress on the status line, ahead of any tool", () => {
    // A plan step is written for the reader; a tool label is the name of a
    // mechanism. The plan wins whenever there is one.
    const record = deriveLabsTurn(
      streamState({
        isStreaming: true,
        agentEvents: [
          event(1, "tool.running", {
            tool_label: "nexus_read_topology_structure",
            status: "running",
          }),
          event(2, "plan.updated", {
            status: "running",
            summary: "Read the collector's components",
            todos: [{ id: "1", content: "Read the collector's components", status: "in_progress" }],
          }),
        ],
      }),
    );

    expect(record?.statusLine).toBe("Read the collector's components");
  });

  it("falls back to the open call when no step is marked in progress", () => {
    const record = deriveLabsTurn(
      streamState({
        isStreaming: true,
        agentEvents: [
          event(1, "tool.running", {
            tool_label: "nexus_search_documentation",
            status: "running",
            summary: "Searching the documentation",
          }),
          event(2, "plan.updated", {
            status: "running",
            todos: [{ id: "1", content: "Read the collector's components", status: "pending" }],
          }),
        ],
      }),
    );

    expect(record?.statusLine).toBe("Searching the documentation");
  });

  it("drops the status line once the turn settles", () => {
    // The answer is on screen by then. A line still claiming to be reading
    // something would be the panel asserting what it cannot know.
    const record = deriveLabsTurn(
      streamState({
        messageId: 12,
        agentEvents: [
          event(1, "plan.updated", {
            status: "running",
            todos: [{ id: "1", content: "Read the collector's components", status: "in_progress" }],
          }),
        ],
      }),
    );

    expect(record?.status).toBe("completed");
    expect(record?.plan).toHaveLength(1);
    expect(record?.statusLine).toBeNull();
  });

  it("ignores a plan frame carrying nothing renderable", () => {
    const record = deriveLabsTurn(
      streamState({
        isStreaming: true,
        agentEvents: [
          event(1, "plan.updated", {
            status: "running",
            todos: [{ id: "1", content: "Read the collector's components", status: "pending" }],
          }),
          event(2, "plan.updated", { status: "running", todos: [{ id: "2", status: "pending" }] }),
          event(3, "plan.updated", { status: "running", todos: "not a list" }),
        ],
      }),
    );

    expect(record?.plan).toEqual([
      { id: "1", content: "Read the collector's components", status: "pending" },
    ]);
  });

  it("reports no plan at all when the lane published none", () => {
    const record = deriveLabsTurn(
      streamState({ isStreaming: true, runId: "run-fixture" }),
    );

    expect(record?.plan).toEqual([]);
    expect(record?.statusLine).toBeNull();
  });
});
