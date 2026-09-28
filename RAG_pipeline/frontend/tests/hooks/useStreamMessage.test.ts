import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useStreamMessage } from "@/hooks/useStreamMessage";

function sseResponse(body: string) {
  return new Response(
    new ReadableStream({
      start(controller) {
        controller.enqueue(new TextEncoder().encode(body));
        controller.close();
      },
    }),
    { status: 200 },
  );
}

function typedResponse(content = "The authoritative answer.") {
  return {
    response_type: "agent_response_v1",
    kind: "answer",
    content,
    synthesis_source: "agent_plan",
    facts: [],
    artifact_links: [],
    evidence_refs: [],
    workflow_cards: [],
    stop_reason: "answered",
    factual_fingerprint: "a".repeat(64),
  };
}

function publicEvent(
  sequence: number,
  type: string,
  publicPayload: Record<string, unknown>,
  replayable = true,
) {
  return {
    event_id: `run-fixture:evt:${String(sequence).padStart(6, "0")}`,
    run_id: "run-fixture",
    sequence,
    type,
    created_at: "2026-07-21T10:00:00Z",
    public_payload: publicPayload,
    replayable,
  };
}

describe("useStreamMessage", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("does not treat an invalid done payload as a successful message", async () => {
    vi.spyOn(global, "fetch").mockResolvedValueOnce(
      sseResponse('event: error\ndata: "Provider timed out"\n\nevent: done\ndata: {}\n\n'),
    );
    const { result } = renderHook(() => useStreamMessage(4));

    await act(async () => {
      await result.current.sendMessage({ query: "Slow question" });
    });

    expect(result.current.streamState.messageId).toBeNull();
    expect(result.current.streamState.error).toBe("Provider timed out");
  });

  it("parses multiline done metadata", async () => {
    vi.spyOn(global, "fetch").mockResolvedValueOnce(
      sseResponse('event: done\ndata: {\ndata: "message_id":7,\ndata: "latency_ms":10\ndata: }\n\n'),
    );
    const { result } = renderHook(() => useStreamMessage(4));

    await act(async () => {
      await result.current.sendMessage({ query: "Question" });
    });

    expect(result.current.streamState.messageId).toBe(7);
    expect(result.current.streamState.latencyMs).toBe(10);
  });

  it("uses agent_response as the only visible answer authority", async () => {
    const response = typedResponse("The workflow is already failed.");
    vi.spyOn(global, "fetch").mockResolvedValueOnce(
      sseResponse(
        'event: token\ndata: "Legacy token text"\n\n' +
          `event: agent_response\ndata: ${JSON.stringify(response)}\n\n` +
          `event: agent_final\ndata: ${JSON.stringify({
            typed_response: typedResponse("Duplicate final answer"),
            output: "Duplicate output",
            stop_reason: "answered",
          })}\n\n` +
          'event: done\ndata: {"message_id":7,"latency_ms":10}\n\n',
      ),
    );
    const { result } = renderHook(() => useStreamMessage(4));

    await act(async () => {
      await result.current.sendMessage({ query: "Cancel it" });
    });

    expect(result.current.streamState.content).toBe(response.content);
    expect(result.current.streamState.typedResponse).toEqual(response);
    expect(result.current.streamState.messageId).toBe(7);
  });

  it("ignores malformed agent responses and removed legacy transport events", async () => {
    vi.spyOn(global, "fetch").mockResolvedValueOnce(
      sseResponse(
        'event: workspace\ndata: {"workspace":"pack"}\n\n' +
          'event: runtime_tool_result\ndata: {"tool_name":"detect_failures"}\n\n' +
          'event: pending_action\ndata: {"prompt":"legacy"}\n\n' +
          'event: agent_response\ndata: {"response_type":"agent_response_v1","content":""}\n\n' +
          'event: done\ndata: {"message_id":8,"latency_ms":11}\n\n',
      ),
    );
    const { result } = renderHook(() => useStreamMessage(4));

    await act(async () => {
      await result.current.sendMessage({ query: "Question" });
    });

    expect(result.current.streamState.content).toBe("");
    expect(result.current.streamState.typedResponse).toBeNull();
    expect(result.current.streamState.messageId).toBe(8);
  });

  it("surfaces non-successful HTTP responses without opening a stream", async () => {
    vi.spyOn(global, "fetch").mockResolvedValueOnce(
      new Response(JSON.stringify({ error: "Chat unavailable" }), { status: 503 }),
    );
    const { result } = renderHook(() => useStreamMessage(4));

    await act(async () => {
      await result.current.sendMessage({ query: "Question" });
    });

    await waitFor(() => expect(result.current.streamState.isStreaming).toBe(false));
    expect(result.current.streamState.error).toBe("Chat unavailable");
  });

  it("reset clears a completed typed response", async () => {
    const response = typedResponse();
    vi.spyOn(global, "fetch").mockResolvedValueOnce(
      sseResponse(
        `event: agent_response\ndata: ${JSON.stringify(response)}\n\n` +
          'event: done\ndata: {"message_id":9,"latency_ms":12}\n\n',
      ),
    );
    const { result } = renderHook(() => useStreamMessage(4));

    await act(async () => {
      await result.current.sendMessage({ query: "Question" });
    });
    act(() => result.current.reset());

    expect(result.current.streamState.content).toBe("");
    expect(result.current.streamState.typedResponse).toBeNull();
    expect(result.current.streamState.messageId).toBeNull();
  });

  it("appends validated deltas once and resumes with the last sequence", async () => {
    const firstDelta = publicEvent(2, "assistant.delta", { content: "Safe " });
    const secondDelta = publicEvent(3, "assistant.delta", { content: "answer." });
    const fetchMock = vi
      .spyOn(global, "fetch")
      .mockResolvedValueOnce(
        sseResponse(
          `event: run.started\ndata: ${JSON.stringify(publicEvent(1, "run.started", { status: "running" }))}\n\n` +
            `event: assistant.delta\ndata: ${JSON.stringify(firstDelta)}\n\n` +
            `event: done\ndata: ${JSON.stringify({ message_id: 9, latency_ms: 12, run_id: "run-fixture" })}\n\n`,
        ),
      )
      .mockResolvedValueOnce(
        sseResponse(
          `event: assistant.delta\ndata: ${JSON.stringify(firstDelta)}\n\n` +
            `event: assistant.delta\ndata: ${JSON.stringify(secondDelta)}\n\n` +
            `event: done\ndata: ${JSON.stringify({ message_id: 9, latency_ms: 12, run_id: "run-fixture", replayed: true })}\n\n`,
        ),
      );
    const { result } = renderHook(() => useStreamMessage(4));
    const request = { query: "Question", client_turn_id: "turn-fixture-123" };

    await act(async () => result.current.sendMessage(request));
    await act(async () => result.current.sendMessage(request));

    expect(result.current.streamState.content).toBe("Safe answer.");
    expect(result.current.streamState.lastSequence).toBe(3);
    expect(result.current.streamState.runId).toBe("run-fixture");
    expect(fetchMock.mock.calls[1][0]).toContain("after_sequence=2");
    expect((fetchMock.mock.calls[1][1]?.headers as Record<string, string>)["Last-Event-ID"])
      .toBe(firstDelta.event_id);
  });

  it("renders live progress during generation without treating it as answer content", async () => {
    const progress = publicEvent(2, "assistant.progress", {
      status: "running",
      phase: "writing_answer",
      summary: "Writing the answer... 14s",
      elapsed_ms: 14000,
    });
    vi.spyOn(global, "fetch").mockResolvedValueOnce(
      sseResponse(
        `event: run.started\ndata: ${JSON.stringify(publicEvent(1, "run.started", { status: "running" }))}\n\n` +
          `event: assistant.progress\ndata: ${JSON.stringify(progress)}\n\n` +
          `event: assistant.delta\ndata: ${JSON.stringify(publicEvent(3, "assistant.delta", { content: "Validated answer." }))}\n\n` +
          `event: done\ndata: ${JSON.stringify({ message_id: 9, latency_ms: 12, run_id: "run-fixture" })}\n\n`,
      ),
    );
    const { result } = renderHook(() => useStreamMessage(4));

    await act(async () => result.current.sendMessage({ query: "Question" }));

    // The progress summary reaches the status line...
    expect(result.current.streamState.progress).toBe("Writing the answer... 14s");
    // ...but never the answer, which still arrives only as validated deltas.
    expect(result.current.streamState.content).toBe("Validated answer.");
    // Heartbeats are not retained: there is one per couple of seconds of decode.
    expect(
      (result.current.streamState.agentEvents ?? []).map((event) => event.type),
    ).toEqual(["run.started"]);
  });
  it("shows the live answer once, then lets the persisted answer replace it", async () => {
    // The trusted RAG answer is streamed live (replayable: false) while it is
    // generated and emitted again after persistence (replayable: true).
    // Appending both would duplicate it; the authoritative run must replace.
    const live = [
      publicEvent(2, "assistant.delta", { content: "Amsterdam " }, false),
      publicEvent(3, "assistant.delta", { content: "is the answer." }, false),
    ];
    const authoritative = [
      publicEvent(5, "assistant.delta", { content: "Amsterdam " }),
      publicEvent(6, "assistant.delta", { content: "is the answer." }),
    ];
    vi.spyOn(global, "fetch").mockResolvedValueOnce(
      sseResponse(
        `event: run.started\ndata: ${JSON.stringify(publicEvent(1, "run.started", { status: "running" }))}\n\n` +
          live
            .map((e) => `event: assistant.delta\ndata: ${JSON.stringify(e)}\n\n`)
            .join("") +
          authoritative
            .map((e) => `event: assistant.delta\ndata: ${JSON.stringify(e)}\n\n`)
            .join("") +
          `event: done\ndata: ${JSON.stringify({ message_id: 9, latency_ms: 12, run_id: "run-fixture" })}\n\n`,
      ),
    );
    const { result } = renderHook(() => useStreamMessage(4));

    await act(async () => result.current.sendMessage({ query: "Where is it?" }));

    expect(result.current.streamState.content).toBe("Amsterdam is the answer.");
    expect(result.current.streamState.deltaSource).toBe("authoritative");
  });

  it("lets the persisted answer correct the live text if the two ever differ", async () => {
    // Safety property: whatever the server persisted wins on screen.
    vi.spyOn(global, "fetch").mockResolvedValueOnce(
      sseResponse(
        `event: assistant.delta\ndata: ${JSON.stringify(publicEvent(2, "assistant.delta", { content: "provisional text" }, false))}\n\n` +
          `event: assistant.delta\ndata: ${JSON.stringify(publicEvent(3, "assistant.delta", { content: "persisted text" }))}\n\n` +
          `event: done\ndata: ${JSON.stringify({ message_id: 9, latency_ms: 12, run_id: "run-fixture" })}\n\n`,
      ),
    );
    const { result } = renderHook(() => useStreamMessage(4));

    await act(async () => result.current.sendMessage({ query: "Where is it?" }));

    expect(result.current.streamState.content).toBe("persisted text");
  });

  it("streams live deltas into content while generation is still running", async () => {
    vi.spyOn(global, "fetch").mockResolvedValueOnce(
      sseResponse(
        `event: assistant.delta\ndata: ${JSON.stringify(publicEvent(2, "assistant.delta", { content: "partial " }, false))}\n\n`,
      ),
    );
    const { result } = renderHook(() => useStreamMessage(4));

    await act(async () => result.current.sendMessage({ query: "Where is it?" }));

    expect(result.current.streamState.content).toBe("partial ");
    expect(result.current.streamState.deltaSource).toBe("live");
  });

  it("drops a retracted draft instead of appending the real answer to it", async () => {
    // The server showed provisional prose, then retracted it: the model was
    // narrating before a tool call, or the validator rejected the draft.
    vi.spyOn(global, "fetch").mockResolvedValueOnce(
      sseResponse(
        `event: assistant.delta\ndata: ${JSON.stringify(publicEvent(2, "assistant.delta", { content: "Let me look that up." }, false))}\n\n` +
          `event: assistant.draft_reset\ndata: ${JSON.stringify(publicEvent(3, "assistant.draft_reset", { status: "discarded" }, false))}\n\n` +
          `event: assistant.delta\ndata: ${JSON.stringify(publicEvent(4, "assistant.delta", { content: "The real answer." }, false))}\n\n` +
          `event: done\ndata: ${JSON.stringify({ message_id: 9, latency_ms: 12, run_id: "run-fixture" })}\n\n`,
      ),
    );
    const { result } = renderHook(() => useStreamMessage(4));

    await act(async () => result.current.sendMessage({ query: "Where is it?" }));

    expect(result.current.streamState.content).toBe("The real answer.");
  });

  it("never lets a retraction discard the persisted answer", async () => {
    // Safety property, mirroring the live/authoritative rule: once the server has
    // sent what it stored, a late retraction frame cannot blank the screen.
    vi.spyOn(global, "fetch").mockResolvedValueOnce(
      sseResponse(
        `event: assistant.delta\ndata: ${JSON.stringify(publicEvent(2, "assistant.delta", { content: "persisted text" }))}\n\n` +
          `event: assistant.draft_reset\ndata: ${JSON.stringify(publicEvent(3, "assistant.draft_reset", { status: "discarded" }, false))}\n\n` +
          `event: done\ndata: ${JSON.stringify({ message_id: 9, latency_ms: 12, run_id: "run-fixture" })}\n\n`,
      ),
    );
    const { result } = renderHook(() => useStreamMessage(4));

    await act(async () => result.current.sendMessage({ query: "Where is it?" }));

    expect(result.current.streamState.content).toBe("persisted text");
    expect(result.current.streamState.deltaSource).toBe("authoritative");
  });

  it("keeps the status line off a retraction, which is not a phase", async () => {
    vi.spyOn(global, "fetch").mockResolvedValueOnce(
      sseResponse(
        `event: assistant.progress\ndata: ${JSON.stringify(publicEvent(2, "assistant.progress", { phase: "writing_answer", summary: "Writing the answer... 3s" }, false))}\n\n` +
          `event: assistant.draft_reset\ndata: ${JSON.stringify(publicEvent(3, "assistant.draft_reset", { status: "discarded" }, false))}\n\n` +
          `event: done\ndata: ${JSON.stringify({ message_id: 9, latency_ms: 12, run_id: "run-fixture" })}\n\n`,
      ),
    );
    const { result } = renderHook(() => useStreamMessage(4));

    await act(async () => result.current.sendMessage({ query: "Where is it?" }));

    expect(result.current.streamState.progress).toBe("Writing the answer... 3s");
    expect(result.current.streamState.progressDetail?.phase).toBe("writing_answer");
  });
});

