import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ReactElement } from "react";
import FleetPage from "../components/FleetPage";
import FleetLicencePanel, { LicenceCard } from "../components/FleetLicencePanel";
import { fleetLicenceBadge } from "../components/FleetLicenceBadge";
import { ClientSnapshotSchema, FleetLicenceSchema, LicencePushResultSchema, type FleetLicence } from "../api/fleet";

const VALID: FleetLicence = {
  state: "valid",
  reason: null,
  plan: "basic",
  features: ["chat", "generation"],
  limits: { generations_per_period: 3 },
  period_start: "2026-09-01T00:00:00Z",
  period_end: "2026-10-01T00:00:00Z",
  days_remaining: 20,
  near_expiry: false,
  enforcement: "enforce",
  usage: { generations_used: 2, generations_limit: 3 },
};

const PLANS = {
  plans: [
    { name: "basic", description: "Chat and docs", features: ["chat", "generation"], limits: { generations_per_period: 3 } },
    { name: "pro", description: "More generations", features: ["chat", "generation"], limits: { generations_per_period: 20 } },
  ],
  issuing_enabled: true,
};

const HISTORY = {
  licences: [
    {
      licence_id: "lic-001",
      client: "acme",
      plan: "basic",
      period_start: "2026-09-01T00:00:00Z",
      period_end: "2026-10-01T00:00:00Z",
      issued_at: "2026-09-01T08:00:00Z",
      issued_by: "operator1",
      push_ok: true,
      push_status_code: 200,
    },
    {
      licence_id: "lic-002",
      client: "acme",
      plan: "pro",
      period_start: "2026-10-01T00:00:00Z",
      period_end: "2027-01-01T00:00:00Z",
      issued_at: "2026-09-20T08:00:00Z",
      issued_by: null,
      push_ok: false,
      push_status_code: 503,
    },
  ],
};

const TOKEN = "eyJhbGciOiJFZERTQSJ9.SECRET-TOKEN-BODY.sig";

function issued(overrides: Record<string, unknown> = {}) {
  return {
    licence_id: "lic-003",
    client: "acme",
    plan: "pro",
    period_start: "2026-10-01T00:00:00Z",
    period_end: "2027-01-01T00:00:00Z",
    issued_at: "2026-09-28T10:00:00Z",
    issued_by: "operator1",
    push: { attempted: true, ok: true, status_code: 200, detail: null, instance_licence: { state: "valid" } },
    ...overrides,
  };
}

type Route = { status: number; body: unknown };
let routes: Record<string, Route>;
let fetchMock: ReturnType<typeof vi.fn>;

function json(route: Route): Response {
  return new Response(JSON.stringify(route.body), {
    status: route.status,
    headers: { "Content-Type": "application/json" },
  });
}

beforeEach(() => {
  routes = {
    "GET /mc/ops/licensing/plans": { status: 200, body: PLANS },
    "GET /mc/ops/clients/acme/licences": { status: 200, body: HISTORY },
    "POST /mc/ops/clients/acme/licences": { status: 201, body: issued() },
  };
  fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const key = `${init?.method ?? "GET"} ${String(input)}`;
    const route = routes[key];
    if (!route) return json({ status: 404, body: { detail: `no route ${key}` } });
    return json(route);
  });
  vi.stubGlobal("fetch", fetchMock);
  localStorage.clear();
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

function postCalls() {
  return fetchMock.mock.calls.filter(([, init]) => (init as RequestInit | undefined)?.method === "POST");
}

async function openIssueConfirm() {
  const issueButton = await screen.findByRole("button", { name: /^Issue( and push)?$/ });
  await waitFor(() => expect(issueButton).not.toBeDisabled());
  fireEvent.click(issueButton);
  return screen.getByRole("group", { name: "Confirm licence issue" });
}

// ── Badge ─────────────────────────────────────────────────────────────────────

