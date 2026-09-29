import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ReactElement } from "react";
import FleetPage from "../components/FleetPage";
import { instanceUrlProblem, safeInstanceHref, slugProblem } from "../lib/fleetClient";

const TELEMETRY_KEY = "tk_SECRET-HEARTBEAT-KEY-0123456789abcdef";
const ROTATED_KEY = "tk_ROTATED-HEARTBEAT-KEY-fedcba9876543210";
const CONTROL_KEY = "ck-SECRET-CONTROL-KEY";
const TELEMETRY_URL = "https://ops.nexus.intodata.nl";

function snapshot(slug: string, extra: Record<string, unknown> = {}) {
  return {
    schema_version: "1",
    client_slug: slug,
    environment: "prod",
    sent_at: new Date().toISOString(),
    health: { backend: "ok" },
    totals: {
      project_count: 0,
      job_count: 0,
      doc_count: 0,
      generate_run_count: 0,
      scan_run_count: 0,
      input_tokens: 0,
      output_tokens: 0,
      cost_usd: 0,
      latest_run_average_quality: null,
    },
    projects: [],
    latest_generate_runs: [],
    ...extra,
  };
}

function registryClient(slug: string, extra: Record<string, unknown> = {}) {
  return {
    slug,
    mcp_url: `https://${slug}.nexus.intodata.nl`,
    has_control_key: true,
    heartbeat_auth: "client_key",
    source: "registry",
    created_at: "2026-09-28T10:00:00Z",
    updated_at: "2026-09-28T10:00:00Z",
    last_heartbeat_at: null,
    ...extra,
  };
}

function clientEnv(slug: string, key: string, url: string | null = TELEMETRY_URL) {
  return { MCP_OPS_TELEMETRY_URL: url, MCP_OPS_TELEMETRY_API_KEY: key, MCP_OPS_CLIENT_SLUG: slug };
}

type Route = { status: number; body?: unknown };
let routes: Record<string, Route>;
let fetchMock: ReturnType<typeof vi.fn>;

function respond(route: Route): Response {
  if (route.status === 204) return new Response(null, { status: 204 });
  return new Response(JSON.stringify(route.body), {
    status: route.status,
    headers: { "Content-Type": "application/json" },
  });
}

beforeEach(() => {
  routes = {
    "GET /mc/ops/snapshots": {
      status: 200,
      body: { acme: snapshot("acme"), oldco: snapshot("oldco", { mcp_url: "https://oldco.nexus.intodata.nl" }) },
    },
    "GET /mc/ops/fleet/clients": {
      status: 200,
      body: {
        clients: [registryClient("acme", { last_heartbeat_at: new Date().toISOString() }), registryClient("newco")],
        telemetry_base_url: TELEMETRY_URL,
      },
    },
    "GET /mc/ops/licensing/plans": {
      status: 200,
      body: { plans: [{ name: "basic", description: null, features: [], limits: {} }], issuing_enabled: true },
    },
    "GET /mc/ops/clients/newco/licences": { status: 200, body: { licences: [] } },
  };
  fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const key = `${init?.method ?? "GET"} ${String(input)}`;
    const route = routes[key];
    if (!route) return respond({ status: 404, body: { detail: `no route ${key}` } });
    return respond(route);
  });
  vi.stubGlobal("fetch", fetchMock);
  localStorage.clear();
  sessionStorage.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function renderWithClient(ui: ReactElement) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return { client, ...render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>) };
}

function calls(method: string) {
  return fetchMock.mock.calls.filter(([, init]) => ((init as RequestInit | undefined)?.method ?? "GET") === method);
}

function bodyOf(call: unknown[]): unknown {
  return JSON.parse(String((call[1] as RequestInit).body));
}

