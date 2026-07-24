import { render, screen } from "@testing-library/react";
import { ReactFlowProvider } from "@xyflow/react";
import { describe, expect, it, vi } from "vitest";

import { ConnectionNode } from "@/components/graph/ConnectionNode";

vi.mock("@/hooks/useZoomBelow", () => ({
  useZoomBelow: () => true,
}));

const baseData = {
  label: "JMS · esb.orders",
  displayLabel: "esb.orders",
  connectionType: "Shared JMS channel",
  host: "",
  database: "",
  usageCount: 2,
  compact: true,
};

describe("ConnectionNode landscape label", () => {
  it("keeps a shared resource name visible at the furthest zoom level", () => {
    render(
      <ReactFlowProvider>
        <ConnectionNode data={{ ...baseData, alwaysShowLabel: true }} />
      </ReactFlowProvider>,
    );

    expect(screen.getByText("esb.orders")).toBeInTheDocument();
  });

  it("retains the compact pill treatment for ordinary connection nodes", () => {
    render(
      <ReactFlowProvider>
        <ConnectionNode data={baseData} />
      </ReactFlowProvider>,
    );

    expect(screen.queryByText("esb.orders")).not.toBeInTheDocument();
  });
});
