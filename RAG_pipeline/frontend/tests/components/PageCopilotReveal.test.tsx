import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { PageCopilot } from "@/components/PageCopilot";
import type { PageCopilotProps } from "@/components/PageCopilot";
import type { CopilotTarget } from "@/types/copilot";
import type { MessageResponse } from "@/types";
import type { StreamState } from "@/hooks/useStreamMessage";

/**
 * Panel-level behaviour of the progressive reveal. The hook's own rules are
 * covered in `tests/hooks/useProgressiveReveal.test.tsx`; what matters here is
 * that the panel paces the answer WITHOUT delaying anything the finished turn
 * earned -- chips, attribution, and the host's own navigation.
 */

const ANSWER =
  "SAP ERP receives order data from the warehouse job, which reads the staging table first, " +
  "then writes the confirmed lines back to the order service.";

/** The reveal's clock and scheduler, faked together so timing is deterministic. */
function useRevealTimers() {
  vi.useFakeTimers({
    toFake: [
      "requestAnimationFrame",
      "cancelAnimationFrame",
      "performance",
      "Date",
      "setTimeout",
      "clearTimeout",
    ],
  });
}

function stream(overrides: Partial<StreamState> = {}): StreamState {
  return {
    isStreaming: false,
    content: "",
    error: null,
    messageId: null,
    latencyMs: null,
    typedResponse: null,
    runId: "run-1",
    progress: null,
    progressDetail: null,
    lastSequence: 0,
    agentEvents: [],
    ...overrides,
  };
}

function assistantMessage(content: string): MessageResponse {
  return {
    id: 900,
    role: "assistant",
    content,
    sources: null,
    latency_ms: 120,
    status: "completed",
    created_at: "2026-08-21T10:00:00Z",
  };
}

const TARGET: CopilotTarget = {
  key: "job:tJob_OrderSync",
  label: "Order sync",
  detail: "Job",
  resolvable: true,
};

function props(overrides: Partial<PageCopilotProps> = {}): PageCopilotProps {
  return {
    scope: { kindLabel: "Landscape", name: "Werkorder" },
    messages: [],
    isSending: false,
    onSend: vi.fn(),
    collapsed: false,
    onCollapsedChange: vi.fn(),
    ...overrides,
  };
}

/** Everything the panel is currently showing, markdown included. */
function panelText(): string {
  return screen.getByRole("complementary", { name: "Assistant" }).textContent ?? "";
}