/** Everything React Query holds, serialised, plus both browser stores. */
function everythingStored(client: QueryClient): string {
  const mutations = client.getMutationCache().getAll().map((m) => [m.state.data, m.state.variables]);
  const queries = client.getQueryCache().getAll().map((q) => q.state.data);
  return JSON.stringify([mutations, queries, { ...localStorage }, { ...sessionStorage }]);
}

async function openAddDialog() {
  const button = await screen.findByRole("button", { name: "Add client" });
  await waitFor(() => expect(button).not.toBeDisabled());
  fireEvent.click(button);
  return screen.getByRole("dialog", { name: "Add client" });
}

function fillAdd(dialog: HTMLElement, values: { slug?: string; url?: string; controlKey?: string }) {
  if (values.slug != null) fireEvent.change(within(dialog).getByLabelText("Slug"), { target: { value: values.slug } });
  if (values.url != null) {
    fireEvent.change(within(dialog).getByLabelText("Instance URL"), { target: { value: values.url } });
  }
  if (values.controlKey != null) {
    fireEvent.change(within(dialog).getByLabelText(/Control key/), { target: { value: values.controlKey } });
  }
}

async function openDetail(slug: string) {
  fireEvent.click(await screen.findByRole("heading", { name: slug }));
  return screen.getByRole("dialog", { name: `Client ${slug}` });
}

// ── Union list ────────────────────────────────────────────────────────────────