describe("fleet licence badge", () => {
  it.each([
    [VALID, "Valid (20 days)"],
    [{ ...VALID, days_remaining: 1 }, "Valid (1 day)"],
    [{ ...VALID, near_expiry: true, days_remaining: 5 }, "Expiring (5 days)"],
    [{ ...VALID, state: "expired" as const }, "Expired"],
    [{ ...VALID, state: "missing" as const }, "Missing"],
    [{ ...VALID, state: "invalid" as const }, "Invalid"],
    [{ ...VALID, state: "unknown" as const }, "Unknown"],
    [null, "Unknown"],
    [undefined, "Unknown"],
  ])("labels %# as %s", (licence, label) => {
    expect(fleetLicenceBadge(licence).label).toBe(label);
  });

  it("renders a badge per client in the fleet list, Unknown when the instance reports no licence", async () => {
    const snapshot = (slug: string, licence?: unknown) => ({
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
      ...(licence === undefined ? {} : { licence }),
    });
    routes["GET /mc/ops/snapshots"] = {
      status: 200,
      body: {
        acme: snapshot("acme", { ...VALID, near_expiry: true, days_remaining: 4 }),
        oldco: snapshot("oldco"),
      },
    };
    renderWithClient(<FleetPage />);
    const badges = await screen.findAllByTestId("fleet-licence-badge");
    expect(badges.map((b) => b.textContent)).toEqual(["Licence: Expiring (4 days)", "Licence: Unknown"]);
  });
});

// ── Card ──────────────────────────────────────────────────────────────────────

describe("licence card", () => {
  it("renders state, plan, period, days remaining and usage", () => {
    render(<LicenceCard licence={VALID} />);
    const card = screen.getByTestId("licence-card");
    expect(card).toHaveTextContent("valid");
    expect(card).toHaveTextContent("basic");
    expect(card).toHaveTextContent("2026-09-01 to 2026-10-01");
    expect(card).toHaveTextContent("20");
    expect(card).toHaveTextContent("Generations: 2/3 this period");
    expect(within(card).queryByRole("note")).toBeNull();
  });

  it("renders near-expiry and expired states", () => {
    const { rerender } = render(<LicenceCard licence={{ ...VALID, near_expiry: true, days_remaining: 3 }} />);
    expect(screen.getByTestId("licence-card")).toHaveTextContent("Expiring (3 days)");
    rerender(<LicenceCard licence={{ ...VALID, state: "expired", days_remaining: 0 }} />);
    const card = screen.getByTestId("licence-card");
    expect(card).toHaveTextContent("Licence: Expired");
    expect(card).toHaveTextContent("expired on 2026-10-01");
  });

  it("warns when enforcement is off", () => {
    render(<LicenceCard licence={{ ...VALID, enforcement: "off" }} />);
    expect(screen.getByRole("note")).toHaveTextContent("Enforcement is off");
  });

  it("shows the missing-licence state", () => {
    render(<LicenceCard licence={{ ...VALID, state: "missing", plan: null, usage: null }} />);
    const card = screen.getByTestId("licence-card");
    expect(card).toHaveTextContent("Licence: Missing");
    expect(card).toHaveTextContent("No licence is installed on this instance.");
    expect(card).toHaveTextContent("Generations: not reported");
  });

  it("parses the instance's bare unknown fallback and says so, instead of 'older build'", () => {
    const parsed = FleetLicenceSchema.parse({ state: "unknown" });
    expect(parsed.enforcement).toBeUndefined();
    const snapshot = ClientSnapshotSchema.parse({
      schema_version: "1",
      client_slug: "acme",
      environment: "prod",
      sent_at: "2026-09-28T10:00:00Z",
      health: {},
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
      licence: { state: "unknown" },
    });
    expect(snapshot.licence?.state).toBe("unknown");
    render(<LicenceCard licence={snapshot.licence} />);
    const card = screen.getByTestId("licence-card");
    expect(card).toHaveTextContent("Instance reported an unknown licence state");
    expect(card).toHaveTextContent("Licence: Unknown");
    expect(card).not.toHaveTextContent("does not include licence state");
    expect(within(card).queryByRole("note")).toBeNull();
  });

  it("shows an explicit block when the instance does not report a licence", () => {
    render(<LicenceCard licence={undefined} />);
    expect(screen.getByTestId("licence-card")).toHaveTextContent("Unknown / instance not reporting licence");
  });
});

// ── Issue form ────────────────────────────────────────────────────────────────

