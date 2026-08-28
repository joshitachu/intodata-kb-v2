import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { GithubAppConnectPanel } from "@/app/settings/sources/GithubAppConnectPanel";
import {
  addGithubSource,
  createMultiRepoProject,
  getGithubAppInstallUrl,
  listGithubAppInstallations,
  listGithubAppRepos,
} from "@/lib/mcpClient";

vi.mock("@/lib/mcpClient", async () => {
  const actual = await vi.importActual<typeof import("@/lib/mcpClient")>("@/lib/mcpClient");
  return {
    ...actual,
    addGithubSource: vi.fn(),
    createMultiRepoProject: vi.fn(),
    getGithubAppInstallUrl: vi.fn(),
    listGithubAppInstallations: vi.fn(),
    listGithubAppRepos: vi.fn(),
  };
});

const INSTALLATION = {
  installation_id: 4242,
  account_login: "acme",
  account_type: "Organization" as const,
  settings_url: "https://github.com/organizations/acme/settings/installations/4242",
};

const PERSONAL_INSTALLATION = {
  installation_id: 4343,
  account_login: "jane",
  account_type: "User" as const,
  settings_url: "https://github.com/settings/installations/4343",
};

const REPOS = [
  { full_name: "acme/etl", clone_url: "https://github.com/acme/etl.git", private: false, default_branch: "main" },
  { full_name: "acme/api", clone_url: "https://github.com/acme/api.git", private: true, default_branch: "main" },
];

function renderPanel(overrides: Partial<Parameters<typeof GithubAppConnectPanel>[0]> = {}) {
  const onConnected = vi.fn();
  const onUseTokenInstead = vi.fn();
  render(
    <GithubAppConnectPanel
      platforms={[{ id: "talend", label: "Talend" }]}
      defaultPlatform="talend"
      existingProjects={[]}
      onConnected={onConnected}
      onUseTokenInstead={onUseTokenInstead}
      {...overrides}
    />,
  );
  return { onConnected, onUseTokenInstead };
}

async function openPicker() {
  await waitFor(() =>
    expect(
      screen.getByRole("button", { name: "Choose repositories from acme" }),
    ).toBeInTheDocument(),
  );
  fireEvent.click(screen.getByRole("button", { name: "Choose repositories from acme" }));
  await screen.findByRole("checkbox", { name: /etl/ });
}

