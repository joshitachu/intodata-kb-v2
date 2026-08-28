"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { CheckCircle2, ExternalLink, Github, Loader2, RotateCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import {
  addGithubSource,
  createMultiRepoProject,
  getGithubAppInstallUrl,
  listGithubAppInstallations,
  listGithubAppRepos,
  McpRequestError,
  type GithubAppInstallation,
  type GithubAppRepo,
  type SourceProject,
} from "@/lib/mcpClient";
import { RepoMultiSelect } from "./RepoMultiSelect";
import {
  branchForMember,
  canonicalRepoKey,
  collidesWithExistingProject,
  orderedSelection,
  slugifyProjectId,
  suggestedProjectName,
  toPickableGithubRepos,
} from "./repoSelection";

interface PlatformOption {
  id: string;
  label: string;
}

interface Props {
  platforms: PlatformOption[];
  defaultPlatform: string;
  existingProjects: SourceProject[];
  onConnected: (project: SourceProject, platform: string) => void | Promise<void>;
  /**
   * Open the manual repository-URL + token form.
   *
   * The App is the preferred path, but it is config-gated per deployment: a
   * server without `MCP_GITHUB_APP_*` answers the install request with 503. On a
   * self-managed install that is the normal state, not a fault, so the panel
   * offers the token path rather than leaving a dead button.
   */
  onUseTokenInstead: () => void;
}

// Slug and repo-key normalisation live in ./repoSelection so this panel and the
// Azure DevOps panel cannot drift apart on identity rules.

function installationOwnerLabel(accountType: GithubAppInstallation["account_type"]): string {
  if (accountType === "Organization") return "Organization";
  if (accountType === "User") return "Personal account";
  return "GitHub owner";
}

function installationSettingsUrl(installation: GithubAppInstallation): string {
  if (installation.account_type === "Organization" && installation.account_login) {
    return `https://github.com/organizations/${encodeURIComponent(
      installation.account_login,
    )}/settings/installations/${installation.installation_id}`;
  }
  if (installation.settings_url?.startsWith("https://github.com/")) {
    return installation.settings_url;
  }
  return `https://github.com/settings/installations/${installation.installation_id}`;
}

/**
 * GitHub App connect flow (preferred over PAT):
 *  1. "Connect with GitHub App" -> redirect the browser to the install URL.
 *  2. GitHub installs the App and redirects back with ?installation_id=...
 *  3. This panel detects it, lists the installation's repos, and lets the user
 *     pick one + a project name, then registers it (no token stored).
 */
