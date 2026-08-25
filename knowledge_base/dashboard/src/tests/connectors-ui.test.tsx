import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import ConnectorsPage, {
  canConnect,
  registeredDialogProviderIds,
} from "../components/connectors/ConnectorsPage";
import type { ConnectorProvider } from "../types";
import { api } from "../api/client";

const connectorHooks = vi.hoisted(() => ({
  catalog: vi.fn(),
  connectors: vi.fn(),
  runtimeSources: vi.fn(),
  runtimeSourceTypes: vi.fn(),
  projects: vi.fn(),
}));

const idleMutation = {
  isPending: false,
  isError: false,
  error: null,
  data: null,
  mutate: vi.fn(),
  mutateAsync: vi.fn(),
};

// Kept separate from `idleMutation` so the create payload can be inspected
// without every other mutation's calls landing in the same spy.
const createMutation = {
  ...idleMutation,
  mutate: vi.fn(),
  mutateAsync: vi.fn(async (_payload: { provider_id: string }) => ({ id: "connector-new" })),
};

// The runtime-source lane. TMC's credential is written here, not into the
// connector store, so its create call must be observable on its own spy.
const createRuntimeSourceMutation = {
  ...idleMutation,
  mutate: vi.fn(),
  mutateAsync: vi.fn(async (_payload: {
    provider_id: string;
    project_id: string;
    label: string;
    credentials: Record<string, string>;
  }) => ({ id: 1 })),
};
const syncRuntimeSourceMutation = { ...idleMutation, mutate: vi.fn(), mutateAsync: vi.fn() };
const deleteRuntimeSourceMutation = { ...idleMutation, mutate: vi.fn(), mutateAsync: vi.fn() };

vi.mock("../hooks/useConnectors", () => ({
  useConnectorCatalog: connectorHooks.catalog,
  useConnectors: connectorHooks.connectors,
  useConnectorSources: () => ({ data: { sources: [] }, isLoading: false, isError: false }),
  useCreateConnector: () => createMutation,
  useDeleteConnector: () => idleMutation,
  useDiscoverConnectorSources: () => idleMutation,
  useKibanaDataViews: () => ({ data: { data_views: [] }, isError: false }),
  useKibanaSpaces: () => ({ data: { spaces: [] }, isError: false }),
  useRotateConnectorCredential: () => idleMutation,
  useTestConnector: () => idleMutation,
  useUpdateConnector: () => idleMutation,
}));

vi.mock("../hooks/useRuntimeSources", () => ({
  useRuntimeSources: connectorHooks.runtimeSources,
  useRuntimeSourceTypes: connectorHooks.runtimeSourceTypes,
  useCreateRuntimeSource: () => createRuntimeSourceMutation,
  useSyncRuntimeSource: () => syncRuntimeSourceMutation,
  useDeleteRuntimeSource: () => deleteRuntimeSourceMutation,
}));

vi.mock("../hooks/useProjects", () => ({
  useProjects: connectorHooks.projects,
}));

/**
 * The credential form TMC actually declares, as
 * `TalendTmcRuntimeProvider.credential_schema()` returns it. The dialog fetches
 * this rather than hardcoding inputs, so a field the provider does not have
 * cannot appear on the form.
 */
const tmcCredentialSchema = [
  { name: "api_token", label: "API token", type: "secret", required: true },
  { name: "base_url", label: "Base URL", type: "url", required: true },
  { name: "task_name_filter", label: "Task/artifact filters (comma-separated)", type: "text", required: false },
  { name: "history_days", label: "Run history in days (1-90)", type: "text", required: false },
];

/** Real intodoc projects, as the dashboard's own project list returns them. */
const projectFixture = [
  { id: "werkorder", display_name: "Werkorder" },
  { id: "IntodataStreams", display_name: "IntodataStreams" },
];

