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

    await waitFor(() => {
      expect(result.current.streamState.isStreaming).toBe(false);
    });

    expect(result.current.streamState.messageId).toBeNull();
    expect(result.current.streamState.error).toBe("Provider timed out");
  });

  it("parses multiline SSE data as one event payload", async () => {
    vi.spyOn(global, "fetch").mockResolvedValueOnce(
      sseResponse('event: done\ndata: {\ndata: "message_id":7,\ndata: "latency_ms":10\ndata: }\n\n'),
    );
    const { result } = renderHook(() => useStreamMessage(4));

    await act(async () => {
      await result.current.sendMessage({ query: "Question" });
    });

    await waitFor(() => {
      expect(result.current.streamState.isStreaming).toBe(false);
    });

    expect(result.current.streamState.messageId).toBe(7);
    expect(result.current.streamState.latencyMs).toBe(10);
  });

  it("parses agent step events", async () => {
    vi.spyOn(global, "fetch").mockResolvedValueOnce(
      sseResponse(
        'event: step\ndata: {"id":"search_documents","label":"Searching documents","status":"completed"}\n\n' +
          'event: done\ndata: {"message_id":7,"latency_ms":10}\n\n',
      ),
    );
    const { result } = renderHook(() => useStreamMessage(4));

    await act(async () => {
      await result.current.sendMessage({ query: "Question" });
    });

    await waitFor(() => {
      expect(result.current.streamState.isStreaming).toBe(false);
    });

    expect(result.current.streamState.steps).toEqual([
      {
        id: "search_documents",
        label: "Searching documents",
        status: "completed",
      },
    ]);
  });
});
