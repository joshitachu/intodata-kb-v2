"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  CheckCircle2,
  ExternalLink,
  GitBranch,
  Loader2,
  RotateCw,
  Unplug,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import {
  addAzureDevOpsSource,
  connectAzureDevOps,
  deleteAzureDevOpsConnection,
  listAzureDevOpsConnections,
  listAzureDevOpsRepos,
  McpRequestError,
  testAzureDevOpsConnection,
  type AzureDevOpsConnection,
  type AzureDevOpsRepo,
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

const PAT_DOCS_URL =
  "https://learn.microsoft.com/azure/devops/organizations/accounts/use-personal-access-tokens-to-authenticate";

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

function repoLabel(repo: AzureDevOpsRepo): string {
  return `${repo.project} / ${repo.name}`;
}

/**
 * Azure DevOps connect flow, deliberately shaped like the GitHub App panel:
 *
 *  1. Paste an organization URL + a Personal Access Token once. Azure DevOps has
 *     no App-style install, so a PAT is the only credential it offers; the server
 *     verifies it, encrypts it, and keeps it per *organization* rather than per
 *     repository.
 *  2. The panel lists every repository that token can see, across all projects.
 *  3. Pick one, name the project, and it registers + clones + scans on the same
 *     pipeline as a GitHub source.
 *
 * The token is write-only from the browser's perspective: it is posted once and
 * never returned by any endpoint.
 */
export function AzureDevOpsConnectPanel({
  platforms,
  defaultPlatform,
  existingProjects,
  onConnected,
}: Props) {
  const [connections, setConnections] = useState<AzureDevOpsConnection[] | null>(null);
  const [loadingConnections, setLoadingConnections] = useState(true);
  const [connectionError, setConnectionError] = useState<string | null>(null);

  const [formOpen, setFormOpen] = useState(false);
  const [organizationUrl, setOrganizationUrl] = useState("");
  const [pat, setPat] = useState("");
  const [connecting, setConnecting] = useState(false);

  const [activeConnection, setActiveConnection] = useState<AzureDevOpsConnection | null>(null);
  const [repos, setRepos] = useState<AzureDevOpsRepo[]>([]);
  const [loadingRepos, setLoadingRepos] = useState(false);
  const [selectedRepo, setSelectedRepo] = useState<string>("");
  const [projectName, setProjectName] = useState("");
  const [platform, setPlatform] = useState(defaultPlatform);
  const [registering, setRegistering] = useState(false);
  const [busyConnectionId, setBusyConnectionId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const connectedRepoKeys = useMemo(
    () => new Set(existingProjects.map((project) => canonicalRepoUrl(project.repo_url))),
    [existingProjects],
  );
  const selectedRepoConnected = connectedRepoKeys.has(canonicalRepoUrl(selectedRepo));
  const hasConnections = Boolean(connections?.length);
  const connectionStatusUnavailable = connections == null && connectionError != null;

  const loadConnections = useCallback(async () => {
    setLoadingConnections(true);
    try {
      setConnections(await listAzureDevOpsConnections());
      setConnectionError(null);
    } catch (err) {
      setConnectionError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoadingConnections(false);
    }
  }, []);

  useEffect(() => {
    void loadConnections();
  }, [loadConnections]);

  const loadRepos = useCallback(
    (connection: AzureDevOpsConnection) => {
      let cancelled = false;
      setLoadingRepos(true);
      setError(null);
      listAzureDevOpsRepos(connection.id)
        .then((data) => {
          if (cancelled) return;
          setRepos(data.repositories);
        })
        .catch((err: unknown) => {
          if (cancelled) return;
          if (err instanceof McpRequestError && err.status === 403) {
            // The connection is gone or belongs to someone else — leave the picker
            // rather than showing an empty repo list under a live-looking header.
            setActiveConnection(null);
            setRepos([]);
            void loadConnections();
          }
          setError(err instanceof Error ? err.message : String(err));
        })
        .finally(() => {
          if (!cancelled) setLoadingRepos(false);
        });
      return () => {
        cancelled = true;
      };
    },
    [loadConnections],
  );

  useEffect(() => {
    if (!activeConnection) return;
    return loadRepos(activeConnection);
  }, [activeConnection, loadRepos]);

  async function submitConnection() {
    const url = organizationUrl.trim();
    const token = pat.trim();
    if (!url || !token) {
      setError("Organization URL and personal access token are required.");
      return;
    }
    setConnecting(true);
    setError(null);
    setNotice(null);
    try {
      const connection = await connectAzureDevOps(url, token);
      // Drop the token from component state as soon as the server has it.
      setPat("");
      setOrganizationUrl("");
      setFormOpen(false);
      await loadConnections();
      useConnection(connection);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setConnecting(false);
    }
  }

  function useConnection(connection: AzureDevOpsConnection) {
    setActiveConnection(connection);
    setRepos([]);
    setSelectedRepo("");
    setProjectName("");
    setError(null);
    setNotice(null);
  }

  function selectRepo(cloneUrl: string) {
    setSelectedRepo(cloneUrl);
    const repo = repos.find((item) => item.clone_url === cloneUrl);
    if (repo && !projectName.trim()) {
      setProjectName(slugify(repo.name));
    }
    setError(null);
  }

  async function verifyConnection(connection: AzureDevOpsConnection) {
    setBusyConnectionId(connection.id);
    setError(null);
    setNotice(null);
    try {
      const verified = await testAzureDevOpsConnection(connection.id);
      setNotice(
        `${verified.organization_name} is reachable${
          verified.account_name ? ` as ${verified.account_name}` : ""
        }.`,
      );
      await loadConnections();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusyConnectionId(null);
    }
  }

  async function disconnect(connection: AzureDevOpsConnection) {
    setBusyConnectionId(connection.id);
    setError(null);
    setNotice(null);
    try {
      await deleteAzureDevOpsConnection(connection.id);
      if (activeConnection?.id === connection.id) {
        setActiveConnection(null);
        setRepos([]);
      }
      await loadConnections();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusyConnectionId(null);
    }
  }

  async function finishConnect() {
    if (!activeConnection || !selectedRepo) {
      setError("Pick a repository to connect.");
      return;
    }
    if (selectedRepoConnected) {
      setError("This repository is already connected to Nexus.");
      return;
    }
    const repo = repos.find((item) => item.clone_url === selectedRepo);
    const name = projectName.trim() || repo?.name || selectedRepo;
    const projectId = slugify(name);
    if (!projectId) {
      setError("Enter a valid project name.");
      return;
    }
    setRegistering(true);
    setError(null);
    try {
      const project = await addAzureDevOpsSource({
        repoUrl: selectedRepo,
        projectId,
        platform,
        displayName: repo ? repoLabel(repo) : name,
        azureDevOpsConnectionId: activeConnection.id,
      });
      setActiveConnection(null);
      setRepos([]);
      setSelectedRepo("");
      setProjectName("");
      await onConnected(project, platform);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setRegistering(false);
    }
  }

  // Repo-picker mode.
  if (activeConnection) {
    return (
      <Card className="rounded-none border-x-0 shadow-none">
        <CardHeader className="px-1">
          <div className="flex items-center gap-3">
            <span className="flex h-9 w-9 items-center justify-center rounded-lg bg-primary/10 text-primary">
              <GitBranch className="h-4 w-4" aria-hidden />
            </span>
            <div>
              <h2 className="text-base font-semibold">Finish connecting</h2>
              <p className="mt-0.5 text-sm text-muted-foreground">
                Connected to {activeConnection.organization_name}. Choose a repository to
                scan &mdash; the token stays on the server.
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
                    Showing every repository this token can read, across all Azure DevOps
                    projects.
                  </p>
                </div>
                <Button
                  variant="ghost"
                  size="sm"
                  isLoading={loadingRepos}
                  onClick={() => loadRepos(activeConnection)}
                >
                  <RotateCw className="h-3.5 w-3.5" /> Refresh
                </Button>
              </div>
              <select
                aria-label="Repository"
                className="h-10 w-full rounded-md border border-input bg-background px-3 text-sm"
                value={selectedRepo}
                onChange={(e) => selectRepo(e.target.value)}
              >
                <option value="">Select a repository&hellip;</option>
                {repos.map((repo) => {
                  const cloneUrl = repo.clone_url ?? "";
                  const connected = connectedRepoKeys.has(canonicalRepoUrl(cloneUrl));
                  return (
                    <option
                      key={repo.id || cloneUrl}
                      value={cloneUrl}
                      disabled={!cloneUrl || connected || repo.is_disabled}
                    >
                      {repoLabel(repo)}
                      {repo.is_disabled ? " - disabled" : ""}
                      {connected ? " - already connected" : ""}
                    </option>
                  );
                })}
              </select>
              {repos.length === 0 && (
                <p className="text-xs text-muted-foreground">
                  The connection works, but Azure DevOps returned no repositories. Check
                  that the token has Code (Read) scope for this organization, then refresh.
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
            </>
          )}
          {error && <p className="text-xs text-destructive">{error}</p>}
          <div className="flex justify-between">
            <Button variant="ghost" size="sm" onClick={() => setActiveConnection(null)}>
              Cancel
            </Button>
            <Button
              size="sm"
              isLoading={registering}
              disabled={!selectedRepo || selectedRepoConnected}
              onClick={finishConnect}
            >
              <GitBranch className="h-3.5 w-3.5" /> Connect &amp; scan
            </Button>
          </div>
        </CardBody>
      </Card>
    );
  }

  // Entry mode.
  return (
    <Card className="rounded-none border-x-0 shadow-none">
      <CardBody className="space-y-3 px-1 py-4">
        <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
          <div className="flex min-w-0 gap-3">
            <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary">
              <GitBranch className="h-5 w-5" aria-hidden />
            </span>
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-2">
                <h2 className="text-base font-semibold">Azure DevOps</h2>
                {loadingConnections && connections == null ? (
                  <Badge variant="muted">
                    <Loader2 className="h-3 w-3 animate-spin" /> Checking
                  </Badge>
                ) : connectionStatusUnavailable ? (
                  <Badge variant="muted">Status unavailable</Badge>
                ) : hasConnections ? (
                  <Badge variant="success">
                    <CheckCircle2 className="h-3 w-3" /> Connected
                  </Badge>
                ) : (
                  <Badge variant="muted">Not connected</Badge>
                )}
              </div>
              <p className="mt-1 text-sm text-muted-foreground">
                {connectionStatusUnavailable
                  ? "Nexus could not verify the Azure DevOps connection right now. Retry before connecting again."
                  : hasConnections
                  ? "Nexus can list this organization's repositories and clone them using the stored token."
                  : "Paste your organization URL and a personal access token once. Nexus then lists every repository the token can read."}
              </p>
            </div>
          </div>

          {/* When connections exist, the per-organization cards below carry the
              actions — a duplicate header button would be ambiguous with more
              than one organization connected. */}
          {hasConnections ? null : connectionStatusUnavailable ? (
            <Button
              size="md"
              variant="secondary"
              isLoading={loadingConnections}
              onClick={loadConnections}
            >
              <RotateCw className="h-4 w-4" /> Retry status
            </Button>
          ) : loadingConnections && connections == null ? (
            <Button size="md" variant="secondary" isLoading disabled>
              Checking connection
            </Button>
          ) : (
            !formOpen && (
              <Button size="md" onClick={() => setFormOpen(true)}>
                <GitBranch className="h-4 w-4" /> Connect Azure DevOps
              </Button>
            )
          )}
        </div>

        {formOpen && (
          <div className="space-y-3 rounded-md border border-border bg-muted/30 p-3">
            <div className="grid gap-2 sm:grid-cols-2">
              <Input
                type="text"
                placeholder="https://dev.azure.com/your-org"
                value={organizationUrl}
                onChange={(e) => {
                  setOrganizationUrl(e.target.value);
                  setError(null);
                }}
                hasError={Boolean(error)}
              />
              <Input
                type="password"
                placeholder="Personal access token"
                autoComplete="off"
                value={pat}
                onChange={(e) => {
                  setPat(e.target.value);
                  setError(null);
                }}
                hasError={Boolean(error)}
              />
            </div>
            <p className="text-xs text-muted-foreground">
              An organization, project or repository URL all work. The token needs only{" "}
              <span className="font-medium">Code (Read)</span>; it is encrypted at rest and
              never sent to the browser or to a model.
            </p>
            <div className="flex flex-wrap items-center justify-between gap-2">
              <Button
                type="button"
                variant="ghost"
                size="sm"
                onClick={() =>
                  window.open(PAT_DOCS_URL, "_blank", "noopener,noreferrer")
                }
              >
                <ExternalLink className="h-3.5 w-3.5" /> How to create a token
              </Button>
              <div className="flex gap-2">
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => {
                    setFormOpen(false);
                    setPat("");
                    setError(null);
                  }}
                >
                  Cancel
                </Button>
                <Button
                  size="sm"
                  isLoading={connecting}
                  disabled={!organizationUrl.trim() || !pat.trim()}
                  onClick={submitConnection}
                >
                  Connect
                </Button>
              </div>
            </div>
          </div>
        )}

        {hasConnections && connections && (
          <div className="space-y-2 rounded-md border border-emerald-200 bg-emerald-50/70 p-3">
            {connections.map((connection) => (
              <div
                key={connection.id}
                className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between"
              >
                <div className="min-w-0">
                  <p className="truncate text-sm font-medium text-emerald-900">
                    {connection.organization_name}
                    {connection.account_name ? ` — ${connection.account_name}` : ""}
                  </p>
                  <p className="mt-0.5 truncate text-xs text-emerald-800">
                    {connection.organization_url}
                  </p>
                </div>
                <div className="flex flex-wrap gap-2">
                  <Button
                    type="button"
                    size="sm"
                    variant="secondary"
                    onClick={() => useConnection(connection)}
                  >
                    Choose repository
                  </Button>
                  <Button
                    type="button"
                    size="sm"
                    variant="ghost"
                    isLoading={busyConnectionId === connection.id}
                    onClick={() => verifyConnection(connection)}
                  >
                    <RotateCw className="h-3.5 w-3.5" /> Test token
                  </Button>
                  <Button
                    type="button"
                    size="sm"
                    variant="ghost"
                    isLoading={busyConnectionId === connection.id}
                    onClick={() => disconnect(connection)}
                  >
                    <Unplug className="h-3.5 w-3.5" /> Disconnect
                  </Button>
                </div>
              </div>
            ))}
            {!formOpen && (
              <Button
                type="button"
                size="sm"
                variant="ghost"
                onClick={() => setFormOpen(true)}
              >
                Connect another organization
              </Button>
            )}
          </div>
        )}

        {connectionError && connections != null && (
          <p className="text-xs text-amber-700">
            The last connection refresh failed. Keeping the previously verified
            connections.
          </p>
        )}
        {notice && <p className="text-xs text-emerald-700">{notice}</p>}
        {error && <p className="text-xs text-destructive">{error}</p>}
      </CardBody>
    </Card>
  );
}
