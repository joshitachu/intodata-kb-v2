import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AzureDevOpsConnectPanel } from "@/app/settings/sources/AzureDevOpsConnectPanel";
import {
  addAzureDevOpsSource,
  connectAzureDevOps,
  createMultiRepoProject,
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
    createMultiRepoProject: vi.fn(),
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
    // Call history is not cleared globally; without this, a
    // `not.toHaveBeenCalled()` assertion sees the previous test's calls.
    vi.clearAllMocks();
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
    vi.mocked(createMultiRepoProject).mockResolvedValue({
      project_id: "pluralfarm",
      members: [],
      sync: [],
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
    // Repositories are listed across every Azure DevOps project, grouped by it.
    await waitFor(() =>
      expect(screen.getByRole("checkbox", { name: /etl/ })).toBeInTheDocument(),
    );
    expect(screen.getByRole("checkbox", { name: /api/ })).toBeInTheDocument();
    expect(screen.getByText("Data")).toBeInTheDocument();
    expect(screen.getByText("Platform")).toBeInTheDocument();
  });

  it("registers a single picked repository on the single-repo path", async () => {
    // One repo must keep using /add-repo: that sets `repo_url`, which is what
    // the push-webhook match keys on. The multi-repo path has no webhook yet.
    vi.mocked(listAzureDevOpsConnections).mockResolvedValue([CONNECTION]);
    const { onConnected } = renderPanel();

    await waitFor(() => expect(screen.getByText("Connected")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: /Choose repositories/ }));
    const etl = await screen.findByRole("checkbox", { name: /etl/ });

    fireEvent.click(etl);
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
    expect(createMultiRepoProject).not.toHaveBeenCalled();
    await waitFor(() => expect(onConnected).toHaveBeenCalled());
  });

  it("registers several picked repositories as one multi-repo project", async () => {
    vi.mocked(listAzureDevOpsConnections).mockResolvedValue([CONNECTION]);
    const { onConnected } = renderPanel();

    await waitFor(() => expect(screen.getByText("Connected")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: /Choose repositories/ }));
    await screen.findByRole("checkbox", { name: /etl/ });

    fireEvent.click(screen.getByRole("checkbox", { name: /etl/ }));
    fireEvent.click(screen.getByRole("checkbox", { name: /api/ }));
    // The two repos live in different Azure DevOps projects, so there is no
    // shared name to suggest and the user must supply one.
    fireEvent.change(screen.getByLabelText("Project name"), {
      target: { value: "Pluralfarm" },
    });
    fireEvent.click(screen.getByRole("button", { name: /Connect 2 repositories/ }));

    await waitFor(() =>
      expect(createMultiRepoProject).toHaveBeenCalledWith({
        projectId: "pluralfarm",
        displayName: "Pluralfarm",
        platform: "talend",
        provider: "azure_devops",
        azureDevOpsConnectionId: CONNECTION.id,
        members: [
          {
            // org/project/repo — the provider-stable identity a push resolves on.
            repoIdentity: "acme/Data/etl",
            repoName: "etl",
            cloneUrl: "https://dev.azure.com/acme/Data/_git/etl",
          },
          {
            repoIdentity: "acme/Platform/api",
            repoName: "api",
            cloneUrl: "https://dev.azure.com/acme/Platform/_git/api",
          },
        ],
      }),
    );
    expect(addAzureDevOpsSource).not.toHaveBeenCalled();
    await waitFor(() => expect(onConnected).toHaveBeenCalled());
  });

  it("reports members that failed to clone instead of claiming full success", async () => {
    vi.mocked(listAzureDevOpsConnections).mockResolvedValue([CONNECTION]);
    vi.mocked(createMultiRepoProject).mockResolvedValue({
      project_id: "pluralfarm",
      members: [],
      sync: [
        {
          subproject_name: "api",
          repo_identity: "acme/Platform/api",
          action: "failed",
          head_sha: null,
          previous_sha: null,
          changed: false,
          error: "authentication failed",
        },
      ],
    });
    renderPanel();

    await waitFor(() => expect(screen.getByText("Connected")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: /Choose repositories/ }));
    await screen.findByRole("checkbox", { name: /etl/ });
    fireEvent.click(screen.getByRole("checkbox", { name: /etl/ }));
    fireEvent.click(screen.getByRole("checkbox", { name: /api/ }));
    fireEvent.change(screen.getByLabelText("Project name"), {
      target: { value: "Pluralfarm" },
    });
    fireEvent.click(screen.getByRole("button", { name: /Connect 2 repositories/ }));

    await waitFor(() =>
      expect(screen.getByText(/could not be cloned: api/)).toBeInTheDocument(),
    );
  });

  it("selects every repository in one Azure DevOps project at once", async () => {
    vi.mocked(listAzureDevOpsConnections).mockResolvedValue([CONNECTION]);
    renderPanel();

    await waitFor(() => expect(screen.getByText("Connected")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: /Choose repositories/ }));
    await screen.findByRole("checkbox", { name: /etl/ });

    // Each group header carries an "all <n>" toggle.
    fireEvent.click(screen.getAllByRole("button", { name: "all 1" })[0]);
    expect(screen.getByRole("checkbox", { name: /etl/ })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: /api/ })).not.toBeChecked();
  });

  it("filters the repository list", async () => {
    vi.mocked(listAzureDevOpsConnections).mockResolvedValue([CONNECTION]);
    renderPanel();

    await waitFor(() => expect(screen.getByText("Connected")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: /Choose repositories/ }));
    await screen.findByRole("checkbox", { name: /etl/ });

    fireEvent.change(screen.getByLabelText("Filter repositories"), {
      target: { value: "api" },
    });
    expect(screen.queryByRole("checkbox", { name: /etl/ })).not.toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: /api/ })).toBeInTheDocument();
  });

  it("notes a repository already in a project but still allows selecting it", async () => {
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
    fireEvent.click(screen.getByRole("button", { name: /Choose repositories/ }));

    const checkbox = await screen.findByRole("checkbox", { name: /etl already in a project/ });
    // Selectable: adding it to a second project is legitimate.
    expect(checkbox).toBeEnabled();
    expect(screen.getByText("already in a project")).toBeInTheDocument();
    fireEvent.click(checkbox);
    expect(checkbox).toBeChecked();
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

  it("pins a per-repo branch and sends it with that member only", async () => {
    vi.mocked(listAzureDevOpsConnections).mockResolvedValue([CONNECTION]);
    renderPanel();

    await waitFor(() => expect(screen.getByText("Connected")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: /Choose repositories/ }));
    await screen.findByRole("checkbox", { name: /etl/ });

    fireEvent.click(screen.getByRole("checkbox", { name: /etl/ }));
    fireEvent.click(screen.getByRole("checkbox", { name: /api/ }));
    // Pin only etl; api keeps following its default branch.
    fireEvent.change(screen.getByLabelText("Branch for etl"), {
      target: { value: "R2024-01" },
    });
    fireEvent.change(screen.getByLabelText("Project name"), {
      target: { value: "Pluralfarm" },
    });
    fireEvent.click(screen.getByRole("button", { name: /Connect 2 repositories/ }));

    await waitFor(() =>
      expect(createMultiRepoProject).toHaveBeenCalledWith(
        expect.objectContaining({
          members: [
            expect.objectContaining({ repoName: "etl", branch: "R2024-01" }),
            // Unset, NOT the current default branch name: unset keeps following
            // whatever the provider's default becomes.
            expect.objectContaining({ repoName: "api", branch: undefined }),
          ],
        }),
      ),
    );
  });

  it("offers a branch field only for selected repositories", async () => {
    vi.mocked(listAzureDevOpsConnections).mockResolvedValue([CONNECTION]);
    renderPanel();

    await waitFor(() => expect(screen.getByText("Connected")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: /Choose repositories/ }));
    await screen.findByRole("checkbox", { name: /etl/ });

    // Unselected: no branch input, so the list stays scannable at ten repos.
    expect(screen.queryByLabelText("Branch for etl")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("checkbox", { name: /etl/ }));
    expect(screen.getByLabelText("Branch for etl")).toBeInTheDocument();
    // The placeholder shows what will be used when nothing is typed.
    expect(screen.getByLabelText("Branch for etl")).toHaveAttribute("placeholder", "main");
  });

  it("forwards a pinned branch on the single-repo path too", async () => {
    vi.mocked(listAzureDevOpsConnections).mockResolvedValue([CONNECTION]);
    renderPanel();

    await waitFor(() => expect(screen.getByText("Connected")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: /Choose repositories/ }));
    await screen.findByRole("checkbox", { name: /etl/ });

    fireEvent.click(screen.getByRole("checkbox", { name: /etl/ }));
    fireEvent.change(screen.getByLabelText("Branch for etl"), {
      target: { value: "R2024-01" },
    });
    fireEvent.click(screen.getByRole("button", { name: /Connect & scan/ }));

    await waitFor(() =>
      expect(addAzureDevOpsSource).toHaveBeenCalledWith(
        expect.objectContaining({ branch: "R2024-01" }),
      ),
    );
  });

  it("refuses to reuse an existing project id, which would repoint it", async () => {
    // add_project UPDATES a project whose id exists, repointing local_path and
    // repo_url -- so reusing an id silently hijacks the other project.
    vi.mocked(listAzureDevOpsConnections).mockResolvedValue([CONNECTION]);
    renderPanel({
      existingProjects: [
        {
          id: "pluralfarm",
          display_name: "Pluralfarm",
          platform: "talend",
          source_type: "git",
          status: "complete",
        },
      ],
    });

    await waitFor(() => expect(screen.getByText("Connected")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: /Choose repositories/ }));
    await screen.findByRole("checkbox", { name: /etl/ });
    fireEvent.click(screen.getByRole("checkbox", { name: /etl/ }));
    fireEvent.change(screen.getByLabelText("Project name"), {
      target: { value: "Pluralfarm" },
    });
    fireEvent.click(screen.getByRole("button", { name: /Connect & scan/ }));

    await waitFor(() =>
      expect(screen.getByText(/already exists/)).toBeInTheDocument(),
    );
    expect(addAzureDevOpsSource).not.toHaveBeenCalled();
    expect(createMultiRepoProject).not.toHaveBeenCalled();
  });

});
