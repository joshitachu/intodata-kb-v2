import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { ComponentNode } from "@/components/graph/ComponentNode";
import { ROLE_COLORS, type ComponentNodeData } from "@/lib/jobGraphTransform";

// Handle requires a live React Flow store; the node's own markup is what
// we assert on, so stub the primitives out.
vi.mock("@xyflow/react", () => ({
  Handle: () => null,
  Position: { Left: "left", Right: "right", Top: "top", Bottom: "bottom" },
}));

function makeData(overrides: Partial<ComponentNodeData> = {}): ComponentNodeData {
  return {
    label: "tMap_1",
    technicalName: "tMap_1",
    componentType: "tMap",
    typeExplanation: "",
    role: "transform",
    color: ROLE_COLORS.transform,
    connectionRef: "",
    hasCode: false,
    hasSubgraph: false,
    subjobTarget: null,
    description: "",
    paramSummary: {},
    inputTableCount: 0,
    outputTableCount: 0,
    ...overrides,
  };
}

describe("ComponentNode", () => {
  it("renders primary emphasis more strongly than path and supporting roles", () => {
    const { container, rerender } = render(
      <ComponentNode data={makeData({ highlighted: true, assistantVisualRole: "primary" })} />,
    );
    const node = container.firstElementChild as HTMLElement;
    const primaryShadow = node.style.boxShadow;
    expect(node).toHaveAttribute("data-assistant-role", "primary");

    rerender(<ComponentNode data={makeData({ highlighted: true, assistantVisualRole: "path" })} />);
    const pathShadow = node.style.boxShadow;
    expect(node).toHaveAttribute("data-assistant-role", "path");
    expect(pathShadow).not.toBe(primaryShadow);

    rerender(<ComponentNode data={makeData({ highlighted: true, assistantVisualRole: "supporting" })} />);
    expect(node).toHaveAttribute("data-assistant-role", "supporting");
    expect(node.style.boxShadow).not.toBe(primaryShadow);
    expect(node.style.boxShadow).not.toBe(pathShadow);
  });

  it("renders the component name, type, and role badge", () => {
    render(<ComponentNode data={makeData()} />);
    expect(screen.getByText("tMap_1")).toBeInTheDocument();
    expect(screen.getByText("tMap")).toBeInTheDocument();
    expect(screen.getByText("XFORM")).toBeInTheDocument();
  });

  it("shows a connector badge when connection_ref is set", () => {
    render(<ComponentNode data={makeData({ connectionRef: "BigQuery_Prod" })} />);
    const badge = screen.getByTestId("connector-badge");
    expect(badge).toHaveTextContent("BigQuery_Prod");
  });

  it("hides the connector badge when connection_ref is empty", () => {
    render(<ComponentNode data={makeData()} />);
    expect(screen.queryByTestId("connector-badge")).not.toBeInTheDocument();
  });

  it("shows a has-code indicator only when has_code is true", () => {
    const { rerender } = render(<ComponentNode data={makeData({ hasCode: true })} />);
    expect(screen.getByTestId("has-code-indicator")).toBeInTheDocument();
    rerender(<ComponentNode data={makeData({ hasCode: false })} />);
    expect(screen.queryByTestId("has-code-indicator")).not.toBeInTheDocument();
  });

  it("marks drillable nodes (has_subgraph) with an indicator and hint", () => {
    render(<ComponentNode data={makeData({ hasSubgraph: true })} />);
    expect(screen.getByTestId("subgraph-indicator")).toBeInTheDocument();
    expect(screen.getByTitle("Double-click to open")).toBeInTheDocument();
  });

  it("renders a portal target for tRunJob nodes", () => {
    render(
      <ComponentNode
        data={makeData({
          label: "tRunJob_1",
          componentType: "tRunJob",
          role: "orchestration",
          color: ROLE_COLORS.orchestration,
          subjobTarget: "sjItem_Child",
        })}
      />,
    );
    const portal = screen.getByTestId("subjob-portal");
    expect(portal).toHaveTextContent("sjItem_Child");
    expect(screen.getByTitle("Double-click to open")).toBeInTheDocument();
  });

  it("shows the description line and uses it as the tooltip when present", () => {
    render(
      <ComponentNode
        data={makeData({ description: "Inserts rows into table Items in MSSQL" })}
      />,
    );
    const line = screen.getByTestId("component-description");
    expect(line).toHaveTextContent("Inserts rows into table Items in MSSQL");
    expect(
      screen.getByTitle("tMap_1: Inserts rows into table Items in MSSQL"),
    ).toBeInTheDocument();
  });

  it("hides the description line and keeps the id tooltip when absent", () => {
    render(<ComponentNode data={makeData()} />);
    expect(screen.queryByTestId("component-description")).not.toBeInTheDocument();
    // The raw id remains the tooltip (outer node + inner name span both carry it).
    expect(screen.getAllByTitle("tMap_1").length).toBeGreaterThan(0);
  });

  it("shows tMap table counts when present", () => {
    render(
      <ComponentNode
        data={makeData({ hasSubgraph: true, inputTableCount: 2, outputTableCount: 1 })}
      />,
    );
    expect(screen.getByTestId("tmap-tables-badge")).toHaveTextContent("2→1");
  });
});
