import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { BusinessContextBanner } from "@/components/graph/BusinessContextBanner";

const claim = { status: "inferred", confidence: 0.7 };

describe("BusinessContextBanner", () => {
  it("renders each annotation kind's own explanation slot", () => {
    render(
      <BusinessContextBanner
        level="L4 mapping"
        annotations={[
          { ...claim, id: "a", name: "Sender", description: "", business_role: "Order intake" },
          { ...claim, id: "c", name: "tMap_1", description: "Context", human_explanation: "Builds the order line." },
          { ...claim, id: "l", name: "ORDERNO", description: "", business_meaning: "The customer order number." },
        ]}
      />,
    );
    expect(screen.getByText("Order intake")).toBeInTheDocument();
    expect(screen.getByText(/Builds the order line\./)).toBeInTheDocument();
    expect(screen.getByText("Context")).toBeInTheDocument();
    expect(screen.getByText(/The customer order number\./)).toBeInTheDocument();
    // A role IS an explanation; the filler line is only for a bare claim.
    expect(screen.queryByText(/No further explanation established/)).toBeNull();
  });

  it("renders nothing without annotations", () => {
    const { container } = render(<BusinessContextBanner level="L3 job" annotations={[]} />);
    expect(container).toBeEmptyDOMElement();
  });
});
