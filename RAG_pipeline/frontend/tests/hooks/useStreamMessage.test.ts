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

  it("exposes a Pack workspace hint before the response finishes", async () => {
    vi.spyOn(global, "fetch").mockResolvedValueOnce(
      sseResponse(
        'event: workspace\ndata: {"workspace":"pack","status":"running"}\n\n' +
          'event: token\ndata: "What should this Pack analyze?"\n\n' +
          'event: done\ndata: {"message_id":7,"latency_ms":10}\n\n',
      ),
    );
    const { result } = renderHook(() => useStreamMessage(4));

    await act(async () => {
      await result.current.sendMessage({ query: "Help me create a Pack" });
    });

    expect(result.current.streamState.workspace).toBe("pack");
    expect(result.current.streamState.workspaceStatus).toBe("running");
  });

  it("parses a typed final answer independently from streamed tokens", async () => {
    const answer = {
      output_contract: "runtime_ranking",
      headline: "Runtime failure ranking",
      summary: "Job A failed most in the returned observations.",
      sections: [
        {
          heading: "Failure ranking",
          rows: [{ rank: 1, job: "Job A", failures: 3 }],
        },
      ],
      limitations: [],
      citations: [],
      evidence_refs: [],
      answer_status: "complete",
      execution_status: "completed",
      domain_status: "failed",
      artifacts: [],
    };
    vi.spyOn(global, "fetch").mockResolvedValueOnce(
      sseResponse(
        `event: token\ndata: "Job A failed most."\n\n` +
          `event: final_answer\ndata: ${JSON.stringify(answer)}\n\n` +
          'event: done\ndata: {"message_id":7,"latency_ms":10}\n\n',
      ),
    );
    const { result } = renderHook(() => useStreamMessage(4));

    await act(async () => {
      await result.current.sendMessage({ query: "Which job failed most?" });
    });

    expect(result.current.streamState.content).toBe("Job A failed most.");
    expect(result.current.streamState.structuredAnswer).toEqual(answer);
    expect(result.current.streamState.isStreaming).toBe(false);
    expect(result.current.streamState.messageId).toBe(7);
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

  it("parses generic harness activity without treating it as runtime progress", async () => {
    vi.spyOn(global, "fetch").mockResolvedValueOnce(
      sseResponse(
        'event: harness_activity\ndata: {"workflow_run_id":"run-1","event_type":"capability.started","label":"Starting documentation generation","status":"running","provider_id":"mcp-docs","capability_id":"generate_project_documentation","summary":"Generation request accepted","safe_details":{"project":"fastapi-e2e"},"payload":{"secret":"ignored"}}\n\n' +
          'event: done\ndata: {"message_id":7,"latency_ms":20}\n\n',
      ),
    );
    const { result } = renderHook(() => useStreamMessage(4));

    await act(async () => {
      await result.current.sendMessage({ query: "Generate docs" });
    });

    await waitFor(() => {
      expect(result.current.streamState.isStreaming).toBe(false);
    });

    expect(result.current.streamState.harnessActivities).toEqual([
      {
        workflow_run_id: "run-1",
        event_type: "capability.started",
        label: "Starting documentation generation",
        status: "running",
        summary: "Generation request accepted",
        safe_details: { project: "fastapi-e2e" },
      },
    ]);
    expect(result.current.streamState.runtimeTools).toEqual([]);
  });

  it("captures a sanitized pending action and ends the active stream", async () => {
    vi.spyOn(global, "fetch").mockResolvedValueOnce(
      sseResponse(
        'event: pending_action\ndata: {"workflow_run_id":42,"interrupt_id":8,"expected_version":2,"kind":"clarification","prompt":"Which project should I use?","choices":[{"label":"Customer API","value":"customer-api"}],"capability_id":"secret-capability","resume_token":"secret-token","payload":{"raw":true}}\n\n',
      ),
    );
    const { result } = renderHook(() => useStreamMessage(4));

    await act(async () => {
      await result.current.sendMessage({ query: "Generate docs" });
    });

    expect(result.current.streamState.isStreaming).toBe(false);
    expect(result.current.streamState.pendingAction).toEqual({
      workflow_run_id: "42",
      interrupt_id: 8,
      expected_version: 2,
      kind: "clarification",
      prompt: "Which project should I use?",
      choices: [{ label: "Customer API", value: "customer-api" }],
      required_fields: [],
      project_id: undefined,
      platform: undefined,
      expires_at: undefined,
    });
    expect(result.current.streamState.pendingAction).not.toHaveProperty("resume_token");
    expect(result.current.streamState.pendingAction).not.toHaveProperty("capability_id");
    expect(result.current.streamState.pendingAction).not.toHaveProperty("payload");
  });

  it("ignores malformed pending actions", async () => {
    vi.spyOn(global, "fetch").mockResolvedValueOnce(
      sseResponse(
        'event: pending_action\ndata: {"workflow_run_id":"run-1","interrupt_id":1,"expected_version":1,"kind":"approval","prompt":""}\n\n' +
          'event: done\ndata: {"message_id":7,"latency_ms":10}\n\n',
      ),
    );
    const { result } = renderHook(() => useStreamMessage(4));

    await act(async () => {
      await result.current.sendMessage({ query: "Update docs" });
    });

    expect(result.current.streamState.pendingAction).toBeNull();
    expect(result.current.streamState.messageId).toBe(7);
  });

  it("normalizes planner activity status aliases across a capability stream", async () => {
    vi.spyOn(global, "fetch").mockResolvedValueOnce(
      sseResponse(
        'event: activity\ndata: {"event_type":"planning","label":"Planning next step","status":"started","safe_details":{"decision":"call_capability"}}\n\n' +
          'event: activity\ndata: {"event_type":"scope_resolution","label":"Resolved project scope","status":"resolved","safe_details":{"project_id":"fastapi-e2e"}}\n\n' +
          'event: activity\ndata: {"event_type":"capability_selected","label":"Selected documentation generation","status":"selected","capability_id":"mcp.docs.generate_project_documentation","safe_details":{"risk_tier":"triggers_workflow"}}\n\n' +
          'event: activity\ndata: {"event_type":"provider_trigger","label":"Starting documentation generation","status":"triggered","safe_details":{"provider":"mcp.docs"}}\n\n' +
          'event: activity\ndata: {"event_type":"provider_result","label":"Documentation generation started","status":"success","safe_details":{"workflow_run_id":42,"status":"generation_triggered"}}\n\n' +
          'event: done\ndata: {"message_id":7,"latency_ms":20}\n\n',
      ),
    );
    const { result } = renderHook(() => useStreamMessage(4));

    await act(async () => {
      await result.current.sendMessage({ query: "Generate docs" });
    });

    await waitFor(() => {
      expect(result.current.streamState.isStreaming).toBe(false);
    });

    expect(result.current.streamState.harnessActivities.map((activity) => activity.event_type)).toEqual([
      "planning",
      "scope_resolution",
      "capability_selected",
      "provider_trigger",
      "provider_result",
    ]);
    expect(result.current.streamState.harnessActivities.map((activity) => activity.status)).toEqual([
      "running",
      "completed",
      "completed",
      "running",
      "completed",
    ]);
    expect(result.current.streamState.runtimeTools).toEqual([]);
  });

  it("updates repeated activity snapshots in place without retaining internal identifiers", async () => {
    vi.spyOn(global, "fetch").mockResolvedValueOnce(
      sseResponse(
        'event: harness_activity\ndata: {"workflow_run_id":"42","event_type":"evidence.search","label":"Searching documentation","status":"running","capability_id":"mcp.docs.search","safe_details":{"search_query":"AssetCheck"}}\n\n' +
          'event: harness_activity\ndata: {"workflow_run_id":"42","event_type":"evidence.search","label":"Searching documentation","status":"completed","provider_id":"mcp.docs","safe_details":{"result_count":2}}\n\n' +
          'event: done\ndata: {"message_id":7,"latency_ms":20}\n\n',
      ),
    );
    const { result } = renderHook(() => useStreamMessage(4));

    await act(async () => {
      await result.current.sendMessage({ query: "Find AssetCheck" });
    });

    expect(result.current.streamState.harnessActivities).toEqual([
      {
        workflow_run_id: "42",
        event_type: "evidence.search",
        label: "Searching documentation",
        status: "completed",
        safe_details: { search_query: "AssetCheck", result_count: 2 },
      },
    ]);
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
