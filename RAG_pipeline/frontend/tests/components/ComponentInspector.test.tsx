import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import React, { type ReactNode } from "react";
import { render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ComponentInspector } from "@/components/graph/ComponentInspector";

function createWrapper() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return function Wrapper({ children }: { children: ReactNode }) {
    return React.createElement(QueryClientProvider, { client: queryClient }, children);
  };
}

function mockJobDetail(components: unknown[]) {
  return new Response(
    JSON.stringify({
      project_id: "proj1",
      job_name: "jobA",
      group: "Item",
      scan_data: { components },
      doc_sections: [],
      summary: "",
      direction: "",
    }),
    { status: 200, headers: { "Content-Type": "application/json" } },
  );
}

describe("ComponentInspector", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders params, code, and connection for the matching component", async () => {
    vi.spyOn(global, "fetch").mockResolvedValueOnce(
      mockJobDetail([
        {
          unique_name: "tJava_1",
          component_type: "tJava",
          params: [{ name: "CODE", value: "log.info(\"hi\");" }],
          code: "log.info(\"hi\");",
          incoming_connections: [],
          outgoing_connections: [],
          metadata_connection_ref: "BigQuery_Prod",
        },
      ]),
    );

    render(
      <ComponentInspector
        projectId="proj1"
        jobName="jobA"
        componentName="tJava_1"
        onClose={vi.fn()}
      />,
      { wrapper: createWrapper() },
    );

    await waitFor(() => expect(screen.getByText("tJava")).toBeInTheDocument());
    expect(screen.getByTestId("inspector-connection-ref")).toHaveTextContent(
      "BigQuery_Prod",
    );
    expect(screen.getByTestId("inspector-params")).toHaveTextContent("CODE");
    expect(screen.getByTestId("inspector-code")).toHaveTextContent('log.info("hi");');
  });

  it("renders the passed-in connectors list", async () => {
    vi.spyOn(global, "fetch").mockResolvedValueOnce(
      mockJobDetail([
        {
          unique_name: "tDBOutput_1",
          component_type: "tDBOutput",
          params: [],
          code: null,
          incoming_connections: [],
          outgoing_connections: [],
          metadata_connection_ref: "",
        },
      ]),
    );

    render(
      <ComponentInspector
        projectId="proj1"
        jobName="jobA"
        componentName="tDBOutput_1"
        connectors={[
          {
            direction: "incoming",
            peer: "tMap_1",
            rowName: "out1",
            connectorName: "FLOW",
            isControlFlow: false,
          },
        ]}
        onClose={vi.fn()}
      />,
      { wrapper: createWrapper() },
    );

    await waitFor(() =>
      expect(screen.getByTestId("inspector-connector-row")).toBeInTheDocument(),
    );
    expect(screen.getByTestId("inspector-connector-row")).toHaveTextContent("tMap_1");
    expect(screen.getByTestId("inspector-connector-row")).toHaveTextContent("via out1");
  });

  it("shows a not-found message when the component is absent from scan data", async () => {
    vi.spyOn(global, "fetch").mockResolvedValueOnce(mockJobDetail([]));

    render(
      <ComponentInspector
        projectId="proj1"
        jobName="jobA"
        componentName="tGhost_1"
        onClose={vi.fn()}
      />,
      { wrapper: createWrapper() },
    );

    await waitFor(() =>
      expect(
        screen.getByText("No scan data found for this component."),
      ).toBeInTheDocument(),
    );
  });

  it("calls onClose when the close button is clicked", async () => {
    vi.spyOn(global, "fetch").mockResolvedValueOnce(mockJobDetail([]));
    const onClose = vi.fn();

    render(
      <ComponentInspector
        projectId="proj1"
        jobName="jobA"
        componentName="tGhost_1"
        onClose={onClose}
      />,
      { wrapper: createWrapper() },
    );

    await waitFor(() => expect(screen.getByTitle("Close (Esc)")).toBeInTheDocument());
    screen.getByTitle("Close (Esc)").click();
    expect(onClose).toHaveBeenCalled();
  });
});
