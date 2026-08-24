import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { PageCopilot, copilotNotice } from "@/components/PageCopilot";
import type { PageCopilotProps } from "@/components/PageCopilot";
import type { CopilotTarget } from "@/types/copilot";
import type { MessageResponse } from "@/types";
import type { StreamState } from "@/hooks/useStreamMessage";

function stream(overrides: Partial<StreamState> = {}): StreamState {
  return {
    isStreaming: false,
    content: "",
    error: null,
    messageId: null,
    latencyMs: null,
    typedResponse: null,
    ...overrides,
  };
}

function assistantMessage(content: string): MessageResponse {
  return {
    id: 12,
    role: "assistant",
    content,
    sources: null,
    latency_ms: 120,
    status: "completed",
    created_at: "2026-08-20T10:00:00Z",
  };
}

function assistantMessageWithEvidence(content: string): MessageResponse {
  return {
    ...assistantMessage(content),
    structured_answer: {
      response_type: "agent_response_v1",
      kind: "answer",
      content,
      synthesis_source: "agent_plan",
      facts: [],
      artifact_links: [],
      evidence_refs: [],
      workflow_cards: [],
      evidence_summary: {
        version: "1.0",
        evidence_step_count: 1,
        successful_sources: [
          { family: "documentation", role: "explanation", evidence_count: 2 },
        ],
        failed_sources: [],
        zero_evidence: false,
        documentation: {
          provider_used: "kimi",
          fallback_used: false,
          attempts: [{ provider: "kimi", status: "succeeded" }],
        },
      },
      stop_reason: "answered",
      factual_fingerprint: "e".repeat(64),
    },
  };
}

function renderPanel(overrides: Partial<PageCopilotProps> = {}) {
  const props: PageCopilotProps = {
    scope: { kindLabel: "Landscape", name: "Werkorder" },
    messages: [],
    isSending: false,
    onSend: vi.fn(),
    collapsed: false,
    onCollapsedChange: vi.fn(),
    ...overrides,
  };
  return { props, ...render(<PageCopilot {...props} />) };
}

