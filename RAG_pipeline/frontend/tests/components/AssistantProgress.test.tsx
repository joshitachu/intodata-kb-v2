import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { AssistantProgress } from "@/components/copilot/AssistantProgress";
import type { StreamProgress } from "@/lib/agentProgress";
import type { PublicAgentEvent } from "@/types";

/**
 * The server's contract: `{status, phase, summary, elapsed_ms}`, `summary`
 * always present and already carrying its own elapsed suffix, and `phase` an
 * OPEN set that grows as capabilities gain source labels. These tests hold the
 * panel to that -- summary is the user-facing text, and an unrecognised phase
 * must render exactly as well as a known one.
 */
function progress(overrides: Partial<StreamProgress> = {}): StreamProgress {
  return {
    status: "running",
    phase: "retrieving_evidence",
    summary: "Reading the Werkorder landscape... 4s",
    elapsedMs: 4200,
    ...overrides,
  };
}

function toolEvent(type: string, payload: Record<string, unknown>): PublicAgentEvent {
  return {
    event_id: `run-1:evt:${type}`,
    run_id: "run-1",
    sequence: 2,
    type,
    created_at: "2026-08-21T10:00:00Z",
    public_payload: payload,
    replayable: true,
  };
}

describe("AssistantProgress", () => {
  it("shows the server's summary as the live line", () => {
    render(<AssistantProgress progress={progress()} fallbackLabel="Searching..." />);

    const panel = screen.getByRole("status", { name: "Assistant progress" });
    expect(panel).toHaveTextContent("Reading the Werkorder landscape... 4s");
    // The phase is a grouping key, not copy, and the summary already carries
    // the elapsed time -- so neither is printed a second time.
    expect(panel.textContent).not.toContain("Retrieving evidence");
    expect(panel.textContent?.match(/4s/g)).toHaveLength(1);
    expect(panel).not.toHaveTextContent("Searching...");
  });

  it("updates as later heartbeats arrive", () => {
    const { rerender } = render(
      <AssistantProgress progress={progress()} fallbackLabel="Searching..." />,
    );
    expect(screen.getByRole("status")).toHaveTextContent("Reading the Werkorder landscape");

    rerender(
      <AssistantProgress
        progress={progress({
          phase: "thinking",
          summary: "Waiting on the model... 1m 11s",
          elapsedMs: 71_000,
        })}
        fallbackLabel="Searching..."
      />,
    );

    const panel = screen.getByRole("status");
    expect(panel).toHaveTextContent("Waiting on the model... 1m 11s");
    expect(panel.textContent).not.toContain("Reading the Werkorder landscape");
  });

  it("renders a phase slug it has never seen exactly as well as a known one", () => {
    render(
      <AssistantProgress
        progress={progress({
          phase: "reading_component_graph_project_topology",
          summary: "Reading the component graph... 9s",
        })}
        fallbackLabel="Searching..."
      />,
    );

    expect(screen.getByRole("status")).toHaveTextContent("Reading the component graph... 9s");
  });

  it("degrades to the humanised phase when a payload carries no summary", () => {
    render(
      <AssistantProgress
        progress={progress({ summary: null })}
        fallbackLabel="Searching..."
      />,
    );

    const panel = screen.getByRole("status");
    expect(panel).toHaveTextContent("Retrieving evidence");
    // Without a summary to carry it, the elapsed time is shown on its own.
    expect(panel).toHaveTextContent("4s");
  });

  it("falls back to status, then to the caller's label, when less arrives", () => {
    const { rerender } = render(
      <AssistantProgress
        progress={progress({ phase: null, summary: null, status: "waiting_on_tool" })}
        fallbackLabel="Searching..."
      />,
    );
    expect(screen.getByRole("status")).toHaveTextContent("Waiting on tool");

    // No heartbeat at all yet: the existing spinner label is all there is.
    rerender(<AssistantProgress progress={null} fallbackLabel="Searching..." />);
    expect(screen.getByRole("status")).toHaveTextContent("Searching...");
  });

  it("summarises the latest tool event alongside the phase", () => {
    render(
      <AssistantProgress
        progress={progress()}
        events={[
          toolEvent("tool.started", { summary: "Reading the diagram" }),
          toolEvent("tool.completed", { summary: "Found 3 jobs" }),
        ]}
        fallbackLabel="Searching..."
      />,
    );

    const panel = screen.getByRole("status");
    expect(panel).toHaveTextContent("Found 3 jobs");
    expect(panel.textContent).not.toContain("Reading the diagram");
  });

  it("names a tool from its event type when the payload has no summary", () => {
    render(
      <AssistantProgress
        progress={null}
        events={[toolEvent("tool.started", { tool: "search_topology" })]}
        fallbackLabel="Searching..."
      />,
    );

    expect(screen.getByRole("status")).toHaveTextContent("search_topology");
  });

  it("drops a tool line that only repeats the live one", () => {
    render(
      <AssistantProgress
        progress={progress({ summary: "Found 3 jobs" })}
        events={[toolEvent("tool.completed", { summary: "Found 3 jobs" })]}
        fallbackLabel="Searching..."
      />,
    );

    expect(screen.getByRole("status").textContent?.match(/Found 3 jobs/g)).toHaveLength(1);
  });

  it("shows no invented progress: no percentage, no bar", () => {
    render(<AssistantProgress progress={progress()} fallbackLabel="Searching..." />);

    expect(screen.getByRole("status").textContent).not.toMatch(/%/);
    expect(screen.queryByRole("progressbar")).toBeNull();
  });
});
