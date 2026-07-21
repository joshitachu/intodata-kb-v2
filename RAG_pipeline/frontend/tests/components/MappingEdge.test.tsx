import { describe, it, expect, vi } from "vitest";
import { render } from "@testing-library/react";
import { Position } from "@xyflow/react";
import { MappingEdge } from "@/components/graph/MappingEdge";

vi.mock("@xyflow/react", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@xyflow/react")>();
  return {
    ...actual,
    getBezierPath: () => ["M0,0 L100,100"],
  };
});

const BASE_PROPS = {
  id: "map:row17.message->out1.logMessage",
  source: "table:row17",
  target: "table:out1",
  sourceX: 0,
  sourceY: 0,
  targetX: 100,
  targetY: 100,
  sourcePosition: Position.Right,
  targetPosition: Position.Left,
};

describe("MappingEdge", () => {
  it("renders the raw expression as a native title (hover tooltip)", () => {
    // The <title> is nested inside <path> (a valid, browser-tooltip-on-hover
    // SVG accessible-name pattern) rather than a direct `svg > title` child,
    // so we assert via querySelector - testing-library's getByTitle only
    // recognizes the latter form.
    const { container } = render(
      <svg>
        <MappingEdge {...BASE_PROPS} data={{ expression: "row17.message.trim()" }} />
      </svg>,
    );
    expect(container.querySelector("title")?.textContent).toBe(
      "row17.message.trim()",
    );
  });

  it("renders an empty title when no expression is present", () => {
    const { container } = render(
      <svg>
        <MappingEdge {...BASE_PROPS} data={{}} />
      </svg>,
    );
    const title = container.querySelector("title");
    expect(title).not.toBeNull();
    expect(title?.textContent).toBe("");
  });

  it("draws the path with the given id and style", () => {
    const { container } = render(
      <svg>
        <MappingEdge
          {...BASE_PROPS}
          data={{ expression: "x" }}
          style={{ stroke: "#3b82f6" }}
        />
      </svg>,
    );
    const path = container.querySelector("path");
    expect(path).not.toBeNull();
    expect(path?.getAttribute("id")).toBe(BASE_PROPS.id);
    expect(path).toHaveStyle({ stroke: "#3b82f6" });
  });
});
