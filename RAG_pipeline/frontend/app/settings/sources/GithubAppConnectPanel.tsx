"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { CheckCircle2, ExternalLink, Github, Loader2, RotateCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import {
  addGithubSource,
  getGithubAppInstallUrl,
  listGithubAppInstallations,
  listGithubAppRepos,
  McpRequestError,
  type GithubAppInstallation,
  type GithubAppRepo,
  type SourceProject,
} from "@/lib/mcpClient";

interface PlatformOption {
  id: string;
  label: string;
}

interface Props {
  platforms: PlatformOption[];
  defaultPlatform: string;
  existingProjects: SourceProject[];
  onConnected: (project: SourceProject, platform: string) => void | Promise<void>;
}

function slugify(input: string): string {
  return input
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9_-]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 100);
}

function canonicalRepoUrl(url: string | null | undefined): string {
  return (url ?? "")
    .trim()
    .replace(/\/+$/g, "")
    .replace(/\.git$/i, "")
    .toLowerCase();
}

function repoNameFromUrl(url: string): string {
  const clean = url.replace(/\/+$/g, "").replace(/\.git$/i, "");
  return clean.split("/").pop() || clean;
}

function installationSettingsUrl(installationId: number): string {
  return `https://github.com/settings/installations/${installationId}`;
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
}: Props) {
  const [installationId, setInstallationId] = useState<number | null>(null);
  const [repos, setRepos] = useState<GithubAppRepo[]>([]);
  const [accountLogin, setAccountLogin] = useState<string | null>(null);
  const [selectedRepo, setSelectedRepo] = useState<string>("");
  const [projectName, setProjectName] = useState("");
  const [platform, setPlatform] = useState(defaultPlatform);
  const [loadingRepos, setLoadingRepos] = useState(false);
  const [installations, setInstallations] = useState<GithubAppInstallation[] | null>(null);
  const [loadingInstallations, setLoadingInstallations] = useState(true);
  const [installationError, setInstallationError] = useState<string | null>(null);
  const [connecting, setConnecting] = useState(false);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const connectedRepoKeys = useMemo(
    () => new Set(existingProjects.map((project) => canonicalRepoUrl(project.repo_url))),
    [existingProjects],
  );
  const selectedRepoConnected = connectedRepoKeys.has(canonicalRepoUrl(selectedRepo));
  const primaryInstallation = installations?.[0];
  const hasInstallations = Boolean(installations?.length);
  const installationStatusUnavailable = installations == null && installationError != null;

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

  function selectRepo(repoUrl: string) {
    setSelectedRepo(repoUrl);
    if (!projectName.trim()) {
      setProjectName(slugify(repoNameFromUrl(repoUrl)));
    }
    setError(null);
  }

  function useInstallation(installation: GithubAppInstallation) {
    setInstallationId(installation.installation_id);
    setAccountLogin(installation.account_login);
    setRepos([]);
    setSelectedRepo("");
    setProjectName("");
    setError(null);
  }

  async function startInstall() {
    setStarting(true);
    setError(null);
    try {
      const url = await getGithubAppInstallUrl();
      window.location.href = url;
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setStarting(false);
    }
  }

  async function finishConnect() {
    if (installationId == null || !selectedRepo) {
      setError("Pick a repository to connect.");
      return;
    }
    if (selectedRepoConnected) {
      setError("This repository is already connected to Nexus.");
      return;
    }
    const name = projectName.trim() || selectedRepo.split("/").pop() || selectedRepo;
    const projectId = slugify(name);
    if (!projectId) {
      setError("Enter a valid project name.");
      return;
    }
    setConnecting(true);
    setError(null);
    try {
      const project = await addGithubSource({
        repoUrl: selectedRepo,
        projectId,
        platform,
        displayName: name,
        installationId,
      });
      clearInstallParam();
      setInstallationId(null);
      setRepos([]);
      setSelectedRepo("");
      setProjectName("");
      await onConnected(project, platform);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setConnecting(false);
    }
  }

  // Repo-picker mode (post-install).
  if (installationId != null) {
    return (
      <Card className="rounded-none border-x-0 shadow-none">
        <CardHeader className="px-1">
          <div className="flex items-center gap-3">
            <span className="flex h-9 w-9 items-center justify-center rounded-lg bg-primary/10 text-primary">
              <Github className="h-4 w-4" aria-hidden />
            </span>
            <div>
              <h2 className="text-base font-semibold">Finish connecting</h2>
              <p className="mt-0.5 text-sm text-muted-foreground">
                GitHub App installed{accountLogin ? ` on ${accountLogin}` : ""}. Choose a
                repository to connect &mdash; no token is stored.
              </p>
            </div>
          </div>
        </CardHeader>
        <CardBody className="space-y-3 px-1">
          {loadingRepos ? (
            <p className="flex items-center gap-2 text-sm text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" /> Loading repositories&hellip;
            </p>
          ) : (
            <>
              <div className="flex flex-col gap-2 rounded-md border border-border bg-muted/30 p-3 sm:flex-row sm:items-center sm:justify-between">
                <div>
                  <p className="text-sm font-medium">Repository access</p>
                  <p className="mt-0.5 text-xs text-muted-foreground">
                    Showing repositories granted to this GitHub App installation.
                  </p>
                </div>
                <div className="flex flex-wrap gap-2">
                  <Button
                    type="button"
                    size="sm"
                    variant="secondary"
                    onClick={() => window.open(installationSettingsUrl(installationId), "_blank", "noopener,noreferrer")}
                  >
                    <ExternalLink className="h-3.5 w-3.5" /> Manage access
                  </Button>
                  <Button variant="ghost" size="sm" isLoading={loadingRepos} onClick={loadRepos}>
                    <RotateCw className="h-3.5 w-3.5" /> Refresh
                  </Button>
                </div>
              </div>
              <select
                className="h-10 w-full rounded-md border border-input bg-background px-3 text-sm"
                value={selectedRepo}
                onChange={(e) => selectRepo(e.target.value)}
              >
                <option value="">Select a repository&hellip;</option>
                {repos.map((r) => {
                  const repoUrl = r.clone_url ?? "";
                  const connected = connectedRepoKeys.has(canonicalRepoUrl(repoUrl));
                  return (
                    <option
                      key={r.full_name ?? repoUrl}
                      value={repoUrl}
                      disabled={!repoUrl || connected}
                    >
                      {r.full_name ?? repoUrl}
                      {r.private ? " (private)" : ""}
                      {connected ? " - already connected" : ""}
                    </option>
                  );
                })}
              </select>
              {repos.length === 0 && (
                <p className="text-xs text-muted-foreground">
                  This installation is connected, but GitHub did not return any repositories.
                  Add repositories to the installation in GitHub, then refresh.
                </p>
              )}
              <div className="grid gap-2 sm:grid-cols-2">
                <Input
                  type="text"
                  placeholder="Project name / ID"
                  value={projectName}
                  onChange={(e) => setProjectName(e.target.value)}
                />
                <select
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
            </>
          )}
          {error && <p className="text-xs text-destructive">{error}</p>}
          <div className="flex justify-between">
            <div className="flex gap-2">
              <Button
                variant="ghost"
                size="sm"
                onClick={() => {
                  clearInstallParam();
                  setInstallationId(null);
                }}
              >
                Cancel
              </Button>
            </div>
            <Button
              size="sm"
              isLoading={connecting}
              disabled={!selectedRepo || selectedRepoConnected}
              onClick={finishConnect}
            >
              <Github className="h-3.5 w-3.5" /> Connect &amp; scan
            </Button>
          </div>
        </CardBody>
      </Card>
    );
  }

  // Entry mode (pre-install).
  return (
    <Card className="rounded-none border-x-0 shadow-none">
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
                  ? "Nexus can use this installation to list granted repositories and clone them without storing a token."
                  : "Install IntoData's GitHub App on your org, grant repositories, then return here to choose one."}
              </p>
            </div>
          </div>

          {hasInstallations ? (
            <Button
              size="md"
              onClick={() => primaryInstallation && useInstallation(primaryInstallation)}
              disabled={!primaryInstallation}
            >
              <Github className="h-4 w-4" /> Choose repository
            </Button>
          ) : installationStatusUnavailable ? (
            <Button size="md" variant="secondary" isLoading={loadingInstallations} onClick={loadInstallations}>
              <RotateCw className="h-4 w-4" /> Retry status
            </Button>
          ) : loadingInstallations ? (
            <Button size="md" variant="secondary" isLoading disabled>
              Checking connection
            </Button>
          ) : (
            <Button size="md" isLoading={starting || loadingInstallations} onClick={startInstall}>
              <Github className="h-4 w-4" /> Connect with GitHub App
            </Button>
          )}
        </div>

        {hasInstallations && installations && (
          <div className="rounded-md border border-emerald-200 bg-emerald-50/70 p-3">
            <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
              <div>
                <p className="text-sm font-medium text-emerald-900">
                  Connected account: {primaryInstallation?.account_login ?? "GitHub installation"}
                </p>
                <p className="mt-0.5 text-xs text-emerald-800">
                  Use this connection to pick a repository, clone it, and start scanning.
                </p>
              </div>
              <div className="flex flex-wrap gap-2">
                {installations.slice(1).map((installation) => (
                  <Button
                    key={installation.installation_id}
                    type="button"
                    size="sm"
                    variant="secondary"
                    onClick={() => useInstallation(installation)}
                  >
                    Use {installation.account_login ?? `installation ${installation.installation_id}`}
                  </Button>
                ))}
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  isLoading={starting || loadingInstallations}
                  onClick={startInstall}
                >
                  <ExternalLink className="h-3.5 w-3.5" /> Configure in GitHub
                </Button>
                {primaryInstallation && (
                  <Button
                    type="button"
                    size="sm"
                    variant="ghost"
                    onClick={() =>
                      window.open(
                        installationSettingsUrl(primaryInstallation.installation_id),
                        "_blank",
                        "noopener,noreferrer",
                      )
                    }
                  >
                    <ExternalLink className="h-3.5 w-3.5" /> Manage repository access
                  </Button>
                )}
              </div>
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
