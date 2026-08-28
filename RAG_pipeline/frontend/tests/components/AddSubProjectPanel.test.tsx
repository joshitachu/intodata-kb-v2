import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AddSubProjectPanel } from "@/app/settings/sources/AddSubProjectPanel";
import {
  addMultiRepoMembers,
  addZipSubProject,
  listAzureDevOpsRepos,
  listGithubAppInstallations,
  listGithubAppRepos,
} from "@/lib/mcpClient";

vi.mock("@/lib/mcpClient", async () => {
  const actual = await vi.importActual<typeof import("@/lib/mcpClient")>("@/lib/mcpClient");
  return {
    ...actual,
    addMultiRepoMembers: vi.fn(),
    addZipSubProject: vi.fn(),
    listAzureDevOpsRepos: vi.fn(),
    listGithubAppInstallations: vi.fn(),
    listGithubAppRepos: vi.fn(),
  };
});

const BASE = {
  projectId: "werkorder",
  platform: "talend",
  members: [],
  onCancel: () => undefined,
};

describe("AddSubProjectPanel — git path", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(listGithubAppInstallations).mockResolvedValue([
      {
        installation_id: 4242,
        account_login: "acme",
        account_type: "Organization",
        settings_url: null,
      },
    ]);
    vi.mocked(listGithubAppRepos).mockResolvedValue({
      account_login: "acme",
      account_type: "Organization",
      settings_url: null,
      repositories: [
        {
          full_name: "acme/werkorder_archive",
          clone_url: "https://github.com/acme/werkorder_archive.git",
          private: true,
          default_branch: "main",
        },
      ],
    });
    vi.mocked(addMultiRepoMembers).mockResolvedValue({
      project_id: "werkorder",
      members: [],
      sync: [],
    });
  });

  it("adds the picked repository as a sub-project", async () => {
    const onAdded = vi.fn();
    render(<AddSubProjectPanel {...BASE} sourceType="git" onAdded={onAdded} />);

    await waitFor(() =>
      expect(screen.getByText("werkorder_archive")).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByRole("checkbox"));
    fireEvent.click(screen.getByRole("button", { name: /^Add sub-project$/ }));

    await waitFor(() =>
      expect(addMultiRepoMembers).toHaveBeenCalledWith({
        projectId: "werkorder",
        platform: "talend",
        provider: "github",
        installationId: 4242,
        azureDevOpsConnectionId: undefined,
        members: [
          {
            repoIdentity: "acme/werkorder_archive",
            repoName: "werkorder_archive",
            cloneUrl: "https://github.com/acme/werkorder_archive.git",
            branch: undefined,
          },
        ],
      }),
    );
    // "Scan to pick up the new code": import and scan stay separate steps, so the
    // panel must not imply anything has been read yet.
    await waitFor(() =>
      expect(onAdded).toHaveBeenCalledWith(expect.stringMatching(/Scan to pick up/), []),
    );
  });

  it("lists an Azure DevOps project's repositories through its organization connection", async () => {
    vi.mocked(listAzureDevOpsRepos).mockResolvedValue({
      organization_name: "acme",
      repositories: [
        {
          id: "r1",
          name: "pplus_beheer",
          project: "Talend",
          clone_url: "https://dev.azure.com/acme/Talend/_git/pplus_beheer",
          default_branch: "main",
        },
      ],
    });
    render(
      <AddSubProjectPanel
        {...BASE}
        sourceType="git"
        connectionKind="azure_devops"
        azureDevOpsConnectionId="conn-1"
        onAdded={vi.fn()}
      />,
    );

    await waitFor(() => expect(listAzureDevOpsRepos).toHaveBeenCalledWith("conn-1"));
    // Never through the GitHub App path.
    expect(listGithubAppInstallations).not.toHaveBeenCalled();
    expect(screen.getByText("pplus_beheer")).toBeInTheDocument();
  });

  it("surfaces a member that could not be cloned instead of claiming success", async () => {
    vi.mocked(addMultiRepoMembers).mockResolvedValue({
      project_id: "werkorder",
      members: [],
      sync: [
        {
          subproject_name: "werkorder_archive",
          repo_identity: "acme/werkorder_archive",
          action: "failed",
          head_sha: null,
          previous_sha: null,
          changed: false,
          error: "auth failed",
        },
      ],
    });
    const onAdded = vi.fn();
    render(<AddSubProjectPanel {...BASE} sourceType="git" onAdded={onAdded} />);

    await waitFor(() => expect(screen.getByText("werkorder_archive")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("checkbox"));
    fireEvent.click(screen.getByRole("button", { name: /^Add sub-project$/ }));

    await waitFor(() =>
      expect(onAdded).toHaveBeenCalledWith(
        expect.stringMatching(/Could not clone: werkorder_archive/),
        [],
      ),
    );
  });
});

describe("AddSubProjectPanel — ZIP path", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("uploads the archive and reports the sub-project the server named", async () => {
    vi.mocked(addZipSubProject).mockResolvedValue({
      project_id: "werkorder",
      subproject: "werkorder_archive",
      migrated_existing_source: true,
      requires_rescan: true,
      status: "added",
    });
    const onAdded = vi.fn();
    render(<AddSubProjectPanel {...BASE} sourceType="zip" onAdded={onAdded} />);

    const file = new File(["zip"], "archive.zip", { type: "application/zip" });
    const input = screen.getByLabelText("Sub-project ZIP file") as HTMLInputElement;
    fireEvent.change(input, { target: { files: [file] } });
    fireEvent.click(screen.getByRole("button", { name: /^Add sub-project$/ }));

    await waitFor(() =>
      expect(addZipSubProject).toHaveBeenCalledWith(
        expect.objectContaining({ projectId: "werkorder", file }),
      ),
    );
    // The migration is reported: on the first added sub-project the original
    // tree moves under a name of its own, and an unexplained extra sub-project
    // looks like a bug.
    await waitFor(() =>
      expect(onAdded).toHaveBeenCalledWith(
        expect.stringMatching(/existing source moved into its own sub-project/),
        ["werkorder_archive"],
      ),
    );
  });

  it("keeps the failure visible and does not report success", async () => {
    vi.mocked(addZipSubProject).mockRejectedValue(
      new Error("Adding the sub-project failed (400): not a ZIP project"),
    );
    const onAdded = vi.fn();
    render(<AddSubProjectPanel {...BASE} sourceType="zip" onAdded={onAdded} />);

    fireEvent.change(screen.getByLabelText("Sub-project ZIP file"), {
      target: { files: [new File(["zip"], "archive.zip")] },
    });
    fireEvent.click(screen.getByRole("button", { name: /^Add sub-project$/ }));

    await waitFor(() =>
      expect(screen.getByText(/not a ZIP project/)).toBeInTheDocument(),
    );
    expect(onAdded).not.toHaveBeenCalled();
  });
});