export function GithubAppConnectPanel({
  platforms,
  defaultPlatform,
  existingProjects,
  onConnected,
  onUseTokenInstead,
}: Props) {
  const [installationId, setInstallationId] = useState<number | null>(null);
  const [repos, setRepos] = useState<GithubAppRepo[]>([]);
  const [accountLogin, setAccountLogin] = useState<string | null>(null);
  const [accountType, setAccountType] = useState<GithubAppInstallation["account_type"]>(null);
  const [settingsUrl, setSettingsUrl] = useState<string | null>(null);
  const [selectedKeys, setSelectedKeys] = useState<Set<string>>(new Set());
  // Per-repo branch pin, keyed by repo key. Absent or blank means "follow the
  // provider's default branch", which then tracks upstream changes to it.
  const [branchOverrides, setBranchOverrides] = useState<Record<string, string>>({});
  const [projectName, setProjectName] = useState("");
  // Whether the operator typed the name themselves. Re-suggesting on every
  // selection change would fight their typing; suggesting only while the field
  // is EMPTY is worse -- picking one repo fills it, and every repo added after
  // that leaves the project named after a single member.
  const [nameEdited, setNameEdited] = useState(false);
  const [platform, setPlatform] = useState(defaultPlatform);
  const [loadingRepos, setLoadingRepos] = useState(false);
  const [installations, setInstallations] = useState<GithubAppInstallation[] | null>(null);
  const [loadingInstallations, setLoadingInstallations] = useState(true);
  const [installationError, setInstallationError] = useState<string | null>(null);
  const [connecting, setConnecting] = useState(false);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // The App is not set up on this server (503). Not an error to retry.
  const [appUnavailable, setAppUnavailable] = useState(false);
  const connectedRepoKeys = useMemo(
    () => new Set(existingProjects.map((project) => canonicalRepoKey(project.repo_url))),
    [existingProjects],
  );
  const pickableRepos = useMemo(
    () => toPickableGithubRepos(repos, connectedRepoKeys),
    [repos, connectedRepoKeys],
  );
  const selectedRepos = useMemo(
    () => orderedSelection(pickableRepos, selectedKeys),
    [pickableRepos, selectedKeys],
  );
  const hasInstallations = Boolean(installations?.length);
  const installationStatusUnavailable = installations == null && installationError != null;
  const activeInstallation = useMemo<GithubAppInstallation | null>(() => {
    if (installationId == null) return null;
    return (
      installations?.find((item) => item.installation_id === installationId) ?? {
        installation_id: installationId,
        account_login: accountLogin,
        account_type: accountType,
        settings_url: settingsUrl,
      }
    );
  }, [accountLogin, accountType, installationId, installations, settingsUrl]);

  // Detect the post-install redirect (?installation_id=...&connected=1).
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const raw = params.get("installation_id");
    if (raw && /^\d+$/.test(raw)) {
      setInstallationId(Number(raw));
    }
    const connectError = params.get("connect_error");
    if (connectError) {
      const detail = params.get("connect_message");
      setError(
        detail
          ? `GitHub App connection failed (${connectError}): ${detail}`
          : `GitHub App callback failed (${connectError}). Start the connection again.`,
      );
    }
  }, []);

  const clearInstallParam = useCallback(() => {
    const url = new URL(window.location.href);
    url.searchParams.delete("installation_id");
    url.searchParams.delete("connected");
    url.searchParams.delete("connect_error");
    url.searchParams.delete("connect_message");
    window.history.replaceState({}, "", url.toString());
  }, []);

  // The server reconciles bindings against GitHub on every read, so this list is
  // the source of truth for whether an App connection still exists.
  const loadInstallations = useCallback(async () => {
    setLoadingInstallations(true);
    try {
      setInstallations(await listGithubAppInstallations());
      setInstallationError(null);
    } catch (err) {
      setInstallationError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoadingInstallations(false);
    }
  }, []);

  const loadRepos = useCallback(() => {
    if (installationId == null) return;
    let cancelled = false;
    setLoadingRepos(true);
    setError(null);
    listGithubAppRepos(installationId)
      .then((data) => {
        if (cancelled) return;
        setRepos(data.repositories);
        setAccountLogin(data.account_login);
        setAccountType(data.account_type);
        setSettingsUrl(data.settings_url);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        if (err instanceof McpRequestError && err.status === 404) {
          // The App was uninstalled on GitHub and the server has dropped the
          // binding. Leave the picker and re-read the (now shorter) list so the
          // card stops claiming a connection.
          setInstallationId(null);
          setRepos([]);
          setAccountLogin(null);
          setAccountType(null);
          setSettingsUrl(null);
          clearInstallParam();
          setError(
            "This GitHub App installation no longer exists on GitHub. Connect with the GitHub App again.",
          );
          void loadInstallations();
          return;
        }
        setError(err instanceof Error ? err.message : String(err));
      })
      .finally(() => {
        if (!cancelled) setLoadingRepos(false);
      });
    return () => {
      cancelled = true;
    };
  }, [clearInstallParam, installationId, loadInstallations]);

  // Load the installation's repos once we have an id.
  useEffect(() => loadRepos(), [loadRepos]);

  useEffect(() => {
    void loadInstallations();
  }, [loadInstallations]);

  function changeSelection(next: Set<string>) {
    setSelectedKeys(next);
    setError(null);
    // Seed the project name only while the user has not typed one. With several
    // repos selected the suggestion is their shared owner when they have one and
    // blank otherwise -- never the first repo's name, which would label a
    // ten-repo project after one of its members.
    if (!nameEdited) {
      const selected = pickableRepos.filter((repo) => next.has(repo.key));
      const suggestion = suggestedProjectName(selected);
      // An empty suggestion clears the field on purpose: a stale name from an
      // earlier, smaller selection would silently label the whole project.
      setProjectName(suggestion ? slugifyProjectId(suggestion) : "");
    }
  }

  function useInstallation(installation: GithubAppInstallation) {
    setInstallationId(installation.installation_id);
    setAccountLogin(installation.account_login);
    setAccountType(installation.account_type);
    setSettingsUrl(installation.settings_url);
    setRepos([]);
    setSelectedKeys(new Set());
    setBranchOverrides({});
    setProjectName("");
    setNameEdited(false);
    setError(null);
  }

  async function startInstall() {
    setStarting(true);
    setError(null);
    try {
      const url = await getGithubAppInstallUrl();
      window.location.href = url;
    } catch (err) {
      if (err instanceof McpRequestError && err.status === 503) {
        // A deployment without GitHub App credentials. Say so plainly and point
        // at the path that does work here, instead of showing a raw 503 body.
        setAppUnavailable(true);
      } else {
        setError(err instanceof Error ? err.message : String(err));
      }
      setStarting(false);
    }
  }

  function resetPicker() {
    clearInstallParam();
    setInstallationId(null);
    setRepos([]);
    setAccountLogin(null);
    setAccountType(null);
    setSettingsUrl(null);
    setSelectedKeys(new Set());
    setBranchOverrides({});
    setProjectName("");
    setNameEdited(false);
  }

  async function finishConnect() {
    if (installationId == null || selectedRepos.length === 0) {
      setError("Pick at least one repository to connect.");
      return;
    }
    const projectId = slugifyProjectId(
      projectName.trim() || suggestedProjectName(selectedRepos),
    );
    if (!projectId) {
      setError(
        selectedRepos.length > 1
          ? "Name the project that will hold these repositories."
          : "Enter a valid project name.",
      );
      return;
    }
    // Reusing an existing project id is the destructive collision: the server's
    // add_project UPDATES a project whose id already exists, repointing its
    // local_path, repo_url and platform. Reusing a REPOSITORY is fine and
    // deliberately allowed; reusing an id silently hijacks another project.
    if (collidesWithExistingProject(projectId, existingProjects.map((p) => p.id))) {
      setError(
        `A project called "${projectId}" already exists. Pick another name — ` +
          "reusing it would repoint the existing project at this source.",
      );
      return;
    }
    setConnecting(true);
    setError(null);
    try {
      if (selectedRepos.length === 1) {
        // One repo stays on the single-repo path deliberately: it sets
        // `repo_url`, which is what the existing push-webhook match keys on.
        // Routing it through the multi-repo path would silently cost auto-update.
        const only = selectedRepos[0];
        // Forward the pin: /mcp/add-repo now takes a branch, and dropping it
        // here would silently clone the default while the UI showed a pin.
        const onlyBranch = branchForMember(only, branchOverrides);
        const project = await addGithubSource({
          repoUrl: only.cloneUrl,
          projectId,
          platform,
          branch: onlyBranch,
          displayName: projectName.trim() || only.name,
          installationId,
        });
        resetPicker();
        await onConnected(project, platform);
        return;
      }

      const result = await createMultiRepoProject({
        projectId,
        displayName: projectName.trim() || projectId,
        platform,
        provider: "github",
        installationId,
        members: selectedRepos.map((repo) => ({
          repoIdentity: repo.identity,
          repoName: repo.name,
          cloneUrl: repo.cloneUrl,
          branch: branchForMember(repo, branchOverrides),
        })),
      });
      const failed = result.sync.filter((outcome) => outcome.action === "failed");
      resetPicker();
      // Report a partial success rather than swallowing it: the project exists
      // with the members that cloned, and the rest can be retried per repo.
      if (failed.length > 0) {
        setError(
          `Created ${projectId} with ${result.members.length} repositories, but ` +
            `${failed.length} could not be cloned: ` +
            failed.map((outcome) => outcome.subproject_name).join(", "),
        );
      }
      // Built explicitly rather than cast: the multi-repo endpoint returns
      // members, not a project row. `repo_url` is null by definition — no single
      // URL describes N members — and the parent refetches the real row anyway.
      const created: SourceProject = {
        id: result.project_id,
        display_name: projectName.trim() || projectId,
        repo_url: null,
        platform,
        source_type: "git",
        status: "registered",
      };
      await onConnected(created, platform);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setConnecting(false);
    }
  }

  // Repo-picker mode (post-install).
  if (installationId != null) {
    return (
      <Card className="rounded-none border-x-0 shadow-none" data-tour="sources-github">
        <CardHeader className="px-1">
          <div className="flex items-center gap-3">
            <span className="flex h-9 w-9 items-center justify-center rounded-lg bg-primary/10 text-primary">
              <Github className="h-4 w-4" aria-hidden />
            </span>
            <div>
              <h2 className="text-base font-semibold">Choose what to import</h2>
              <p className="mt-0.5 text-sm text-muted-foreground">
                Repositories from {accountLogin ?? `installation ${installationId}`}
                {accountType ? ` (${installationOwnerLabel(accountType).toLowerCase()})` : ""}.
                Pick what to import &mdash; no token is stored. Nothing is scanned until
                you ask for it.
              </p>
            </div>
          </div>
        </CardHeader>
        <CardBody className="space-y-3 px-1">
          <RepoMultiSelect
            repos={pickableRepos}
            selected={selectedKeys}
            onSelectedChange={changeSelection}
            branchOverrides={branchOverrides}
            onBranchChange={(repoKey, branch) =>
              setBranchOverrides((current) => ({ ...current, [repoKey]: branch }))
            }
            loading={loadingRepos}
            onRefresh={loadRepos}
            scopeHint="Showing repositories granted to this GitHub App installation."
            emptyHint="This installation is connected, but GitHub did not return any repositories. Add repositories to the installation in GitHub, then refresh."
            actions={activeInstallation ? (
              <Button
                type="button"
                size="sm"
                variant="secondary"
                onClick={() =>
                  window.open(
                    installationSettingsUrl(activeInstallation),
                    "_blank",
                    "noopener,noreferrer",
                  )
                }
              >
                <ExternalLink className="h-3.5 w-3.5" /> Manage access
              </Button>
            ) : null}
          />
          {!loadingRepos && (
            <div className="grid gap-2 sm:grid-cols-2">
              <Input
                type="text"
                aria-label="Project name"
                placeholder={
                  selectedRepos.length > 1 ? "Name for all selected repos" : "Project name / ID"
                }
                value={projectName}
                onChange={(e) => {
                  setNameEdited(true);
                  setProjectName(e.target.value);
                }}
              />
              <select
                aria-label="Platform"
                className="h-10 rounded-md border border-input bg-background px-3 text-sm"
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
          )}
          {error && <p className="text-xs text-destructive">{error}</p>}
          <div className="flex justify-between">
            <div className="flex gap-2">
              <Button variant="ghost" size="sm" onClick={resetPicker}>
                Cancel
              </Button>
            </div>
            <Button
              size="sm"
              isLoading={connecting}
              disabled={selectedRepos.length === 0}
              onClick={finishConnect}
            >
              <Github className="h-3.5 w-3.5" />{" "}
              {selectedRepos.length > 1
                ? `Import ${selectedRepos.length} repositories`
                : "Import repository"}
            </Button>
          </div>
        </CardBody>
      </Card>
    );
  }

  // Entry mode (pre-install).
  return (
    <Card className="rounded-none border-x-0 shadow-none" data-tour="sources-github">
      <CardBody className="space-y-3 px-1 py-4">
        <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
          <div className="flex min-w-0 gap-3">
            <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary">
              <Github className="h-5 w-5" aria-hidden />
            </span>
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-2">
                <h2 className="text-base font-semibold">GitHub App</h2>
                {loadingInstallations && installations == null ? (
                  <Badge variant="muted">
                    <Loader2 className="h-3 w-3 animate-spin" /> Checking
                  </Badge>
                ) : installationStatusUnavailable ? (
                  <Badge variant="muted">Status unavailable</Badge>
                ) : hasInstallations ? (
                  <Badge variant="success">
                    <CheckCircle2 className="h-3 w-3" /> Connected
                  </Badge>
                ) : (
                  <Badge variant="muted">Not connected</Badge>
                )}
              </div>
              <p className="mt-1 text-sm text-muted-foreground">
                {installationStatusUnavailable
                  ? "Nexus could not verify the GitHub App connection right now. Retry without starting a second installation."
                  : hasInstallations
                  ? `Nexus can use ${installations?.length ?? 0} separate GitHub App ${
                      installations?.length === 1 ? "installation" : "installations"
                    } without storing a token.`
                  : "Install IntoData's GitHub App on your org, grant repositories, then return here to choose one."}
              </p>
            </div>
          </div>

          {installationStatusUnavailable ? (
            <Button size="md" variant="secondary" isLoading={loadingInstallations} onClick={loadInstallations}>
              <RotateCw className="h-4 w-4" /> Retry status
            </Button>
          ) : loadingInstallations ? (
            <Button size="md" variant="secondary" isLoading disabled>
              Checking connection
            </Button>
          ) : !hasInstallations ? (
            <Button size="md" isLoading={starting || loadingInstallations} onClick={startInstall}>
              <Github className="h-4 w-4" /> Connect with GitHub App
            </Button>
          ) : null}
        </div>

        {/* Discoverable before the user hits a 503: some deployments only ever
            have the token path, and burying it in the ZIP card made it look like
            the App was the only way in. */}
        {!hasInstallations && !appUnavailable && !loadingInstallations && (
          <p className="text-xs text-muted-foreground">
            No GitHub App?{" "}
            <button
              type="button"
              onClick={onUseTokenInstead}
              className="font-semibold text-foreground underline decoration-[#fe9543] decoration-2 underline-offset-2 hover:text-[#d96f23]"
            >
              Import with an access token instead
            </button>
          </p>
        )}

        {hasInstallations && installations && (
          <div className="rounded-md border border-emerald-200 bg-emerald-50/70 p-3">
            <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
              <div>
                <p className="text-sm font-medium text-emerald-900">
                  GitHub installations ({installations.length})
                </p>
                <p className="mt-0.5 text-xs text-emerald-800">
                  Each row is a separate personal account or organization installation.
                </p>
              </div>
              <Button
                type="button"
                size="sm"
                variant="ghost"
                isLoading={starting || loadingInstallations}
                onClick={startInstall}
              >
                <ExternalLink className="h-3.5 w-3.5" /> Add installation
              </Button>
            </div>
            <div className="mt-3 divide-y divide-emerald-200 border-t border-emerald-200">
              {installations.map((installation) => {
                const owner =
                  installation.account_login ?? `Installation ${installation.installation_id}`;
                return (
                  <div
                    key={installation.installation_id}
                    className="flex flex-col gap-2 py-3 sm:flex-row sm:items-center sm:justify-between"
                  >
                    <div className="min-w-0">
                      <p className="truncate text-sm font-medium text-emerald-950">{owner}</p>
                      <p className="text-xs text-emerald-800">
                        {installationOwnerLabel(installation.account_type)}
                      </p>
                    </div>
                    <div className="flex flex-wrap gap-2">
                      <Button
                        type="button"
                        size="sm"
                        variant="secondary"
                        aria-label={`Choose repositories from ${owner}`}
                        onClick={() => useInstallation(installation)}
                      >
                        <Github className="h-3.5 w-3.5" /> Choose repositories
                      </Button>
                      <Button
                        type="button"
                        size="sm"
                        variant="ghost"
                        aria-label={`Manage repository access for ${owner}`}
                        onClick={() =>
                          window.open(
                            installationSettingsUrl(installation),
                            "_blank",
                            "noopener,noreferrer",
                          )
                        }
                      >
                        <ExternalLink className="h-3.5 w-3.5" /> Manage access
                      </Button>
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        )}

        {appUnavailable && (
          <div className="rounded-md border border-amber-200 bg-amber-50/70 p-3">
            <p className="text-sm font-medium text-amber-900">
              The GitHub App is not set up on this server
            </p>
            <p className="mt-0.5 text-xs text-amber-800">
              Nothing is wrong with your account. This installation has no GitHub
              App configured, so you can import a repository with an access token
              instead &mdash; or ask your administrator to configure the App.
            </p>
            <div className="mt-2">
              <Button type="button" size="sm" variant="secondary" onClick={onUseTokenInstead}>
                Import with an access token
              </Button>
            </div>
          </div>
        )}

        {installationError && installations != null && (
          <p className="text-xs text-amber-700">
            The last connection refresh failed. Keeping the previously verified connection.
          </p>
        )}
        {error && <p className="text-xs text-destructive">{error}</p>}
      </CardBody>
    </Card>
  );
}
