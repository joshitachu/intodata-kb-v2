import { render, screen } from "@testing-library/react";
import { ReactFlowProvider } from "@xyflow/react";
import { describe, expect, it, vi } from "vitest";

import { JobNode } from "@/components/graph/JobNode";

vi.mock("@/hooks/useZoomBelow", () => ({
  useZoomBelow: () => false,
}));

const baseData = {
  label: "OffRamp_DB",
  displayLabel: "OffRamp_DB",
  group: "ITEM",
  color: { bg: "#eff6ff", border: "#2563eb", text: "#1e40af" },
  callsOut: 0,
  calledBy: 0,
  connCount: 0,
  purpose: "",
  componentCount: 1,
  hasDocs: false,
  externalSystems: [],
  summary: "",
  direction: "OffRamp",
  compact: true,
  artifactTaxonomy: true,
};

function renderNode(artifactKind: "job" | "route") {
  return render(
    <ReactFlowProvider>
      <JobNode data={{ ...baseData, artifactKind }} />
    </ReactFlowProvider>,
  );
}

describe("JobNode artifact label", () => {
  it("places a quiet Job label in the card content beneath the design name", () => {
    renderNode("job");

    expect(screen.getByText("OffRamp_DB")).toBeInTheDocument();
    const type = screen.getByTestId("artifact-kind-label");
    expect(type).toHaveTextContent("Job");
    expect(type).toHaveAttribute("aria-label", "Talend Job");
  });

  it("uses the same non-overlapping treatment for Routes", () => {
    renderNode("route");

    const type = screen.getByTestId("artifact-kind-label");
    expect(type).toHaveTextContent("Route");
    expect(type).toHaveAttribute("aria-label", "Talend Route");
  });
});
