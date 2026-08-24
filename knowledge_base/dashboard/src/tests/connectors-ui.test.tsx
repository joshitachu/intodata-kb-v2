import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import ConnectorsPage from "../components/connectors/ConnectorsPage";

const connectorHooks = vi.hoisted(() => ({
  catalog: vi.fn(),
  connectors: vi.fn(),
}));

const idleMutation = {
  isPending: false,
  isError: false,
  error: null,
  data: null,
  mutate: vi.fn(),
  mutateAsync: vi.fn(),
};

vi.mock("../hooks/useConnectors", () => ({
  useConnectorCatalog: connectorHooks.catalog,
  useConnectors: connectorHooks.connectors,
  useConnectorSources: () => ({ data: { sources: [] }, isLoading: false, isError: false }),
  useCreateConnector: () => idleMutation,
  useDeleteConnector: () => idleMutation,
  useDiscoverConnectorSources: () => idleMutation,
  useKibanaDataViews: () => ({ data: { data_views: [] }, isError: false }),
  useKibanaSpaces: () => ({ data: { spaces: [] }, isError: false }),
  useRotateConnectorCredential: () => idleMutation,
  useTestConnector: () => idleMutation,
  useUpdateConnector: () => idleMutation,
}));

const elasticProvider = {
  provider_id: "elasticsearch",
  label: "Elastic Stack",
  version: "1",
  authentication_types: ["api_key", "basic"],
  capabilities: [
    { id: "search_logs", label: "Search logs", description: null, read_only: true, gather_eligible: true },
  ],
  enabled: true,
  kind: "live_evidence" as const,
  build_state: "available" as const,
  description: "",
};

// A provider on the roadmap: listed so the directory doubles as a roadmap, but
// with no Connect action behind it.
const comingSoonProvider = {
  provider_id: "workato",
  label: "Workato",
  version: "1",
  authentication_types: ["api_key"],
  capabilities: [],
  enabled: true,
  kind: "live_evidence" as const,
  build_state: "coming_soon" as const,
  description: "",
};

const connectedElastic = {
  id: "connector-1",
  provider_id: "elasticsearch",
  display_name: "Production Elastic",
  ownership_scope: "workspace",
  enabled: true,
  status: "connected" as const,
  last_checked_at: "2026-08-21T07:00:00Z",
  health_code: null,
  provider_version: "1",
  redaction_policy_version: "1",
  has_credential: true,
  has_kibana_credential: false,
  created_at: "2026-08-21T06:00:00Z",
  updated_at: "2026-08-21T07:00:00Z",
};

beforeEach(() => {
  vi.clearAllMocks();
  connectorHooks.catalog.mockReturnValue({ data: { providers: [elasticProvider] }, isLoading: false, isError: false, error: null });
  connectorHooks.connectors.mockReturnValue({ data: { connectors: [], total: 0 }, isLoading: false, isError: false, error: null });
});

describe("connector catalogue", () => {
  it("shows installed providers as connectable rows", () => {
    render(<ConnectorsPage canManage />);

    expect(screen.getByRole("heading", { name: "Connectors" })).toBeInTheDocument();
    expect(screen.getByText("Elastic Stack")).toBeInTheDocument();
    expect(screen.getByText("Live API")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Connect" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Browse" })).toBeInTheDocument();
  });

  it("expands a configured connector without cluttering the list", () => {
    connectorHooks.connectors.mockReturnValue({ data: { connectors: [connectedElastic], total: 1 }, isLoading: false, isError: false, error: null });
    render(<ConnectorsPage canManage />);

    fireEvent.click(screen.getByRole("button", { name: "✓ Connected" }));

    expect(screen.getByText("Production Elastic")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Test connection" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Discover sources" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Edit" })).toBeInTheDocument();
  });

  it("offers no Connect action for a provider that is not built yet", () => {
    connectorHooks.catalog.mockReturnValue({
      data: { providers: [elasticProvider, comingSoonProvider] },
      isLoading: false, isError: false, error: null,
    });
    render(<ConnectorsPage canManage />);

    expect(screen.getByText("Workato")).toBeInTheDocument();
    expect(screen.queryAllByRole("button", { name: "Connect" })).toHaveLength(1);
  });

  it("opens a provider browser from the catalogue header", () => {
    render(<ConnectorsPage canManage />);
    fireEvent.click(screen.getByRole("button", { name: "Browse" }));

    expect(screen.getByRole("heading", { name: "Browse connectors" })).toBeInTheDocument();
    expect(screen.getByText("Live evidence - 1 governed read capabilities")).toBeInTheDocument();
  });
});
