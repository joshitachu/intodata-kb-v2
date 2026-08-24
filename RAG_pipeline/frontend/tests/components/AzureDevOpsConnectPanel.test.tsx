import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AzureDevOpsConnectPanel } from "@/app/settings/sources/AzureDevOpsConnectPanel";
import {
  addAzureDevOpsSource,
  connectAzureDevOps,
  deleteAzureDevOpsConnection,
  listAzureDevOpsConnections,
  listAzureDevOpsRepos,
  testAzureDevOpsConnection,
} from "@/lib/mcpClient";

vi.mock("@/lib/mcpClient", async () => {
  const actual = await vi.importActual<typeof import("@/lib/mcpClient")>("@/lib/mcpClient");
  return {
    ...actual,
    connectAzureDevOps: vi.fn(),
    listAzureDevOpsConnections: vi.fn(),
    listAzureDevOpsRepos: vi.fn(),
    testAzureDevOpsConnection: vi.fn(),
    deleteAzureDevOpsConnection: vi.fn(),
    addAzureDevOpsSource: vi.fn(),
  };
});

const CONNECTION = {
  id: "3f2504e0-4f89-11d3-9a0c-0305e82c3301",
  organization_url: "https://dev.azure.com/acme",
  organization_name: "acme",
  account_name: "Ada L",
  last_verified_at: null,
};

const REPOS = [
  {
    id: "r1",
    name: "etl",
    project: "Data",
    clone_url: "https://dev.azure.com/acme/Data/_git/etl",
    web_url: null,
    default_branch: "main",
    is_disabled: false,
    size_bytes: 10,
  },
  {
    id: "r2",
    name: "api",
    project: "Platform",
    clone_url: "https://dev.azure.com/acme/Platform/_git/api",
    web_url: null,
    default_branch: "main",
    is_disabled: false,
    size_bytes: 20,
  },
];

function renderPanel(overrides: Partial<Parameters<typeof AzureDevOpsConnectPanel>[0]> = {}) {
  const onConnected = vi.fn();
  render(
    <AzureDevOpsConnectPanel
      platforms={[{ id: "talend", label: "Talend" }]}
      defaultPlatform="talend"
      existingProjects={[]}
      onConnected={onConnected}
      {...overrides}
    />,
  );
  return { onConnected };
}

