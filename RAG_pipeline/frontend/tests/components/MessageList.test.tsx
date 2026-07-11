import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { MessageList } from "@/components/MessageList";
import type { StreamState } from "@/hooks/useStreamMessage";

function failedStream(error = "Provider timed out"): StreamState {
  return {
    isStreaming: false,
    content: "",
    sources: [],
    error,
    messageId: null,
    latencyMs: null,
    usage: null,
    steps: [],
    harnessActivities: [],
    runtimeTools: [],
  };
}

function streamingStream(overrides: Partial<StreamState> = {}): StreamState {
  return {
    isStreaming: true,
    content: "",
    sources: [],
    error: null,
    messageId: null,
    latencyMs: null,
    usage: null,
    steps: [],
    harnessActivities: [],
    runtimeTools: [],
    ...overrides,
  };
}

describe("MessageList", () => {
  beforeEach(() => {
    Element.prototype.scrollIntoView = vi.fn();
  });

  it("keeps a streamed chat failure visible and retryable", () => {
    const onRetryMessage = vi.fn();

    render(
      <MessageList
        messages={[]}
        streamingMessage={failedStream()}
        onRetryMessage={onRetryMessage}
      />,
    );

    expect(screen.getByText("Provider timed out")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /retry/i }));
    expect(onRetryMessage).toHaveBeenCalledOnce();
  });

  it("renders persisted failed assistant messages after reload", () => {
    render(
      <MessageList
        messages={[
          {
            id: 7,
            role: "assistant",
            content: "",
            sources: null,
            latency_ms: null,
            status: "failed",
            error_message: "Chat response failed",
            created_at: new Date().toISOString(),
          },
        ]}
      />,
    );

    expect(screen.getByText("Chat response failed")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /retry/i })).not.toBeInTheDocument();
  });

  it("shows the runtime running label before answer text arrives", () => {
    render(
      <MessageList
        messages={[]}
        streamingMessage={streamingStream({
          runtimeTools: [
            {
              toolName: "detect_failures",
              status: "running",
              latencyMs: null,
              truncated: false,
              error: null,
            },
          ],
        })}
      />,
    );

    expect(
      screen.getByText("Checking runtime: Detect failures"),
    ).toBeInTheDocument();
  });

  it("shows generic harness activity before answer text arrives", () => {
    render(
      <MessageList
        messages={[]}
        streamingMessage={streamingStream({
          harnessActivities: [
            {
              event_type: "capability.started",
              label: "Starting documentation generation",
              status: "running",
              summary: null,
              safe_details: {},
            },
          ],
        })}
      />,
    );

    expect(
      screen.getByText("Starting documentation generation"),
    ).toBeInTheDocument();
    expect(screen.queryByText(/runtime/i)).not.toBeInTheDocument();
  });

  it("renders a successful runtime result summary", () => {
    render(
      <MessageList
        messages={[]}
        streamingMessage={streamingStream({
          content: "Answer text",
          runtimeTools: [
            {
              toolName: "list_executions",
              status: "completed",
              latencyMs: 42,
              truncated: false,
              error: null,
            },
          ],
        })}
      />,
    );

    expect(
      screen.getByText("Runtime check complete: List executions"),
    ).toBeInTheDocument();
    expect(screen.getByText("42ms")).toBeInTheDocument();
  });

  it("renders a failed runtime result summary with its error code", () => {
    render(
      <MessageList
        messages={[]}
        streamingMessage={streamingStream({
          content: "Answer text",
          runtimeTools: [
            {
              toolName: "get_execution_logs",
              status: "failed",
              latencyMs: 8,
              truncated: false,
              error: { code: "unavailable", message: "Runtime offline" },
            },
          ],
        })}
      />,
    );

    expect(
      screen.getByText(
        "Runtime check unavailable: Get execution logs (unavailable)",
      ),
    ).toBeInTheDocument();
  });

  it("does not render raw runtime data payloads", () => {
    render(
      <MessageList
        messages={[]}
        streamingMessage={streamingStream({
          content: "Answer text",
          runtimeTools: [
            {
              toolName: "detect_failures",
              status: "completed",
              latencyMs: 10,
              truncated: true,
              error: null,
            },
          ],
        })}
      />,
    );

    expect(screen.getByText("(truncated)")).toBeInTheDocument();
    expect(screen.queryByText(/secret-payload/)).not.toBeInTheDocument();
  });
});
