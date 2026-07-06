import { useEffect, useState } from "react";
import { usePlatforms } from "../hooks/useDiscovery";
import { useAddRepo, useUploadZip } from "../hooks/useProjects";
import { parseApiError } from "../lib/errors";
import { api } from "../api/client";

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

export default function AddRepoDialog({ open, onClose }: Props) {
  const [tab, setTab] = useState<"path" | "zip" | "app">("path");
  const [url, setUrl] = useState("");
  const [projectId, setProjectId] = useState("");
  const [platform, setPlatform] = useState("talend");
  const [file, setFile] = useState<File | null>(null);
  const [githubToken, setGithubToken] = useState("");

  // GitHub App connect state.
  const [appInstallationId, setAppInstallationId] = useState<number | null>(null);
  const [appRepos, setAppRepos] = useState<AppRepo[]>([]);
  const [appAccount, setAppAccount] = useState<string | null>(null);
  const [appSelectedRepo, setAppSelectedRepo] = useState("");
  const [appLoadingRepos, setAppLoadingRepos] = useState(false);
  const [appStarting, setAppStarting] = useState(false);
  const [appError, setAppError] = useState<string | null>(null);

  const addRepo = useAddRepo();
  const uploadZip = useUploadZip();
  const { data: platformsData } = usePlatforms();
  const platforms = platformsData?.platforms ?? [
    { id: "talend", label: "Talend" },
    { id: "workato", label: "Workato" },
    { id: "lobster", label: "Lobster" },
    { id: "generic", label: "Generic" },
  ];

  const isGithubUrl = url.includes("github.com");

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

  useEffect(() => {
    if (appInstallationId == null) return;
    let cancelled = false;
    setAppLoadingRepos(true);
    setAppError(null);
    api
      .listGithubAppRepos(appInstallationId)
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
  }, [appInstallationId]);

  if (!open) return null;

  const clearInstallParam = () => {
    const u = new URL(window.location.href);
    u.searchParams.delete("installation_id");
    u.searchParams.delete("connected");
    window.history.replaceState({}, "", u.toString());
  };

  const handleSubmitPath = () => {
    if (!url || !projectId) return;
    addRepo.mutate(
      { url, project_id: projectId, platform, github_token: githubToken || undefined },
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

  const handleSubmitApp = () => {
    if (appInstallationId == null || !appSelectedRepo || !projectId) return;
    addRepo.mutate(
      { url: appSelectedRepo, project_id: projectId, platform, installation_id: appInstallationId },
      {
        onSuccess: () => {
          clearInstallParam();
          onClose();
          reset();
        },
      }
    );
  };

  const reset = () => {
    setUrl("");
    setProjectId("");
    setFile(null);
    setGithubToken("");
    setAppInstallationId(null);
    setAppRepos([]);
    setAppSelectedRepo("");
    setAppError(null);
  };

  const busy = addRepo.isPending || uploadZip.isPending;

  const tabClass = (name: "path" | "zip" | "app") =>
    `px-3 py-1.5 text-sm font-medium rounded transition-all ${
      tab === name
        ? "bg-accent-primary/10 text-accent-primary border border-accent-primary/40"
        : "border border-white/[0.07] text-slate-400 hover:text-slate-200"
    }`;

  return (
    <div className="fixed inset-0 bg-black/60 backdrop-blur-md flex items-center justify-center z-50">
      <div className="bg-surface-card border border-white/[0.07] rounded shadow-2xl w-full max-w-md p-6 animate-fade-in">
        <h2 className="text-lg font-semibold text-slate-100 mb-4">Add Repository</h2>

        {/* Tabs */}
        <div className="flex gap-2 mb-4">
          <button className={tabClass("path")} onClick={() => setTab("path")}>
            URL / Path
          </button>
          <button className={tabClass("zip")} onClick={() => setTab("zip")}>
            Upload ZIP
          </button>
          <button className={tabClass("app")} onClick={() => setTab("app")}>
            GitHub App
          </button>
        </div>

        {/* Common fields (not shown for the pre-install App entry screen) */}
        {!(tab === "app" && appInstallationId == null) && (
          <div className="space-y-3">
            <div>
              <label className="block text-sm font-medium text-slate-400 mb-1">Project ID</label>
              <input
                type="text"
                className="input-dark"
                placeholder="e.g. vaxxinova"
                value={projectId}
                onChange={(e) => setProjectId(e.target.value)}
              />
            </div>

            <div>
              <label className="block text-sm font-medium text-slate-400 mb-1">Platform</label>
              <select
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
                {isGithubUrl && (
                  <div>
                    <label className="block text-sm font-medium text-slate-400 mb-1">
                      GitHub Token{" "}
                      <span className="text-slate-500 font-normal">(optional — required for private repos)</span>
                    </label>
                    <input
                      type="password"
                      className="input-dark"
                      placeholder="ghp_..."
                      value={githubToken}
                      onChange={(e) => setGithubToken(e.target.value)}
                      autoComplete="new-password"
                    />
                    <p className="text-xs text-slate-500 mt-1">
                      Scopes needed: <code className="font-mono">repo</code>. Or use the GitHub App tab to connect without storing a token.
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
              <div>
                <label className="block text-sm font-medium text-slate-400 mb-1">
                  Repository{appAccount ? ` (${appAccount})` : ""}
                </label>
                {appLoadingRepos ? (
                  <p className="text-sm text-slate-500">Loading repositories…</p>
                ) : (
                  <select
                    className="input-dark"
                    value={appSelectedRepo}
                    onChange={(e) => setAppSelectedRepo(e.target.value)}
                  >
                    <option value="">Select a repository…</option>
                    {appRepos.map((r) => (
                      <option key={r.full_name ?? r.clone_url ?? ""} value={r.clone_url ?? ""}>
                        {r.full_name ?? r.clone_url}
                        {r.private ? " (private)" : ""}
                      </option>
                    ))}
                  </select>
                )}
                <p className="text-xs text-slate-500 mt-1">
                  Connected via the GitHub App — no token stored. Access is managed in GitHub.
                </p>
              </div>
            )}
          </div>
        )}

        {/* Pre-install App entry screen */}
        {tab === "app" && appInstallationId == null && (
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

        {/* Error */}
        {(addRepo.isError || uploadZip.isError) && (
          <p className="text-red-400 text-sm mt-3 font-mono">
            {parseApiError(addRepo.error ?? uploadZip.error)}
          </p>
        )}
        {tab === "app" && appInstallationId != null && appError && (
          <p className="text-red-400 text-sm mt-3 font-mono">{appError}</p>
        )}

        {/* Actions */}
        <div className="flex justify-end gap-2 mt-6">
          <button
            className="px-4 py-2 text-sm font-medium text-slate-400 border border-white/[0.1] rounded hover:text-slate-200 hover:border-white/[0.2] transition-all"
            onClick={() => { clearInstallParam(); onClose(); reset(); }}
          >
            Cancel
          </button>
          {!(tab === "app" && appInstallationId == null) && (
            <button
              className="px-4 py-2 text-sm font-medium bg-accent-primary text-surface-bg rounded hover:bg-accent-primary/90 disabled:opacity-40 disabled:cursor-not-allowed transition-all"
              disabled={
                busy ||
                !projectId ||
                (tab === "path" ? !url : tab === "zip" ? !file : !appSelectedRepo)
              }
              onClick={
                tab === "path"
                  ? handleSubmitPath
                  : tab === "zip"
                    ? handleSubmitZip
                    : handleSubmitApp
              }
            >
              {busy ? "Adding..." : "Add"}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
