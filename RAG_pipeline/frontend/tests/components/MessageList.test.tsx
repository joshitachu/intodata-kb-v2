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
});