describe("fleet list", () => {
  it("is the union of registry and heartbeat clients, with waiting and not-registered states", async () => {
    renderWithClient(<FleetPage />);
    const waiting = await screen.findByTestId("waiting-client-card");
    expect(screen.getAllByRole("heading", { level: 3 }).map((h) => h.textContent)).toEqual([
      "acme",
      "newco",
      "oldco",
    ]);
    expect(waiting).toHaveTextContent("newco");
    expect(waiting).toHaveTextContent("Waiting for first heartbeat");
    expect(within(waiting).getByTestId("fleet-licence-badge")).toHaveTextContent("Licence: Unknown");

    const hints = screen.getAllByTestId("not-registered-hint");
    expect(hints).toHaveLength(1);
    expect(hints[0]).toHaveTextContent("Not registered -- add it to push licences");
    expect(hints[0].closest("div.bg-surface-card")).toHaveTextContent("oldco");
    expect(screen.getByText(/3 instances/)).toBeInTheDocument();
  });

  it("claims nothing about registration when the registry is unavailable", async () => {
    delete routes["GET /mc/ops/fleet/clients"];
    renderWithClient(<FleetPage />);
    expect(await screen.findByText(/Client registry unavailable \(404/)).toBeInTheDocument();
    expect(screen.queryAllByTestId("not-registered-hint")).toHaveLength(0);
    expect(screen.getByRole("button", { name: "Add client" })).toBeDisabled();
    expect(screen.getAllByRole("heading", { level: 3 }).map((h) => h.textContent)).toEqual(["acme", "oldco"]);
  });

  it("prefills only the slug from a not-registered heartbeat; the reported URL is unverified text", async () => {
    renderWithClient(<FleetPage />);
    const hint = await screen.findByTestId("not-registered-hint");
    fireEvent.click(within(hint).getByRole("button", { name: "Register" }));
    const dialog = screen.getByRole("dialog", { name: "Add client" });
    expect(within(dialog).getByLabelText("Slug")).toHaveValue("oldco");
    // The heartbeat URL is client-controlled: never a form value, never a link.
    expect(within(dialog).getByLabelText("Instance URL")).toHaveValue("");
    const reported = within(dialog).getByTestId("reported-url-hint");
    expect(reported).toHaveTextContent(
      "Reported by the instance (unverified) -- type the address yourself: https://oldco.nexus.intodata.nl",
    );
    expect(within(reported).queryByRole("link")).toBeNull();
    expect(screen.queryByRole("dialog", { name: /Client oldco/ })).toBeNull();
  });

  it("renders one malformed snapshot as an invalid card and keeps the rest of the fleet", async () => {
    routes["GET /mc/ops/snapshots"] = {
      status: 200,
      body: { acme: snapshot("acme"), broken: { client_slug: "broken", totals: "not an object" } },
    };
    renderWithClient(<FleetPage />);
    const invalid = await screen.findByTestId("invalid-snapshot-card");
    expect(invalid).toHaveTextContent("broken");
    expect(invalid).toHaveTextContent("Invalid heartbeat data");
    expect(within(invalid).getByTestId("fleet-licence-badge")).toHaveTextContent("Licence: Unknown");
    expect(screen.getAllByRole("heading", { level: 3 }).map((h) => h.textContent)).toEqual([
      "acme",
      "broken",
      "newco",
    ]);
    expect(screen.queryByText(/Mission Control unreachable/)).toBeNull();

    fireEvent.click(within(invalid).getByRole("heading", { name: "broken" }));
    const panel = screen.getByRole("dialog", { name: "Client broken" });
    expect(within(panel).getByTestId("invalid-heartbeat")).toHaveTextContent("Invalid heartbeat data");
  });

  it("links a registered client to its registry URL, never the heartbeat's", async () => {
    routes["GET /mc/ops/snapshots"] = {
      status: 200,
      body: { acme: snapshot("acme", { mcp_url: "https://attacker.example" }) },
    };
    renderWithClient(<FleetPage />);
    const link = await screen.findByRole("link", { name: /Open/ });
    expect(link).toHaveAttribute("href", "https://acme.nexus.intodata.nl/");
    expect(document.body.innerHTML).not.toContain("attacker.example");
  });

  it("shows a non-https heartbeat URL as plain text, not a link", async () => {
    routes["GET /mc/ops/snapshots"] = {
      status: 200,
      body: { oldco: snapshot("oldco", { mcp_url: "javascript:alert(1)" }) },
    };
    renderWithClient(<FleetPage />);
    const text = await screen.findByTestId("instance-url-text");
    expect(text).toHaveTextContent("javascript:alert(1)");
    expect(screen.queryByRole("link")).toBeNull();

    const panel = await openDetail("oldco");
    expect(within(panel).getByTestId("instance-url-text")).toHaveTextContent("javascript:alert(1)");
    expect(within(panel).queryByRole("link")).toBeNull();
  });

  it("warns and disables changes when the registry file could not be read", async () => {
    routes["GET /mc/ops/fleet/clients"] = {
      status: 200,
      body: { clients: [registryClient("acme")], telemetry_base_url: TELEMETRY_URL, registry_unavailable: true },
    };
    renderWithClient(<FleetPage />);
    expect(await screen.findByTestId("registry-unavailable-banner")).toHaveTextContent(
      "The Fleet registry file could not be read; registered clients may be missing and changes are disabled.",
    );
    expect(screen.getByRole("button", { name: "Add client" })).toBeDisabled();
    // Registration is unknown, so no "not registered" claim and no Register button.
    expect(screen.queryAllByTestId("not-registered-hint")).toHaveLength(0);

    const panel = await openDetail("acme");
    fireEvent.click(within(panel).getByRole("button", { name: "Settings" }));
    expect(within(panel).getByText("The Fleet registry file could not be read; changes are disabled.")).toBeInTheDocument();
    expect(within(panel).queryByRole("button", { name: /Save URL|Rotate heartbeat key|Remove client/ })).toBeNull();
  });
});

describe("safeInstanceHref", () => {
  it("allows https and loopback http only", () => {
    expect(safeInstanceHref("https://acme.nexus.intodata.nl")).toBe("https://acme.nexus.intodata.nl/");
    expect(safeInstanceHref("http://localhost:8002")).toBe("http://localhost:8002/");
    expect(safeInstanceHref("http://acme.example")).toBeNull();
    expect(safeInstanceHref("javascript:alert(1)")).toBeNull();
    expect(safeInstanceHref("data:text/html,x")).toBeNull();
    expect(safeInstanceHref("https://user:pw@acme.example")).toBeNull();
    expect(safeInstanceHref("not a url")).toBeNull();
    expect(safeInstanceHref(null)).toBeNull();
  });
});

// ── Add dialog ────────────────────────────────────────────────────────────────

describe("add client dialog", () => {
  it("mirrors the server's slug and URL rules", () => {
    expect(slugProblem("acme-2")).toBeNull();
    expect(slugProblem("Acme")).not.toBeNull();
    expect(slugProblem("-acme")).not.toBeNull();
    expect(slugProblem("a".repeat(64))).not.toBeNull();
    expect(instanceUrlProblem("https://acme.nexus.intodata.nl")).toBeNull();
    expect(instanceUrlProblem("http://localhost:8002")).toBeNull();
    expect(instanceUrlProblem("http://127.0.0.1:8002")).toBeNull();
    expect(instanceUrlProblem("http://acme.nexus.intodata.nl")).toMatch(/https/);
    expect(instanceUrlProblem("https://user:pw@acme.example")).toMatch(/credentials/);
    expect(instanceUrlProblem("https://acme.example/?x=1")).toMatch(/query/);
    expect(instanceUrlProblem("ftp://acme.example")).toMatch(/https/);
    expect(instanceUrlProblem("not a url")).toMatch(/valid/);
  });

  it("blocks invalid input client-side and shows the placeholder and helper", async () => {
    renderWithClient(<FleetPage />);
    const dialog = await openAddDialog();
    expect(within(dialog).getByLabelText("Instance URL")).toHaveAttribute(
      "placeholder",
      "https://<client>.nexus.intodata.nl",
    );
    const controlKey = within(dialog).getByLabelText(/Control key/);
    expect(controlKey).toHaveAttribute("type", "password");
    expect(dialog).toHaveTextContent(
      "The client's MCP_OPS_CONTROL_KEY, from /etc/intodata-kb/envs/<client>.env on its server; you can add it later.",
    );

    fillAdd(dialog, { slug: "Bad Slug", url: "http://acme.nexus.intodata.nl" });
    fireEvent.click(within(dialog).getByRole("button", { name: "Add client" }));
    expect(within(dialog).getByText(/lowercase letters, digits or hyphens/)).toBeInTheDocument();
    expect(within(dialog).getByText(/Must use https:\/\//)).toBeInTheDocument();
    expect(calls("POST")).toHaveLength(0);
  });

  it("posts the exact body, control_key null when left empty", async () => {
    routes["POST /mc/ops/fleet/clients"] = {
      status: 201,
      body: { client: registryClient("zeta"), telemetry_key: TELEMETRY_KEY, client_env: clientEnv("zeta", TELEMETRY_KEY) },
    };
    renderWithClient(<FleetPage />);
    const dialog = await openAddDialog();
    fillAdd(dialog, { slug: " zeta ", url: "https://zeta.nexus.intodata.nl " });
    fireEvent.click(within(dialog).getByRole("button", { name: "Add client" }));
    await waitFor(() => expect(calls("POST")).toHaveLength(1));
    expect(calls("POST")[0][0]).toBe("/mc/ops/fleet/clients");
    expect(bodyOf(calls("POST")[0])).toEqual({
      slug: "zeta",
      mcp_url: "https://zeta.nexus.intodata.nl",
      control_key: null,
    });
    await screen.findByTestId("connect-panel");
  });

  it("sends the control key when given", async () => {
    routes["POST /mc/ops/fleet/clients"] = {
      status: 201,
      body: { client: registryClient("zeta"), telemetry_key: TELEMETRY_KEY, client_env: clientEnv("zeta", TELEMETRY_KEY) },
    };
    renderWithClient(<FleetPage />);
    const dialog = await openAddDialog();
    fillAdd(dialog, { slug: "zeta", url: "https://zeta.nexus.intodata.nl", controlKey: CONTROL_KEY });
    fireEvent.click(within(dialog).getByRole("button", { name: "Add client" }));
    await waitFor(() => expect(calls("POST")).toHaveLength(1));
    expect(bodyOf(calls("POST")[0])).toEqual({
      slug: "zeta",
      mcp_url: "https://zeta.nexus.intodata.nl",
      control_key: CONTROL_KEY,
    });
  });

  it("shows a 409 client_exists as a sentence", async () => {
    routes["POST /mc/ops/fleet/clients"] = { status: 409, body: { detail: { code: "client_exists" } } };
    renderWithClient(<FleetPage />);
    const dialog = await openAddDialog();
    fillAdd(dialog, { slug: "acme", url: "https://acme.nexus.intodata.nl" });
    fireEvent.click(within(dialog).getByRole("button", { name: "Add client" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "Add failed: 409: A client with this slug is already registered",
    );
    expect(screen.queryByTestId("connect-panel")).toBeNull();
  });

  it("shows the server's 422 message", async () => {
    routes["POST /mc/ops/fleet/clients"] = {
      status: 422,
      body: { detail: { code: "insecure_url", message: "mcp_url must resolve to a public host" } },
    };
    renderWithClient(<FleetPage />);
    const dialog = await openAddDialog();
    fillAdd(dialog, { slug: "zeta", url: "https://zeta.nexus.intodata.nl" });
    fireEvent.click(within(dialog).getByRole("button", { name: "Add client" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "Add failed: 422: mcp_url must resolve to a public host",
    );
  });
});

// ── One-time connect panel ────────────────────────────────────────────────────

describe("one-time connect panel", () => {
  it("shows the key-file deploy commands and env lines once, and never stores the keys", async () => {
    routes["POST /mc/ops/fleet/clients"] = {
      status: 201,
      body: { client: registryClient("zeta"), telemetry_key: TELEMETRY_KEY, client_env: clientEnv("zeta", TELEMETRY_KEY) },
    };
    const { client, unmount } = renderWithClient(<FleetPage />);
    const dialog = await openAddDialog();
    fillAdd(dialog, { slug: "zeta", url: "https://zeta.nexus.intodata.nl", controlKey: CONTROL_KEY });
    fireEvent.click(within(dialog).getByRole("button", { name: "Add client" }));

    const panel = await screen.findByTestId("connect-panel");
    expect(within(panel).getByLabelText("Env lines")).toHaveValue(
      `MCP_OPS_TELEMETRY_URL=${TELEMETRY_URL}\nMCP_OPS_TELEMETRY_API_KEY=${TELEMETRY_KEY}\nMCP_OPS_CLIENT_SLUG=zeta`,
    );
    const deploy = String((within(panel).getByLabelText("Deploy commands") as HTMLTextAreaElement).value);
    expect(deploy).toBe(
      `printf '%s' '${TELEMETRY_KEY}' | sudo tee /etc/intodata-kb/ops-telemetry.key >/dev/null && ` +
        `sudo chmod 600 /etc/intodata-kb/ops-telemetry.key\n` +
        `deploy-client.sh --client-name zeta --ops-telemetry-url ${TELEMETRY_URL} ` +
        `--ops-telemetry-key-file /etc/intodata-kb/ops-telemetry.key`,
    );
    // The key is never a deploy-client.sh argument (ps, shell history, sudo logs).
    expect(deploy).not.toContain("--ops-telemetry-key ");
    expect(deploy.split("\n")[1]).not.toContain(TELEMETRY_KEY);
    expect(panel).toHaveTextContent(
      "This key is shown once. Paste it on the client's server, then redeploy/restart its mcp-server.",
    );

    // Neither the heartbeat key nor the submitted control key is held by React Query or storage.
    await waitFor(() => {
      const stored = everythingStored(client);
      expect(stored).not.toContain(TELEMETRY_KEY);
      expect(stored).not.toContain(CONTROL_KEY);
    });

    fireEvent.click(within(panel).getByRole("button", { name: /close/i }));
    expect(screen.queryByTestId("connect-panel")).toBeNull();
    expect(document.body.innerHTML).not.toContain(TELEMETRY_KEY);

    unmount();
    expect(everythingStored(client)).not.toContain(TELEMETRY_KEY);
  });

  it("is gone when its owner unmounts", async () => {
    routes["POST /mc/ops/fleet/clients"] = {
      status: 201,
      body: { client: registryClient("zeta"), telemetry_key: TELEMETRY_KEY, client_env: clientEnv("zeta", TELEMETRY_KEY) },
    };
    const { client, unmount } = renderWithClient(<FleetPage />);
    const dialog = await openAddDialog();
    fillAdd(dialog, { slug: "zeta", url: "https://zeta.nexus.intodata.nl" });
    fireEvent.click(within(dialog).getByRole("button", { name: "Add client" }));
    await screen.findByTestId("connect-panel");
    unmount();
    expect(document.body.innerHTML).not.toContain(TELEMETRY_KEY);
    expect(everythingStored(client)).not.toContain(TELEMETRY_KEY);
  });

  it("says to set MC_PUBLIC_URL when the telemetry URL is unknown", async () => {
    routes["POST /mc/ops/fleet/clients"] = {
      status: 201,
      body: {
        client: registryClient("zeta"),
        telemetry_key: TELEMETRY_KEY,
        client_env: clientEnv("zeta", TELEMETRY_KEY, null),
      },
    };
    renderWithClient(<FleetPage />);
    const dialog = await openAddDialog();
    fillAdd(dialog, { slug: "zeta", url: "https://zeta.nexus.intodata.nl" });
    fireEvent.click(within(dialog).getByRole("button", { name: "Add client" }));
    const panel = await screen.findByTestId("connect-panel");
    expect(within(panel).getByRole("alert")).toHaveTextContent(
      "Set MC_PUBLIC_URL on this Mission Control to fill this in",
    );
    const envLines = String((within(panel).getByLabelText("Env lines") as HTMLTextAreaElement).value);
    expect(envLines).not.toContain("null");
    expect(envLines).not.toMatch(/^MCP_OPS_TELEMETRY_URL=/m);
    expect(envLines).toContain(`MCP_OPS_TELEMETRY_API_KEY=${TELEMETRY_KEY}`);
  });
});

// ── Detail panel: registry-only clients and settings ──────────────────────────

describe("client detail", () => {
  it("opens for a registry-only client, with a working Licence tab", async () => {
    renderWithClient(<FleetPage currentUsername="operator1" />);
    fireEvent.click(await screen.findByTestId("waiting-client-card"));
    const panel = screen.getByRole("dialog", { name: "Client newco" });
    expect(panel).toHaveTextContent("No heartbeat yet");
    expect(within(panel).getByTestId("waiting-heartbeat")).toHaveTextContent("Waiting for first heartbeat.");
    expect(within(panel).queryByRole("button", { name: /Projects/ })).toBeNull();

    fireEvent.click(within(panel).getByRole("button", { name: "Licence" }));
    expect(within(panel).getByTestId("licence-card")).toHaveTextContent("Unknown / instance not reporting licence");
    expect(await within(panel).findByText("No licences issued for this client yet.")).toBeInTheDocument();
    expect(await within(panel).findByRole("button", { name: "Issue and push" })).toBeInTheDocument();
  });

  it("warns about the legacy shared key and a missing control key", async () => {
    routes["GET /mc/ops/fleet/clients"] = {
      status: 200,
      body: {
        clients: [registryClient("newco", { heartbeat_auth: "shared_key_only", has_control_key: false })],
        telemetry_base_url: TELEMETRY_URL,
      },
    };
    renderWithClient(<FleetPage />);
    const panel = await openDetail("newco");
    fireEvent.click(within(panel).getByRole("button", { name: "Settings" }));
    expect(panel).toHaveTextContent("Last heartbeat: never");
    expect(panel).toHaveTextContent("Using shared key (legacy)");
    expect(panel).toHaveTextContent("No control key -- licence push disabled");
    expect(within(panel).getByTestId("control-key-state")).toHaveTextContent("not configured");
  });

  it("saves the instance URL and a control key, keeping the key out of every cache", async () => {
    routes["PATCH /mc/ops/fleet/clients/newco"] = { status: 200, body: { client: registryClient("newco") } };
    const { client } = renderWithClient(<FleetPage />);
    const panel = await openDetail("newco");
    fireEvent.click(within(panel).getByRole("button", { name: "Settings" }));
    expect(within(panel).getByTestId("control-key-state")).toHaveTextContent("configured");

    fireEvent.change(within(panel).getByLabelText("Instance URL"), {
      target: { value: "https://newco2.nexus.intodata.nl" },
    });
    fireEvent.click(within(panel).getByRole("button", { name: "Save URL" }));
    await waitFor(() => expect(calls("PATCH")).toHaveLength(1));
    expect(calls("PATCH")[0][0]).toBe("/mc/ops/fleet/clients/newco");
    expect(bodyOf(calls("PATCH")[0])).toEqual({ mcp_url: "https://newco2.nexus.intodata.nl" });
    expect(await within(panel).findByText("Instance URL saved.")).toBeInTheDocument();

    const keyInput = within(panel).getByLabelText(/^Replace control key/);
    expect(keyInput).toHaveAttribute("type", "password");
    fireEvent.change(keyInput, { target: { value: CONTROL_KEY } });
    fireEvent.click(within(panel).getByRole("button", { name: "Replace key" }));
    await waitFor(() => expect(calls("PATCH")).toHaveLength(2));
    expect(bodyOf(calls("PATCH")[1])).toEqual({ control_key: CONTROL_KEY });
    expect(await within(panel).findByText("Control key saved.")).toBeInTheDocument();
    expect(keyInput).toHaveValue("");
    await waitFor(() => expect(everythingStored(client)).not.toContain(CONTROL_KEY));
  });

  it("shows a managed_by_file conflict from the server", async () => {
    routes["PATCH /mc/ops/fleet/clients/newco"] = { status: 409, body: { detail: { code: "managed_by_file" } } };
    renderWithClient(<FleetPage />);
    const panel = await openDetail("newco");
    fireEvent.click(within(panel).getByRole("button", { name: "Settings" }));
    fireEvent.change(within(panel).getByLabelText("Instance URL"), {
      target: { value: "https://newco2.nexus.intodata.nl" },
    });
    fireEvent.click(within(panel).getByRole("button", { name: "Save URL" }));
    expect(await within(panel).findByRole("alert")).toHaveTextContent(
      "Save failed: 409: This client is managed by clients.json and cannot be changed here",
    );
  });

  it("rotates the heartbeat key only after confirmation, then shows the one-time panel", async () => {
    routes["POST /mc/ops/fleet/clients/newco/telemetry-key"] = {
      status: 200,
      body: { telemetry_key: ROTATED_KEY, client_env: clientEnv("newco", ROTATED_KEY) },
    };
    const { client } = renderWithClient(<FleetPage />);
    const panel = await openDetail("newco");
    fireEvent.click(within(panel).getByRole("button", { name: "Settings" }));

    fireEvent.click(within(panel).getByRole("button", { name: "Rotate heartbeat key" }));
    const confirm = within(panel).getByRole("group", { name: "Confirm key rotation" });
    fireEvent.click(within(confirm).getByRole("button", { name: "Cancel" }));
    expect(calls("POST")).toHaveLength(0);

    fireEvent.click(within(panel).getByRole("button", { name: "Rotate heartbeat key" }));
    fireEvent.click(
      within(within(panel).getByRole("group", { name: "Confirm key rotation" })).getByRole("button", {
        name: "Rotate",
      }),
    );
    await waitFor(() => expect(calls("POST")).toHaveLength(1));
    expect(calls("POST")[0][0]).toBe("/mc/ops/fleet/clients/newco/telemetry-key");

    const connect = await within(panel).findByTestId("connect-panel");
    expect(within(connect).getByLabelText("Env lines")).toHaveValue(
      `MCP_OPS_TELEMETRY_URL=${TELEMETRY_URL}\nMCP_OPS_TELEMETRY_API_KEY=${ROTATED_KEY}\nMCP_OPS_CLIENT_SLUG=newco`,
    );
    await waitFor(() => expect(everythingStored(client)).not.toContain(ROTATED_KEY));

    fireEvent.click(within(connect).getByRole("button", { name: /close/i }));
    expect(within(panel).queryByTestId("connect-panel")).toBeNull();
    expect(document.body.innerHTML).not.toContain(ROTATED_KEY);
  });

  it("removes a client only after confirmation, explaining history is kept", async () => {
    routes["DELETE /mc/ops/fleet/clients/newco"] = { status: 204 };
    renderWithClient(<FleetPage />);
    const panel = await openDetail("newco");
    fireEvent.click(within(panel).getByRole("button", { name: "Settings" }));

    fireEvent.click(within(panel).getByRole("button", { name: "Remove client" }));
    const confirm = within(panel).getByRole("group", { name: "Confirm client removal" });
    expect(confirm).toHaveTextContent("heartbeat snapshots and issued-licence history are kept");
    fireEvent.click(within(confirm).getByRole("button", { name: "Cancel" }));
    expect(calls("DELETE")).toHaveLength(0);

    fireEvent.click(within(panel).getByRole("button", { name: "Remove client" }));
    fireEvent.click(
      within(within(panel).getByRole("group", { name: "Confirm client removal" })).getByRole("button", {
        name: "Remove",
      }),
    );
    await waitFor(() => expect(calls("DELETE")).toHaveLength(1));
    expect(calls("DELETE")[0][0]).toBe("/mc/ops/fleet/clients/newco");
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "Client newco" })).toBeNull());
    // The list is refetched after the removal.
    await waitFor(() =>
      expect(fetchMock.mock.calls.filter(([url]) => url === "/mc/ops/fleet/clients").length).toBeGreaterThan(1),
    );
  });

  it("shows a file-sourced client read-only", async () => {
    routes["GET /mc/ops/fleet/clients"] = {
      status: 200,
      body: {
        clients: [registryClient("acme", { source: "file", last_heartbeat_at: new Date().toISOString() })],
        telemetry_base_url: TELEMETRY_URL,
      },
    };
    renderWithClient(<FleetPage />);
    const panel = await openDetail("acme");
    fireEvent.click(within(panel).getByRole("button", { name: "Settings" }));
    expect(panel).toHaveTextContent("Managed by clients.json");
    expect(panel).toHaveTextContent("Instance URL: https://acme.nexus.intodata.nl");
    expect(within(panel).queryByRole("button", { name: "Save URL" })).toBeNull();
    expect(within(panel).queryByRole("button", { name: /key/i })).toBeNull();
    expect(within(panel).queryByRole("button", { name: "Remove client" })).toBeNull();
    expect(within(panel).queryAllByRole("textbox")).toHaveLength(0);
  });

  it("says a heartbeat-only client is not registered in its Settings tab", async () => {
    renderWithClient(<FleetPage />);
    const panel = await openDetail("oldco");
    fireEvent.click(within(panel).getByRole("button", { name: "Settings" }));
    expect(panel).toHaveTextContent("oldco is not registered");
  });
});