function runtimeSource(overrides: Record<string, unknown> = {}) {
  return {
    id: 35,
    project_id: "werkorder",
    source_type: "tmc",
    label: "Talend TMC",
    status: "success",
    enabled: true,
    sync_interval_seconds: 60,
    disabled_reason: null,
    last_synced_at: "2026-08-24T10:00:00Z",
    ...overrides,
  };
}

/**
 * Provider ids the SERVER registers, copied from the manifests rather than from
 * the dashboard. The Elastic manifest is registered as `elastic_stack`
 * (`mcp-server/.../connectors/providers/elasticsearch.py`) with no alias; the
 * dashboard used to post `elasticsearch`, which the create route rejects with a
 * 422 ("Unknown connector provider").
 */
const SERVER_PROVIDER_IDS = ["elastic_stack", "talend_tmc"];

const elasticProvider: ConnectorProvider = {
  provider_id: "elastic_stack",
  label: "Elastic Stack",
  version: "1",
  authentication_types: ["api_key", "basic"],
  capabilities: [
    { id: "search_logs", label: "Search logs", description: null, read_only: true, gather_eligible: true },
  ],
  enabled: true,
  kind: "live_evidence",
  build_state: "available",
  managed_by: "connector_store",
  description: "",
};

// A provider on the roadmap: listed so the directory doubles as a roadmap, but
// with no Connect action behind it.
const comingSoonProvider: ConnectorProvider = {
  provider_id: "workato",
  label: "Workato",
  version: "1",
  authentication_types: ["api_key"],
  capabilities: [],
  enabled: true,
  kind: "live_evidence",
  build_state: "coming_soon",
  managed_by: "connector_store",
  description: "",
};

// TMC: available and enabled, but its credential lives in the RAG backend's
// runtime_sources tables next to the scheduler that reads it.
const tmcProvider: ConnectorProvider = {
  provider_id: "talend_tmc",
  label: "Talend Management Console",
  version: "1",
  authentication_types: ["api_key"],
  capabilities: [],
  enabled: true,
  kind: "runtime_sync",
  build_state: "available",
  managed_by: "runtime_sources",
  description: "",
};

/**
 * Every provider the catalogue could hand this page. The registry-coverage test
 * walks this list, so a provider added here without a dialog is a red test
 * rather than a Connect button that opens someone else's form.
 */
const catalogueFixture: ConnectorProvider[] = [elasticProvider, comingSoonProvider, tmcProvider];

const connectedElastic = {
  id: "connector-1",
  provider_id: "elastic_stack",
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
  // Default: TMC is installed but nothing is connected to it yet.
  connectorHooks.runtimeSources.mockReturnValue({ data: { sources: [], total: 0 }, isLoading: false, isError: false, error: null });
  connectorHooks.runtimeSourceTypes.mockReturnValue({
    data: { source_types: [{ provider_key: "tmc", provider_label: "Talend Management Console", credential_schema: tmcCredentialSchema }] },
    isLoading: false, isError: false, error: null,
  });
  connectorHooks.projects.mockReturnValue({ data: { projects: projectFixture, total: 2 }, isLoading: false, isError: false, error: null });
});

describe("connector catalogue", () => {
  it("shows installed providers as connectable rows", () => {
    render(<ConnectorsPage canManage />);

    expect(screen.getByRole("heading", { name: "Connectors" })).toBeInTheDocument();
    expect(screen.getByText("Elastic Stack")).toBeInTheDocument();
    // The Type column is manifest-driven. "Live API" plus an "OpenCode" badge
    // was a literal that lied about every runtime-sync provider.
    expect(screen.getByText("Live evidence - 1 governed read capabilities")).toBeInTheDocument();
    expect(screen.queryByText("OpenCode")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Connect" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Browse" })).toBeInTheDocument();
  });

  it("describes a runtime-sync provider as mirrored, not as an OpenCode live API", () => {
    connectorHooks.catalog.mockReturnValue({
      data: { providers: [tmcProvider] }, isLoading: false, isError: false, error: null,
    });
    render(<ConnectorsPage canManage />);

    expect(screen.getByText("Runtime sync - mirrored on a schedule")).toBeInTheDocument();
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
    // Once in the row's Type column, once as the browser's subtitle.
    expect(screen.getAllByText("Live evidence - 1 governed read capabilities")).toHaveLength(2);
  });
});

