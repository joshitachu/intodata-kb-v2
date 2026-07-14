import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { PackSelector } from "@/components/harness-packs/PackSelector";

const pack = {
  id: "nexus.documentation", name: "Documentation", version: "1.0.0", pack_type: "documentation" as const,
  includes: [], workflow_labels: ["Generate documentation"], workflow_descriptions: ["Generate scoped project documentation."],
  entity_labels: ["Document"], documentation_provider_ref: "mcp.docs.default", content_hash: "a".repeat(64),
};

describe("PackSelector", () => {
  it("shows concise recommendation reasons and updates the selection", () => {
    const onChange = vi.fn();
    render(<PackSelector packs={[pack]} selectedPackIds={[]} recommendations={[{ pack_id: pack.id, confidence: 0.82, reasons: ["Project already has indexed documents"] }]} onChange={onChange} />);
    expect(screen.getByText("Project already has indexed documents (82% match)")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Select" }));
    expect(onChange).toHaveBeenCalledWith(["nexus.documentation"]);
  });
});
