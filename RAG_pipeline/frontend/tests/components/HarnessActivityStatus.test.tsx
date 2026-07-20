import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { HarnessActivityStatus } from "@/components/HarnessActivityStatus";

describe("HarnessActivityStatus", () => {
  it("renders nothing when there are no activities", () => {
    const { container } = render(<HarnessActivityStatus activities={[]} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("renders generic activity without runtime wording and keeps details collapsed", () => {
    render(
      <HarnessActivityStatus
        activities={[
          {
            event_type: "capability.started",
            label: "Starting documentation generation",
            status: "running",
            summary: "Generation request accepted",
            safe_details: { project: "fastapi-e2e" },
          },
        ]}
      />,
    );

    expect(screen.getByText("Starting documentation generation")).toBeInTheDocument();
    expect(screen.getByText("In progress")).toBeInTheDocument();
    expect(screen.getByText("1 action")).toBeInTheDocument();
    expect(screen.queryByText(/runtime/i)).not.toBeInTheDocument();
    expect(screen.queryByText("Generation request accepted")).not.toBeInTheDocument();
    expect(screen.queryByText("fastapi-e2e")).not.toBeInTheDocument();
  });

  it("expands safe details and filters unsafe detail keys", () => {
    render(
      <HarnessActivityStatus
        activities={[
          {
            event_type: "capability.completed",
            label: "Documentation generation started",
            status: "completed",
            summary: "Follow the run in the scanner dashboard.",
            safe_details: {
              project: "fastapi-e2e",
              raw_payload: "secret-payload",
              api_key: "sk-secret",
            },
          },
        ]}
      />,
    );

    fireEvent.click(screen.getByRole("button"));

    expect(
      screen.getByText("Follow the run in the scanner dashboard."),
    ).toBeInTheDocument();
    expect(screen.getByText("fastapi-e2e")).toBeInTheDocument();
    expect(screen.queryByText("secret-payload")).not.toBeInTheDocument();
    expect(screen.queryByText("sk-secret")).not.toBeInTheDocument();
  });

  it("filters unsafe-looking detail values and summarizes multiple updates", () => {
    render(
      <HarnessActivityStatus
        activities={[
          {
            event_type: "planning",
            label: "Planning next step",
            status: "completed",
            safe_details: { decision: "call_capability" },
          },
          {
            event_type: "provider_trigger",
            label: "Starting documentation generation",
            status: "running",
            safe_details: {
              provider: "mcp.docs",
              callback: "https://example.test/callback?access_token=secret",
            },
          },
        ]}
      />,
    );

    expect(screen.getByText("2 actions")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button"));

    expect(screen.queryByText("mcp.docs")).not.toBeInTheDocument();
    expect(screen.queryByText(/access_token/)).not.toBeInTheDocument();
  });

  it("renders result counts while never exposing internal IDs", () => {
    render(
      <HarnessActivityStatus
        activities={[
          {
            workflow_run_id: "42",
            event_type: "evidence.collected",
            label: "Searching documentation",
            status: "completed",
            summary: "Found matching documentation.",
            safe_details: {
              result_count: 3,
              capability_id: "mcp.docs.search",
              provider_id: "mcp.docs",
              tool_name: "search_documents",
            },
          },
        ]}
      />,
    );

    expect(screen.getByText("3 results")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Searching documentation/i }));
    expect(screen.queryByText(/mcp\.docs\.search/)).not.toBeInTheDocument();
    expect(screen.queryByText(/search_documents/)).not.toBeInTheDocument();
  });

  it("renders activity history without inferring canonical workflow state or timing", () => {
    render(
      <HarnessActivityStatus
        activities={[
          {
            event_type: "provider.request",
            label: "Starting documentation generation",
            status: "running",
            safe_details: {},
            timestamp: "2026-07-18T12:00:00Z",
          },
          {
            event_type: "graph.completed",
            label: "Graph workflow completed",
            status: "completed",
            safe_details: {},
            timestamp: "2026-07-18T12:00:05Z",
            workflow_status: "failed",
            workflow_started_at: "2026-07-18T12:00:00Z",
            workflow_finished_at: "2026-07-18T12:00:12Z",
          },
        ]}
      />,
    );

    expect(screen.getByText("Complete")).toBeInTheDocument();
    expect(screen.queryByText("12s")).not.toBeInTheDocument();
    expect(screen.queryByText("Needs attention")).not.toBeInTheDocument();
  });
});
