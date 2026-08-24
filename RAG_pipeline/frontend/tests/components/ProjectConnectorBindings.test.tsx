import { afterEach, describe, expect, it, vi } from "vitest";
import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ProjectConnectorBindings } from "@/app/settings/sources/ProjectConnectorBindings";

function renderPanel() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <ProjectConnectorBindings projectId="werkorder" />
    </QueryClientProvider>,
  );
}

function jsonResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as Response;
}

const CONNECTOR = {
  id: "connector-1",
  provider_id: "elastic_stack",
  display_name: "Production Elastic",
  status: "connected",
  enabled: true,
};

const SOURCES = [
  { id: "src-1", display_label: "logs-talend-*", source_type: "index", enabled: true },
  { id: "src-2", display_label: "metrics-*", source_type: "index", enabled: true },
];

describe("ProjectConnectorBindings", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("lets an owner pick which sources this project may read", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input) === "/api/source/connectors/projects/werkorder" && init?.method === undefined) {
        return jsonResponse({
          project_id: "werkorder",
          can_manage: true,
          connectors: [{ connector: CONNECTOR, sources: SOURCES }],
          bindings: [],
        });
      }
      return jsonResponse({ ok: true });
    });
    vi.stubGlobal("fetch", fetchMock);

    renderPanel();

    expect(await screen.findByText("Production Elastic")).toBeInTheDocument();
    // The source list stays collapsed behind a summary so a 60-index cluster
    // cannot bury the rest of the row.
    expect(screen.queryByRole("button", { name: "logs-talend-*" })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /Choose sources/ }));

    // Filtering narrows the list rather than scrolling a wall of chips.
    fireEvent.change(screen.getByLabelText("Filter sources"), { target: { value: "logs" } });
    expect(screen.queryByRole("button", { name: "metrics-*" })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "logs-talend-*" }));
    fireEvent.click(screen.getByLabelText("Use for this project"));
    fireEvent.click(screen.getByRole("button", { name: "Save connector access" }));

    await waitFor(() => {
      const saveCall = fetchMock.mock.calls.find(([, init]) => init?.method === "PUT");
      expect(saveCall).toBeDefined();
      expect(String(saveCall?.[0])).toBe("/api/source/connectors/projects/werkorder/connector-1");
      expect(JSON.parse(String(saveCall?.[1]?.body))).toMatchObject({
        enabled: true,
        source_ids: ["src-1"],
      });
    });
  });

  it("shows a read-only summary when the user may view but not configure the project", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse({
      project_id: "werkorder",
      can_manage: false,
      connectors: [],
      bindings: [
        {
          connector_instance_id: "connector-1",
          source_ids: ["src-1", "src-2"],
          capability_ids: ["runtime.events.search"],
          max_time_window_days: null,
          enabled: true,
          version: 3,
        },
      ],
    })));

    renderPanel();

    expect(await screen.findByText(/Only a project owner can change this/)).toBeInTheDocument();
    expect(screen.getByText(/2 sources enabled/)).toBeInTheDocument();
    expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Save connector access" })).not.toBeInTheDocument();
  });
});
