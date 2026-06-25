import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { SourceCitation } from "@/components/SourceCitation";
import type { DocumentSource } from "@/types";

function makeSource(role: DocumentSource["document_role"]): DocumentSource {
  return {
    document_id: 1,
    filename: "guidance.pdf",
    score: 0.82,
    text: "Use this as guidance.",
    reference_number: 1,
    chunk_indexes: [0],
    document_role: role,
  };
}

describe("SourceCitation", () => {
  it("shows a role badge for non-source citations", () => {
    render(<SourceCitation source={makeSource("guidance")} index={0} />);
    expect(screen.getByText("Guidance")).toBeInTheDocument();
  });

  it("does not show a redundant badge for source citations", () => {
    render(<SourceCitation source={makeSource("source")} index={0} />);
    expect(screen.queryByText("Source")).not.toBeInTheDocument();
  });
});
