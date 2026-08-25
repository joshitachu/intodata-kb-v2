import { afterEach, describe, expect, it, vi } from "vitest";
import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import AdminRuntimePage from "@/app/admin/runtime/page";

/**
 * Test-then-choose flow for a runtime source's ingest scope.
 *
 * Registering a TMC source used to poll the whole tenant, which is expensive
 * (run history is fetched per executable) and usually wrong -- a source bound
 * to one project rarely wants another environment's runs. The dialog now proves
 * the credentials reach the source, then makes the operator pick from what the
 * source actually reported.
 *
 * The scope list is driven entirely by the provider's `scope_selection`
 * descriptor, so these tests assert on the generic flow rather than on
 * anything TMC-specific.
 */

vi.mock("@/hooks/useUserProjects", () => ({
  useUserProjects: () => ({
    data: {
      projects: [
        { id: "p1", display_name: "Project One" },
        { id: "p2", display_name: "Project Two" },
      ],
    },
    isLoading: false,
  }),
}));

const SOURCE_TYPE = {
  provider_key: "tmc",
  provider_label: "Talend Management Console",
  credential_schema: [
    { name: "api_token", type: "secret", label: "API token", required: true },
    { name: "base_url", type: "url", label: "Base URL", required: true },
  ],
  scope_selection: {
    field: "environment_ids",
    label: "Environments",
    description: "Only tasks from the selected environments are polled.",
    required: true,
    multiple: true,
  },
  editable_config_fields: ["environment_ids"],
};

const SCOPES = [
  { id: "env-dev", label: "DEV", detail: "81 tasks", default_selected: true },
  { id: "env-prod", label: "PROD", detail: "32 tasks", default_selected: true },
  { id: "env-empty", label: "Scratch", detail: "0 tasks", default_selected: false },
];

function jsonResponse(body: unknown, status = 200): Response {
  return { ok: status >= 200 && status < 300, status, json: async () => body } as Response;
}

/** Serve the page's mount reads plus a scriptable discovery response. */
function mockApi(overrides: {
  discover?: () => Response;
  create?: () => Response;
  sources?: RuntimeSourceFixture[];
} = {}) {
  const calls: Array<{ url: string; init?: RequestInit }> = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    calls.push({ url, init });
    if (url.startsWith("/api/runtime/sources/discover")) {
      return overrides.discover?.() ?? jsonResponse({ scopes: SCOPES });
    }
    if (url.startsWith("/api/runtime/source-types")) {
      return jsonResponse({ source_types: [SOURCE_TYPE] });
    }
    if (url.startsWith("/api/runtime/sources") && init?.method === "POST") {
      return overrides.create?.() ?? jsonResponse({ source: { id: 1 } });
    }
    if (url.startsWith("/api/runtime/sources")) {
      return jsonResponse({ sources: overrides.sources ?? [], total: overrides.sources?.length ?? 0 });
    }
    if (url.startsWith("/api/runtime/audit")) return jsonResponse({ events: [] });
    return jsonResponse({});
  });
  vi.stubGlobal("fetch", fetchMock);
  return { fetchMock, calls };
}

type RuntimeSourceFixture = {
  id: number;
  project_id: string;
  source_key: string;
  source_type: string;
  label: string;
  status: string;
  enabled: boolean;
  sync_interval_seconds: number;
};

function tmcSource(id: number, projectId: string): RuntimeSourceFixture {
  return {
    id,
    project_id: projectId,
    source_key: `tmc-${id}`,
    source_type: "tmc",
    label: "Talend TMC",
    status: "success",
    enabled: true,
    sync_interval_seconds: 60,
  };
}

async function openDialogWithCredentials() {
  render(<AdminRuntimePage />);
  await waitFor(() => expect(screen.getByRole("button", { name: /add source/i })).toBeTruthy());
  fireEvent.click(screen.getByRole("button", { name: /add source/i }));
  await waitFor(() => expect(screen.getByLabelText(/api token/i)).toBeTruthy());
  fireEvent.change(screen.getByLabelText(/api token/i), { target: { value: "tmc-token" } });
  fireEvent.change(screen.getByLabelText(/base url/i), {
    target: { value: "https://api.eu.cloud.talend.com" },
  });
}