describe("licence issue form", () => {
  it("requires confirmation before posting, and Cancel posts nothing", async () => {
    renderWithClient(<FleetLicencePanel slug="acme" licence={VALID} currentUsername="operator1" />);
    const confirm = await openIssueConfirm();
    expect(within(confirm).getByTestId("issue-confirm-summary")).toHaveTextContent(
      "Issue a basic licence for acme: 1 month, starting now. It will be pushed to the instance.",
    );
    expect(postCalls()).toHaveLength(0);
    fireEvent.click(within(confirm).getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("group", { name: "Confirm licence issue" })).toBeNull();
    expect(postCalls()).toHaveLength(0);
  });

  it("posts the exact body with push on", async () => {
    renderWithClient(<FleetLicencePanel slug="acme" licence={VALID} currentUsername="operator1" />);
    await screen.findByRole("option", { name: /pro/ });
    fireEvent.change(screen.getByLabelText("Plan"), { target: { value: "pro" } });
    fireEvent.click(screen.getByLabelText("3 months"));
    fireEvent.click(screen.getByLabelText(/When the current licence ends/));
    const confirm = await openIssueConfirm();
    expect(within(confirm).getByTestId("issue-confirm-summary")).toHaveTextContent(
      "Issue a pro licence for acme: 3 months, starting after the last issued licence ends (instance reports 2026-10-01).",
    );
    fireEvent.click(within(confirm).getByRole("button", { name: "Confirm" }));
    await waitFor(() => expect(postCalls()).toHaveLength(1));
    const [url, init] = postCalls()[0] as [string, RequestInit];
    expect(url).toBe("/mc/ops/clients/acme/licences");
    expect(JSON.parse(String(init.body))).toEqual({
      plan: "pro",
      months: 3,
      start_after_current: true,
      push: true,
      return_token: false,
      issued_by: "operator1",
    });
    const result = await screen.findByTestId("issue-result");
    expect(result).toHaveTextContent("Licence lic-003 issued (pro): 2026-10-01 to 2027-01-01.");
    expect(result).toHaveTextContent("Pushed to instance (instance reports: valid).");
    expect(screen.queryByRole("textbox", { name: /manual install/ })).toBeNull();
  });

  it("posts push false and return_token true when manual install is requested", async () => {
    routes["POST /mc/ops/clients/acme/licences"] = {
      status: 201,
      body: issued({ push: { attempted: false, ok: null, status_code: null, detail: null }, token: TOKEN }),
    };
    const { client } = renderWithClient(
      <FleetLicencePanel slug="acme" licence={VALID} currentUsername="operator1" />,
    );
    fireEvent.click(await screen.findByLabelText("Push to instance"));
    fireEvent.click(screen.getByLabelText("Show licence for manual install"));
    const confirm = await openIssueConfirm();
    fireEvent.click(within(confirm).getByRole("button", { name: "Confirm" }));
    await waitFor(() => expect(postCalls()).toHaveLength(1));
    expect(JSON.parse(String((postCalls()[0][1] as RequestInit).body))).toEqual({
      plan: "basic",
      months: 1,
      start_after_current: false,
      push: false,
      return_token: true,
      issued_by: "operator1",
    });

    const box = await screen.findByRole("textbox", { name: /manual install/ });
    expect(box).toHaveValue(TOKEN);
    expect(box).toHaveAttribute("readonly");
    expect(screen.getByText(/Bound to client acme/)).toBeInTheDocument();
    expect(screen.getByTestId("issue-result")).toHaveTextContent("Not pushed to the instance.");

    // The token lives in component state only: not in any cache, not in storage.
    const mutationData = client.getMutationCache().getAll().map((m) => m.state.data);
    const queryData = client.getQueryCache().getAll().map((q) => q.state.data);
    expect(JSON.stringify([mutationData, queryData])).not.toContain(TOKEN);
    expect(JSON.stringify({ ...localStorage })).not.toContain(TOKEN);
    expect(JSON.stringify({ ...sessionStorage })).not.toContain(TOKEN);
  });

  it("does not render a token area when none was returned", async () => {
    routes["POST /mc/ops/clients/acme/licences"] = {
      status: 201,
      body: issued({ push: { attempted: false, ok: null, status_code: null, detail: null } }),
    };
    renderWithClient(<FleetLicencePanel slug="acme" licence={VALID} currentUsername="operator1" />);
    fireEvent.click(await screen.findByLabelText("Push to instance"));
    const confirm = await openIssueConfirm();
    fireEvent.click(within(confirm).getByRole("button", { name: "Confirm" }));
    await screen.findByTestId("issue-result");
    expect(screen.queryByRole("textbox", { name: /manual install/ })).toBeNull();
    expect(JSON.parse(String((postCalls()[0][1] as RequestInit).body)).return_token).toBe(false);
  });

  it("says the licence was queued as next when the instance put it in the next slot", async () => {
    routes["POST /mc/ops/clients/acme/licences"] = {
      status: 201,
      body: issued({
        push: {
          attempted: true,
          ok: true,
          status_code: 200,
          detail: null,
          instance_licence: VALID,
          slot: "next",
          next_licence: { ...VALID, period_start: "2026-10-01T00:00:00Z", period_end: "2027-01-01T00:00:00Z" },
        },
      }),
    };
    renderWithClient(<FleetLicencePanel slug="acme" licence={VALID} currentUsername="operator1" />);
    const confirm = await openIssueConfirm();
    fireEvent.click(within(confirm).getByRole("button", { name: "Confirm" }));
    const result = await screen.findByTestId("issue-result");
    expect(result).toHaveTextContent("Queued as next licence (starts 2026-10-01).");
    expect(result).not.toHaveTextContent("instance reports");
  });

  it("keeps a push result with a drifted slot or next_licence, treating them as absent", () => {
    const parsed = LicencePushResultSchema.parse({
      attempted: true,
      ok: true,
      status_code: 200,
      slot: "later",
      next_licence: "garbage",
    });
    expect(parsed.slot).toBeNull();
    expect(parsed.next_licence).toBeNull();
  });

  it("shows a push failure with the status and the instance reason", async () => {
    routes["POST /mc/ops/clients/acme/licences"] = {
      status: 201,
      body: issued({
        push: {
          attempted: true,
          ok: false,
          status_code: 422,
          detail: { detail: "licence is for a different client" },
          instance_licence: null,
        },
      }),
    };
    renderWithClient(<FleetLicencePanel slug="acme" licence={VALID} currentUsername="operator1" />);
    const confirm = await openIssueConfirm();
    fireEvent.click(within(confirm).getByRole("button", { name: "Confirm" }));
    const result = await screen.findByTestId("issue-result");
    expect(within(result).getByRole("alert")).toHaveTextContent(
      "Push failed (HTTP 422): licence is for a different client",
    );
  });

  it("shows the Mission Control reason when the issue request fails", async () => {
    routes["POST /mc/ops/clients/acme/licences"] = { status: 404, body: { detail: "unknown client 'acme'" } };
    renderWithClient(<FleetLicencePanel slug="acme" licence={VALID} currentUsername="operator1" />);
    const confirm = await openIssueConfirm();
    fireEvent.click(within(confirm).getByRole("button", { name: "Confirm" }));
    expect(await screen.findByText(/Issue failed: 404: unknown client 'acme'/)).toBeInTheDocument();
  });

  it("maps a 503 issuing_disabled from the POST to the configuration sentence", async () => {
    routes["POST /mc/ops/clients/acme/licences"] = { status: 503, body: { detail: { code: "issuing_disabled" } } };
    renderWithClient(<FleetLicencePanel slug="acme" licence={VALID} currentUsername="operator1" />);
    const confirm = await openIssueConfirm();
    fireEvent.click(within(confirm).getByRole("button", { name: "Confirm" }));
    expect(
      await screen.findByText(/Issue failed: 503: Issuing is not configured on this Mission Control/),
    ).toBeInTheDocument();
  });

  it("disables the form when issuing is not enabled", async () => {
    routes["GET /mc/ops/licensing/plans"] = { status: 200, body: { ...PLANS, issuing_enabled: false } };
    renderWithClient(<FleetLicencePanel slug="acme" licence={VALID} currentUsername="operator1" />);
    expect(
      await screen.findByText("Issuing is not configured on this Mission Control (no signing key)"),
    ).toBeInTheDocument();
    expect(screen.getByLabelText("Plan")).toBeDisabled();
    expect(screen.getByRole("button", { name: "Issue and push" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Issue and push" }));
    expect(screen.queryByRole("group", { name: "Confirm licence issue" })).toBeNull();
    expect(postCalls()).toHaveLength(0);
  });

  it("disables renewal after the current licence when there is no valid licence", async () => {
    renderWithClient(
      <FleetLicencePanel slug="acme" licence={{ ...VALID, state: "expired" }} currentUsername="operator1" />,
    );
    await screen.findByRole("option", { name: /basic/ });
    expect(screen.getByLabelText(/When the current licence ends/)).toBeDisabled();
    expect(screen.getByLabelText("Now")).toBeChecked();
  });
});

// ── History ───────────────────────────────────────────────────────────────────

describe("licence history", () => {
  it("renders issued licences with push results", async () => {
    renderWithClient(<FleetLicencePanel slug="acme" licence={VALID} currentUsername="operator1" />);
    const table = await screen.findByRole("table", { name: "Licence history" });
    const rows = within(table).getAllByRole("row").slice(1);
    expect(rows).toHaveLength(2);
    expect(rows[0]).toHaveTextContent("2026-09-01basic2026-09-01 to 2026-10-01operator1ok");
    expect(rows[1]).toHaveTextContent("2026-09-20pro2026-10-01 to 2027-01-01-failed (503)");
  });
});
