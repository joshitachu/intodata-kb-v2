import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import {
  RuntimeToolStatus,
  runtimeToolLabel,
} from "@/components/RuntimeToolStatus";

describe("runtimeToolLabel", () => {
  it("maps known generic tool names to readable labels", () => {
    expect(runtimeToolLabel("detect_failures")).toBe("Detect failures");
    expect(runtimeToolLabel("get_execution_details")).toBe(
      "Get execution details",
    );
  });

  it("prettifies unknown tool names without leaking the raw token", () => {
    expect(runtimeToolLabel("fetch_runtime_metrics")).toBe(
      "Fetch Runtime Metrics",
    );
  });
});

describe("RuntimeToolStatus", () => {
  it("renders nothing when there are no runtime tools", () => {
    const { container } = render(<RuntimeToolStatus tools={[]} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("renders one status row per runtime tool", () => {
    render(
      <RuntimeToolStatus
        tools={[
          {
            toolName: "detect_failures",
            status: "running",
            latencyMs: null,
            truncated: false,
            error: null,
          },
          {
            toolName: "list_executions",
            status: "completed",
            latencyMs: 12,
            truncated: false,
            error: null,
          },
        ]}
      />,
    );

    expect(
      screen.getByText("Checking runtime: Detect failures"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Runtime check complete: List executions"),
    ).toBeInTheDocument();
  });
});
