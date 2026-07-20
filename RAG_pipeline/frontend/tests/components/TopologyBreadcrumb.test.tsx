import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { TopologyBreadcrumb } from "@/components/graph/TopologyBreadcrumb";
import type { TopologyLevel } from "@/hooks/useTopologyNavigation";

const STACK: TopologyLevel[] = [
  { kind: "project" },
  { kind: "job", jobName: "jobA" },
  { kind: "job", jobName: "jobB" },
];

describe("TopologyBreadcrumb", () => {
  it("renders the project label and every job crumb", () => {
    render(
      <TopologyBreadcrumb projectLabel="Demo Project" stack={STACK} onNavigate={vi.fn()} />,
    );
    expect(screen.getByText("Demo Project")).toBeInTheDocument();
    expect(screen.getByText("jobA")).toBeInTheDocument();
    expect(screen.getByText("jobB")).toBeInTheDocument();
  });

  it("marks the deepest level as the current page, not a button", () => {
    render(
      <TopologyBreadcrumb projectLabel="Demo Project" stack={STACK} onNavigate={vi.fn()} />,
    );
    const current = screen.getByText("jobB");
    expect(current).toHaveAttribute("aria-current", "page");
    expect(current.tagName).not.toBe("BUTTON");
    expect(screen.getByText("jobA").tagName).toBe("BUTTON");
  });

  it("navigates to the clicked crumb index (ascend)", () => {
    const onNavigate = vi.fn();
    render(
      <TopologyBreadcrumb projectLabel="Demo Project" stack={STACK} onNavigate={onNavigate} />,
    );
    fireEvent.click(screen.getByText("jobA"));
    expect(onNavigate).toHaveBeenCalledWith(1);
    fireEvent.click(screen.getByText("Demo Project"));
    expect(onNavigate).toHaveBeenCalledWith(0);
  });

  it("renders a component crumb as the deepest level", () => {
    const stackWithComponent: TopologyLevel[] = [
      { kind: "project" },
      { kind: "job", jobName: "jobA" },
      { kind: "component", jobName: "jobA", componentName: "tMap_1" },
    ];
    render(
      <TopologyBreadcrumb
        projectLabel="Demo Project"
        stack={stackWithComponent}
        onNavigate={vi.fn()}
      />,
    );
    const current = screen.getByText("tMap_1");
    expect(current).toHaveAttribute("aria-current", "page");
    expect(screen.getByText("jobA").tagName).toBe("BUTTON");
  });
});
