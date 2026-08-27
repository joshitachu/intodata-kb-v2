import { useCallback, useEffect, useMemo, useState } from "react";
import { usePlatforms } from "../hooks/useDiscovery";
import { useAddRepo, useProjects, useUploadZip } from "../hooks/useProjects";
import { parseApiError } from "../lib/errors";
import { api } from "../api/client";
import RepoMultiSelect from "./RepoMultiSelect";
import {
  branchForMember,
  collidesWithExistingProject,
  orderedSelection,
  slugifyProjectId,
  suggestedProjectName,
  toPickableAzureRepos,
  toPickableGithubRepos,
  type PickableRepo,
} from "../lib/repoSelection";
import type { AzureDevOpsConnection, AzureDevOpsRepo } from "../types";

interface Props {
  open: boolean;
  onClose: () => void;
}

interface AppRepo {
  full_name: string | null;
  clone_url: string | null;
  private: boolean | null;
  default_branch: string | null;
}

type Tab = "path" | "zip" | "app" | "ado";

const PAT_DOCS_URL =
  "https://learn.microsoft.com/azure/devops/organizations/accounts/use-personal-access-tokens-to-authenticate";

/**
 * Add Repository.
 *
 * Four routes in, matching the RAG frontend's /settings/sources panels:
 *
 *  - URL / Path   -- paste a git remote or a local path (optionally with a PAT).
 *  - Upload ZIP   -- an archive; nested archives already become sub-projects.
 *  - GitHub App   -- install the App, then multi-select from its repositories.
 *  - Azure DevOps -- connect an organization with a PAT, then multi-select from
 *                    every repository that token can read, across all projects.
 *
 * The two connection tabs share one picker. Selecting a single repository keeps
 * the historic single-repo path (`/mcp/add-repo`), which sets `repo_url` and so
 * keeps push-webhook auto-update working. Selecting several creates a multi-repo
 * project instead: one doc set per repository plus a shared cross-repo view.
 */
