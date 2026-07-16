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
    pendingAction: null,
    runtimeTools: [],
    structuredAnswer: null,
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
    pendingAction: null,
    runtimeTools: [],
    structuredAnswer: null,
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

  it("rehydrates persisted harness activity and pending action after reload", () => {
    render(
      <MessageList
        conversationId={48}
        messages={[
          {
            id: 9,
            role: "assistant",
            content: "Choose a project.",
            sources: null,
            latency_ms: 12,
            status: "completed",
            created_at: new Date().toISOString(),
            harness_activities: [
              {
                workflow_run_id: "42",
                event_type: "clarification.requested",
                label: "Project scope required",
                status: "running",
                summary: "Choose one project.",
                safe_details: {},
              },
            ],
            pending_action: {
              workflow_run_id: "42",
              interrupt_id: 8,
              expected_version: 2,
              kind: "clarification",
              prompt: "Which project should I use?",
              choices: [{ label: "Project A", value: "project-a" }],
            },
          },
        ]}
      />,
    );

    expect(screen.getByText("Project scope required")).toBeInTheDocument();
    expect(screen.getByText("Which project should I use?")).toBeInTheDocument();
  });

  it("rehydrates structured answers and keeps full evidence behind the source drawer", () => {
    render(
      <MessageList
        messages={[
          {
            id: 10,
            role: "assistant",
            content: "",
            sources: [
              {
                document_id: "doc-1",
                filename: "01_ARCHITECTURE_MAP.md",
                display_name: "Architecture map",
                text: `${"Documented architecture context. ".repeat(10)}FULL EVIDENCE EXCERPT TAIL: AssetCheck reads Maximo and writes SQL.`,
                score: 0.91,
                reference_number: 1,
                chunk_indexes: [4],
                document_role: "source",
              },
            ],
            latency_ms: 42,
            status: "failed",
            error_message: "Legacy message status should not hide a typed answer",
            structured_answer: {
              output_contract: "inventory",
              headline: "Documented jobs",
              summary: "One job was documented.",
              sections: [{ heading: "Jobs", rows: [{ job: "AssetCheck" }] }],
              limitations: ["Only indexed documentation was used."],
              citations: [
                {
                  evidence_id: "doc-1",
                  label: "Architecture map",
                  source_class: "indexed_documentation",
                },
              ],
              evidence_refs: ["doc-1"],
              answer_status: "insufficient",
              execution_status: "completed",
              domain_status: "unknown",
              artifacts: [],
            },
            created_at: new Date().toISOString(),
          },
        ]}
      />,
    );

    expect(screen.getByRole("heading", { name: "Documented jobs" })).toBeInTheDocument();
    expect(screen.queryByText(/Legacy message status/)).not.toBeInTheDocument();
    expect(screen.queryByText(/FULL EVIDENCE EXCERPT TAIL/)).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Show 1 source" }));
    expect(screen.getByText("Architecture map")).toBeInTheDocument();
    expect(screen.queryByText(/FULL EVIDENCE EXCERPT TAIL/)).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /Architecture map/i }));
    expect(screen.getByText(/FULL EVIDENCE EXCERPT TAIL/)).toBeInTheDocument();
  });

  it("keeps legacy plain-text assistant messages compatible", () => {
    render(
      <MessageList
        messages={[
          {
            id: 11,
            role: "assistant",
            content: "## Legacy answer\n\nStill readable.",
            sources: null,
            latency_ms: 8,
            status: "completed",
            created_at: new Date().toISOString(),
          },
        ]}
      />,
    );

    expect(screen.getByRole("heading", { name: "Legacy answer" })).toBeInTheDocument();
    expect(screen.getByText("Still readable.")).toBeInTheDocument();
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
      screen.getAllByText("Starting documentation generation").length,
    ).toBeGreaterThanOrEqual(1);
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