describe("PageCopilot", () => {
  beforeEach(() => {
    Element.prototype.scrollIntoView = vi.fn();
  });

  it("renders the idle state with the scope indicator and suggested prompts", () => {
    const onSuggestionClick = vi.fn();
    renderPanel({
      suggestions: ["Which systems feed SAP?", "Where does order data enter?"],
      onSuggestionClick,
    });

    expect(screen.getByRole("complementary", { name: "Assistant" })).toBeInTheDocument();
    expect(screen.getByLabelText("Current scope")).toHaveTextContent("Landscape");
    expect(screen.getByLabelText("Current scope")).toHaveTextContent("Werkorder");

    fireEvent.click(screen.getByRole("button", { name: "Which systems feed SAP?" }));
    expect(onSuggestionClick).toHaveBeenCalledWith("Which systems feed SAP?");
  });

  it("reflects a different scope descriptor without any topology knowledge", () => {
    renderPanel({ scope: { kindLabel: "Job", name: "tJob_OrderSync" } });
    const chip = screen.getByLabelText("Current scope");
    expect(chip).toHaveTextContent("Job");
    expect(chip).toHaveTextContent("tJob_OrderSync");
  });

  it("renders partial text while streaming and disables sending", () => {
    renderPanel({ streamState: stream({ isStreaming: true, content: "SAP ERP receives" }) });

    expect(screen.getByText(/SAP ERP receives/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Send message" })).toBeDisabled();
  });

  it("shows tool-progress activity while a turn is in flight", () => {
    renderPanel({
      isSending: true,
      streamState: stream({ isStreaming: true }),
      activities: [
        {
          event_type: "tool.completed",
          label: "Reading the landscape",
          status: "running",
          safe_details: {},
        },
      ],
    });

    expect(screen.getByText("Reading the landscape")).toBeInTheDocument();
  });

  it("renders a completed answer with its clickable targets", () => {
    const onTargetClick = vi.fn();
    const onTargetHover = vi.fn();
    const target: CopilotTarget = {
      key: "integration:system:landscape-system:aaaa1111",
      label: "SAP ERP",
      detail: "System",
      resolvable: true,
    };
    renderPanel({
      messages: [assistantMessage("SAP ERP receives order data.")],
      targets: [target],
      onTargetClick,
      onTargetHover,
    });

    const chip = screen.getByRole("button", { name: /SAP ERP/ });
    fireEvent.mouseEnter(chip);
    expect(onTargetHover).toHaveBeenCalledWith(target);
    fireEvent.mouseLeave(chip);
    expect(onTargetHover).toHaveBeenLastCalledWith(null);

    fireEvent.click(chip);
    expect(onTargetClick).toHaveBeenCalledWith(target);
  });

  it("offers an explicit continue-in-chat action for an embedded thread", () => {
    const onContinueInChat = vi.fn();
    renderPanel({
      conversationId: 42,
      messages: [assistantMessage("A saved topology answer.")],
      onContinueInChat,
    });

    fireEvent.click(
      screen.getByRole("button", { name: "Continue this thread in main chat" }),
    );
    expect(onContinueInChat).toHaveBeenCalledOnce();
  });

  it("uses the message evidence envelope instead of duplicating legacy attribution", () => {
    renderPanel({
      messages: [assistantMessageWithEvidence("Documentation explains this flow.")],
      answerSource: {
        attribution: "other",
        label: "Answered from documents, not the diagram",
      },
    });

    expect(screen.getByLabelText("Answer evidence")).toHaveTextContent("Documentation · 2 via kimi");
    expect(screen.queryByText("Answered from documents, not the diagram")).not.toBeInTheDocument();
  });

  it("keeps legacy answer attribution when the message has no evidence envelope", () => {
    renderPanel({
      messages: [assistantMessage("A legacy answer.")],
      answerSource: {
        attribution: "surface",
        label: "Answered from this diagram",
      },
    });

    expect(screen.getByText("Answered from this diagram")).toBeInTheDocument();
    expect(screen.queryByLabelText("Answer evidence")).not.toBeInTheDocument();
  });

  it("renders an unresolvable target inert: no button, no click-through", () => {
    const onTargetClick = vi.fn();
    renderPanel({
      messages: [assistantMessage("There is also a CRM.")],
      targets: [
        {
          key: "integration:system:system:connection:9:crm",
          label: "CRM",
          detail: "System",
          resolvable: false,
          unresolvableReason: "This system is not in the diagram you have loaded.",
        },
      ],
      onTargetClick,
    });

    expect(screen.queryByRole("button", { name: /CRM/ })).not.toBeInTheDocument();
    const inert = screen.getByTitle("This system is not in the diagram you have loaded.");
    expect(inert).toHaveAttribute("aria-disabled", "true");

    fireEvent.click(inert);
    expect(onTargetClick).not.toHaveBeenCalled();
  });

  it("keeps provisional targets off the panel while the canvas highlights them", () => {
    renderPanel({
      streamState: stream({ isStreaming: true, content: "Looking" }),
      liveTargets: [{ key: "job::tJob_OrderSync", label: "tJob_OrderSync", resolvable: true }],
      targets: [{ key: "job::tJob_Other", label: "tJob_Other", resolvable: true }],
    });

    expect(screen.queryByRole("region", { name: "Found so far" })).not.toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "On this page" })).not.toBeInTheDocument();
  });

  it("surfaces a host-level error with a retry affordance", () => {
    const onRetry = vi.fn();
    renderPanel({ errorMessage: "The assistant could not be reached.", onRetry });

    expect(screen.getByRole("alert")).toHaveTextContent("The assistant could not be reached.");
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(onRetry).toHaveBeenCalledOnce();
  });

  it("names an unvalidated business overlay as scanner-derived", () => {
    renderPanel({ notices: [copilotNotice("stale-overlay")] });
    expect(screen.getByText("Names are scanner-derived")).toBeInTheDocument();
    expect(screen.getByText(/has not been validated/)).toBeInTheDocument();
  });

  it("asks for a rescan when the project has no published artifact", () => {
    renderPanel({ notices: [copilotNotice("missing-artifact")] });
    expect(screen.getByText(/needs a rescan/)).toBeInTheDocument();
  });

  it("says the service is unreachable and that the page itself is unaffected", () => {
    renderPanel({ notices: [copilotNotice("service-unreachable")] });
    expect(screen.getByText("I cannot reach the topology service")).toBeInTheDocument();
    expect(screen.getByText(/diagram on this page is unaffected/)).toBeInTheDocument();
  });

  it("allows the host to override notice copy per surface", () => {
    renderPanel({
      notices: [{ kind: "missing-artifact", title: "No documents indexed", detail: "Upload one." }],
    });
    expect(screen.getByText("No documents indexed")).toBeInTheDocument();
    expect(screen.queryByText(/needs a rescan/)).not.toBeInTheDocument();
  });

  it("collapses to a rail that can be expanded again", () => {
    const onCollapsedChange = vi.fn();
    const { unmount } = renderPanel({ onCollapsedChange });

    fireEvent.click(screen.getByRole("button", { name: "Collapse Assistant" }));
    expect(onCollapsedChange).toHaveBeenCalledWith(true);
    unmount();

    renderPanel({ collapsed: true, onCollapsedChange });
    const expand = screen.getByRole("button", { name: "Expand Assistant" });
    expect(expand).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByLabelText("Suggested prompts")).not.toBeInTheDocument();

    fireEvent.click(expand);
    expect(onCollapsedChange).toHaveBeenLastCalledWith(false);
  });

  it("sends a typed question through the host callback", () => {
    const onSend = vi.fn();
    renderPanel({ onSend });

    const input = screen.getByRole("textbox");
    fireEvent.change(input, { target: { value: "Which systems feed SAP?" } });
    fireEvent.click(screen.getByRole("button", { name: "Send message" }));

    expect(onSend).toHaveBeenCalledWith("Which systems feed SAP?");
  });
});