export default function AddRepoDialog({ open, onClose }: Props) {
  const [tab, setTab] = useState<Tab>("path");
  const [url, setUrl] = useState("");
  const [projectId, setProjectId] = useState("");
  // Whether the operator typed the project id themselves. Re-suggesting on
  // every selection change would fight their typing; suggesting only while the
  // field is EMPTY is worse -- picking one repo fills it, and every repo added
  // after that leaves the project named after a single member.
  const [projectIdEdited, setProjectIdEdited] = useState(false);
  const [platform, setPlatform] = useState("talend");
  const [file, setFile] = useState<File | null>(null);
  const [githubToken, setGithubToken] = useState("");
  const [selectedKeys, setSelectedKeys] = useState<Set<string>>(new Set());
  // Per-repo branch pin, keyed by repo key. Absent or blank means "follow the
  // provider's default branch", which then tracks upstream changes to it.
  const [branchOverrides, setBranchOverrides] = useState<Record<string, string>>({});
  const [multiBusy, setMultiBusy] = useState(false);

  // GitHub App connect state.
  const [appInstallationId, setAppInstallationId] = useState<number | null>(null);
  const [appRepos, setAppRepos] = useState<AppRepo[]>([]);
  const [appAccount, setAppAccount] = useState<string | null>(null);
  const [appLoadingRepos, setAppLoadingRepos] = useState(false);
  const [appStarting, setAppStarting] = useState(false);
  const [appError, setAppError] = useState<string | null>(null);

  // Azure DevOps connect state.
  const [adoConnections, setAdoConnections] = useState<AzureDevOpsConnection[] | null>(null);
  const [adoActive, setAdoActive] = useState<AzureDevOpsConnection | null>(null);
  const [adoRepos, setAdoRepos] = useState<AzureDevOpsRepo[]>([]);
  const [adoLoadingRepos, setAdoLoadingRepos] = useState(false);
  const [adoFormOpen, setAdoFormOpen] = useState(false);
  const [adoOrgUrl, setAdoOrgUrl] = useState("");
  const [adoPat, setAdoPat] = useState("");
  const [adoConnecting, setAdoConnecting] = useState(false);
  const [adoError, setAdoError] = useState<string | null>(null);

  const addRepo = useAddRepo();
  const uploadZip = useUploadZip();
  // Needed for the project-id collision guard below: the server UPDATES a
  // project whose id already exists rather than rejecting it.
  const { data: projectsData } = useProjects();
  const { data: platformsData } = usePlatforms();
  const platforms = platformsData?.platforms ?? [
    { id: "talend", label: "Talend" },
    { id: "workato", label: "Workato" },
    { id: "lobster", label: "Lobster" },
    { id: "generic", label: "Generic" },
  ];

  // Credentials are injected as HTTPS userinfo by the server, so they only apply
  // to http(s) remotes — never to a local path or an SSH (git@) URL, which
  // authenticates on the agent key instead.
  const trimmedUrl = url.trim();
  const isHttpUrl = /^https?:\/\//i.test(trimmedUrl);
  const isGithubUrl = /^https?:\/\/([^/@]*@)?(www\.)?github\.com\//i.test(trimmedUrl);

  const pickableRepos: PickableRepo[] = useMemo(() => {
    // Nothing is "already connected"-aware here: the dashboard's dialog does not
    // load the project list, and the server rejects a duplicate project id
    // anyway. The picker still renders reasons for provider-side unavailability.
    if (tab === "app") return toPickableGithubRepos(appRepos, new Set());
    if (tab === "ado")
      return toPickableAzureRepos(adoRepos, adoActive?.organization_name ?? "", new Set());
    return [];
  }, [tab, appRepos, adoRepos, adoActive]);

  const selectedRepos = useMemo(
    () => orderedSelection(pickableRepos, selectedKeys),
    [pickableRepos, selectedKeys],
  );

  // Detect the post-install redirect (?installation_id=...) and load its repos.
  useEffect(() => {
    if (!open) return;
    const params = new URLSearchParams(window.location.search);
    const raw = params.get("installation_id");
    if (raw && /^\d+$/.test(raw)) {
      setTab("app");
      setAppInstallationId(Number(raw));
    }
  }, [open]);

  // Named rather than inlined in the effect so Refresh can call it directly.
  // Re-setting appInstallationId to the same value would not re-run the effect,
  // so a Refresh button wired that way silently does nothing.
  const loadAppRepos = useCallback((installationId: number) => {
    let cancelled = false;
    setAppLoadingRepos(true);
    setAppError(null);
    api
      .listGithubAppRepos(installationId)
      .then((data) => {
        if (cancelled) return;
        setAppRepos(data.repositories);
        setAppAccount(data.account_login);
      })
      .catch((err: unknown) => {
        if (!cancelled) setAppError(err instanceof Error ? err.message : String(err));
      })
      .finally(() => {
        if (!cancelled) setAppLoadingRepos(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (appInstallationId == null) return;
    return loadAppRepos(appInstallationId);
  }, [appInstallationId, loadAppRepos]);

  // Load Azure DevOps connections when that tab is first opened.
  useEffect(() => {
    if (!open || tab !== "ado" || adoConnections != null) return;
    let cancelled = false;
    api
      .listAzureDevOpsConnections()
      .then((data) => {
        if (!cancelled) setAdoConnections(data.connections ?? []);
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setAdoConnections([]);
          setAdoError(err instanceof Error ? err.message : String(err));
        }
      });
    return () => {
      cancelled = true;
    };
  }, [open, tab, adoConnections]);

  if (!open) return null;

  const clearInstallParam = () => {
    const u = new URL(window.location.href);
    u.searchParams.delete("installation_id");
    u.searchParams.delete("connected");
    window.history.replaceState({}, "", u.toString());
  };

  function changeSelection(next: Set<string>) {
    setSelectedKeys(next);
    if (!projectIdEdited) {
      const suggestion = suggestedProjectName(orderedSelection(pickableRepos, next));
      // An empty suggestion clears the field on purpose: a stale name from an
      // earlier, smaller selection would silently label the whole project.
      setProjectId(suggestion ? slugifyProjectId(suggestion) : "");
    }
  }

  const loadAdoRepos = (connection: AzureDevOpsConnection) => {
    setAdoActive(connection);
    setAdoRepos([]);
    setSelectedKeys(new Set());
    setAdoLoadingRepos(true);
    setAdoError(null);
    api
      .listAzureDevOpsRepos(connection.id)
      .then((data) => setAdoRepos(data.repositories ?? []))
      .catch((err: unknown) =>
        setAdoError(err instanceof Error ? err.message : String(err)),
      )
      .finally(() => setAdoLoadingRepos(false));
  };

  const submitAdoConnection = async () => {
    const org = adoOrgUrl.trim();
    const pat = adoPat.trim();
    if (!org || !pat) {
      setAdoError("Organization URL and personal access token are required.");
      return;
    }
    setAdoConnecting(true);
    setAdoError(null);
    try {
      const connection = await api.connectAzureDevOps(org, pat);
      // Drop the token from component state as soon as the server has it.
      setAdoPat("");
      setAdoOrgUrl("");
      setAdoFormOpen(false);
      setAdoConnections((current) => [...(current ?? []), connection]);
      loadAdoRepos(connection);
    } catch (err) {
      setAdoError(err instanceof Error ? err.message : String(err));
    } finally {
      setAdoConnecting(false);
    }
  };

  const handleSubmitPath = () => {
    if (!url || !projectId) return;
    addRepo.mutate(
      {
        url,
        project_id: projectId,
        platform,
        // Only an https remote can carry the token; never ship it for a local path or SSH URL.
        github_token: isHttpUrl && githubToken ? githubToken : undefined,
      },
      { onSuccess: () => { onClose(); reset(); } }
    );
  };

  const handleSubmitZip = () => {
    if (!file || !projectId) return;
    uploadZip.mutate(
      { file, projectId, platform },
      { onSuccess: () => { onClose(); reset(); } }
    );
  };

  const startAppInstall = async () => {
    setAppStarting(true);
    setAppError(null);
    try {
      const { install_url } = await api.getGithubAppInstallUrl();
      window.location.href = install_url;
    } catch (err) {
      setAppError(err instanceof Error ? err.message : String(err));
      setAppStarting(false);
    }
  };

  /**
   * Register the selected repositories.
   *
   * One repo keeps the single-repo path deliberately: it sets `repo_url`, which
   * is what the existing push-webhook match keys on. Routing it through the
   * multi-repo path would silently cost auto-update.
   */
  const handleSubmitSelection = async () => {
    const provider = tab === "app" ? "github" : "azure_devops";
    const installationId = tab === "app" ? appInstallationId ?? undefined : undefined;
    const connectionId = tab === "ado" ? adoActive?.id : undefined;
    const setErr = tab === "app" ? setAppError : setAdoError;

    if (selectedRepos.length === 0 || !projectId) return;

    // Reusing an existing project id is the destructive collision: the server's
    // add_project UPDATES a project whose id already exists, repointing its
    // local_path, repo_url and platform. Reusing a REPOSITORY is fine and
    // deliberately allowed; reusing an id silently hijacks another project.
    const existingIds = (projectsData?.projects ?? []).map((p) => p.id);
    if (collidesWithExistingProject(projectId, existingIds)) {
      setErr(
        `A project called "${projectId}" already exists. Pick another id — ` +
          "reusing it would repoint the existing project at this source.",
      );
      return;
    }

    if (selectedRepos.length === 1) {
      const only = selectedRepos[0];
      addRepo.mutate(
        {
          url: only.cloneUrl,
          project_id: projectId,
          platform,
          // Forward the pin: /mcp/add-repo takes a branch, and dropping it here
          // would clone the default while the UI showed a pin.
          branch: branchForMember(only, branchOverrides),
          installation_id: installationId,
          azure_devops_connection_id: connectionId,
        },
        {
          onSuccess: () => {
            clearInstallParam();
            onClose();
            reset();
          },
        },
      );
      return;
    }

    setMultiBusy(true);
    setErr(null);
    try {
      const result = await api.createMultiRepoProject({
        project_id: projectId,
        display_name: projectId,
        platform,
        provider,
        installation_id: installationId,
        azure_devops_connection_id: connectionId,
        members: selectedRepos.map((repo) => ({
          repo_identity: repo.identity,
          repo_name: repo.name,
          clone_url: repo.cloneUrl,
          branch: branchForMember(repo, branchOverrides),
        })),
      });
      const failed = result.sync.filter((outcome) => outcome.action === "failed");
      if (failed.length > 0) {
        // Report the partial success rather than swallowing it: the project
        // exists with the members that cloned, and each failure can be retried.
        setErr(
          `Created ${result.project_id} with ${result.members.length} repositories, but ` +
            `${failed.length} could not be cloned: ` +
            failed.map((outcome) => outcome.subproject_name).join(", "),
        );
        return;
      }
      clearInstallParam();
      onClose();
      reset();
    } catch (err) {
      setErr(err instanceof Error ? err.message : String(err));
    } finally {
      setMultiBusy(false);
    }
  };

  const reset = () => {
    setUrl("");
    setProjectId("");
    setProjectIdEdited(false);
    setFile(null);
    setGithubToken("");
    setAppInstallationId(null);
    setAppRepos([]);
    setAppError(null);
    setAdoActive(null);
    setAdoRepos([]);
    setAdoError(null);
    setSelectedKeys(new Set());
    setBranchOverrides({});
  };

  const busy = addRepo.isPending || uploadZip.isPending || multiBusy;
  // Entry screens have no common fields and no submit button of their own.
  const onAppEntryScreen = tab === "app" && appInstallationId == null;
  const onAdoEntryScreen = tab === "ado" && adoActive == null;
  const onEntryScreen = onAppEntryScreen || onAdoEntryScreen;
  const isPickerTab = tab === "app" || tab === "ado";

  const tabClass = (name: Tab) =>
    `px-3 py-1.5 text-sm font-medium rounded transition-all ${
      tab === name
        ? "bg-accent-primary/10 text-accent-primary border border-accent-primary/40"
        : "border border-white/[0.07] text-slate-400 hover:text-slate-200"
    }`;

  const submitLabel = () => {
    if (busy) return "Adding...";
    if (isPickerTab && selectedRepos.length > 1) {
      return `Add ${selectedRepos.length} repositories`;
    }
    return "Add";
  };

  return (
    <div className="fixed inset-0 bg-black/60 backdrop-blur-md flex items-center justify-center z-50">
      <div className="bg-surface-card border border-white/[0.07] rounded shadow-2xl w-full max-w-lg p-6 animate-fade-in">
        <h2 className="text-lg font-semibold text-slate-100 mb-4">Add Repository</h2>

        {/* Tabs */}
        <div className="flex flex-wrap gap-2 mb-4">
          <button className={tabClass("path")} onClick={() => setTab("path")}>
            URL / Path
          </button>
          <button className={tabClass("zip")} onClick={() => setTab("zip")}>
            Upload ZIP
          </button>
          <button className={tabClass("app")} onClick={() => setTab("app")}>
            GitHub App
          </button>
          <button className={tabClass("ado")} onClick={() => setTab("ado")}>
            Azure DevOps
          </button>
        </div>

        {/* Common fields (hidden on the connection entry screens) */}
        {!onEntryScreen && (
          <div className="space-y-3">
            <div>
              <label className="block text-sm font-medium text-slate-400 mb-1" htmlFor="project-id">
                Project ID
              </label>
              <input
                id="project-id"
                type="text"
                className="input-dark"
                placeholder={
                  isPickerTab && selectedRepos.length > 1
                    ? "Name for all selected repos"
                    : "e.g. vaxxinova"
                }
                value={projectId}
                onChange={(e) => {
                  setProjectIdEdited(true);
                  setProjectId(e.target.value);
                }}
              />
            </div>

            <div>
              <label className="block text-sm font-medium text-slate-400 mb-1" htmlFor="platform">
                Platform
              </label>
              <select
                id="platform"
                className="input-dark"
                value={platform}
                onChange={(e) => setPlatform(e.target.value)}
              >
                {platforms.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.label}
                  </option>
                ))}
              </select>
            </div>

            {tab === "path" && (
              <>
                <div>
                  <label className="block text-sm font-medium text-slate-400 mb-1">
                    Git URL or Local Path
                  </label>
                  <input
                    type="text"
                    className="input-dark"
                    placeholder="https://github.com/... or /path/to/repo"
                    value={url}
                    onChange={(e) => setUrl(e.target.value)}
                  />
                </div>
                {isHttpUrl && (
                  <div>
                    <label className="block text-sm font-medium text-slate-400 mb-1">
                      Access Token{" "}
                      <span className="text-slate-500 font-normal">(optional — required for private repos)</span>
                    </label>
                    <input
                      type="password"
                      className="input-dark"
                      placeholder="ghp_... / glpat-... / PAT"
                      value={githubToken}
                      onChange={(e) => setGithubToken(e.target.value)}
                      autoComplete="new-password"
                    />
                    <p className="text-xs text-slate-500 mt-1">
                      GitHub PAT (scope <code className="font-mono">repo</code>), GitLab or Azure DevOps PAT,
                      or a Bitbucket app password. Sent as the HTTPS password with the username{" "}
                      <code className="font-mono">x-access-token</code> and stored encrypted for later fetches.
                      {isGithubUrl && " Or use the GitHub App tab to connect without storing a token."}
                    </p>
                  </div>
                )}
              </>
            )}

            {tab === "zip" && (
              <div>
                <label className="block text-sm font-medium text-slate-400 mb-1">ZIP File</label>
                <input
                  type="file"
                  accept=".zip"
                  className="w-full text-sm text-slate-400 file:mr-3 file:py-1.5 file:px-3 file:rounded file:border-0 file:text-sm file:font-medium file:bg-surface-raised file:text-slate-300 hover:file:bg-[#1e2d4a]"
                  onChange={(e) => setFile(e.target.files?.[0] ?? null)}
                />
              </div>
            )}

            {tab === "app" && appInstallationId != null && (
              <RepoMultiSelect
                repos={pickableRepos}
                selected={selectedKeys}
                onSelectedChange={changeSelection}
                branchOverrides={branchOverrides}
                onBranchChange={(repoKey, branch) =>
                  setBranchOverrides((current) => ({ ...current, [repoKey]: branch }))
                }
                loading={appLoadingRepos}
                onRefresh={() => loadAppRepos(appInstallationId)}
                scopeHint={`Repositories granted to this GitHub App installation${
                  appAccount ? ` (${appAccount})` : ""
                }. No token is stored — access is managed in GitHub.`}
                emptyHint="This installation is connected, but GitHub did not return any repositories. Add repositories to the installation in GitHub, then refresh."
              />
            )}

            {tab === "ado" && adoActive != null && (
              <RepoMultiSelect
                repos={pickableRepos}
                selected={selectedKeys}
                onSelectedChange={changeSelection}
                branchOverrides={branchOverrides}
                onBranchChange={(repoKey, branch) =>
                  setBranchOverrides((current) => ({ ...current, [repoKey]: branch }))
                }
                loading={adoLoadingRepos}
                onRefresh={() => loadAdoRepos(adoActive)}
                scopeHint={`Every repository this token can read in ${adoActive.organization_name}, across all Azure DevOps projects.`}
                emptyHint="The connection works, but Azure DevOps returned no repositories. Check that the token has Code (Read) scope, then refresh."
              />
            )}
          </div>
        )}

        {/* GitHub App entry screen */}
        {onAppEntryScreen && (
          <div className="space-y-3">
            <p className="text-sm text-slate-400">
              Install IntoData&rsquo;s GitHub App on your organization and pick which
              repositories to grant. No token is stored — access is managed in GitHub and
              revoked by uninstalling.
            </p>
            <button
              className="px-4 py-2 text-sm font-medium bg-accent-primary text-surface-bg rounded hover:bg-accent-primary/90 disabled:opacity-40 transition-all"
              disabled={appStarting}
              onClick={startAppInstall}
            >
              {appStarting ? "Redirecting…" : "Connect with GitHub App"}
            </button>
            {appError && <p className="text-red-400 text-sm font-mono">{appError}</p>}
          </div>
        )}

        {/* Azure DevOps entry screen */}
        {onAdoEntryScreen && (
          <div className="space-y-3">
            <p className="text-sm text-slate-400">
              Azure DevOps has no App-style install, so a personal access token is the only
              credential it offers. It is held once per <span className="font-medium">organization</span>,
              encrypted at rest, and never sent to the browser or to a model.
            </p>

            {adoConnections == null ? (
              <p className="text-sm text-slate-500">Checking connections…</p>
            ) : (
              adoConnections.length > 0 && (
                <div className="space-y-2 rounded border border-white/[0.07] p-2">
                  {adoConnections.map((connection) => (
                    <div
                      key={connection.id}
                      className="flex items-center justify-between gap-2"
                    >
                      <div className="min-w-0">
                        <p className="truncate text-sm text-slate-300">
                          {connection.organization_name}
                        </p>
                        <p className="truncate text-xs text-slate-500">
                          {connection.organization_url}
                        </p>
                      </div>
                      <button
                        type="button"
                        className="shrink-0 px-2 py-1 text-xs font-medium text-slate-300 border border-white/[0.1] rounded hover:border-white/[0.2] transition-all"
                        onClick={() => loadAdoRepos(connection)}
                      >
                        Choose repositories
                      </button>
                    </div>
                  ))}
                </div>
              )
            )}

            {adoFormOpen ? (
              <div className="space-y-2 rounded border border-white/[0.07] p-2">
                <input
                  type="text"
                  className="input-dark"
                  placeholder="https://dev.azure.com/your-org"
                  value={adoOrgUrl}
                  onChange={(e) => setAdoOrgUrl(e.target.value)}
                />
                <input
                  type="password"
                  className="input-dark"
                  placeholder="Personal access token"
                  autoComplete="off"
                  value={adoPat}
                  onChange={(e) => setAdoPat(e.target.value)}
                />
                <p className="text-xs text-slate-500">
                  An organization, project or repository URL all work. The token needs only{" "}
                  <span className="font-medium">Code (Read)</span>.
                </p>
                <div className="flex items-center justify-between gap-2">
                  <a
                    className="text-xs text-accent-primary hover:underline"
                    href={PAT_DOCS_URL}
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    How to create a token
                  </a>
                  <div className="flex gap-2">
                    <button
                      type="button"
                      className="px-3 py-1.5 text-sm font-medium text-slate-400 border border-white/[0.1] rounded hover:text-slate-200 transition-all"
                      onClick={() => {
                        setAdoFormOpen(false);
                        setAdoPat("");
                        setAdoError(null);
                      }}
                    >
                      Cancel
                    </button>
                    <button
                      type="button"
                      className="px-3 py-1.5 text-sm font-medium bg-accent-primary text-surface-bg rounded hover:bg-accent-primary/90 disabled:opacity-40 transition-all"
                      disabled={adoConnecting || !adoOrgUrl.trim() || !adoPat.trim()}
                      onClick={submitAdoConnection}
                    >
                      {adoConnecting ? "Connecting…" : "Connect"}
                    </button>
                  </div>
                </div>
              </div>
            ) : (
              <button
                type="button"
                className="px-4 py-2 text-sm font-medium bg-accent-primary text-surface-bg rounded hover:bg-accent-primary/90 transition-all"
                onClick={() => setAdoFormOpen(true)}
              >
                {adoConnections && adoConnections.length > 0
                  ? "Connect another organization"
                  : "Connect Azure DevOps"}
              </button>
            )}
            {adoError && <p className="text-red-400 text-sm font-mono">{adoError}</p>}
          </div>
        )}

        {/* Error */}
        {(addRepo.isError || uploadZip.isError) && (
          <p className="text-red-400 text-sm mt-3 font-mono">
            {parseApiError(addRepo.error ?? uploadZip.error)}
          </p>
        )}
        {tab === "app" && appInstallationId != null && appError && (
          <p className="text-red-400 text-sm mt-3 font-mono">{appError}</p>
        )}
        {tab === "ado" && adoActive != null && adoError && (
          <p className="text-red-400 text-sm mt-3 font-mono">{adoError}</p>
        )}

        {/* Actions */}
        <div className="flex justify-end gap-2 mt-6">
          <button
            className="px-4 py-2 text-sm font-medium text-slate-400 border border-white/[0.1] rounded hover:text-slate-200 hover:border-white/[0.2] transition-all"
            onClick={() => { clearInstallParam(); onClose(); reset(); }}
          >
            Cancel
          </button>
          {!onEntryScreen && (
            <button
              className="px-4 py-2 text-sm font-medium bg-accent-primary text-surface-bg rounded hover:bg-accent-primary/90 disabled:opacity-40 disabled:cursor-not-allowed transition-all"
              disabled={
                busy ||
                !projectId ||
                (tab === "path"
                  ? !url
                  : tab === "zip"
                    ? !file
                    : selectedRepos.length === 0)
              }
              onClick={
                tab === "path"
                  ? handleSubmitPath
                  : tab === "zip"
                    ? handleSubmitZip
                    : handleSubmitSelection
              }
            >
              {submitLabel()}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
