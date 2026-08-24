import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { MessageBubble } from "@/components/MessageBubble";
import type { MessageResponse } from "@/types";
import type { PageScope } from "@/types/topology";

function userMessage(pageScope?: PageScope): MessageResponse {
  return {
    id: 7,
    role: "user",
    content: "Which systems feed this?",
    sources: null,
    latency_ms: null,
    status: "completed",
    created_at: "2026-08-20T10:00:00Z",
    page_scope: pageScope,
  };
}

describe("MessageBubble page-scope chip", () => {
  it("records the surface and level a question was asked from", () => {
    render(
      <MessageBubble
        message={userMessage({
          surface: "topology",
          project_id: "werkorder",
          level: { kind: "job", jobName: "tJob_OrderSync" },
        })}
      />,
    );

    const chip = screen.getByLabelText("Asked from");
    expect(chip).toHaveTextContent("Topology");
    expect(chip).toHaveTextContent("tJob_OrderSync");
  });

  it("names the landscape level rather than leaving the chip empty", () => {
    render(
      <MessageBubble
        message={userMessage({
          surface: "topology",
          project_id: "werkorder",
          level: { kind: "project" },
        })}
      />,
    );

    expect(screen.getByLabelText("Asked from")).toHaveTextContent("Topology");
    expect(screen.getByLabelText("Asked from")).toHaveTextContent("Landscape");
  });

  it("shows the job and component for a level-4 scope", () => {
    render(
      <MessageBubble
        message={userMessage({
          surface: "topology",
          project_id: "werkorder",
          level: { kind: "component", jobName: "tJob_OrderSync", componentName: "tMap_1" },
        })}
      />,
    );

    expect(screen.getByLabelText("Asked from")).toHaveTextContent("tJob_OrderSync › tMap_1");
  });

  it("renders no chip for a message without a page scope", () => {
    render(<MessageBubble message={userMessage()} />);

    expect(screen.queryByLabelText("Asked from")).not.toBeInTheDocument();
    expect(screen.getByText("Which systems feed this?")).toBeInTheDocument();
  });
});
