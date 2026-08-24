import { afterEach, describe, expect, it, vi } from "vitest";
import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { SimpleConnectorPanel } from "@/app/settings/sources/SimpleConnectorPanel";

function renderPanel() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <SimpleConnectorPanel />
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

describe("SimpleConnectorPanel", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("shows connected systems without project source or capability controls", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse({
      connectors: [{
        id: "connector-1",
        provider_id: "elastic_stack",
        display_name: "Production Elastic",
        enabled: true,
        status: "connected",
        last_checked_at: "2026-08-21T10:00:00Z",
        health_code: null,
      }],
    })));

    renderPanel();

    expect(await screen.findByText("Production Elastic")).toBeInTheDocument();
    expect(screen.getByText("connected")).toBeInTheDocument();
    expect(screen.queryByText(/select indices/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/assign to project/i)).not.toBeInTheDocument();
    expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
  });

  it("creates an API-key Elastic connection and tests it immediately", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/source/connectors" && init?.method === "POST") {
        return jsonResponse({
          id: "connector-2",
          provider_id: "elastic_stack",
          display_name: "Test Elastic",
          enabled: true,
          status: "not_connected",
          last_checked_at: null,
          health_code: null,
        });
      }
      if (url === "/api/source/connectors/connector-2/test" && init?.method === "POST") {
        return jsonResponse({ status: "connected", message: "Connection successful." });
      }
      return jsonResponse({ connectors: [] });
    });
    vi.stubGlobal("fetch", fetchMock);

    renderPanel();
    await screen.findByText("No live data connection has been added yet.");
    fireEvent.click(screen.getByRole("button", { name: "Connect Elastic" }));
    fireEvent.change(screen.getByLabelText("Display name"), { target: { value: "Test Elastic" } });
    fireEvent.change(screen.getByLabelText("Elasticsearch HTTPS endpoint"), {
      target: { value: "https://elastic.example.test/" },
    });
    fireEvent.change(screen.getByLabelText("API key"), { target: { value: "secret-test-key" } });
    fireEvent.click(screen.getByRole("button", { name: "Connect & test" }));

    expect(await screen.findByText("Connection successful.")).toBeInTheDocument();
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/source/connectors/connector-2/test",
        expect.objectContaining({ method: "POST" }),
      );
    });

    const createCall = fetchMock.mock.calls.find(
      ([url, init]) => String(url) === "/api/source/connectors" && init?.method === "POST",
    );
    expect(createCall).toBeDefined();
    expect(JSON.parse(String(createCall?.[1]?.body))).toEqual({
      provider_id: "elasticsearch",
      display_name: "Test Elastic",
      safe_config: {
        endpoint: "https://elastic.example.test",
        auth_type: "api_key",
        verify_tls: true,
        kibana_reuse_elasticsearch_credentials: true,
      },
      credential_type: "api_key",
      credential: "secret-test-key",
    });
  });
});