describe("PageCopilot progressive reveal", () => {
  beforeEach(() => {
    Element.prototype.scrollIntoView = vi.fn();
    useRevealTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("paces an answer that arrived all at once", () => {
    const { rerender } = render(<PageCopilot {...props({ streamState: stream({ isStreaming: true }) })} />);

    // The backend fires every chunk in one tight loop, so the buffer goes from
    // empty to complete in a single update.
    rerender(<PageCopilot {...props({ streamState: stream({ isStreaming: true, content: ANSWER }) })} />);
    expect(panelText()).not.toContain("SAP ERP receives order data");

    act(() => vi.advanceTimersByTime(64));
    expect(panelText()).toContain("SAP ERP receives");
    expect(panelText()).not.toContain("writes the confirmed lines");

    act(() => vi.advanceTimersByTime(600));
    expect(panelText()).toContain("writes the confirmed lines back to the order service.");
  });

  it("does not delay chips or attribution behind the reveal", () => {
    const onTargetClick = vi.fn();
    const base = {
      targets: [TARGET],
      answerSource: {
        attribution: "surface" as const,
        label: "Answered from this diagram",
      },
      onTargetClick,
    };

    const { rerender } = render(
      <PageCopilot {...props({ ...base, streamState: stream({ isStreaming: true }) })} />,
    );
    rerender(
      <PageCopilot
        {...props({ ...base, streamState: stream({ isStreaming: true, content: ANSWER }) })}
      />,
    );

    // The turn completes: the host clears the buffer and the saved message
    // arrives. The reveal is still running -- and must not hold the turn's
    // outputs back for even one frame.
    act(() => vi.advanceTimersByTime(48));
    rerender(
      <PageCopilot
        {...props({ ...base, streamState: null, messages: [assistantMessage(ANSWER)] })}
      />,
    );

    expect(panelText()).not.toContain("writes the confirmed lines");
    expect(screen.getByLabelText("On this page")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Order sync/ })).toBeEnabled();
    // The composer is live again the moment the turn ends, not when the
    // animation does.
    expect(screen.getByPlaceholderText("Ask about what you see...")).not.toBeDisabled();

    // The reveal carries across the hand-off instead of restarting.
    act(() => vi.advanceTimersByTime(600));
    expect(panelText()).toContain("writes the confirmed lines back to the order service.");
  });

  it("keeps revealing across a parent re-render", () => {
    const { rerender } = render(<PageCopilot {...props({ streamState: stream({ isStreaming: true }) })} />);
    rerender(<PageCopilot {...props({ streamState: stream({ isStreaming: true, content: ANSWER }) })} />);
    act(() => vi.advanceTimersByTime(80));
    const revealed = panelText();

    // A parent re-render with new, unrelated props: the reveal must not replay
    // the answer from the top.
    rerender(
      <PageCopilot
        {...props({
          streamState: stream({ isStreaming: true, content: ANSWER }),
          notices: [{ kind: "stale-overlay" as const, title: "Overlay is stale" }],
        })}
      />,
    );
    expect(panelText()).toContain("Overlay is stale");
    expect(panelText()).toContain(revealed.slice(0, 20).trim().slice(0, 15));
    expect(ANSWER.startsWith(messageProse(panelText()))).toBe(true);
  });

  it("reveals the whole answer at once under prefers-reduced-motion", () => {
    vi.spyOn(window, "matchMedia").mockImplementation((query: string) => ({
      matches: query.includes("prefers-reduced-motion"),
      media: query,
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    }) as MediaQueryList);

    const { rerender } = render(<PageCopilot {...props({ streamState: stream({ isStreaming: true }) })} />);
    rerender(<PageCopilot {...props({ streamState: stream({ isStreaming: true, content: ANSWER }) })} />);

    expect(panelText()).toContain("writes the confirmed lines back to the order service.");
  });

  it("completes the previous reveal instantly when the next question is sent", () => {
    const onSend = vi.fn();
    const { rerender } = render(
      <PageCopilot {...props({ onSend, streamState: stream({ isStreaming: true }) })} />,
    );
    rerender(
      <PageCopilot {...props({ onSend, streamState: stream({ isStreaming: true, content: ANSWER }) })} />,
    );
    act(() => vi.advanceTimersByTime(48));
    rerender(
      <PageCopilot
        {...props({ onSend, streamState: null, messages: [assistantMessage(ANSWER)] })}
      />,
    );
    expect(panelText()).not.toContain("writes the confirmed lines");

    fireEvent.change(screen.getByPlaceholderText("Ask about what you see..."), {
      target: { value: "And which job writes it back?" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Send message" }));

    expect(onSend).toHaveBeenCalledWith("And which job writes it back?");
    // No text left mid-flight behind the new question.
    expect(panelText()).toContain("writes the confirmed lines back to the order service.");
  });

  it("completes the reveal when the user jumps to a target", () => {
    const onTargetClick = vi.fn();
    const base = { targets: [TARGET], onTargetClick };
    const { rerender } = render(
      <PageCopilot {...props({ ...base, streamState: stream({ isStreaming: true }) })} />,
    );
    rerender(
      <PageCopilot
        {...props({ ...base, streamState: stream({ isStreaming: true, content: ANSWER }) })}
      />,
    );
    act(() => vi.advanceTimersByTime(48));
    rerender(
      <PageCopilot
        {...props({ ...base, streamState: null, messages: [assistantMessage(ANSWER)] })}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /Order sync/ }));

    expect(onTargetClick).toHaveBeenCalledWith(TARGET);
    expect(panelText()).toContain("writes the confirmed lines back to the order service.");
  });

  it("renders a finished thread in full, with no animation to wait for", () => {
    render(<PageCopilot {...props({ messages: [assistantMessage(ANSWER)] })} />);

    expect(panelText()).toContain("writes the confirmed lines back to the order service.");
  });
});

/**
 * The answer prose out of the panel's full text. The panel wraps it in chrome
 * (title, scope chip, input placeholder), so a prefix assertion needs the
 * answer's own span.
 */
function messageProse(text: string): string {
  const start = text.indexOf("SAP ERP");
  return start < 0 ? "" : text.slice(start);
}
