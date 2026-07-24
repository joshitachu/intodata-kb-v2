import { describe, it, expect } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { GraphLegend } from "@/components/graph/GraphLegend";

const GROUP_COLOR_MAP = {
  GroupA: { bg: "#dbeafe", border: "#3b82f6", text: "#1e40af" },
};

describe("GraphLegend", () => {
  it("renders expanded by default with a close control", () => {
    render(<GraphLegend groupColorMap={GROUP_COLOR_MAP} />);
    expect(screen.getByText("Legend")).toBeInTheDocument();
    expect(screen.getByText("Component orchestration")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Hide legend" })).toBeInTheDocument();
  });

  it("collapses to a small reopen chip and never blocks the canvas underneath", () => {
    render(<GraphLegend groupColorMap={GROUP_COLOR_MAP} />);
    fireEvent.click(screen.getByRole("button", { name: "Hide legend" }));

    expect(screen.queryByText("Component orchestration")).not.toBeInTheDocument();
    const reopen = screen.getByRole("button", { name: "Show legend" });
    expect(reopen).toBeInTheDocument();
  });

  it("re-expands from the collapsed chip", () => {
    render(<GraphLegend groupColorMap={GROUP_COLOR_MAP} />);
    fireEvent.click(screen.getByRole("button", { name: "Hide legend" }));
    fireEvent.click(screen.getByRole("button", { name: "Show legend" }));

    expect(screen.getByText("Component orchestration")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Hide legend" })).toBeInTheDocument();
  });

  it("explains Talend Jobs, Routes, and their distinct invocation edge", () => {
    render(
      <GraphLegend
        groupColorMap={GROUP_COLOR_MAP}
        showArtifactTaxonomy
      />,
    );

    expect(screen.getByText("Talend Job")).toBeInTheDocument();
    expect(screen.getByText("Talend Route")).toBeInTheDocument();
    expect(screen.getByText("Job calls Job")).toBeInTheDocument();
    expect(screen.getByText("Route invokes Job")).toBeInTheDocument();
    expect(screen.queryByText("Component orchestration")).not.toBeInTheDocument();
  });
});