describe("useStreamMessage licence refusal", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("surfaces a 402 as a licence refusal, not a generic failed turn", async () => {
    const { resetLicenceRefusedThrottle, onLicenceRefused } = await import("@/lib/licence");
    resetLicenceRefusedThrottle();
    const refused = vi.fn();
    const unsubscribe = onLicenceRefused(refused);
    const detail = {
      code: "licence_expired",
      message: "The licence for this instance has expired.",
      capability: "chat",
    };
    const fetchMock = vi.spyOn(global, "fetch").mockResolvedValueOnce(
      new Response(
        JSON.stringify({ error: detail.message, code: detail.code, detail, retryable: false }),
        { status: 402, headers: { "Content-Type": "application/json" } },
      ),
    );

    const { result } = renderHook(() => useStreamMessage(7));
    await act(async () => {
      await result.current.sendMessage({ query: "hello" });
    });

    expect(result.current.streamState.isStreaming).toBe(false);
    expect(result.current.streamState.licenceRefusal?.code).toBe("licence_expired");
    expect(result.current.streamState.error).toBe(detail.message);
    expect(refused).toHaveBeenCalled();
    // One request; nothing tried to refresh a session that is not expired.
    expect(fetchMock).toHaveBeenCalledTimes(1);
    unsubscribe();
  });
});
