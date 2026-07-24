import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import React, { type ReactNode } from "react";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { JobPurposeBanner } from "@/components/graph/JobPurposeBanner";

function createWrapper() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return function Wrapper({ children }: { children: ReactNode }) {
    return React.createElement(QueryClientProvider, { client: queryClient }, children);
  };
}

function mockJobDetail(summary: string) {
  return new Response(
    JSON.stringify({
      project_id: "proj1",
      job_name: "jobA",
      group: "Item",
      scan_data: { components: [] },
      doc_sections: [],
      summary,
      direction: "",
    }),
    { status: 200, headers: { "Content-Type": "application/json" } },
  );
}

describe("JobPurposeBanner", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders the job summary and name when present", async () => {
    vi.spyOn(global, "fetch").mockResolvedValue(
      mockJobDetail("Loads work orders into the warehouse."),
    );

    render(<JobPurposeBanner projectId="proj1" jobName="jobA" />, {
      wrapper: createWrapper(),
    });

    await waitFor(() =>
      expect(screen.getByTestId("job-purpose-text")).toHaveTextContent(
        "Loads work orders into the warehouse.",
      ),
    );
    expect(screen.getByText("jobA")).toBeInTheDocument();
  });

  it("renders nothing when the summary is empty (no fabrication)", async () => {
    vi.spyOn(global, "fetch").mockResolvedValue(mockJobDetail("   "));

    const { container } = render(
      <JobPurposeBanner projectId="proj1" jobName="jobA" />,
      { wrapper: createWrapper() },
    );

    await waitFor(() => expect(global.fetch).toHaveBeenCalled());
    expect(screen.queryByTestId("job-purpose-banner")).not.toBeInTheDocument();
    expect(screen.queryByTestId("job-purpose-banner-pill")).not.toBeInTheDocument();
    expect(container).toBeEmptyDOMElement();
  });

  it("collapses to a pill and expands again", async () => {
    vi.spyOn(global, "fetch").mockResolvedValue(
      mockJobDetail("Loads assets nightly."),
    );

    render(<JobPurposeBanner projectId="proj1" jobName="jobA" />, {
      wrapper: createWrapper(),
    });

    await waitFor(() =>
      expect(screen.getByTestId("job-purpose-banner")).toBeInTheDocument(),
    );

    // Collapse -> pill only.
    fireEvent.click(screen.getByTestId("job-purpose-banner-collapse"));
    expect(screen.queryByTestId("job-purpose-banner")).not.toBeInTheDocument();
    expect(screen.getByTestId("job-purpose-banner-pill")).toBeInTheDocument();

    // Expand -> full banner again.
    fireEvent.click(screen.getByTestId("job-purpose-banner-pill"));
    expect(screen.getByTestId("job-purpose-banner")).toBeInTheDocument();
    expect(screen.queryByTestId("job-purpose-banner-pill")).not.toBeInTheDocument();
  });
});