describe("GithubAppConnectPanel", () => {
  beforeEach(() => {
    // Call history is not cleared globally; without this, a
    // `not.toHaveBeenCalled()` assertion sees the previous test's calls.
    vi.clearAllMocks();
    vi.mocked(listGithubAppInstallations).mockResolvedValue([INSTALLATION]);
    vi.mocked(listGithubAppRepos).mockResolvedValue({
      account_login: "acme",
      account_type: "Organization",
      settings_url: "https://github.com/organizations/acme/settings/installations/4242",
      repositories: REPOS,
    });
    vi.mocked(getGithubAppInstallUrl).mockResolvedValue("https://github.com/apps/x/installations/new");
    vi.mocked(addGithubSource).mockResolvedValue({
      id: "etl",
      display_name: "etl",
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

  it("shows separate personal and organization installations without implying a primary account", async () => {
    vi.mocked(listGithubAppInstallations).mockResolvedValue([
      INSTALLATION,
      PERSONAL_INSTALLATION,
    ]);
    renderPanel();

    expect(await screen.findByText("GitHub installations (2)")).toBeInTheDocument();
    expect(screen.getByText("Organization")).toBeInTheDocument();
    expect(screen.getByText("Personal account")).toBeInTheDocument();

    fireEvent.click(
      screen.getByRole("button", { name: "Choose repositories from jane" }),
    );
    await waitFor(() => expect(listGithubAppRepos).toHaveBeenCalledWith(4343));
  });

  it("opens the owner-specific GitHub settings page", async () => {
    const open = vi.spyOn(window, "open").mockImplementation(() => null);
    renderPanel();

    fireEvent.click(
      await screen.findByRole("button", { name: "Manage repository access for acme" }),
    );

    expect(open).toHaveBeenCalledWith(
      "https://github.com/organizations/acme/settings/installations/4242",
      "_blank",
      "noopener,noreferrer",
    );
    open.mockRestore();
  });

  it("registers a single picked repository on the single-repo path", async () => {
    // One repo must keep using /add-repo: that sets `repo_url`, which is what
    // the push-webhook match keys on. The multi-repo path has no webhook yet.
    const { onConnected } = renderPanel();
    await openPicker();

    fireEvent.click(screen.getByRole("checkbox", { name: /etl/ }));
    fireEvent.click(screen.getByRole("button", { name: /Import repository/ }));

    await waitFor(() =>
      expect(addGithubSource).toHaveBeenCalledWith({
        repoUrl: "https://github.com/acme/etl.git",
        projectId: "etl",
        platform: "talend",
        displayName: "etl",
        installationId: 4242,
      }),
    );
    expect(createMultiRepoProject).not.toHaveBeenCalled();
    await waitFor(() => expect(onConnected).toHaveBeenCalled());
  });

  it("registers several picked repositories as one multi-repo project", async () => {
    const { onConnected } = renderPanel();
    await openPicker();

    fireEvent.click(screen.getByRole("checkbox", { name: /etl/ }));
    fireEvent.click(screen.getByRole("checkbox", { name: /api/ }));
    fireEvent.click(screen.getByRole("button", { name: /Import 2 repositories/ }));

    await waitFor(() =>
      expect(createMultiRepoProject).toHaveBeenCalledWith({
        // Both repos share the owner `acme`, so that is the suggested name.
        projectId: "acme",
        displayName: "acme",
        platform: "talend",
        provider: "github",
        installationId: 4242,
        members: [
          {
            // full_name verbatim — the key a push event resolves against.
            repoIdentity: "acme/api",
            repoName: "api",
            cloneUrl: "https://github.com/acme/api.git",
          },
          {
            repoIdentity: "acme/etl",
            repoName: "etl",
            cloneUrl: "https://github.com/acme/etl.git",
          },
        ],
      }),
    );
    expect(addGithubSource).not.toHaveBeenCalled();
    await waitFor(() => expect(onConnected).toHaveBeenCalled());
  });

  it("reports members that failed to clone instead of claiming full success", async () => {
    vi.mocked(createMultiRepoProject).mockResolvedValue({
      project_id: "acme",
      members: [],
      sync: [
        {
          subproject_name: "api",
          repo_identity: "acme/api",
          action: "failed",
          head_sha: null,
          previous_sha: null,
          changed: false,
          error: "authentication failed",
        },
      ],
    });
    renderPanel();
    await openPicker();

    fireEvent.click(screen.getByRole("checkbox", { name: /etl/ }));
    fireEvent.click(screen.getByRole("checkbox", { name: /api/ }));
    fireEvent.click(screen.getByRole("button", { name: /Import 2 repositories/ }));

    await waitFor(() =>
      expect(screen.getByText(/could not be cloned: api/)).toBeInTheDocument(),
    );
  });

  it("notes a repository already in a project but still allows selecting it", async () => {
    renderPanel({
      existingProjects: [
        {
          id: "etl",
          display_name: "etl",
          platform: "talend",
          source_type: "git",
          status: "complete",
          // Case and .git suffix differ on purpose: matching is canonical.
          repo_url: "https://github.com/acme/ETL",
        },
      ],
    });
    await openPicker();

    const checkbox = screen.getByRole("checkbox", { name: /etl already in a project/ });
    // Selectable: one repo may belong to several projects.
    expect(checkbox).toBeEnabled();
    expect(screen.getByText("already in a project")).toBeInTheDocument();
  });

  it("selects every listed repository with one action", async () => {
    renderPanel();
    await openPicker();

    fireEvent.click(screen.getByRole("button", { name: /Select all \(2\)/ }));
    expect(screen.getByRole("checkbox", { name: /etl/ })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: /api/ })).toBeChecked();

    fireEvent.click(screen.getByRole("button", { name: "Clear all" }));
    expect(screen.getByRole("checkbox", { name: /etl/ })).not.toBeChecked();
  });
});
