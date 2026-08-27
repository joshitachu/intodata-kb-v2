import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import AddRepoDialog from "../components/AddRepoDialog";

const addRepoMutate = vi.hoisted(() => vi.fn());
// Mutable so a test can install a colliding project id.
const existingProjects = vi.hoisted(() => [] as { id: string }[]);
const listGithubAppRepos = vi.hoisted(() => vi.fn());
const listAzureDevOpsConnections = vi.hoisted(() => vi.fn());
const listAzureDevOpsRepos = vi.hoisted(() => vi.fn());
const connectAzureDevOps = vi.hoisted(() => vi.fn());
const createMultiRepoProject = vi.hoisted(() => vi.fn());

vi.mock("../hooks/useDiscovery", () => ({
  usePlatforms: () => ({ data: { platforms: [{ id: "talend", label: "Talend" }] } }),
}));

vi.mock("../hooks/useProjects", () => ({
  useAddRepo: () => ({ mutate: addRepoMutate, isPending: false, isError: false, error: null }),
  useUploadZip: () => ({ mutate: vi.fn(), isPending: false, isError: false, error: null }),
  // The dialog reads the project list to block reuse of an existing project id.
  useProjects: () => ({ data: { projects: existingProjects, total: existingProjects.length } }),
}));

vi.mock("../api/client", () => ({
  api: {
    listGithubAppRepos,
    getGithubAppInstallUrl: vi.fn(),
    listAzureDevOpsConnections,
    listAzureDevOpsRepos,
    connectAzureDevOps,
    testAzureDevOpsConnection: vi.fn(),
    deleteAzureDevOpsConnection: vi.fn(),
    createMultiRepoProject,
  },
}));

const CONNECTION = {
  id: "3f2504e0-4f89-11d3-9a0c-0305e82c3301",
  organization_url: "https://dev.azure.com/acme",
  organization_name: "acme",
  account_name: "Ada L",
  last_verified_at: null,
};

const ADO_REPOS = [
  { id: "r1", name: "pplus_ais", project: "Talend", clone_url: "https://dev.azure.com/acme/Talend/_git/pplus_ais" },
  { id: "r2", name: "pplus_beheer", project: "Talend", clone_url: "https://dev.azure.com/acme/Talend/_git/pplus_beheer" },
  { id: "r3", name: "Integratie", project: "Enterprise_DWH", clone_url: "https://dev.azure.com/acme/Enterprise_DWH/_git/Integratie" },
];

const GH_REPOS = [
  { full_name: "acme/etl", clone_url: "https://github.com/acme/etl.git", private: false, default_branch: "main" },
  { full_name: "acme/api", clone_url: "https://github.com/acme/api.git", private: true, default_branch: "main" },
];

async function openAzureTab() {
  fireEvent.click(screen.getByRole("button", { name: "Azure DevOps" }));
  await waitFor(() =>
    expect(screen.getByRole("button", { name: "Choose repositories" })).toBeInTheDocument(),
  );
  fireEvent.click(screen.getByRole("button", { name: "Choose repositories" }));
  await screen.findByRole("checkbox", { name: /pplus_ais/ });
}

