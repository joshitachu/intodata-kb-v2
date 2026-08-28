import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import MultiRepoMembersPanel from "../components/MultiRepoMembersPanel";

const listMultiRepoMembers = vi.hoisted(() => vi.fn());
const removeMultiRepoMember = vi.hoisted(() => vi.fn());
const syncMultiRepoProject = vi.hoisted(() => vi.fn());
const addMultiRepoMembers = vi.hoisted(() => vi.fn());
const addProjectZipSource = vi.hoisted(() => vi.fn());
const listGithubAppInstallations = vi.hoisted(() => vi.fn());
const listGithubAppRepos = vi.hoisted(() => vi.fn());
const listAzureDevOpsConnections = vi.hoisted(() => vi.fn());
const listAzureDevOpsRepos = vi.hoisted(() => vi.fn());

vi.mock("../api/client", () => ({
  api: {
    listMultiRepoMembers,
    removeMultiRepoMember,
    syncMultiRepoProject,
    addMultiRepoMembers,
    addProjectZipSource,
    listGithubAppInstallations,
    listGithubAppRepos,
    listAzureDevOpsConnections,
    listAzureDevOpsRepos,
  },
}));

const MEMBER_A = {
  id: "11111111-1111-1111-1111-111111111111",
  provider: "azure_devops",
  repo_identity: "acme/Talend/pplus_ais",
  repo_name: "pplus_ais",
  clone_url: "https://dev.azure.com/acme/Talend/_git/pplus_ais",
  branch: null,
  subproject_name: "pplus_ais",
  local_path: "/data/repos/pf/pplus_ais.__intodoc_repo__",
  last_ingested_commit_sha: "abcdef1234567890",
  last_ingested_at: "2026-08-27T09:00:00Z",
  enabled: true,
};

const MEMBER_B = {
  ...MEMBER_A,
  id: "22222222-2222-2222-2222-222222222222",
  repo_identity: "acme/Talend/pplus_beheer",
  repo_name: "pplus_beheer",
  subproject_name: "pplus_beheer",
  last_ingested_commit_sha: null,
  last_ingested_at: null,
};