describe("Add runtime source — ingest scope selection", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("offers no scope list until the connection has been tested", async () => {
    mockApi();
    await openDialogWithCredentials();

    expect(screen.queryByText("PROD")).toBeNull();
    expect(screen.getByRole("button", { name: /save source/i })).toHaveProperty("disabled", true);
  });

  it("does not allow another TMC connection when every project already has one", async () => {
    mockApi({ sources: [tmcSource(1, "p1"), tmcSource(2, "p2")] });
    render(<AdminRuntimePage />);
    await waitFor(() => expect(screen.getByRole("button", { name: /add source/i })).toBeTruthy());
    fireEvent.click(screen.getByRole("button", { name: /add source/i }));

    await waitFor(() =>
      expect(screen.getByRole("status").textContent).toContain("Edit ingest scope"),
    );
    expect(screen.getByRole("button", { name: /test connection/i })).toHaveProperty("disabled", true);
    expect(screen.getByRole("button", { name: /save source/i })).toHaveProperty("disabled", true);
  });

  it("lists what the source reported once the test succeeds", async () => {
    mockApi();
    await openDialogWithCredentials();

    fireEvent.click(screen.getByRole("button", { name: /test connection/i }));

    await waitFor(() => expect(screen.getByText("PROD")).toBeTruthy());
    expect(screen.getByText("32 tasks")).toBeTruthy();
    expect(screen.getByText("DEV")).toBeTruthy();
    expect(screen.getByText("Scratch")).toBeTruthy();
  });

  it("sends the typed credentials to the discovery endpoint", async () => {
    const { calls } = mockApi();
    await openDialogWithCredentials();

    fireEvent.click(screen.getByRole("button", { name: /test connection/i }));
    await waitFor(() => expect(screen.getByText("PROD")).toBeTruthy());

    const discover = calls.find((call) => call.url.includes("/discover"));
    expect(JSON.parse(String(discover?.init?.body))).toEqual({
      provider_key: "tmc",
      credentials: { api_token: "tmc-token", base_url: "https://api.eu.cloud.talend.com" },
    });
  });

  it("preselects the non-empty scopes and leaves empty ones unticked", async () => {
    mockApi();
    await openDialogWithCredentials();

    fireEvent.click(screen.getByRole("button", { name: /test connection/i }));
    await waitFor(() => expect(screen.getByText("PROD")).toBeTruthy());

    const boxes = screen.getAllByRole("checkbox") as HTMLInputElement[];
    expect(boxes.map((box) => box.checked)).toEqual([true, true, false]);
  });

  it("saves only the ticked scopes", async () => {
    const { calls } = mockApi();
    await openDialogWithCredentials();

    fireEvent.click(screen.getByRole("button", { name: /test connection/i }));
    await waitFor(() => expect(screen.getByText("PROD")).toBeTruthy());
    // Drop DEV, keep PROD.
    fireEvent.click((screen.getAllByRole("checkbox") as HTMLInputElement[])[0]);
    fireEvent.click(screen.getByRole("button", { name: /save source/i }));

    await waitFor(() => {
      const create = calls.find(
        (call) => call.init?.method === "POST" && !call.url.includes("/discover"),
      );
      expect(create).toBeTruthy();
      expect(JSON.parse(String(create?.init?.body)).credentials.environment_ids).toEqual([
        "env-prod",
      ]);
    });
  });

  it("blocks the save when every scope is unticked", async () => {
    mockApi();
    await openDialogWithCredentials();

    fireEvent.click(screen.getByRole("button", { name: /test connection/i }));
    await waitFor(() => expect(screen.getByText("PROD")).toBeTruthy());
    const boxes = screen.getAllByRole("checkbox") as HTMLInputElement[];
    fireEvent.click(boxes[0]);
    fireEvent.click(boxes[1]);

    expect(screen.getByRole("button", { name: /save source/i })).toHaveProperty("disabled", true);
  });

  it("discards a discovered scope list when a credential is edited", async () => {
    mockApi();
    await openDialogWithCredentials();

    fireEvent.click(screen.getByRole("button", { name: /test connection/i }));
    await waitFor(() => expect(screen.getByText("PROD")).toBeTruthy());

    // A list discovered with the previous token must not be saved against a new one.
    fireEvent.change(screen.getByLabelText(/api token/i), { target: { value: "other-token" } });

    await waitFor(() => expect(screen.queryByText("PROD")).toBeNull());
    expect(screen.getByRole("button", { name: /save source/i })).toHaveProperty("disabled", true);
  });

  it("reports a failed connection test without listing scopes", async () => {
    mockApi({ discover: () => jsonResponse({ detail: "401 Unauthorized" }, 400) });
    await openDialogWithCredentials();

    fireEvent.click(screen.getByRole("button", { name: /test connection/i }));

    await waitFor(() => expect(screen.getByRole("alert").textContent).toContain("401"));
    expect(screen.queryByText("PROD")).toBeNull();
  });
});
