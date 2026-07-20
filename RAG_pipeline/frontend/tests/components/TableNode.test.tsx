import { describe, it, expect, vi } from "vitest";
import { createElement } from "react";
import { render, screen, fireEvent } from "@testing-library/react";
import { TableNode } from "@/components/graph/TableNode";
import type { TableNodeData } from "@/lib/componentGraphTransform";

// Handle requires a live React Flow store; the node's own markup is what
// we assert on, so stub the primitives out (mirrors ComponentNode.test.tsx),
// while still rendering a discoverable marker per handle so tests can
// assert a target+source pair exists per column row.
vi.mock("@xyflow/react", () => ({
  Handle: (props: { id: string; type: string }) =>
    createElement("div", { "data-testid": `handle-${props.type}-${props.id}` }),
  Position: { Left: "left", Right: "right", Top: "top", Bottom: "bottom" },
}));

function makeData(overrides: Partial<TableNodeData> = {}): TableNodeData {
  return {
    tableName: "row17",
    kind: "input",
    sourceComponent: null,
    columns: [],
    ...overrides,
  };
}

describe("TableNode", () => {
  it("renders the table name and kind badge", () => {
    render(<TableNode data={makeData()} />);
    expect(screen.getByText("row17")).toBeInTheDocument();
    expect(screen.getByText("IN")).toBeInTheDocument();
  });

  it("shows the upstream source component as a title tooltip", () => {
    render(<TableNode data={makeData({ sourceComponent: "tFixedFlowInput_1" })} />);
    expect(screen.getByTitle("From tFixedFlowInput_1")).toBeInTheDocument();
  });

  it("renders one row per column with both a target and source handle", () => {
    render(
      <TableNode
        data={makeData({
          columns: [
            { name: "message", expression: null },
            { name: "id", expression: null },
          ],
        })}
      />,
    );
    expect(screen.getByTestId("column-row-message")).toBeInTheDocument();
    expect(screen.getByTestId("column-row-id")).toBeInTheDocument();
    expect(screen.getByTestId("handle-target-target:message")).toBeInTheDocument();
    expect(screen.getByTestId("handle-source-source:message")).toBeInTheDocument();
  });

  it("shows the raw expression as the row's hover title when present", () => {
    render(
      <TableNode
        data={makeData({
          kind: "output",
          columns: [{ name: "logMessage", expression: "Var.Message" }],
        })}
      />,
    );
    expect(screen.getByTitle("Var.Message")).toBeInTheDocument();
  });

  it("falls back to the column name as the title when there is no expression", () => {
    render(
      <TableNode data={makeData({ columns: [{ name: "message", expression: null }] })} />,
    );
    expect(screen.getByTitle("message")).toBeInTheDocument();
  });

  it("shows an empty-state row when a table has no columns", () => {
    render(<TableNode data={makeData({ columns: [] })} />);
    expect(screen.getByTestId("table-node-empty")).toHaveTextContent(
      "No columns captured",
    );
  });

  it("exposes a table-level source handle for input and Var tables", () => {
    const { rerender } = render(<TableNode data={makeData({ kind: "input" })} />);
    expect(screen.getByTestId("handle-source-table-source")).toBeInTheDocument();
    rerender(<TableNode data={makeData({ kind: "var" })} />);
    expect(screen.getByTestId("handle-source-table-source")).toBeInTheDocument();
  });

  it("does not expose a table-level source handle for output tables (terminal)", () => {
    render(<TableNode data={makeData({ kind: "output" })} />);
    expect(screen.queryByTestId("handle-source-table-source")).not.toBeInTheDocument();
  });
});

describe("TableNode - S5 field lineage overlay", () => {
  it("is not clickable when no onColumnClick handler is wired", () => {
    render(
      <TableNode data={makeData({ columns: [{ name: "message", expression: null }] })} />,
    );
    const row = screen.getByTestId("column-row-message");
    expect(row).not.toHaveAttribute("role");
  });

  it("invokes onColumnClick with the column name on click", () => {
    const onColumnClick = vi.fn();
    render(
      <TableNode
        data={makeData({
          columns: [{ name: "message", expression: null }],
          onColumnClick,
        })}
      />,
    );
    fireEvent.click(screen.getByTestId("column-row-message"));
    expect(onColumnClick).toHaveBeenCalledWith("message");
  });

  it("invokes onColumnClick on Enter/Space for keyboard accessibility", () => {
    const onColumnClick = vi.fn();
    render(
      <TableNode
        data={makeData({
          columns: [{ name: "message", expression: null }],
          onColumnClick,
        })}
      />,
    );
    const row = screen.getByTestId("column-row-message");
    fireEvent.keyDown(row, { key: "Enter" });
    fireEvent.keyDown(row, { key: " " });
    expect(onColumnClick).toHaveBeenCalledTimes(2);
  });

  it("marks the clicked (active) column distinctly from the rest of the path", () => {
    render(
      <TableNode
        data={makeData({
          columns: [
            { name: "message", expression: null },
            { name: "id", expression: null },
          ],
          lineageActiveColumn: "message",
          lineageTouchedColumns: new Set(["message", "id"]),
        })}
      />,
    );
    expect(screen.getByTestId("column-row-message")).toHaveAttribute("data-lineage-active", "true");
    expect(screen.getByTestId("column-row-id")).not.toHaveAttribute("data-lineage-active");
    // Both are on the path, so both get the "touched" marker dot.
    expect(screen.getByTestId("lineage-dot-message")).toBeInTheDocument();
    expect(screen.getByTestId("lineage-dot-id")).toBeInTheDocument();
  });

  it("shows a confidence badge on a column fed only by an unresolved (dynamic/opaque) hop", () => {
    render(
      <TableNode
        data={makeData({
          kind: "output",
          columns: [{ name: "logId", expression: "((String)globalMap.get(x))" }],
          lineageUnresolvedColumns: new Map([["logId", "dynamic"]]),
        })}
      />,
    );
    const badge = screen.getByTestId("lineage-badge-logId");
    expect(badge).toHaveTextContent("dynamic");
  });

  it("never shows a confidence badge for a column with no lineage selection active", () => {
    render(
      <TableNode data={makeData({ columns: [{ name: "message", expression: null }] })} />,
    );
    expect(screen.queryByTestId("lineage-badge-message")).not.toBeInTheDocument();
    expect(screen.queryByTestId("lineage-dot-message")).not.toBeInTheDocument();
  });
});
