import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { LineagePathBanner } from "@/components/graph/LineagePathBanner";
import { LineageFieldResponseSchema, type LineageFieldResponse } from "@/types/lineage";

function fixture(overrides: Record<string, unknown> = {}): LineageFieldResponse {
  return LineageFieldResponseSchema.parse({
    project_id: "proj1",
    job_name: "jobA",
    start: { job_name: "jobA", table: "row17", column: "message", component: "tMap_1" },
    direction: "both",
    max_depth: 10,
    nodes: [],
    edges: [
      { job_name: "jobA", target_table: "out1", target_column: "logMessage", confidence: "certain", resolved: true },
      { job_name: "jobA", target_table: "out1", target_column: "logId", confidence: "dynamic", resolved: false, dynamic_refs: ["globalMap.get(x)"] },
    ],
    truncated: false,
    ...overrides,
  });
}

describe("LineagePathBanner", () => {
  it("renders nothing when there is no active trace", () => {
    const { container } = render(
      <LineagePathBanner response={null} isLoading={false} error={null} onClear={vi.fn()} />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("shows a loading state while the trace is in flight", () => {
    render(<LineagePathBanner response={null} isLoading={true} error={null} onClear={vi.fn()} />);
    expect(screen.getByText(/Tracing field lineage/i)).toBeInTheDocument();
  });

  it("shows the error message on failure", () => {
    render(
      <LineagePathBanner
        response={null}
        isLoading={false}
        error={new Error("No lineage data for project 'proj1'")}
        onClear={vi.fn()}
      />,
    );
    expect(screen.getByTestId("lineage-banner-error")).toHaveTextContent(
      "No lineage data for project 'proj1'",
    );
  });

  it("summarizes the start field, hop count, and confidence tiers", () => {
    render(
      <LineagePathBanner response={fixture()} isLoading={false} error={null} onClear={vi.fn()} />,
    );
    expect(screen.getByText("row17.message")).toBeInTheDocument();
    expect(screen.getByText("2 hops")).toBeInTheDocument();
    expect(screen.getByTestId("lineage-confidence-badge-certain")).toBeInTheDocument();
    expect(screen.getByTestId("lineage-confidence-badge-dynamic")).toBeInTheDocument();
  });

  it("calls out unresolved dynamic references so they are never presented as certain", () => {
    render(
      <LineagePathBanner response={fixture()} isLoading={false} error={null} onClear={vi.fn()} />,
    );
    expect(screen.getByTestId("lineage-dynamic-refs")).toHaveTextContent("1 unresolved dynamic ref");
  });

  it("flags a truncated BFS instead of silently showing a partial path as complete", () => {
    render(
      <LineagePathBanner
        response={fixture({ truncated: true })}
        isLoading={false}
        error={null}
        onClear={vi.fn()}
      />,
    );
    expect(screen.getByText(/truncated at max depth/i)).toBeInTheDocument();
  });

  it("calls onClear when the close button is clicked", () => {
    const onClear = vi.fn();
    render(
      <LineagePathBanner response={fixture()} isLoading={false} error={null} onClear={onClear} />,
    );
    screen.getByTestId("lineage-banner-close").click();
    expect(onClear).toHaveBeenCalledTimes(1);
  });
});
