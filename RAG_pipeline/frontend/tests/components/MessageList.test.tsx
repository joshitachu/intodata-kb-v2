import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { MessageList } from "@/components/MessageList";
import type { StreamState } from "@/hooks/useStreamMessage";

function failedStream(error = "Provider timed out"): StreamState {
  return {
    isStreaming: false,
    content: "",
    error,
    messageId: null,
    latencyMs: null,
    typedResponse: null,
  };
}

function streamingStream(overrides: Partial<StreamState> = {}): StreamState {
  return {
    isStreaming: true,
    content: "",
    error: null,
    messageId: null,
    latencyMs: null,
    typedResponse: null,
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

  it("keeps the completed stream visible while its persisted twin is handed off", () => {
    render(
      <MessageList
        messages={[
          {
            id: 21,
            role: "assistant",
            content: "Persisted twin",
            sources: null,
            latency_ms: 20,
            status: "completed",
            created_at: new Date().toISOString(),
          },
        ]}
        streamingMessage={streamingStream({
          isStreaming: false,
          content: "Completed live answer",
          messageId: 21,
        })}
      />,
    );

    expect(screen.getByText("Completed live answer")).toBeInTheDocument();
    expect(screen.queryByText("Persisted twin")).not.toBeInTheDocument();
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
              required_fields: [],
            },
          },
        ]}
      />,
    );

    expect(screen.getByText("Project scope required")).toBeInTheDocument();
    expect(screen.getByText("Which project should I use?")).toBeInTheDocument();
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

  it("renders typed response content and fresh authoritative cards over stored stale cards", () => {
    const staleCard = {
      workflow_run_id: 42,
      project_id: "intodatastreams",
      capability_id: "mcp.docs.generate_project_documentation",
      label: "Documentation generation",
      status: "in_progress" as const,
      display_label: "Stale in progress",
      display_tone: "progress" as const,
      is_active: true,
      summary: null,
      error_message: null,
      started_at: "2026-07-19T08:00:00Z",
      finished_at: null,
      projected_at: "2026-07-19T08:01:00Z",
      elapsed_ms: 60_000,
      timing_complete: true,
      children: [],
      artifact_links: [],
    };
    const freshCard = {
      ...staleCard,
      status: "failed" as const,
      display_label: "Already failed",
      display_tone: "danger" as const,
      is_active: false,
      error_message: "Graph workflow failed.",
      finished_at: "2026-07-19T08:10:00Z",
      elapsed_ms: 600_000,
    };
    render(
      <MessageList
        messages={[{
          id: 12,
          role: "assistant",
          content: "The workflow failed before cancellation could complete.",
          sources: null,
          latency_ms: 10,
          status: "completed",
          structured_answer: {
            response_type: "agent_response_v1",
            kind: "status",
            content: "The workflow failed before cancellation could complete.",
            synthesis_source: "agent_plan",
            facts: [],
            artifact_links: [],
            evidence_refs: [],
            workflow_cards: [staleCard],
            stop_reason: "final_answer",
            factual_fingerprint: "a".repeat(64),
          },
          workflow_cards: [freshCard],
          created_at: new Date().toISOString(),
        }]}
      />,
    );

    expect(screen.getByText("The workflow failed before cancellation could complete.")).toBeInTheDocument();
    expect(screen.getByText("Already failed")).toBeInTheDocument();
    expect(screen.getByText("Graph workflow failed.")).toBeInTheDocument();
    expect(screen.queryByText("Stale in progress")).not.toBeInTheDocument();
  });

  it("renders the server-issued Pack artifact link from a typed response", () => {
    render(
      <MessageList
        messages={[{
          id: 13,
          role: "assistant",
          content: "The Pack draft is ready.",
          sources: null,
          latency_ms: 9,
          status: "completed",
          structured_answer: {
            response_type: "agent_response_v1",
            kind: "answer",
            content: "The Pack draft is ready.",
            synthesis_source: "agent_plan",
            facts: [],
            artifact_links: [{
              kind: "pack_draft",
              label: "Open Pack draft",
              url: "/pack?draft=73",
            }],
            evidence_refs: [],
            workflow_cards: [],
            stop_reason: "answered",
            factual_fingerprint: "b".repeat(64),
          },
          created_at: new Date().toISOString(),
        }]}
      />,
    );

    expect(screen.getByRole("link", { name: /Open Pack draft/i })).toHaveAttribute(
      "href",
      "/pack?draft=73",
    );
  });
});
