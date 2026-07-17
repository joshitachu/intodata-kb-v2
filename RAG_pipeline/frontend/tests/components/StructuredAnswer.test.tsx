import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { StructuredAnswer } from "@/components/StructuredAnswer";

describe("StructuredAnswer", () => {
  it("renders ranking tables and keeps execution separate from domain state", () => {
    render(
      <StructuredAnswer
        answer={{
          output_contract: "runtime_ranking",
          headline: "Runtime failure ranking",
          summary: "Job A had the most returned failures.",
          sections: [
            {
              heading: "Failure ranking",
              rows: [
                { rank: 1, job: "Job A", failures: 3 },
                { rank: 2, job: "Job B", failures: 1 },
              ],
            },
          ],
          limitations: ["Counts cover returned observations only."],
          citations: [
            {
              evidence_id: "runtime:1",
              label: "Runtime observations",
              source_class: "current_runtime",
            },
          ],
          evidence_refs: ["runtime:1"],
          answer_status: "partial",
          execution_status: "completed",
          domain_status: "failed",
          artifacts: [],
        }}
      />,
    );

    expect(screen.getByRole("heading", { name: "Runtime failure ranking" })).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: /job/i })).toBeInTheDocument();
    expect(screen.getByText("Job A")).toBeInTheDocument();
    expect(screen.getByText("Execution:")).toBeInTheDocument();
    expect(screen.getByText("Observed state:")).toBeInTheDocument();
    expect(screen.getByText("Counts cover returned observations only.")).toBeInTheDocument();
    expect(screen.getByTestId("answer-table-scroll")).toHaveClass("overflow-x-auto");
  });

  it("renders migration sections and artifacts", () => {
    render(
      <StructuredAnswer
        answer={{
          output_contract: "migration_proposal",
          headline: "ADF migration proposal",
          summary: "AssetCheck is the candidate.",
          sections: [
            { heading: "Current flow", body: "Maximo to SQL.", rows: [] },
            { heading: "Target mapping", body: "ADF Copy Activity and Data Flow.", rows: [] },
          ],
          limitations: [],
          citations: [],
          evidence_refs: [],
          answer_status: "complete",
          execution_status: "completed",
          domain_status: "unknown",
          artifacts: [
            { label: "Open proposal", url: "/projects/alitalend/proposal", kind: "document" },
          ],
        }}
      />,
    );

    expect(screen.getByText("Current flow")).toBeInTheDocument();
    expect(screen.getByText("ADF Copy Activity and Data Flow.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Open proposal/ })).toHaveAttribute(
      "href",
      "/projects/alitalend/proposal",
    );
  });

  it("presents insufficient evidence separately from successful execution", () => {
    render(
      <StructuredAnswer
        answer={{
          output_contract: "documentation_answer",
          headline: "Available documentation",
          summary: "The indexed evidence answers part of the question.",
          sections: [],
          limitations: ["Connection details were not documented."],
          citations: [],
          evidence_refs: [],
          answer_status: "insufficient",
          execution_status: "completed",
          domain_status: "unknown",
          artifacts: [],
        }}
      />,
    );

    expect(screen.getByText("completed")).toBeInTheDocument();
    expect(screen.getByText("insufficient")).toBeInTheDocument();
    expect(screen.queryByText(/response failed/i)).not.toBeInTheDocument();
  });
});
