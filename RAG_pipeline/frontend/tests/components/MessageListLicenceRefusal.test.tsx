import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { MessageList } from "@/components/MessageList";
import type { StreamState } from "@/hooks/useStreamMessage";

describe("MessageList licence refusal", () => {
  beforeEach(() => {
    Element.prototype.scrollIntoView = vi.fn();
  });

  it("shows the refusal inline instead of a failed, retryable turn", () => {
    const message = "The licence for this instance has expired.";
    const refused: StreamState = {
      isStreaming: false,
      content: "",
      error: message,
      messageId: null,
      latencyMs: null,
      typedResponse: null,
      licenceRefusal: { code: "licence_expired", message, capability: "chat" },
    };

    render(<MessageList messages={[]} streamingMessage={refused} onRetryMessage={vi.fn()} />);

    const notice = screen.getByTestId("licence-refusal");
    expect(notice).toHaveTextContent(message);
    expect(notice).toHaveAttribute("data-refusal-code", "licence_expired");
    // No retry: the licence, not the turn, is what failed.
    expect(screen.queryByRole("button", { name: /retry/i })).not.toBeInTheDocument();
  });
});
