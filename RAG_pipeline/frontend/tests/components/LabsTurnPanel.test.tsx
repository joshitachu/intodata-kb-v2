import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { LabsTurnPanel } from "@/components/labs/LabsTurnPanel";
import type { LabsTurnRecord } from "@/components/labs/labsTurn";

/**
 * The rendered shape of a turn, which is the whole point of the panel:
 *
 *   plan card      collapsed, a discrete element, not prose
 *   status line    ONE line, replaced as work proceeds
 *   reasoning      collapsed
 *   tool activity  collapsed
 *
 * It matters that the first two are not expanded by default and that the third
 * and fourth never render their content unasked: the failure this replaced was
 * the agent pouring all four channels into the answer body as thirteen
 * paragraphs of narration.
 */
function record(overrides: Partial<LabsTurnRecord> = {}): LabsTurnRecord {
  return {
    runId: "run-fixture",
    status: "streaming",
    reasoning: "",
    plan: [],
    statusLine: null,
    activities: [],
    usage: [],
    latencyMs: null,
    stopReason: null,
    error: null,
    ...overrides,
  };
}

const PLAN: LabsTurnRecord["plan"] = [
  { id: "1", content: "Find the jobs in this chain", status: "completed" },
  { id: "2", content: "Read the collector's components", status: "in_progress" },
  { id: "3", content: "Check the runtime records", status: "pending" },
];

describe("LabsTurnPanel", () => {
  it("collapses the plan and reports how far through it the turn is", () => {
    render(
      <LabsTurnPanel
        record={record({ plan: PLAN, statusLine: "Read the collector's components" })}
        onStop={vi.fn()}
      />,
    );

    const toggle = screen.getByRole("button", { name: /plan/i });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(toggle).toHaveTextContent("1/3");
    // Collapsed means collapsed: no step is on screen until it is opened.
    expect(screen.queryByText("Check the runtime records")).not.toBeInTheDocument();

    fireEvent.click(toggle);

    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText("Check the runtime records")).toBeInTheDocument();
  });

  it("shows exactly one status line while the turn runs", () => {
    render(
      <LabsTurnPanel
        record={record({ plan: PLAN, statusLine: "Read the collector's components" })}
        onStop={vi.fn()}
      />,
    );

    // One occurrence: the status line. The identical step text inside the plan
    // card is not rendered, because the card is collapsed -- so the line does
    // not read as a second copy of the plan.
    expect(screen.getAllByText("Read the collector's components")).toHaveLength(1);
  });

  it("drops the status line when the lane reports none", () => {
    render(
      <LabsTurnPanel
        record={record({ status: "completed", plan: PLAN, statusLine: null })}
        onStop={vi.fn()}
      />,
    );

    expect(screen.queryByText("Read the collector's components")).not.toBeInTheDocument();
  });

  it("names both counts instead of showing two that disagree", () => {
    render(
      <LabsTurnPanel
        record={record({
          status: "completed",
          activities: [
            {
              id: "e1",
              label: "nexus_read_topology_structure",
              detail: "Read 3 components",
              status: "succeeded",
              evidenceCount: 3,
              at: "2026-09-17T10:00:00Z",
            },
            {
              id: "e2",
              label: "todowrite",
              detail: null,
              status: "succeeded",
              evidenceCount: null,
              at: "2026-09-17T10:00:01Z",
            },
          ],
          usage: [{ key: "capability_calls", label: "Capability calls", value: "1" }],
        })}
        onStop={vi.fn()}
      />,
    );

    // The turn made two tool calls, one of which was a Nexus capability. Both
    // numbers are true and both are labelled; the header used to carry them
    // unlabelled and contradictory ("81 tool calls" beside "Tool calls 40").
    expect(screen.getByText("2 tool calls")).toBeInTheDocument();
    expect(screen.getByText("Capability calls")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /tool activity \(2\)/i })).toHaveAttribute(
      "aria-expanded",
      "false",
    );
  });

  it("renders no plan card at all when the lane published no plan", () => {
    render(<LabsTurnPanel record={record()} onStop={vi.fn()} />);

    expect(screen.queryByRole("button", { name: /plan/i })).not.toBeInTheDocument();
  });

  it("keeps reasoning collapsed and out of the answer", () => {
    render(
      <LabsTurnPanel
        record={record({ status: "completed", reasoning: "I should check the router first." })}
        onStop={vi.fn()}
      />,
    );

    const toggle = screen.getByRole("button", { name: /reasoning/i });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByText(/I should check the router first/)).not.toBeInTheDocument();

    fireEvent.click(toggle);
    expect(screen.getByText(/I should check the router first/)).toBeInTheDocument();
  });
});
