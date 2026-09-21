import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useStreamMessage } from "@/hooks/useStreamMessage";

/**
 * Two properties of the dispatcher that the labs lane depends on:
 *
 * 1. Reasoning arrives as its own frame type and must never reach the answer.
 * 2. A frame type this client does not know must degrade -- be kept, and keep
 *    its sequence number -- rather than disappear. Before this, the dispatcher
 *    gated on a whitelist of known types, so anything the backend added reached
 *    no parser at all.
 *
 * Plus the client-side stop, which is the only cancel either lane has.
 */

const encoder = new TextEncoder();

function sseResponse(body: string) {
  return new Response(
    new ReadableStream({
      start(controller) {
        controller.enqueue(encoder.encode(body));
        controller.close();
      },
    }),
    { status: 200 },
  );
}

function frame(
  sequence: number,
  type: string,
  publicPayload: Record<string, unknown>,
  replayable = false,
) {
  const event = {
    event_id: `run-fixture:evt:${String(sequence).padStart(6, "0")}`,
    run_id: "run-fixture",
    sequence,
    type,
    created_at: "2026-09-17T10:00:00Z",
    public_payload: publicPayload,
    replayable,
  };
  return `event: ${type}\ndata: ${JSON.stringify(event)}\n\n`;
}

/** Lets microtasks and the stream reader run between assertions. */
function flush() {
  return new Promise((resolve) => setTimeout(resolve, 0));
}

describe("useStreamMessage reasoning and unknown frames", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("accumulates assistant.reasoning without letting it reach the answer", async () => {
    vi.spyOn(global, "fetch").mockResolvedValueOnce(
      sseResponse(
        frame(1, "run.started", { status: "running" }) +
          frame(2, "assistant.reasoning", { content: "The user asks about " }) +
          frame(3, "tool.running", { tool_label: "read_docs", status: "running" }) +
          frame(4, "assistant.reasoning", { content: "invoices." }) +
          frame(5, "assistant.delta", { content: "Invoices are synced nightly." }) +
          'event: done\ndata: {"message_id":41,"latency_ms":900}\n\n',
      ),
    );
    const { result } = renderHook(() => useStreamMessage(4, "/api/labs"));

    await act(async () => {
      await result.current.sendMessage({ query: "How are invoices synced?" });
    });

    expect(result.current.streamState.reasoning).toBe(
      "The user asks about invoices.",
    );
    expect(result.current.streamState.content).toBe("Invoices are synced nightly.");
    // Reasoning deltas are not retained -- there is one per generated token --
    // and they are not a phase, so the status line keeps the tool's summary.
    expect(
      (result.current.streamState.agentEvents ?? []).map((event) => event.type),
    ).toEqual(["run.started", "tool.running"]);
    expect(result.current.streamState.progress).toBe("running");
  });

  it("starts a new turn with empty reasoning and keeps it on a resumed one", async () => {
    const fetchMock = vi.spyOn(global, "fetch");
    fetchMock.mockResolvedValueOnce(
      sseResponse(
        frame(1, "assistant.reasoning", { content: "First thought." }) +
          'event: done\ndata: {"message_id":42,"latency_ms":10}\n\n',
      ),
    );
    const { result } = renderHook(() => useStreamMessage(4, "/api/labs"));

    await act(async () => {
      await result.current.sendMessage({ query: "One", client_turn_id: "turn-aaa" });
    });
    expect(result.current.streamState.reasoning).toBe("First thought.");

    fetchMock.mockResolvedValueOnce(
      sseResponse(
        frame(2, "assistant.reasoning", { content: " Resumed." }) +
          'event: done\ndata: {"message_id":42,"latency_ms":10}\n\n',
      ),
    );
    await act(async () => {
      await result.current.sendMessage({ query: "One", client_turn_id: "turn-aaa" });
    });
    expect(result.current.streamState.reasoning).toBe("First thought. Resumed.");

    fetchMock.mockResolvedValueOnce(
      sseResponse(
        frame(1, "assistant.reasoning", { content: "Second turn." }) +
          'event: done\ndata: {"message_id":43,"latency_ms":10}\n\n',
      ),
    );
    await act(async () => {
      await result.current.sendMessage({ query: "Two", client_turn_id: "turn-bbb" });
    });
    expect(result.current.streamState.reasoning).toBe("Second turn.");

    act(() => result.current.reset());
    expect(result.current.streamState.reasoning).toBe("");
  });

  it("retains an unrecognised frame type instead of dropping it", async () => {
    vi.spyOn(global, "fetch").mockResolvedValueOnce(
      sseResponse(
        frame(1, "tool.completed", { tool_label: "read_docs", status: "succeeded", summary: "Read 3 docs" }) +
          frame(2, "assistant.not_yet_known", { status: "who_knows" }) +
          'event: done\ndata: {"message_id":44,"latency_ms":10}\n\n',
      ),
    );
    const { result } = renderHook(() => useStreamMessage(4, "/api/labs"));

    await act(async () => {
      await result.current.sendMessage({ query: "Question" });
    });

    const events = result.current.streamState.agentEvents ?? [];
    expect(events.map((event) => event.type)).toEqual([
      "tool.completed",
      "assistant.not_yet_known",
    ]);
    // ...but it may not relabel the turn, and its sequence still counts.
    expect(result.current.streamState.progress).toBe("Read 3 docs");
    expect(result.current.streamState.lastSequence).toBe(2);
  });

  it("ignores a frame that does not validate rather than breaking the stream", async () => {
    vi.spyOn(global, "fetch").mockResolvedValueOnce(
      sseResponse(
        'event: assistant.reasoning\ndata: {"not":"an event"}\n\n' +
          frame(1, "assistant.delta", { content: "Still here." }) +
          'event: done\ndata: {"message_id":45,"latency_ms":10}\n\n',
      ),
    );
    const { result } = renderHook(() => useStreamMessage(4, "/api/labs"));

    await act(async () => {
      await result.current.sendMessage({ query: "Question" });
    });

    expect(result.current.streamState.reasoning).toBe("");
    expect(result.current.streamState.content).toBe("Still here.");
    expect(result.current.streamState.messageId).toBe(45);
  });

  it("cancelStream stops reading without discarding text or inventing an error", async () => {
    let sink: ReadableStreamDefaultController<Uint8Array> | null = null;
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        sink = controller;
      },
    });
    vi.spyOn(global, "fetch").mockResolvedValueOnce(new Response(body, { status: 200 }));
    const { result } = renderHook(() => useStreamMessage(4, "/api/labs"));

    await act(async () => {
      void result.current.sendMessage({ query: "Long question" });
      await flush();
    });
    await act(async () => {
      sink?.enqueue(encoder.encode(frame(1, "assistant.delta", { content: "Half an " })));
      await flush();
    });
    expect(result.current.streamState.isStreaming).toBe(true);
    expect(result.current.streamState.content).toBe("Half an ");

    await act(async () => {
      result.current.cancelStream();
      await flush();
    });

    expect(result.current.streamState.isStreaming).toBe(false);
    // Stopping is not a failure, and what the server already sent is kept.
    expect(result.current.streamState.error).toBeNull();
    expect(result.current.streamState.content).toBe("Half an ");

    // Idempotent, and a no-op once nothing is in flight.
    act(() => result.current.cancelStream());
    expect(result.current.streamState.error).toBeNull();

    // The mocked fetch cannot reject the pending read the way a real abort
    // would, so the loop is unwound here to let the hook settle.
    await act(async () => {
      sink?.close();
      await flush();
    });
  });
});
