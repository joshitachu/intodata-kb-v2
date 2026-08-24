import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useStreamMessage } from "@/hooks/useStreamMessage";

/**
 * `progressDetail` -- the whole `assistant.progress` payload, which the waiting
 * state renders. The collapsed `progress` one-liner and the non-retention of
 * heartbeats in `agentEvents` are asserted alongside it, because both are
 * existing behaviour this must not disturb.
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

function frame(sequence: number, type: string, publicPayload: Record<string, unknown>) {
  const event = {
    event_id: `run-fixture:evt:${String(sequence).padStart(6, "0")}`,
    run_id: "run-fixture",
    sequence,
    type,
    created_at: "2026-08-21T10:00:00Z",
    public_payload: publicPayload,
    replayable: true,
  };
  return `event: ${type}\ndata: ${JSON.stringify(event)}\n\n`;
}

const DONE = 'event: done\ndata: {"message_id":11,"latency_ms":40}\n\n';

describe("useStreamMessage progressDetail", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("keeps the latest heartbeat whole, including an unrecognised phase", async () => {
    vi.spyOn(global, "fetch").mockResolvedValueOnce(
      sseResponse(
        frame(1, "assistant.progress", {
          status: "running",
          phase: "resolving_candidates",
          summary: "Resolving candidates... 2s",
          elapsed_ms: 2100,
        }) +
          frame(2, "assistant.progress", {
            status: "running",
            // A slug this build has never seen: it must survive untouched.
            phase: "reading_component_graph_project_topology",
            summary: "Reading the component graph... 6s",
            elapsed_ms: 6400,
          }) +
          DONE,
      ),
    );
    const { result } = renderHook(() => useStreamMessage(4));

    await act(async () => {
      await result.current.sendMessage({ query: "Which job writes orders?" });
    });

    expect(result.current.streamState.progressDetail).toEqual({
      status: "running",
      phase: "reading_component_graph_project_topology",
      summary: "Reading the component graph... 6s",
      elapsedMs: 6400,
    });
    // Unchanged: heartbeats stay out of the retained event buffer.
    expect(result.current.streamState.agentEvents).toEqual([]);
    expect(result.current.streamState.progress).toBe("Reading the component graph... 6s");
  });

  it("keeps the last known phase when a heartbeat carries nothing usable", async () => {
    vi.spyOn(global, "fetch").mockResolvedValueOnce(
      sseResponse(
        frame(1, "assistant.progress", { phase: "thinking", summary: "Waiting on the model... 3s" }) +
          frame(2, "assistant.progress", { unrelated: true }) +
          DONE,
      ),
    );
    const { result } = renderHook(() => useStreamMessage(4));

    await act(async () => {
      await result.current.sendMessage({ query: "Question" });
    });

    expect(result.current.streamState.progressDetail).toEqual({
      status: null,
      phase: "thinking",
      summary: "Waiting on the model... 3s",
      elapsedMs: null,
    });
  });

  it("drops a heartbeat's nonsense values rather than showing them", async () => {
    vi.spyOn(global, "fetch").mockResolvedValueOnce(
      sseResponse(
        frame(1, "assistant.progress", {
          status: "   ",
          phase: "thinking",
          summary: 42,
          elapsed_ms: -5,
        }) + DONE,
      ),
    );
    const { result } = renderHook(() => useStreamMessage(4));

    await act(async () => {
      await result.current.sendMessage({ query: "Question" });
    });

    expect(result.current.streamState.progressDetail).toEqual({
      status: null,
      phase: "thinking",
      summary: null,
      elapsedMs: null,
    });
  });

  it("starts a new turn without the previous turn's phase", async () => {
    const fetchMock = vi.spyOn(global, "fetch");
    fetchMock.mockResolvedValueOnce(
      sseResponse(frame(1, "assistant.progress", { phase: "thinking" }) + DONE),
    );
    const { result } = renderHook(() => useStreamMessage(4));

    await act(async () => {
      await result.current.sendMessage({ query: "First", client_turn_id: "turn-one-aaaa" });
    });
    expect(result.current.streamState.progressDetail?.phase).toBe("thinking");

    fetchMock.mockResolvedValueOnce(sseResponse(DONE));
    await act(async () => {
      await result.current.sendMessage({ query: "Second", client_turn_id: "turn-two-bbbb" });
    });
    expect(result.current.streamState.progressDetail).toBeNull();
  });

  it("clears the phase on reset", async () => {
    vi.spyOn(global, "fetch").mockResolvedValueOnce(
      sseResponse(frame(1, "assistant.progress", { phase: "thinking" }) + DONE),
    );
    const { result } = renderHook(() => useStreamMessage(4));

    await act(async () => {
      await result.current.sendMessage({ query: "Question" });
    });
    expect(result.current.streamState.progressDetail).not.toBeNull();

    act(() => result.current.reset());
    expect(result.current.streamState.progressDetail).toBeNull();
  });
});
