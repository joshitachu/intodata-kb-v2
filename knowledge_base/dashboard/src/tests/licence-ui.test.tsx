import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ReactElement } from "react";
import GenerateDialog from "../components/GenerateDialog";
import ActionButtons from "../components/ActionButtons";
import { LicenceBannerView } from "../components/LicenceBanner";
import { LicenceContext } from "../hooks/useLicence";
import { api } from "../api/client";
import type { LicenceStatus } from "../lib/licence";
import type { Project } from "../types";

vi.mock("../api/client", () => ({
  api: {
    getGenerationPlan: vi.fn(),
    generate: vi.fn(),
    scan: vi.fn(),
    listModels: vi.fn(),
    listPluginPacks: vi.fn(),
    assignPluginPack: vi.fn(),
    listConnectorCatalog: vi.fn(),
    listConnectors: vi.fn(),
    listProjectConnectorBindings: vi.fn(),
    listRuntimeSources: vi.fn(),
    getRuntimeReadScope: vi.fn(),
  },
}));

const VALID: LicenceStatus = {
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

const project: Project = {
  id: "werkorder",
  display_name: "Werkorder",
  repo_url: null,
  local_path: "/tmp/werkorder",
  platform: "talend",
  doc_generation_provider: null,
  source_type: "zip",
  status: "ready",
  last_scan_at: null,
  last_generate_at: null,
  last_rag_push_at: null,
  job_count: 1,
  doc_count: 0,
  error_message: null,
  last_ingested_commit_sha: null,
  auto_reingest_enabled: false,
  auto_reingest_last_checked_at: null,
  auto_reingest_error: null,
  git_update_summary: null,
  has_github_token: false,
  connection_kind: "pat",
  github_account_login: null,
  webhook_status: null,
  instruction_pack_dir: null,
  plugin_pack_id: null,
  created_at: null,
  updated_at: null,
};

function renderWith(ui: ReactElement, status: LicenceStatus | null) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <LicenceContext.Provider value={{ status }}>{ui}</LicenceContext.Provider>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(api.listModels).mockResolvedValue({ models: [] });
  vi.mocked(api.listPluginPacks).mockResolvedValue([]);
  vi.mocked(api.listConnectorCatalog).mockResolvedValue({ providers: [] });
  vi.mocked(api.listConnectors).mockResolvedValue({ connectors: [], total: 0 });
  vi.mocked(api.listProjectConnectorBindings).mockResolvedValue({ project_id: "werkorder", bindings: [] });
  vi.mocked(api.listRuntimeSources).mockResolvedValue({ sources: [], total: 0 });
  vi.mocked(api.getGenerationPlan).mockRejectedValue(new Error("no plan"));
});

describe("dashboard licence banner", () => {
  it("renders nothing when enforcement is off", () => {
    const { container } = render(<LicenceBannerView status={{ ...VALID, state: "expired", enforcement: "off" }} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("renders nothing for a valid licence that is not near expiry", () => {
    expect(render(<LicenceBannerView status={VALID} />).container).toBeEmptyDOMElement();
  });

  it("warns with the days remaining near expiry", () => {
    render(<LicenceBannerView status={{ ...VALID, near_expiry: true, days_remaining: 5 }} />);
    expect(screen.getByTestId("licence-banner")).toHaveTextContent("expires in 5 days (2026-10-01)");
  });

  it("shows the view-only notice when expired", () => {
    render(<LicenceBannerView status={{ ...VALID, state: "expired" }} />);
    const banner = screen.getByRole("alert");
    expect(banner).toHaveTextContent("expired on 2026-10-01");
    expect(banner).toHaveTextContent("view-only");
    expect(banner).toHaveTextContent("remain readable");
  });
});

describe("generate and scan controls under a licence", () => {
  it("disables scan and generate with the reason when the licence has expired", () => {
    renderWith(
      <ActionButtons project={project} status="ready" onAction={vi.fn()} />,
      { ...VALID, state: "expired" },
    );
    const scan = screen.getByTitle(/expired on 2026-10-01.*scanning is disabled/);
    const generate = screen.getByTitle(/expired on 2026-10-01.*documentation generation is disabled/);
    expect(scan).toBeDisabled();
    expect(generate).toBeDisabled();
  });

  it("disables generate, but not scan, when the quota is used", () => {
    renderWith(
      <ActionButtons project={project} status="ready" onAction={vi.fn()} />,
      { ...VALID, usage: { generations_used: 3, generations_limit: 3 } },
    );
    expect(screen.getByTitle("Re-scan")).not.toBeDisabled();
    expect(screen.getByTitle(/All 3 documentation generations/)).toBeDisabled();
  });

  it("shows the period's usage in the generate dialog", async () => {
    renderWith(<GenerateDialog project={project} open onClose={vi.fn()} />, VALID);
    expect(await screen.findByTestId("generation-usage")).toHaveTextContent("Generations: 2/3 this period");
    expect(screen.queryByTestId("generate-licence-reason")).not.toBeInTheDocument();
  });

  it("disables the generate button with the reason when the quota is used", async () => {
    renderWith(
      <GenerateDialog project={project} open onClose={vi.fn()} />,
      { ...VALID, usage: { generations_used: 3, generations_limit: 3 } },
    );
    expect(await screen.findByTestId("generate-licence-reason")).toHaveTextContent(
      "All 3 documentation generations for this period have been used.",
    );
    expect(screen.getByRole("button", { name: /^Generate/ })).toBeDisabled();
  });

  it("shows the server's refusal sentence when a generate still gets a 402", async () => {
    const { LicenceRefusal } = await import("../lib/errors");
    const message = "All 3 documentation generations for this period have been used.";
    vi.mocked(api.generate).mockRejectedValue(
      new LicenceRefusal({ code: "quota_exhausted", message, used: 3, limit: 3 }, "{}"),
    );
    renderWith(<GenerateDialog project={project} open onClose={vi.fn()} />, null);

    fireEvent.click(screen.getByRole("button", { name: /^Generate/ }));

    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent(message));
    expect(screen.getByRole("alert")).not.toHaveTextContent("402");
  });
});