/**
 * `managed_by` says WHERE a credential is stored. It used to be read as "you
 * cannot do this here", which is what forced an operator into a second
 * application to paste a Talend token. These tests pin the corrected meaning:
 * TMC is connectable in-page, and the field still records the real store.
 */
describe("credential ownership", () => {
  beforeEach(() => {
    connectorHooks.catalog.mockReturnValue({
      data: { providers: catalogueFixture }, isLoading: false, isError: false, error: null,
    });
  });

  it("offers Connect for a runtime-managed provider, in this page", () => {
    render(<ConnectorsPage canManage />);

    expect(screen.getByText("Talend Management Console")).toBeInTheDocument();
    // Elastic and TMC both connect here now; only Workato is unbuilt.
    expect(screen.queryAllByRole("button", { name: "Connect" })).toHaveLength(2);
    // The old dead end must not come back as the row's primary state.
    expect(screen.queryByText(/Managed in Runtime sources/)).not.toBeInTheDocument();
  });

  it("says where the credential will be stored without withholding the action", () => {
    render(<ConnectorsPage canManage />);
    fireEvent.click(screen.getByRole("button", { name: "Browse" }));

    // Honest about the store, in the browser dialog, next to a live Connect.
    expect(screen.getByText("Credential stored in Runtime sources")).toBeInTheDocument();
    // Row + browser for each of Elastic and TMC.
    expect(screen.queryAllByRole("button", { name: "Connect" })).toHaveLength(4);
    expect(screen.queryByRole("button", { name: "Coming soon" })).toBeInTheDocument();
  });

  it("keeps the RAG deep link out of the way until a source exists", () => {
    render(<ConnectorsPage canManage />);
    // VITE_RAG_APP_URL is unset under test, and the advanced link only appears
    // inside an expanded runtime panel -- never as the row's primary path.
    expect(screen.queryByRole("link", { name: "Runtime sources" })).not.toBeInTheDocument();
  });
});

/**
 * The row must describe the mirror as it actually is. A runtime source is bound
 * to exactly ONE project, so a flat "Connected" on a provider row would imply
 * coverage the scheduler does not have.
 */