describe("MultiRepoMembersPanel (dashboard)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    listMultiRepoMembers.mockResolvedValue({
      project_id: "pf",
      members: [MEMBER_A, MEMBER_B],
      sync: [],
    });
    syncMultiRepoProject.mockResolvedValue({
      project_id: "pf",
      members: [MEMBER_A, MEMBER_B],
      sync: [],
    });
    removeMultiRepoMember.mockResolvedValue({ status: "ok" });
    listGithubAppInstallations.mockResolvedValue({ installations: [] });
    listAzureDevOpsConnections.mockResolvedValue({ connections: [] });
  });

  it("lists members with per-repo ingest state", async () => {
    render(<MultiRepoMembersPanel projectId="pf" />);

    await waitFor(() =>
      expect(screen.getByText("2 member repositories")).toBeInTheDocument(),
    );
    expect(screen.getByText("pplus_ais")).toBeInTheDocument();
    expect(screen.getByText(/abcdef1/)).toBeInTheDocument();
    // A never-ingested member says so rather than showing a blank.
    expect(screen.getByText(/not ingested/)).toBeInTheDocument();
  });

  it("renders for a single-source project so it can gain a sub-project", async () => {
    // It used to return null on an empty member list, which hid the only
    // surface that can add a sub-project from exactly the projects that have
    // none: a one-repo project could never become a two-sub-project one.
    listMultiRepoMembers.mockResolvedValue({ project_id: "solo", members: [], sync: [] });
    render(<MultiRepoMembersPanel projectId="solo" />);

    expect(
      await screen.findByRole("button", { name: "+ Add sub-project" }),
    ).toBeInTheDocument();
    expect(screen.getByText("No member repositories")).toBeInTheDocument();
    // Nothing to sync, so no button offering it.
    expect(screen.queryByRole("button", { name: "Sync all" })).not.toBeInTheDocument();
  });

  it("syncs one repository by identity so siblings are untouched", async () => {
    render(<MultiRepoMembersPanel projectId="pf" />);
    await waitFor(() => expect(screen.getByText("pplus_ais")).toBeInTheDocument());

    fireEvent.click(screen.getAllByRole("button", { name: "Sync" })[0]);

    await waitFor(() =>
      expect(syncMultiRepoProject).toHaveBeenCalledWith("pf", "acme/Talend/pplus_ais"),
    );
  });

  it("syncs every member with Sync all", async () => {
    render(<MultiRepoMembersPanel projectId="pf" />);
    await waitFor(() => expect(screen.getByText("pplus_ais")).toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: "Sync all" }));

    await waitFor(() => expect(syncMultiRepoProject).toHaveBeenCalledWith("pf", undefined));
  });

  it("reports what changed and stays quiet about skipped siblings", async () => {
    syncMultiRepoProject.mockResolvedValue({
      project_id: "pf",
      members: [MEMBER_A, MEMBER_B],
      sync: [
        {
          subproject_name: "pplus_ais",
          repo_identity: "acme/Talend/pplus_ais",
          action: "pulled",
          head_sha: "beef",
          previous_sha: "abcdef1234567890",
          changed: true,
          error: null,
        },
        {
          subproject_name: "pplus_beheer",
          repo_identity: "acme/Talend/pplus_beheer",
          action: "skipped",
          head_sha: null,
          previous_sha: null,
          changed: false,
          error: null,
        },
      ],
    });
    render(<MultiRepoMembersPanel projectId="pf" />);
    await waitFor(() => expect(screen.getByText("pplus_ais")).toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: "Sync all" }));

    await waitFor(() => expect(screen.getByText(/pplus_ais: pulled/)).toBeInTheDocument());
    expect(screen.queryByText(/pplus_beheer: skipped/)).not.toBeInTheDocument();
  });

  it("surfaces a failed member sync", async () => {
    syncMultiRepoProject.mockResolvedValue({
      project_id: "pf",
      members: [MEMBER_A, MEMBER_B],
      sync: [
        {
          subproject_name: "pplus_beheer",
          repo_identity: "acme/Talend/pplus_beheer",
          action: "failed",
          head_sha: null,
          previous_sha: null,
          changed: false,
          error: "not a fast-forward",
        },
      ],
    });
    render(<MultiRepoMembersPanel projectId="pf" />);
    await waitFor(() => expect(screen.getByText("pplus_ais")).toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: "Sync all" }));

    await waitFor(() =>
      expect(screen.getByText(/pplus_beheer: failed — not a fast-forward/)).toBeInTheDocument(),
    );
  });

  it("confirms before removing a member", async () => {
    const onChanged = vi.fn();
    render(<MultiRepoMembersPanel projectId="pf" onChanged={onChanged} />);
    await waitFor(() => expect(screen.getByText("pplus_ais")).toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: "Remove pplus_ais" }));
    expect(removeMultiRepoMember).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "Remove" }));
    await waitFor(() =>
      expect(removeMultiRepoMember).toHaveBeenCalledWith(
        "pf",
        "11111111-1111-1111-1111-111111111111",
      ),
    );
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
  });

  it("abandons a removal when Keep is chosen", async () => {
    render(<MultiRepoMembersPanel projectId="pf" />);
    await waitFor(() => expect(screen.getByText("pplus_ais")).toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: "Remove pplus_ais" }));
    fireEvent.click(screen.getByRole("button", { name: "Keep" }));

    expect(removeMultiRepoMember).not.toHaveBeenCalled();
    expect(screen.queryByText("Remove pplus_ais?")).not.toBeInTheDocument();
  });

  it("shows a load failure instead of an empty panel", async () => {
    listMultiRepoMembers.mockRejectedValue(new Error("members unavailable"));
    render(<MultiRepoMembersPanel projectId="pf" />);

    await waitFor(() => expect(screen.getByText("members unavailable")).toBeInTheDocument());
  });

  it("adds a git sub-project through the repo picker", async () => {
    listGithubAppInstallations.mockResolvedValue({
      installations: [{ installation_id: 42, account_login: "acme" }],
    });
    listGithubAppRepos.mockResolvedValue({
      installation_id: 42,
      account_login: "acme",
      repositories: [
        {
          full_name: "acme/pplus_archive",
          clone_url: "https://github.com/acme/pplus_archive.git",
          private: true,
          default_branch: "main",
        },
      ],
    });
    const MEMBER_C = {
      ...MEMBER_A,
      id: "33333333-3333-3333-3333-333333333333",
      repo_identity: "acme/pplus_archive",
      repo_name: "pplus_archive",
      subproject_name: "pplus_archive",
    };
    // The refetch after a successful add sees the new member.
    listMultiRepoMembers
      .mockResolvedValueOnce({ project_id: "pf", members: [MEMBER_A, MEMBER_B], sync: [] })
      .mockResolvedValue({
        project_id: "pf",
        members: [MEMBER_A, MEMBER_B, MEMBER_C],
        sync: [],
      });
    addMultiRepoMembers.mockResolvedValue({
      project_id: "pf",
      members: [MEMBER_A, MEMBER_B, MEMBER_C],
      sync: [
        {
          subproject_name: "pplus_archive",
          repo_identity: "acme/pplus_archive",
          action: "cloned",
          head_sha: "cafe1234",
          previous_sha: null,
          changed: true,
          error: null,
        },
      ],
    });
    const onChanged = vi.fn();

    render(
      <MultiRepoMembersPanel projectId="pf" sourceType="git" platform="talend" onChanged={onChanged} />,
    );
    fireEvent.click(await screen.findByRole("button", { name: "+ Add sub-project" }));

    // The credential is CHOSEN here: there is no post-install redirect to read
    // an installation id from when adding to a project that already exists.
    fireEvent.click(await screen.findByRole("button", { name: /GitHub App · acme/ }));
    fireEvent.click(await screen.findByRole("checkbox"));
    fireEvent.click(screen.getByRole("button", { name: "Add sub-project" }));

    await waitFor(() =>
      expect(addMultiRepoMembers).toHaveBeenCalledWith("pf", {
        platform: "talend",
        provider: "github",
        installation_id: 42,
        azure_devops_connection_id: undefined,
        members: [
          {
            repo_identity: "acme/pplus_archive",
            repo_name: "pplus_archive",
            clone_url: "https://github.com/acme/pplus_archive.git",
            branch: undefined,
          },
        ],
      }),
    );

    // Freshly cloned is NOT scanned: the clone has a commit sha, so the row
    // would otherwise read as ingested while no scan has read a byte of it.
    await waitFor(() =>
      expect(screen.getByText(/Added, not scanned:/)).toBeInTheDocument(),
    );
    // Once in the banner, once as the member row the refetch brought back, and
    // that row is flagged rather than reading as ingested off its clone sha.
    expect(screen.getAllByText("pplus_archive")).toHaveLength(2);
    expect(screen.getByText(/· not scanned/)).toBeInTheDocument();
    expect(listMultiRepoMembers).toHaveBeenCalledTimes(2);
    expect(onChanged).toHaveBeenCalled();
  });

  it("adds a ZIP sub-project and reports a migrated source tree", async () => {
    listMultiRepoMembers.mockResolvedValue({
      project_id: "werkorder",
      members: [],
      sync: [],
    });
    addProjectZipSource.mockResolvedValue({
      project_id: "werkorder",
      subproject: "werkorder_archive",
      migrated_existing_source: true,
      requires_rescan: true,
      status: "added",
    });

    render(<MultiRepoMembersPanel projectId="werkorder" sourceType="zip" />);
    fireEvent.click(await screen.findByRole("button", { name: "+ Add sub-project" }));

    const file = new File(["zip bytes"], "archive.zip", { type: "application/zip" });
    fireEvent.change(screen.getByLabelText("Sub-project ZIP file"), {
      target: { files: [file] },
    });
    fireEvent.click(screen.getByRole("button", { name: "Add sub-project" }));

    await waitFor(() =>
      expect(addProjectZipSource).toHaveBeenCalledWith("werkorder", file),
    );
    expect(await screen.findByText("werkorder_archive")).toBeInTheDocument();
    expect(screen.getByText(/Added, not scanned:/)).toBeInTheDocument();
    // Where the existing tree's docs now live changed, so it is said out loud.
    expect(
      screen.getByText(/original source was moved into its own sub-project directory/),
    ).toBeInTheDocument();
  });

  it("does not demand a scan the server says is unnecessary", async () => {
    listMultiRepoMembers.mockResolvedValue({ project_id: "werkorder", members: [], sync: [] });
    addProjectZipSource.mockResolvedValue({
      project_id: "werkorder",
      subproject: "werkorder_archive",
      migrated_existing_source: false,
      requires_rescan: false,
      status: "added",
    });

    render(<MultiRepoMembersPanel projectId="werkorder" sourceType="zip" />);
    fireEvent.click(await screen.findByRole("button", { name: "+ Add sub-project" }));
    fireEvent.change(screen.getByLabelText("Sub-project ZIP file"), {
      target: { files: [new File(["z"], "archive.zip")] },
    });
    fireEvent.click(screen.getByRole("button", { name: "Add sub-project" }));

    await waitFor(() => expect(screen.getByText("Added:")).toBeInTheDocument());
    expect(screen.queryByText(/not scanned/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Run Scan/)).not.toBeInTheDocument();
  });

  it("surfaces a failed add without claiming the sub-project exists", async () => {
    listGithubAppInstallations.mockResolvedValue({
      installations: [{ installation_id: 42, account_login: "acme" }],
    });
    listGithubAppRepos.mockResolvedValue({
      installation_id: 42,
      account_login: "acme",
      repositories: [
        {
          full_name: "acme/pplus_archive",
          clone_url: "https://github.com/acme/pplus_archive.git",
          private: true,
          default_branch: "main",
        },
      ],
    });
    addMultiRepoMembers.mockRejectedValue(new Error("403: no access to that repository"));

    render(<MultiRepoMembersPanel projectId="pf" sourceType="git" />);
    fireEvent.click(await screen.findByRole("button", { name: "+ Add sub-project" }));
    fireEvent.click(await screen.findByRole("button", { name: /GitHub App · acme/ }));
    fireEvent.click(await screen.findByRole("checkbox"));
    fireEvent.click(screen.getByRole("button", { name: "Add sub-project" }));

    await waitFor(() =>
      expect(screen.getByText(/403: no access to that repository/)).toBeInTheDocument(),
    );
    expect(screen.queryByText(/Added, not scanned:/)).not.toBeInTheDocument();
  });

  it("says push-triggered updates are not wired yet", async () => {
    // Honesty over polish: otherwise the Sync button looks redundant.
    render(<MultiRepoMembersPanel projectId="pf" />);
    await waitFor(() =>
      expect(
        screen.getByText(/Push-triggered updates are not wired for multi-repo projects yet/),
      ).toBeInTheDocument(),
    );
  });
});
