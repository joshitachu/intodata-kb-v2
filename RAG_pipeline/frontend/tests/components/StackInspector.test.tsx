import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { StackInspector } from "@/components/harness-packs/StackInspector";

const foundation = {
  id: "core.foundation", name: "Core", version: "1.0.0", pack_type: "domain" as const, phase: "foundation" as const,
  content_hash: "a".repeat(64), includes: [], workflow_labels: ["Investigate"], workflow_descriptions: ["Establishes evidence-first investigation."],
  entity_labels: ["Execution"], domains: ["operations"], documentation_provider_ref: null,
  authority: "intodata_certified", trust: "certified", mutability: "immutable", scope: "global", owner: "intodata",
  quality_score: 0.9, evaluation_score: 0.9, blocked_override_rules: ["organization policy is mandatory"],
};
const functional = { ...foundation, id: "analysis.docs", name: "Documentation analysis", phase: "functional" as const };

describe("StackInspector", () => {
  it("keeps the required Foundation while selecting approved Functional Packs", () => {
    const onChange = vi.fn();
    render(<StackInspector policy={{ id: "policy.default", required_certified_pack_ids: [foundation.id], allowed_functional_pack_ids: [functional.id], permitted_style: { permitted_fields: [], allowed_audiences: [], allowed_tones: [], allowed_technical_depths: [], allowed_output_organizations: [], allowed_formatting: [], allowed_communication: [] }, minimum_quality: { quality_score: 0.8, evaluation_score: 0.8 }, protected_invariants: [] }} library={[foundation, functional]} styles={[]} selection={{ foundation_pack_ids: [foundation.id], functional_pack_ids: [], style_pack_ids: [] }} activation={null} onChange={onChange} />);

    expect(screen.getByRole("radio")).toBeDisabled();
    expect(screen.getAllByText("Adds:")[0].parentElement).toHaveTextContent("workflows Investigate");
    expect(screen.getAllByText(new RegExp(`Hash ${"a".repeat(64)}`))[0]).toBeInTheDocument();
    fireEvent.click(screen.getByRole("checkbox"));
    expect(onChange).toHaveBeenCalledWith({ foundation_pack_ids: [foundation.id], functional_pack_ids: [functional.id], style_pack_ids: [] });
  });
});