describe("runtime-managed row state", () => {
  beforeEach(() => {
    connectorHooks.catalog.mockReturnValue({
      data: { providers: [tmcProvider] }, isLoading: false, isError: false, error: null,
    });
  });

  it("names the connected project rather than implying blanket coverage", () => {
    connectorHooks.runtimeSources.mockReturnValue({
      data: { sources: [runtimeSource()], total: 1 }, isLoading: false, isError: false, error: null,
    });
    render(<ConnectorsPage canManage />);

    expect(screen.getByText("Mirroring werkorder")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "✓ Connected" })).toBeInTheDocument();
  });

  it("counts projects once a rollout is partial", () => {
    connectorHooks.runtimeSources.mockReturnValue({
      data: {
        sources: [
          runtimeSource({ id: 1, project_id: "a" }),
          runtimeSource({ id: 2, project_id: "b" }),
          runtimeSource({ id: 3, project_id: "c" }),
        ],
        total: 3,
      },
      isLoading: false, isError: false, error: null,
    });
    render(<ConnectorsPage canManage />);

    expect(screen.getByText("Mirroring 3 projects")).toBeInTheDocument();
  });

  it("does not show a green tick over a failing mirror", () => {
    connectorHooks.runtimeSources.mockReturnValue({
      data: { sources: [runtimeSource({ status: "failed" })], total: 1 },
      isLoading: false, isError: false, error: null,
    });
    render(<ConnectorsPage canManage />);

    expect(screen.getByRole("button", { name: "Attention" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "✓ Connected" })).not.toBeInTheDocument();
  });

  it("distinguishes an unreachable proxy from nothing being connected", () => {
    connectorHooks.runtimeSources.mockReturnValue({
      data: undefined, isLoading: false, isError: true, error: new Error("502"),
    });
    render(<ConnectorsPage canManage />);

    // Offering Connect here would invite a duplicate registration that 409s.
    expect(screen.getByText("Status unavailable")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Connect" })).toBeNull();
  });

  it("exposes per-project sync and disconnect, plus the advanced link as secondary", () => {
    connectorHooks.runtimeSources.mockReturnValue({
      data: { sources: [runtimeSource()], total: 1 }, isLoading: false, isError: false, error: null,
    });
    render(<ConnectorsPage canManage />);
    fireEvent.click(screen.getByRole("button", { name: "✓ Connected" }));

    expect(screen.getByText("werkorder")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Sync now" }));
    expect(syncRuntimeSourceMutation.mutate).toHaveBeenCalledWith(35);
    expect(screen.getByRole("button", { name: "Connect another project" })).toBeInTheDocument();
  });

  it("hides every mutating action from a read-only operator", () => {
    connectorHooks.runtimeSources.mockReturnValue({
      data: { sources: [runtimeSource()], total: 1 }, isLoading: false, isError: false, error: null,
    });
    render(<ConnectorsPage canManage={false} />);
    fireEvent.click(screen.getByRole("button", { name: "✓ Connected" }));

    expect(screen.queryByRole("button", { name: "Sync now" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Disconnect" })).toBeNull();
  });
});

/**
 * The project selector is the anti-recurrence device for a live defect: runtime
 * history filed under a project id that does not match the intodoc project, so
 * documentation silently attached mostly-wrong runtime evidence. A free-text box
 * recreates it; a dropdown of real projects cannot.
 */
describe("Talend Management Console connect form", () => {
  beforeEach(() => {
    connectorHooks.catalog.mockReturnValue({
      data: { providers: [tmcProvider] }, isLoading: false, isError: false, error: null,
    });
  });

  function openDialog() {
    render(<ConnectorsPage canManage />);
    fireEvent.click(screen.getByRole("button", { name: "Connect" }));
  }

  it("opens its OWN form, not another provider's", () => {
    openDialog();
    expect(screen.getByRole("heading", { name: "Connect Talend Management Console" })).toBeInTheDocument();
    // The Elastic form's signature field must be nowhere near this dialog.
    expect(screen.queryByLabelText("Elasticsearch HTTPS endpoint")).toBeNull();
  });

  it("binds the project with a dropdown of real projects, never free text", () => {
    openDialog();

    const project = screen.getByLabelText("Project");
    expect(project.tagName).toBe("SELECT");
    // Every option is an id the dashboard's own project list returned, so a
    // typo cannot become a project_id.
    const values = Array.from(project.querySelectorAll("option"))
      .map((option) => (option as HTMLOptionElement).value)
      .filter(Boolean);
    expect(values).toEqual(["werkorder", "IntodataStreams"]);
  });

  it("refuses to submit until a project is chosen", async () => {
    openDialog();

    fireEvent.change(screen.getByLabelText("API token"), { target: { value: "fake-token" } });
    fireEvent.change(screen.getByLabelText("Base URL"), { target: { value: "https://eu.cloud.talend.com" } });
    fireEvent.click(screen.getByRole("button", { name: "Connect & test" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Choose the project this Talend environment belongs to.",
    );
    expect(createRuntimeSourceMutation.mutateAsync).not.toHaveBeenCalled();
  });

  it("renders exactly the fields the provider declares", () => {
    openDialog();

    expect(screen.getByLabelText("API token")).toHaveAttribute("type", "password");
    expect(screen.getByLabelText("Base URL")).toHaveAttribute("type", "url");
    // Optional fields are marked as such rather than silently required.
    expect(screen.getByLabelText("Task/artifact filters (comma-separated) (optional)")).toBeInTheDocument();
    expect(screen.getByLabelText("Run history in days (1-90) (optional)")).toBeInTheDocument();
    // No invented settings. The whole form is the provider's four credential
    // fields plus the project binding and a display name -- asserted as an
    // exact set, so a field added here without a provider to back it fails.
    const labels = Array.from(document.querySelectorAll("label > span:first-child"))
      .map((node) => node.textContent);
    expect(labels).toEqual([
      "Project",
      "Display name",
      "API token",
      "Base URL",
      "Task/artifact filters (comma-separated) (optional)",
      "Run history in days (1-90) (optional)",
    ]);
  });

  it("posts the connector id and the selected project, and omits blank optionals", async () => {
    openDialog();

    fireEvent.change(screen.getByLabelText("Project"), { target: { value: "IntodataStreams" } });
    fireEvent.change(screen.getByLabelText("API token"), { target: { value: "fake-token" } });
    fireEvent.change(screen.getByLabelText("Base URL"), { target: { value: "https://eu.cloud.talend.com" } });
    fireEvent.click(screen.getByRole("button", { name: "Connect & test" }));

    await waitFor(() => expect(createRuntimeSourceMutation.mutateAsync).toHaveBeenCalledTimes(1));
    const payload = createRuntimeSourceMutation.mutateAsync.mock.calls[0][0];
    // The CONNECTOR id. The server owns the mapping to the RAG runtime provider
    // key (`tmc`); a browser that sent either literal could drift from it.
    expect(payload.provider_id).toBe(tmcProvider.provider_id);
    expect(payload.project_id).toBe("IntodataStreams");
    expect(payload.credentials).toEqual({
      api_token: "fake-token",
      base_url: "https://eu.cloud.talend.com",
    });
  });

  it("surfaces the backend's own conflict message verbatim", async () => {
    createRuntimeSourceMutation.mutateAsync.mockRejectedValueOnce(
      new Error('409: {"detail":"That physical runtime source is already registered"}'),
    );
    openDialog();

    fireEvent.change(screen.getByLabelText("Project"), { target: { value: "werkorder" } });
    fireEvent.change(screen.getByLabelText("API token"), { target: { value: "fake-token" } });
    fireEvent.change(screen.getByLabelText("Base URL"), { target: { value: "https://eu.cloud.talend.com" } });
    fireEvent.click(screen.getByRole("button", { name: "Connect & test" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "That physical runtime source is already registered",
    );
  });

  it("never renders a credential back into the form", async () => {
    openDialog();

    const token = screen.getByLabelText("API token") as HTMLInputElement;
    fireEvent.change(screen.getByLabelText("Project"), { target: { value: "werkorder" } });
    fireEvent.change(token, { target: { value: "fake-token" } });
    fireEvent.change(screen.getByLabelText("Base URL"), { target: { value: "https://eu.cloud.talend.com" } });
    // Write-only: masked while typing, and cleared the moment it leaves the form.
    expect(token).toHaveAttribute("type", "password");

    fireEvent.click(screen.getByRole("button", { name: "Connect & test" }));
    await waitFor(() => expect(createRuntimeSourceMutation.mutateAsync).toHaveBeenCalled());
  });
});

/**
 * The anti-recurrence guard for "Connect on Talend Management Console opened
 * Connect Elastic Stack".
 *
 * Drives the real UI rather than inspecting the registry object: it asserts
 * that whenever a Connect action exists, clicking it opens a form that names
 * THAT provider. A provider added to the catalogue fixture without a registered
 * dialog fails here instead of shipping the wrong form.
 */
describe("connector dialog registry coverage", () => {
  it.each(catalogueFixture.map((provider) => [provider.provider_id, provider] as const))(
    "%s offers Connect only when a matching form is registered",
    (_providerId, provider) => {
      connectorHooks.catalog.mockReturnValue({
        data: { providers: [provider] }, isLoading: false, isError: false, error: null,
      });
      render(<ConnectorsPage canManage />);

      const connect = screen.queryByRole("button", { name: "Connect" });
      if (!canConnect(provider)) {
        expect(connect).toBeNull();
        return;
      }

      expect(connect).not.toBeNull();
      fireEvent.click(connect!);
      expect(
        screen.getByRole("heading", { name: `Connect ${provider.label}` }),
      ).toBeInTheDocument();
    },
  );

  it("decides on the registered form, not on where the credential is stored", () => {
    // Both are connectable, and they post to DIFFERENT stores. `managed_by` is
    // not a veto -- reading it as one is what sent operators to a second app.
    expect(canConnect(tmcProvider)).toBe(true);
    expect(canConnect(elasticProvider)).toBe(true);
    // Changing only the storage location changes nothing about connectability.
    expect(canConnect({ ...tmcProvider, managed_by: "connector_store" })).toBe(true);
    expect(canConnect({ ...elasticProvider, managed_by: "runtime_sources" })).toBe(true);
    // A managed_by this build has never seen must not blank the page either.
    expect(canConnect({ ...tmcProvider, managed_by: "some_future_vault" })).toBe(true);
  });

  it("still refuses a provider with no form, whatever its storage says", () => {
    // The guard that remains load-bearing: no dialog, no Connect action.
    expect(canConnect({ ...tmcProvider, provider_id: "workato" })).toBe(false);
    expect(canConnect({ ...elasticProvider, provider_id: "workato", managed_by: "runtime_sources" })).toBe(false);
  });

  it("refuses a provider this build has never heard of", () => {
    // An unknown provider_id parses fine (resilient schema) and must simply not
    // be connectable, rather than falling through to whichever form exists.
    expect(canConnect({ ...elasticProvider, provider_id: "servicenow" })).toBe(false);
  });

  it("refuses a disabled provider on both surfaces", () => {
    expect(canConnect({ ...elasticProvider, enabled: false })).toBe(false);
  });
});

/**
 * The anti-recurrence guard for the 422 the registry hid.
 *
 * `provider_id: "elasticsearch"` was a literal in the create call while the
 * server registers the manifest as `elastic_stack`, so every Connect ended in
 * "Unknown connector provider". The registry mapping BOTH ids made the dialog
 * open correctly, which is exactly why nothing looked wrong.
 */
describe("create posts the provider the operator clicked", () => {
  beforeEach(() => {
    connectorHooks.catalog.mockReturnValue({
      data: { providers: [elasticProvider] }, isLoading: false, isError: false, error: null,
    });
  });

  it("sends the catalogue entry's own provider_id, not a literal", async () => {
    render(<ConnectorsPage canManage />);
    fireEvent.click(screen.getByRole("button", { name: "Connect" }));

    fireEvent.change(screen.getByLabelText("Elasticsearch HTTPS endpoint"), {
      target: { value: "https://es.example.com:9200" },
    });
    fireEvent.change(screen.getByLabelText("API key"), { target: { value: "secret-key" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(createMutation.mutateAsync).toHaveBeenCalledTimes(1));
    const payload = createMutation.mutateAsync.mock.calls[0][0];
    // Compared against the fixture, never a string: a re-introduced literal
    // fails here even if someone updates the fixture id.
    expect(payload.provider_id).toBe(elasticProvider.provider_id);
  });

  it("registers dialogs only under ids the server actually serves", () => {
    // An alias for an id no manifest registers is a Connect button that ends in
    // a 422, and it hides the mismatch instead of surfacing it.
    for (const providerId of registeredDialogProviderIds()) {
      expect(SERVER_PROVIDER_IDS).toContain(providerId);
    }
  });
});

/**
 * Ingest scope: test the connection, then choose which environments to poll.
 *
 * A TMC token reaches every environment in the tenant, and polling all of them
 * is both expensive and usually wrong -- a source bound to one project rarely
 * wants another environment's runs. The RAG backend now requires the selection,
 * so this dialog has to collect it or every save fails validation.
 *
 * Driven by the provider's `scope_selection` descriptor, not by the connector
 * id: the fixtures above omit it and those tests still see the old one-step
 * form, which is the behaviour a provider without a partition must keep.
 */
describe("Talend Management Console ingest scope", () => {
  const scopedSourceTypes = {
    data: {
      source_types: [
        {
          provider_key: "tmc",
          provider_label: "Talend Management Console",
          credential_schema: tmcCredentialSchema,
          scope_selection: {
            field: "environment_ids",
            label: "Environments",
            description: "Only tasks from the selected environments are polled.",
            required: true,
            multiple: true,
          },
        },
      ],
    },
    isLoading: false,
    isError: false,
    error: null,
  };

  beforeEach(() => {
    connectorHooks.catalog.mockReturnValue({
      data: { providers: [tmcProvider] }, isLoading: false, isError: false, error: null,
    });
    connectorHooks.runtimeSourceTypes.mockReturnValue(scopedSourceTypes);
  });

  /** The dialog's footer button. The connector row has one with the same name. */
  function saveButton() {
    const buttons = screen.getAllByRole("button", { name: "Connect" });
    return buttons[buttons.length - 1];
  }

  function openScopedDialog() {
    render(<ConnectorsPage canManage />);
    fireEvent.click(saveButton());
    fireEvent.change(screen.getByLabelText("Project"), { target: { value: "IntodataStreams" } });
    fireEvent.change(screen.getByLabelText("API token"), { target: { value: "fake-token" } });
    fireEvent.change(screen.getByLabelText("Base URL"), {
      target: { value: "https://api.eu.cloud.talend.com" },
    });
  }

  it("cannot save before the connection is tested", () => {
    openScopedDialog();

    expect(saveButton()).toBeDisabled();
    expect(screen.queryByText("PROD")).toBeNull();
  });

  it("lists the environments the token reached, and saves only the ticked ones", async () => {
    const discover = vi.spyOn(api, "discoverRuntimeSourceScopes").mockResolvedValue({
      scopes: [
        { id: "env-dev", label: "DEV", detail: "81 tasks", default_selected: true },
        { id: "env-prod", label: "PROD", detail: "32 tasks", default_selected: true },
        { id: "env-empty", label: "Scratch", detail: "0 tasks", default_selected: false },
      ],
    });
    openScopedDialog();

    fireEvent.click(screen.getByRole("button", { name: "Test connection" }));
    await waitFor(() => expect(screen.getByText("PROD")).toBeInTheDocument());

    // The typed credentials reached the probe, and an empty environment is
    // offered but not preselected.
    expect(discover).toHaveBeenCalledWith({
      provider_id: tmcProvider.provider_id,
      credentials: { api_token: "fake-token", base_url: "https://api.eu.cloud.talend.com" },
    });
    const boxes = screen.getAllByRole("checkbox") as HTMLInputElement[];
    expect(boxes.map((box) => box.checked)).toEqual([true, true, false]);

    fireEvent.click(boxes[0]); // drop DEV
    fireEvent.click(saveButton());

    await waitFor(() => expect(createRuntimeSourceMutation.mutateAsync).toHaveBeenCalledTimes(1));
    expect(createRuntimeSourceMutation.mutateAsync.mock.calls[0][0].credentials).toEqual({
      api_token: "fake-token",
      base_url: "https://api.eu.cloud.talend.com",
      environment_ids: ["env-prod"],
    });
  });

  it("discards the environment list when a credential is edited", async () => {
    vi.spyOn(api, "discoverRuntimeSourceScopes").mockResolvedValue({
      scopes: [{ id: "env-prod", label: "PROD", detail: "32 tasks", default_selected: true }],
    });
    openScopedDialog();

    fireEvent.click(screen.getByRole("button", { name: "Test connection" }));
    await waitFor(() => expect(screen.getByText("PROD")).toBeInTheDocument());

    // A list discovered with the previous token must not be saved against a new one.
    fireEvent.change(screen.getByLabelText("API token"), { target: { value: "other-token" } });

    expect(screen.queryByText("PROD")).toBeNull();
    expect(saveButton()).toBeDisabled();
  });
});
