"use client";

import { useCallback, useEffect, useState } from "react";
import { Github, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import {
  addGithubSource,
  getGithubAppInstallUrl,
  listGithubAppRepos,
  type GithubAppRepo,
} from "@/lib/mcpClient";

interface PlatformOption {
  id: string;
  label: string;
}

interface Props {
  platforms: PlatformOption[];
  defaultPlatform: string;
  onConnected: () => void;
}

function slugify(input: string): string {
  return input
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9_-]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 100);
}

/**
 * GitHub App connect flow (preferred over PAT):
 *  1. "Connect with GitHub App" -> redirect the browser to the install URL.
 *  2. GitHub installs the App and redirects back with ?installation_id=...
 *  3. This panel detects it, lists the installation's repos, and lets the user
 *     pick one + a project name, then registers it (no token stored).
 */
export function GithubAppConnectPanel({ platforms, defaultPlatform, onConnected }: Props) {
  const [installationId, setInstallationId] = useState<number | null>(null);
  const [repos, setRepos] = useState<GithubAppRepo[]>([]);
  const [accountLogin, setAccountLogin] = useState<string | null>(null);
  const [selectedRepo, setSelectedRepo] = useState<string>("");
  const [projectName, setProjectName] = useState("");
  const [platform, setPlatform] = useState(defaultPlatform);
  const [loadingRepos, setLoadingRepos] = useState(false);
  const [connecting, setConnecting] = useState(false);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Detect the post-install redirect (?installation_id=...&connected=1).
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const raw = params.get("installation_id");
    if (raw && /^\d+$/.test(raw)) {
      setInstallationId(Number(raw));
    }
  }, []);

  // Load the installation's repos once we have an id.
  useEffect(() => {
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
        if (!cancelled) setError(err instanceof Error ? err.message : String(err));
      })
      .finally(() => {
        if (!cancelled) setLoadingRepos(false);
      });
    return () => {
      cancelled = true;
    };
  }, [installationId]);

  const clearInstallParam = useCallback(() => {
    const url = new URL(window.location.href);
    url.searchParams.delete("installation_id");
    url.searchParams.delete("connected");
    window.history.replaceState({}, "", url.toString());
  }, []);

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
    const name = projectName.trim() || selectedRepo.split("/").pop() || selectedRepo;
    const projectId = slugify(name);
    if (!projectId) {
      setError("Enter a valid project name.");
      return;
    }
    setConnecting(true);
    setError(null);
    try {
      await addGithubSource({
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
      onConnected();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setConnecting(false);
    }
  }

  // Repo-picker mode (post-install).
  if (installationId != null) {
    return (
      <Card>
        <CardHeader>
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
        <CardBody className="space-y-3">
          {loadingRepos ? (
            <p className="flex items-center gap-2 text-sm text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" /> Loading repositories&hellip;
            </p>
          ) : (
            <>
              <select
                className="h-10 w-full rounded-md border border-input bg-background px-3 text-sm"
                value={selectedRepo}
                onChange={(e) => setSelectedRepo(e.target.value)}
              >
                <option value="">Select a repository&hellip;</option>
                {repos.map((r) => (
                  <option key={r.full_name ?? r.clone_url ?? ""} value={r.clone_url ?? ""}>
                    {r.full_name ?? r.clone_url}
                    {r.private ? " (private)" : ""}
                  </option>
                ))}
              </select>
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
            <Button size="sm" isLoading={connecting} disabled={!selectedRepo} onClick={finishConnect}>
              <Github className="h-3.5 w-3.5" /> Connect &amp; scan
            </Button>
          </div>
        </CardBody>
      </Card>
    );
  }

  // Entry mode (pre-install).
  return (
    <Card>
      <CardBody className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h2 className="text-base font-semibold">Connect with the GitHub App</h2>
          <p className="mt-0.5 text-sm text-muted-foreground">
            Install IntoData&rsquo;s GitHub App on your org and pick repositories. Access is
            managed in GitHub and no token is stored here.
          </p>
          {error && <p className="mt-2 text-xs text-destructive">{error}</p>}
        </div>
        <Button size="sm" isLoading={starting} onClick={startInstall}>
          <Github className="h-3.5 w-3.5" /> Connect with GitHub App
        </Button>
      </CardBody>
    </Card>
  );
}