describe("AzureDevOpsConnectPanel", () => {
  beforeEach(() => {
    vi.mocked(listAzureDevOpsConnections).mockResolvedValue([]);
    vi.mocked(listAzureDevOpsRepos).mockResolvedValue({
      organization_name: "acme",
      repositories: REPOS,
    });
    vi.mocked(connectAzureDevOps).mockResolvedValue(CONNECTION);
    vi.mocked(testAzureDevOpsConnection).mockResolvedValue(CONNECTION);
    vi.mocked(deleteAzureDevOpsConnection).mockResolvedValue(undefined);
    vi.mocked(addAzureDevOpsSource).mockResolvedValue({
      id: "etl",
      display_name: "Data / etl",
      platform: "talend",
      source_type: "git",
      status: "pending",
    });
  });

  it("shows 'Not connected' until an organization is connected", async () => {
    renderPanel();
    await waitFor(() => expect(screen.getByText("Not connected")).toBeInTheDocument());
    expect(screen.getByRole("button", { name: /Connect Azure DevOps/ })).toBeInTheDocument();
  });

  it("connects an organization and moves straight to the repository picker", async () => {
    renderPanel();
    await waitFor(() => expect(screen.getByText("Not connected")).toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: /Connect Azure DevOps/ }));
    fireEvent.change(screen.getByPlaceholderText("https://dev.azure.com/your-org"), {
      target: { value: "https://dev.azure.com/acme/Data/_git/etl?path=/x" },
    });
    fireEvent.change(screen.getByPlaceholderText("Personal access token"), {
      target: { value: "pat-value" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Connect" }));

    await waitFor(() =>
      expect(connectAzureDevOps).toHaveBeenCalledWith(
        "https://dev.azure.com/acme/Data/_git/etl?path=/x",
        "pat-value",
      ),
    );
    await waitFor(() => expect(screen.getByText("Finish connecting")).toBeInTheDocument());
    // Repositories are listed across every Azure DevOps project.
    await waitFor(() =>
      expect(screen.getByRole("option", { name: /Data \/ etl/ })).toBeInTheDocument(),
    );
    expect(screen.getByRole("option", { name: /Platform \/ api/ })).toBeInTheDocument();
  });

  it("registers the picked repository against the connection, not a per-project token", async () => {
    vi.mocked(listAzureDevOpsConnections).mockResolvedValue([CONNECTION]);
    const { onConnected } = renderPanel();

    await waitFor(() => expect(screen.getByText("Connected")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: /Choose repository/ }));
    await waitFor(() =>
      expect(screen.getByRole("option", { name: /Data \/ etl/ })).toBeInTheDocument(),
    );

    fireEvent.change(screen.getByRole("combobox", { name: "Repository" }), {
      target: { value: "https://dev.azure.com/acme/Data/_git/etl" },
    });
    fireEvent.click(screen.getByRole("button", { name: /Connect & scan/ }));

    await waitFor(() =>
      expect(addAzureDevOpsSource).toHaveBeenCalledWith({
        repoUrl: "https://dev.azure.com/acme/Data/_git/etl",
        projectId: "etl",
        platform: "talend",
        displayName: "Data / etl",
        azureDevOpsConnectionId: CONNECTION.id,
      }),
    );
    await waitFor(() => expect(onConnected).toHaveBeenCalled());
  });

  it("disables a repository that is already connected", async () => {
    vi.mocked(listAzureDevOpsConnections).mockResolvedValue([CONNECTION]);
    renderPanel({
      existingProjects: [
        {
          id: "etl",
          display_name: "etl",
          platform: "talend",
          source_type: "git",
          status: "complete",
          // Trailing slash and case differ on purpose: matching is canonical.
          repo_url: "https://dev.azure.com/acme/Data/_git/ETL/",
        },
      ],
    });

    await waitFor(() => expect(screen.getByText("Connected")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: /Choose repository/ }));

    const option = await screen.findByRole("option", { name: /Data \/ etl - already connected/ });
    expect(option).toBeDisabled();
  });

  it("surfaces an expired token from the test action instead of failing silently", async () => {
    vi.mocked(listAzureDevOpsConnections).mockResolvedValue([CONNECTION]);
    vi.mocked(testAzureDevOpsConnection).mockRejectedValue(
      new Error("Azure DevOps rejected the personal access token."),
    );
    renderPanel();

    await waitFor(() => expect(screen.getByText("Connected")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: /Test token/ }));

    await waitFor(() =>
      expect(
        screen.getByText("Azure DevOps rejected the personal access token."),
      ).toBeInTheDocument(),
    );
  });

  it("shows a rejected connection error without leaving the form", async () => {
    vi.mocked(connectAzureDevOps).mockRejectedValue(
      new Error("The personal access token lacks permission for this organization"),
    );
    renderPanel();

    await waitFor(() => expect(screen.getByText("Not connected")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: /Connect Azure DevOps/ }));
    fireEvent.change(screen.getByPlaceholderText("https://dev.azure.com/your-org"), {
      target: { value: "https://dev.azure.com/acme" },
    });
    fireEvent.change(screen.getByPlaceholderText("Personal access token"), {
      target: { value: "bad" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Connect" }));

    await waitFor(() =>
      expect(
        screen.getByText(/lacks permission for this organization/),
      ).toBeInTheDocument(),
    );
    // Still on the form so the user can correct the token.
    expect(screen.getByPlaceholderText("Personal access token")).toBeInTheDocument();
  });
});
