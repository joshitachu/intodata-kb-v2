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

  it("tracks a runtime tool call as running then completed", async () => {
    vi.spyOn(global, "fetch").mockResolvedValueOnce(
      sseResponse(
        'event: runtime_tool_call\ndata: {"tool_name":"detect_failures","arguments":{}}\n\n' +
          'event: runtime_tool_result\ndata: {"tool_name":"detect_failures","success":true,"data":{"failures":[]},"error":null,"latency_ms":42,"truncated":false}\n\n' +
          'event: done\ndata: {"message_id":7,"latency_ms":50}\n\n',
      ),
    );
    const { result } = renderHook(() => useStreamMessage(4));

    await act(async () => {
      await result.current.sendMessage({ query: "Any failures?" });
    });

    await waitFor(() => {
      expect(result.current.streamState.isStreaming).toBe(false);
    });

    expect(result.current.streamState.runtimeTools).toEqual([
      {
        toolName: "detect_failures",
        status: "completed",
        latencyMs: 42,
        truncated: false,
        error: null,
      },
    ]);
  });

  it("marks a failed runtime result without setting a stream-level error", async () => {
    vi.spyOn(global, "fetch").mockResolvedValueOnce(
      sseResponse(
        'event: runtime_tool_call\ndata: {"tool_name":"list_executions","arguments":{}}\n\n' +
          'event: runtime_tool_result\ndata: {"tool_name":"list_executions","success":false,"data":null,"error":{"code":"unavailable","message":"Runtime offline"},"latency_ms":8,"truncated":false}\n\n' +
          'event: done\ndata: {"message_id":7,"latency_ms":20}\n\n',
      ),
    );
    const { result } = renderHook(() => useStreamMessage(4));

    await act(async () => {
      await result.current.sendMessage({ query: "List runs" });
    });

    await waitFor(() => {
      expect(result.current.streamState.isStreaming).toBe(false);
    });

    expect(result.current.streamState.runtimeTools).toEqual([
      {
        toolName: "list_executions",
        status: "failed",
        latencyMs: 8,
        truncated: false,
        error: { code: "unavailable", message: "Runtime offline" },
      },
    ]);
    expect(result.current.streamState.error).toBeNull();
  });

  it("ignores malformed runtime payloads without ending the stream early", async () => {
    vi.spyOn(global, "fetch").mockResolvedValueOnce(
      sseResponse(
        'event: runtime_tool_call\ndata: {"arguments":{}}\n\n' +
          'event: runtime_tool_result\ndata: {"tool_name":123}\n\n' +
          'event: token\ndata: "Answer"\n\n' +
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

    expect(result.current.streamState.runtimeTools).toEqual([]);
    expect(result.current.streamState.content).toBe("Answer");
    expect(result.current.streamState.messageId).toBe(7);
    expect(result.current.streamState.error).toBeNull();
  });
});
