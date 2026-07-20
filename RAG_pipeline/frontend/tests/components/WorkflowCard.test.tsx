import { act, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { WorkflowCard } from "@/components/WorkflowCard";
import type { WorkflowCard as WorkflowCardValue } from "@/types";
import { WorkflowCardSchema } from "@/types";

function card(overrides: Partial<WorkflowCardValue> = {}): WorkflowCardValue {
  return {
    workflow_run_id: 42,
    project_id: "intodatastreams",
    capability_id: "mcp.docs.generate_project_documentation",
    label: "Documentation generation",
    status: "in_progress",
    display_label: "Provider still working",
    display_tone: "progress",
    is_active: true,
    summary: null,
    error_message: null,
    started_at: "2026-07-19T08:59:45.000Z",
    finished_at: null,
    projected_at: "2026-07-19T09:00:00.000Z",
    elapsed_ms: 15_000,
    timing_complete: true,
    children: [],
    artifact_links: [],
    ...overrides,
  };
}

describe("WorkflowCard", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it("uses the server display contract and advances elapsed time only while active", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-07-19T09:00:00.000Z"));
    render(<WorkflowCard card={card()} />);

    expect(screen.getByText("Provider still working")).toBeInTheDocument();
    expect(screen.getByText("15s")).toBeInTheDocument();

    act(() => {
      vi.advanceTimersByTime(2_000);
    });
    expect(screen.getByText("17s")).toBeInTheDocument();
  });

  it("keeps terminal elapsed time frozen and shows incomplete timing explicitly", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-07-19T09:00:00.000Z"));
    const { rerender } = render(
      <WorkflowCard
        card={card({
          status: "failed",
          display_label: "Failed upstream",
          display_tone: "danger",
          is_active: false,
          elapsed_ms: 12_000,
          finished_at: "2026-07-19T08:59:57.000Z",
        })}
      />,
    );

    expect(screen.getByText("Failed upstream")).toBeInTheDocument();
    expect(screen.getByText("12s")).toBeInTheDocument();
    act(() => {
      vi.advanceTimersByTime(10_000);
    });
    expect(screen.getByText("12s")).toBeInTheDocument();

    rerender(<WorkflowCard card={card({ is_active: false, timing_complete: false })} />);
    expect(screen.getByText("Duration unavailable")).toBeInTheDocument();
  });

  it("renders only the finite server contract for tone, children, errors, and artifacts", () => {
    render(
      <WorkflowCard
        card={card({
          status: "failed",
          display_label: "Rejected by policy",
          display_tone: "danger",
          is_active: false,
          error_message: "The scanner rejected the request.",
          children: [{ kind: "Security scan", status: "failed", error_message: "Gate failed." }],
          artifact_links: [{ label: "Safe report", url: "/projects/intodatastreams/docs", kind: "report" }],
        })}
      />,
    );

    expect(screen.getByText("Rejected by policy")).toBeInTheDocument();
    expect(screen.getByText("The scanner rejected the request.")).toBeInTheDocument();
    expect(screen.getByText("Security scan: failed — Gate failed.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Safe report" })).toHaveAttribute(
      "href",
      "/projects/intodatastreams/docs",
    );
  });

  it("rejects unknown workflow states instead of inventing a frontend fallback", () => {
    expect(WorkflowCardSchema.safeParse({ ...card(), status: "still_working_probably" }).success).toBe(false);
    expect(WorkflowCardSchema.safeParse({ ...card(), display_tone: "blue" }).success).toBe(false);
  });
});