describe("AddRepoDialog multi-select", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    listGithubAppRepos.mockResolvedValue({
      installation_id: 4242,
      account_login: "acme",
      repositories: GH_REPOS,
    });
    listAzureDevOpsConnections.mockResolvedValue({ connections: [CONNECTION] });
    listAzureDevOpsRepos.mockResolvedValue({
      connection_id: CONNECTION.id,
      organization_name: "acme",
      repositories: ADO_REPOS,
    });
    connectAzureDevOps.mockResolvedValue(CONNECTION);
    createMultiRepoProject.mockResolvedValue({
      project_id: "pluralfarm",
      members: [],
      sync: [],
    });
    window.history.replaceState({}, "", "/");
  });

  it("lists Azure DevOps repositories grouped by project", async () => {
    render(<AddRepoDialog open onClose={() => {}} />);
    await openAzureTab();

    // Scoped to the list: "Talend" is also a Platform option, so an unscoped
    // getByText would match two unrelated elements.
    const list = screen.getByRole("group", { name: "Repositories" });
    expect(within(list).getByText("Talend")).toBeInTheDocument();
    expect(within(list).getByText("Enterprise_DWH")).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: /Integratie/ })).toBeInTheDocument();
    // Enterprise_DWH sorts before Talend, so the single-repo group renders first.
    expect(within(list).getAllByRole("checkbox").map((b) => (b as HTMLInputElement).checked))
      .toEqual([false, false, false]);
  });

  it("creates a multi-repo project from several Azure DevOps repositories", async () => {
    const onClose = vi.fn();
    render(<AddRepoDialog open onClose={onClose} />);
    await openAzureTab();

    // "all 2" selects every repository in the Talend project.
    fireEvent.click(screen.getByRole("button", { name: "all 2" }));
    // Both selected repos share the Talend project, so it is suggested as the id.
    expect((screen.getByLabelText("Project ID") as HTMLInputElement).value).toBe("talend");

    fireEvent.click(screen.getByRole("button", { name: "Add 2 repositories" }));

    await waitFor(() =>
      expect(createMultiRepoProject).toHaveBeenCalledWith({
        project_id: "talend",
        display_name: "talend",
        platform: "talend",
        provider: "azure_devops",
        installation_id: undefined,
        azure_devops_connection_id: CONNECTION.id,
        members: [
          {
            // org/project/repo — the identity a push event resolves against.
            repo_identity: "acme/Talend/pplus_ais",
            repo_name: "pplus_ais",
            clone_url: "https://dev.azure.com/acme/Talend/_git/pplus_ais",
          },
          {
            repo_identity: "acme/Talend/pplus_beheer",
            repo_name: "pplus_beheer",
            clone_url: "https://dev.azure.com/acme/Talend/_git/pplus_beheer",
          },
        ],
      }),
    );
    expect(addRepoMutate).not.toHaveBeenCalled();
    await waitFor(() => expect(onClose).toHaveBeenCalled());
  });

  it("keeps a single Azure DevOps repository on the single-repo path", async () => {
    // One repo must keep using /mcp/add-repo: that sets `repo_url`, which is
    // what the push-webhook match keys on.
    render(<AddRepoDialog open onClose={() => {}} />);
    await openAzureTab();

    fireEvent.click(screen.getByRole("checkbox", { name: /Integratie/ }));
    fireEvent.click(screen.getByRole("button", { name: "Add" }));

    expect(addRepoMutate).toHaveBeenCalledWith(
      {
        url: "https://dev.azure.com/acme/Enterprise_DWH/_git/Integratie",
        project_id: "integratie",
        platform: "talend",
        installation_id: undefined,
        // The organization connection supplies the clone credential; without
        // this the server would fall back to an unauthenticated clone.
        azure_devops_connection_id: CONNECTION.id,
      },
      expect.anything(),
    );
    expect(createMultiRepoProject).not.toHaveBeenCalled();
  });

  it("clears a stale suggested id when the selection spans projects", async () => {
    render(<AddRepoDialog open onClose={() => {}} />);
    await openAzureTab();

    fireEvent.click(screen.getByRole("checkbox", { name: /pplus_ais/ }));
    expect((screen.getByLabelText("Project ID") as HTMLInputElement).value).toBe("pplus_ais");

    // Adding a repo from another project leaves no obvious shared name, so the
    // field is cleared rather than keeping a name taken from one member.
    fireEvent.click(screen.getByRole("checkbox", { name: /Integratie/ }));
    expect((screen.getByLabelText("Project ID") as HTMLInputElement).value).toBe("");
    expect(screen.getByRole("button", { name: "Add 2 repositories" })).toBeDisabled();
  });

  it("keeps a hand-typed project id when the selection changes", async () => {
    render(<AddRepoDialog open onClose={() => {}} />);
    await openAzureTab();

    fireEvent.change(screen.getByLabelText("Project ID"), { target: { value: "pluralfarm" } });
    fireEvent.click(screen.getByRole("checkbox", { name: /pplus_ais/ }));
    fireEvent.click(screen.getByRole("checkbox", { name: /Integratie/ }));

    expect((screen.getByLabelText("Project ID") as HTMLInputElement).value).toBe("pluralfarm");
  });

  it("reports members that failed to clone instead of closing silently", async () => {
    const onClose = vi.fn();
    createMultiRepoProject.mockResolvedValue({
      project_id: "talend",
      members: [{ subproject_name: "pplus_ais" }],
      sync: [
        {
          subproject_name: "pplus_beheer",
          repo_identity: "acme/Talend/pplus_beheer",
          action: "failed",
          head_sha: null,
          previous_sha: null,
          changed: false,
          error: "authentication failed",
        },
      ],
    });
    render(<AddRepoDialog open onClose={onClose} />);
    await openAzureTab();

    fireEvent.click(screen.getByRole("button", { name: "all 2" }));
    fireEvent.click(screen.getByRole("button", { name: "Add 2 repositories" }));

    await waitFor(() =>
      expect(screen.getByText(/could not be cloned: pplus_beheer/)).toBeInTheDocument(),
    );
    // The dialog stays open so the operator sees which member needs a retry.
    expect(onClose).not.toHaveBeenCalled();
  });

  it("creates a multi-repo project from several GitHub App repositories", async () => {
    window.history.replaceState({}, "", "/?installation_id=4242");
    render(<AddRepoDialog open onClose={() => {}} />);
    await screen.findByRole("checkbox", { name: /etl/ });

    fireEvent.click(screen.getByRole("button", { name: "Select all (2)" }));
    fireEvent.click(screen.getByRole("button", { name: "Add 2 repositories" }));

    await waitFor(() =>
      expect(createMultiRepoProject).toHaveBeenCalledWith(
        expect.objectContaining({
          provider: "github",
          installation_id: 4242,
          azure_devops_connection_id: undefined,
          members: [
            {
              // full_name verbatim — GitHub's own stable identity.
              repo_identity: "acme/api",
              repo_name: "api",
              clone_url: "https://github.com/acme/api.git",
            },
            {
              repo_identity: "acme/etl",
              repo_name: "etl",
              clone_url: "https://github.com/acme/etl.git",
            },
          ],
        }),
      ),
    );
  });

  it("filters the repository list", async () => {
    render(<AddRepoDialog open onClose={() => {}} />);
    await openAzureTab();

    fireEvent.change(screen.getByLabelText("Filter repositories"), {
      target: { value: "beheer" },
    });
    expect(screen.queryByRole("checkbox", { name: /pplus_ais/ })).not.toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: /pplus_beheer/ })).toBeInTheDocument();
  });

  it("connects a new Azure DevOps organization and moves to its repositories", async () => {
    listAzureDevOpsConnections.mockResolvedValue({ connections: [] });
    render(<AddRepoDialog open onClose={() => {}} />);

    fireEvent.click(screen.getByRole("button", { name: "Azure DevOps" }));
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Connect Azure DevOps" })).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByRole("button", { name: "Connect Azure DevOps" }));

    fireEvent.change(screen.getByPlaceholderText("https://dev.azure.com/your-org"), {
      target: { value: "https://dev.azure.com/acme" },
    });
    fireEvent.change(screen.getByPlaceholderText("Personal access token"), {
      target: { value: "pat-value" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Connect" }));

    await waitFor(() =>
      expect(connectAzureDevOps).toHaveBeenCalledWith("https://dev.azure.com/acme", "pat-value"),
    );
    await screen.findByRole("checkbox", { name: /pplus_ais/ });
    // The token must not survive in the form once the server holds it.
    expect(screen.queryByPlaceholderText("Personal access token")).not.toBeInTheDocument();
  });

  it("pins a per-repo branch and sends it with that member only", async () => {
    render(<AddRepoDialog open onClose={() => {}} />);
    await openAzureTab();

    fireEvent.click(screen.getByRole("button", { name: "all 2" }));
    // Pin only pplus_ais; pplus_beheer keeps following its default branch.
    fireEvent.change(screen.getByLabelText("Branch for pplus_ais"), {
      target: { value: "R2024-01" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Add 2 repositories" }));

    await waitFor(() =>
      expect(createMultiRepoProject).toHaveBeenCalledWith(
        expect.objectContaining({
          members: [
            expect.objectContaining({ repo_name: "pplus_ais", branch: "R2024-01" }),
            // Unset, NOT the current default name: unset keeps following the
            // provider's default if it later changes.
            expect.objectContaining({ repo_name: "pplus_beheer", branch: undefined }),
          ],
        }),
      ),
    );
  });

  it("offers a branch field only for selected repositories", async () => {
    render(<AddRepoDialog open onClose={() => {}} />);
    await openAzureTab();

    expect(screen.queryByLabelText("Branch for pplus_ais")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("checkbox", { name: /pplus_ais/ }));
    expect(screen.getByLabelText("Branch for pplus_ais")).toBeInTheDocument();
  });

  it("forwards a pinned branch on the single-repo path too", async () => {
    render(<AddRepoDialog open onClose={() => {}} />);
    await openAzureTab();

    fireEvent.click(screen.getByRole("checkbox", { name: /Integratie/ }));
    fireEvent.change(screen.getByLabelText("Branch for Integratie"), {
      target: { value: "R2024-01" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Add" }));

    expect(addRepoMutate).toHaveBeenCalledWith(
      expect.objectContaining({ branch: "R2024-01" }),
      expect.anything(),
    );
  });

  it("refuses to reuse an existing project id, which would repoint it", async () => {
    // add_project UPDATES a project whose id exists, repointing local_path and
    // repo_url -- so reusing an id silently hijacks the other project.
    existingProjects.push({ id: "talend" });
    try {
      render(<AddRepoDialog open onClose={() => {}} />);
      await openAzureTab();

      fireEvent.click(screen.getByRole("button", { name: "all 2" }));
      // The suggestion for the shared Talend project slugifies to "talend",
      // which already exists.
      expect((screen.getByLabelText("Project ID") as HTMLInputElement).value).toBe("talend");
      fireEvent.click(screen.getByRole("button", { name: "Add 2 repositories" }));

      await waitFor(() => expect(screen.getByText(/already exists/)).toBeInTheDocument());
      expect(createMultiRepoProject).not.toHaveBeenCalled();
      expect(addRepoMutate).not.toHaveBeenCalled();
    } finally {
      existingProjects.length = 0;
    }
  });

});
