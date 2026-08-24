import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { EvidenceSummaryNote } from "@/components/copilot/EvidenceSummaryNote";
import type { EvidenceSummary } from "@/types";

function summary(overrides: Partial<EvidenceSummary> = {}): EvidenceSummary {
  return {
    version: "1.0",
    evidence_step_count: 1,
    successful_sources: [
      { family: "topology", role: "structure", evidence_count: 2 },
    ],
    failed_sources: [],
    zero_evidence: false,
    documentation: {
      provider_used: null,
      fallback_used: false,
      attempts: [],
    },
    ...overrides,
  };
}

describe("EvidenceSummaryNote", () => {
  it("labels topology-only evidence", () => {
    render(<EvidenceSummaryNote summary={summary()} />);

    const note = screen.getByLabelText("Answer evidence");
    expect(note).toHaveAttribute("data-evidence-state", "topology");
    expect(note).toHaveTextContent("Topology · 2");
    expect(note).toHaveTextContent("1 evidence step");
  });

  it("labels documentation and discloses its fallback provider", () => {
    render(
      <EvidenceSummaryNote
        summary={summary({
          evidence_step_count: 3,
          successful_sources: [
            { family: "documentation", role: "explanation", evidence_count: 3 },
          ],
          documentation: {
            provider_used: "anthropic",
            fallback_used: true,
            attempts: [
              { provider: "kimi", status: "failed" },
              { provider: "anthropic", status: "succeeded" },
            ],
          },
        })}
      />,
    );

    const note = screen.getByLabelText("Answer evidence");
    expect(note).toHaveAttribute("data-evidence-state", "documentation");
    expect(note).toHaveTextContent("Documentation · 3 via anthropic (fallback)");
  });

  it("labels runtime-only evidence", () => {
    render(
      <EvidenceSummaryNote
        summary={summary({
          successful_sources: [
            { family: "runtime", role: "runtime_verification", evidence_count: 4 },
          ],
        })}
      />,
    );

    expect(screen.getByLabelText("Answer evidence")).toHaveAttribute(
      "data-evidence-state",
      "runtime",
    );
    expect(screen.getByText(/Runtime · 4/)).toBeInTheDocument();
  });

  it("labels a fused answer and retains every source family", () => {
    render(
      <EvidenceSummaryNote
        summary={summary({
          evidence_step_count: 5,
          successful_sources: [
            { family: "topology", role: "structure", evidence_count: 2 },
            { family: "documentation", role: "explanation", evidence_count: 3 },
          ],
          documentation: {
            provider_used: "kimi",
            fallback_used: false,
            attempts: [{ provider: "kimi", status: "succeeded" }],
          },
        })}
      />,
    );

    const note = screen.getByLabelText("Answer evidence");
    expect(note).toHaveAttribute("data-evidence-state", "fused");
    expect(note).toHaveTextContent("Combined evidence");
    expect(note).toHaveTextContent("Topology · 2");
    expect(note).toHaveTextContent("Documentation · 3 via kimi");
  });

  it("discloses a partial answer when one source failed", () => {
    render(
      <EvidenceSummaryNote
        summary={summary({
          failed_sources: [
            {
              family: "documentation",
              status: "unavailable",
              reason_code: "all_providers_failed",
              message: "Documentation is temporarily unavailable.",
            },
          ],
        })}
      />,
    );

    const note = screen.getByLabelText("Answer evidence");
    expect(note).toHaveAttribute("data-evidence-state", "partial");
    expect(note).toHaveTextContent("Partially supported");
    expect(note).toHaveTextContent("Unavailable: Documentation");
    expect(note).toHaveTextContent("Topology · 2");
  });

  it("warns when the current turn has zero evidence", () => {
    render(
      <EvidenceSummaryNote
        summary={summary({
          evidence_step_count: 0,
          successful_sources: [],
          failed_sources: [],
          zero_evidence: true,
        })}
      />,
    );

    const note = screen.getByRole("alert", { name: "Answer evidence" });
    expect(note).toHaveAttribute("data-evidence-state", "zero");
    expect(note).toHaveTextContent("No current-turn evidence");
    expect(note).toHaveTextContent("No validated project source supported this answer.");
  });
});
