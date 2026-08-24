import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useStreamMessage } from "@/hooks/useStreamMessage";

/**
 * The events `useProgressiveTargets` consumes. `progress` (the collapsed
 * one-line summary the chat page renders) must keep behaving exactly as before,
 * so it is asserted alongside every case here.
 */

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

function frame(
  sequence: number,
  type: string,
  publicPayload: Record<string, unknown>,
  eventId?: string,
) {
  const event = {
    event_id: eventId ?? `run-fixture:evt:${String(sequence).padStart(6, "0")}`,
    run_id: "run-fixture",
    sequence,
    type,
    created_at: "2026-08-20T10:00:00Z",
    public_payload: publicPayload,
    replayable: true,
  };
  return `event: ${type}\ndata: ${JSON.stringify(event)}\n\n`;
}

const TARGETS = [{ label: "Order sync", level: { kind: "job", jobName: "tJob_OrderSync" } }];

describe("useStreamMessage agentEvents", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("exposes parsed tool events so a host can react before the prose finishes", async () => {
    vi.spyOn(global, "fetch").mockResolvedValueOnce(
      sseResponse(
        frame(1, "tool.started", { summary: "Reading the topology" }) +
          frame(2, "tool.completed", { summary: "Found 1 job", targets: TARGETS }) +
          'event: done\ndata: {"message_id":11,"latency_ms":40}\n\n',
      ),
    );
    const { result } = renderHook(() => useStreamMessage(4));

    await act(async () => {
      await result.current.sendMessage({ query: "Which job syncs orders?" });
    });

    const events = result.current.streamState.agentEvents ?? [];
    expect(events.map((event) => event.type)).toEqual(["tool.started", "tool.completed"]);
    expect(events[1].public_payload.targets).toEqual(TARGETS);
    // Unchanged behaviour: the collapsed summary still tracks the last event.
    expect(result.current.streamState.progress).toBe("Found 1 job");
  });

  it("does not retain assistant.delta, whose prose is already in content", async () => {
    vi.spyOn(global, "fetch").mockResolvedValueOnce(
      sseResponse(
        frame(1, "assistant.delta", { content: "Order" }) +
          frame(2, "assistant.delta", { content: " sync." }) +
          frame(3, "tool.completed", { summary: "Done", targets: TARGETS }) +
          'event: done\ndata: {"message_id":12,"latency_ms":40}\n\n',
      ),
    );
    const { result } = renderHook(() => useStreamMessage(4));

    await act(async () => {
      await result.current.sendMessage({ query: "Question" });
    });

    expect(result.current.streamState.content).toBe("Order sync.");
    expect((result.current.streamState.agentEvents ?? []).map((event) => event.type)).toEqual([
      "tool.completed",
    ]);
  });

  it("keeps progress untouched by assistant.delta frames", async () => {
    vi.spyOn(global, "fetch").mockResolvedValueOnce(
      sseResponse(
        frame(1, "tool.completed", { summary: "Read the diagram" }) +
          frame(2, "assistant.delta", { content: "Text", summary: "should not win" }) +
          'event: done\ndata: {"message_id":13,"latency_ms":40}\n\n',
      ),
    );
    const { result } = renderHook(() => useStreamMessage(4));

    await act(async () => {
      await result.current.sendMessage({ query: "Question" });
    });

    expect(result.current.streamState.progress).toBe("Read the diagram");
  });

  it("suppresses a replayed event_id rather than appending it twice", async () => {
    vi.spyOn(global, "fetch").mockResolvedValueOnce(
      sseResponse(
        frame(1, "tool.completed", { summary: "First" }, "run-fixture:evt:dup") +
          frame(2, "tool.completed", { summary: "Replay" }, "run-fixture:evt:dup") +
          'event: done\ndata: {"message_id":14,"latency_ms":40}\n\n',
      ),
    );
    const { result } = renderHook(() => useStreamMessage(4));

    await act(async () => {
      await result.current.sendMessage({ query: "Question" });
    });

    expect(result.current.streamState.agentEvents).toHaveLength(1);
    expect(result.current.streamState.progress).toBe("First");
  });

  it("resets the buffer for a new turn but keeps it for a resumed one", async () => {
    const fetchMock = vi.spyOn(global, "fetch");
    fetchMock.mockResolvedValueOnce(
      sseResponse(
        frame(1, "tool.completed", { summary: "Turn one" }) +
          'event: done\ndata: {"message_id":21,"latency_ms":40}\n\n',
      ),
    );
    const { result } = renderHook(() => useStreamMessage(4));

    await act(async () => {
      await result.current.sendMessage({ query: "First", client_turn_id: "turn-one-aaaa" });
    });
    expect(result.current.streamState.agentEvents).toHaveLength(1);

    // Same client_turn_id: a resumed turn, so already-delivered events stay.
    fetchMock.mockResolvedValueOnce(
      sseResponse(
        frame(2, "tool.completed", { summary: "Turn one, resumed" }) +
          'event: done\ndata: {"message_id":21,"latency_ms":40}\n\n',
      ),
    );
    await act(async () => {
      await result.current.sendMessage({ query: "First", client_turn_id: "turn-one-aaaa" });
    });
    expect(result.current.streamState.agentEvents).toHaveLength(2);

    // A different turn starts from empty.
    fetchMock.mockResolvedValueOnce(
      sseResponse(
        frame(1, "tool.completed", { summary: "Turn two" }) +
          'event: done\ndata: {"message_id":22,"latency_ms":40}\n\n',
      ),
    );
    await act(async () => {
      await result.current.sendMessage({ query: "Second", client_turn_id: "turn-two-bbbb" });
    });
    expect(result.current.streamState.agentEvents).toHaveLength(1);
    expect(result.current.streamState.progress).toBe("Turn two");
  });

  it("clears the buffer on reset", async () => {
    vi.spyOn(global, "fetch").mockResolvedValueOnce(
      sseResponse(
        frame(1, "tool.completed", { summary: "Something" }) +
          'event: done\ndata: {"message_id":31,"latency_ms":40}\n\n',
      ),
    );
    const { result } = renderHook(() => useStreamMessage(4));

    await act(async () => {
      await result.current.sendMessage({ query: "Question" });
    });
    expect(result.current.streamState.agentEvents).toHaveLength(1);

    act(() => result.current.reset());
    expect(result.current.streamState.agentEvents).toEqual([]);
  });
});
