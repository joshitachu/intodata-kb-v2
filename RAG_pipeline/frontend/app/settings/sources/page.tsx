"use client";

import { useMemo, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertCircle,
  Check,
  ChevronDown,
  ChevronRight,
  Database,
  FileArchive,
  Github,
  Loader2,
  Search,
  RotateCw,
  Trash2,
  UploadCloud,
  X,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { useOnboardingStatus, useOnboardingConfig } from "@/hooks/useOnboarding";
import {
  addGithubSource,
  checkGithubWebhookStatus,
  deleteSourceProject,
  deleteGithubWebhook,
  generateDocs,
  pushProjectToRag,
  registerGithubWebhook,
  resetProjectStatus,
  scanProject,
  subscribeToLogs,
  updateGithubToken,
  uploadZip,
  type LogEvent,
  type SourceProject,
} from "@/lib/mcpClient";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";

type RowStage =
  | "pending"
  | "uploading"
  | "scanning"
  | "generating"
  | "pushing"
  | "complete"
  | "failed";

interface RowState {
  file: File | null;
  projectName: string;
  stage: RowStage;
  progress: number;
  error: string | null;
  logs: string[];
  logsOpen: boolean;
}

type SourceMode = "zip" | "github";

interface GithubState {
  repoUrl: string;
  token: string;
  projectName: string;
  displayName: string;
  busy: boolean;
  error: string | null;
}

const STAGE_LABELS: Record<RowStage, string> = {
  pending: "Pending",
  uploading: "Uploading",
  scanning: "Scanning",
  generating: "Generating docs",
  pushing: "Pushing to knowledge base",
  complete: "Complete",
  failed: "Failed",
};

function slugify(input: string): string {
  return (
    input
      .toLowerCase()
      .replace(/[^a-z0-9_-]+/g, "-")
      .replace(/^-+|-+$/g, "")
      .slice(0, 80) || "project"
  );
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function formatDate(value?: string | null): string {
  if (!value) return "Never";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "Unknown";
  return date.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function stageFromEvent(ev: LogEvent): RowStage | null {
  const text = `${ev.type ?? ""} ${ev.data ?? ""}`.toLowerCase();
  if (ev.type === "scan_complete" || text.includes("scan complete")) return "generating";
  if (ev.type === "generate_complete" || text.includes("generate complete")) return "pushing";
  if (
    ev.type === "push_complete" ||
    text.includes("push complete") ||
    text.includes("pushed to rag")
  )
    return "complete";
  if (ev.type === "error" || ev.level === "error") return "failed";
  return null;
}

async function fetchSourceProjects(): Promise<{ projects: SourceProject[]; total: number }> {
  const res = await fetch("/api/source/projects", { credentials: "include" });
  if (!res.ok) {
    throw new Error(`Failed to fetch sources (${res.status}): ${await res.text()}`);
  }
  return res.json();
}

export default function SourcesSettingsPage() {
  const { data: status, isLoading: statusLoading } = useOnboardingStatus();
  const { data: config } = useOnboardingConfig();
  const { data: sourceProjects, isLoading: projectsLoading } = useQuery({
    queryKey: ["source-projects"],
    queryFn: fetchSourceProjects,
  });
  const qc = useQueryClient();

  const platformLabel = useMemo(() => {
    const byId = new Map(
      (config?.platforms ?? []).map((p) => [p.id, p.label] as const),
    );
    return (id: string) => byId.get(id) ?? id;
  }, [config]);

  const workspaceSlug = slugify(status?.workspace_name || "workspace");
  const selected = status?.selected_platforms ?? [];
  const platformOptions = useMemo(() => {
    const ids = selected.length > 0 ? selected : (config?.platforms ?? []).map((p) => p.id);
    return ids.length > 0 ? ids : ["generic"];
  }, [config, selected]);
  const [addPlatform, setAddPlatform] = useState("generic");
  const activeAddPlatform = platformOptions.includes(addPlatform)
    ? addPlatform
    : platformOptions[0];
  const [search, setSearch] = useState("");
  const [platformFilter, setPlatformFilter] = useState("all");
  const [sourceTypeFilter, setSourceTypeFilter] = useState("all");
  const projects = sourceProjects?.projects ?? [];
  const filteredProjects = useMemo(() => {
    const q = search.trim().toLowerCase();
    return projects.filter((p) => {
      const matchesSearch =
        !q ||
        p.id.toLowerCase().includes(q) ||
        p.display_name.toLowerCase().includes(q) ||
        (p.repo_url ?? "").toLowerCase().includes(q);
      const matchesPlatform = platformFilter === "all" || p.platform === platformFilter;
      const matchesType = sourceTypeFilter === "all" || p.source_type === sourceTypeFilter;
      return matchesSearch && matchesPlatform && matchesType;
    });
  }, [platformFilter, projects, search, sourceTypeFilter]);

  // Per-platform interactive state for the pending rows
  const [rows, setRows] = useState<Record<string, RowState>>({});
  const [modes, setModes] = useState<Record<string, SourceMode>>({});
  const [githubForms, setGithubForms] = useState<Record<string, GithubState>>({});

  function patchRow(platformId: string, patch: Partial<RowState>) {
    setRows((prev) => ({
      ...prev,
      [platformId]: {
        file: prev[platformId]?.file ?? null,
        projectName: prev[platformId]?.projectName ?? "",
        stage: prev[platformId]?.stage ?? "pending",
        progress: prev[platformId]?.progress ?? 0,
        error: prev[platformId]?.error ?? null,
        logs: prev[platformId]?.logs ?? [],
        logsOpen: prev[platformId]?.logsOpen ?? false,
        ...patch,
      },
    }));
  }

  function pushLog(platformId: string, ev: LogEvent) {
    setRows((prev) => {
      const cur = prev[platformId];
      if (!cur) return prev;
      const line = `[${ev.level ?? "info"}] ${ev.type ? `${ev.type}: ` : ""}${ev.data ?? ""}`;
      return {
        ...prev,
        [platformId]: { ...cur, logs: [...cur.logs.slice(-199), line] },
      };
    });
  }

  function patchGithub(platformId: string, patch: Partial<GithubState>) {
    setGithubForms((prev) => ({
      ...prev,
      [platformId]: {
        repoUrl: prev[platformId]?.repoUrl ?? "",
        token: prev[platformId]?.token ?? "",
        projectName: prev[platformId]?.projectName ?? "",
        displayName: prev[platformId]?.displayName ?? "",
        busy: prev[platformId]?.busy ?? false,
        error: prev[platformId]?.error ?? null,
        ...patch,
      },
    }));
  }

  async function runProjectPipeline(platformId: string, projectId: string) {
    const sub: { stop?: () => void } = {};
    try {
      patchRow(platformId, { stage: "scanning", progress: 100, error: null });
      await scanProject(projectId);

      await new Promise<void>((resolve, reject) => {
        let generateTriggered = false;
        sub.stop = subscribeToLogs(
          projectId,
          (ev) => {
            pushLog(platformId, ev);
            const next = stageFromEvent(ev);
            if (!next) return;
            if (next === "generating" && !generateTriggered) {
              generateTriggered = true;
              patchRow(platformId, { stage: "generating" });
              generateDocs(projectId).catch((err) => {
                patchRow(platformId, {
                  stage: "failed",
                  error: err instanceof Error ? err.message : String(err),
                });
                reject(err);
              });
            } else if (next === "pushing") {
              patchRow(platformId, { stage: "pushing" });
            } else if (next === "complete") {
              patchRow(platformId, { stage: "complete" });
              resolve();
            } else if (next === "failed") {
              patchRow(platformId, {
                stage: "failed",
                error: ev.data ?? "Pipeline reported an error",
              });
              reject(new Error(ev.data ?? "Pipeline error"));
            }
          },
          () => {
            // SSE errors are expected on server restart; don't fail the pipeline.
          },
        );
      });

      qc.invalidateQueries({ queryKey: ["source-projects"] });
      qc.invalidateQueries({ queryKey: ["docs-projects"] });
    } finally {
      sub.stop?.();
    }
  }

  async function runUpload(platformId: string, file: File) {
    const projectName = rows[platformId]?.projectName.trim();
    const projectId = slugify(projectName || `${workspaceSlug}-${platformId}`);
    try {
      patchRow(platformId, { stage: "uploading", progress: 0, error: null });
      await uploadZip({
        file,
        projectId,
        platform: platformId,
        onProgress: (pct) => patchRow(platformId, { progress: pct }),
      });
      await runProjectPipeline(platformId, projectId);
    } catch (err) {
      patchRow(platformId, {
        stage: "failed",
        error: err instanceof Error ? err.message : String(err),
      });
    }
  }

  async function runGithubConnect(platformId: string) {
    const form = githubForms[platformId];
    const label = platformLabel(platformId);
    const repoUrl = form?.repoUrl.trim() ?? "";
    const token = form?.token.trim() ?? "";
    const projectName = form?.projectName.trim() ?? "";
    const displayName = form?.displayName.trim() || projectName || `${label} source`;
    const projectId = slugify(projectName || displayName || `${workspaceSlug}-${platformId}`);

    if (!repoUrl || !token) {
      patchGithub(platformId, { error: "Repository URL and token are required." });
      return;
    }

    try {
      patchGithub(platformId, { busy: true, error: null });
      patchRow(platformId, { stage: "uploading", progress: 0, error: null });
      await addGithubSource({
        repoUrl,
        projectId,
        platform: platformId,
        displayName,
        githubToken: token,
      });
      patchGithub(platformId, { token: "" });
      qc.invalidateQueries({ queryKey: ["source-projects"] });
      await runProjectPipeline(platformId, projectId);
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      patchGithub(platformId, { error: message });
      patchRow(platformId, { stage: "failed", error: message });
    } finally {
      patchGithub(platformId, { busy: false });
    }
  }

  async function runExistingScan(platformId: string, projectId: string) {
    try {
      await runProjectPipeline(platformId, projectId);
    } catch (err) {
      patchRow(platformId, {
        stage: "failed",
        error: err instanceof Error ? err.message : String(err),
      });
    }
  }

  async function runExistingGenerate(platformId: string, projectId: string) {
    try {
      patchRow(platformId, { stage: "generating", progress: 100, error: null });
      await generateDocs(projectId);
      qc.invalidateQueries({ queryKey: ["source-projects"] });
      qc.invalidateQueries({ queryKey: ["docs-projects"] });
    } catch (err) {
      patchRow(platformId, {
        stage: "failed",
        error: err instanceof Error ? err.message : String(err),
      });
    }
  }

  async function runExistingPush(platformId: string, projectId: string) {
    try {
      patchRow(platformId, { stage: "pushing", progress: 100, error: null });
      await pushProjectToRag(projectId);
      patchRow(platformId, { stage: "complete" });
      qc.invalidateQueries({ queryKey: ["source-projects"] });
      qc.invalidateQueries({ queryKey: ["docs-projects"] });
    } catch (err) {
      patchRow(platformId, {
        stage: "failed",
        error: err instanceof Error ? err.message : String(err),
      });
    }
  }

  if (statusLoading || projectsLoading) {
    return (
      <div className="mx-auto w-full max-w-3xl p-6">
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" /> Loading sources…
        </div>
      </div>
    );
  }

  return (
    <div className="mx-auto w-full max-w-3xl p-6 space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Sources</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Connect a GitHub repository or upload a ZIP for each platform you
          declared during onboarding. Scans run sequentially in the
          background.
        </p>
      </div>

      <section className="space-y-3">
        <div>
          <h2 className="text-base font-semibold">Add source</h2>
          <p className="mt-1 text-sm text-muted-foreground">
            Add a new ZIP or GitHub repository, then scan and generate docs
            from the same customer admin screen.
          </p>
        </div>

        <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
          <label className="text-sm font-medium text-muted-foreground">Platform</label>
          <select
            className="h-9 rounded-md border border-input bg-background px-3 text-sm"
            value={activeAddPlatform}
            onChange={(e) => setAddPlatform(e.target.value)}
          >
            {platformOptions.map((platformId) => (
              <option key={platformId} value={platformId}>
                {platformLabel(platformId)}
              </option>
            ))}
          </select>
        </div>

        <SourceRow
          key={`add-${activeAddPlatform}`}
          platformId={activeAddPlatform}
          label={platformLabel(activeAddPlatform)}
          platformLabel={platformLabel(activeAddPlatform)}
          row={rows[activeAddPlatform]}
          mode={modes[activeAddPlatform] ?? "zip"}
          github={githubForms[activeAddPlatform]}
          onModeChange={(mode) => setModes((prev) => ({ ...prev, [activeAddPlatform]: mode }))}
          onGithubChange={(patch) => patchGithub(activeAddPlatform, patch)}
          onGithubConnect={() => runGithubConnect(activeAddPlatform)}
          onProjectNameChange={(projectName) => patchRow(activeAddPlatform, { projectName })}
          onFilePicked={(file) =>
            patchRow(activeAddPlatform, { file, error: null, stage: "pending" })
          }
          onClear={() =>
            patchRow(activeAddPlatform, { file: null, error: null, stage: "pending" })
          }
          onUpload={(file) => runUpload(activeAddPlatform, file)}
          onScanExisting={async () => undefined}
          onGenerateExisting={async () => undefined}
          onPushToRag={async () => undefined}
          onResetStatus={async () => undefined}
          onUpdateToken={async () => undefined}
          onRegisterWebhook={async () => undefined}
          onDeleteWebhook={async () => undefined}
          onCheckWebhook={async () => undefined}
          onDeleteProject={async () => undefined}
          onToggleLogs={() =>
            patchRow(activeAddPlatform, {
              logsOpen: !(rows[activeAddPlatform]?.logsOpen ?? false),
            })
          }
        />
      </section>

      <section className="space-y-3">
        <div>
          <h2 className="text-base font-semibold">Existing sources</h2>
          <p className="mt-1 text-sm text-muted-foreground">
            Every project this admin can access. GitHub credentials and webhook
            controls appear on GitHub sources only.
          </p>
        </div>

        <div className="grid gap-2 sm:grid-cols-[1fr_auto_auto]">
          <div className="relative">
            <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
            <Input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search projects, repos, IDs"
              className="pl-9"
            />
          </div>
          <select
            className="h-10 rounded-md border border-input bg-background px-3 text-sm"
            value={platformFilter}
            onChange={(e) => setPlatformFilter(e.target.value)}
          >
            <option value="all">All platforms</option>
            {platformOptions.map((platformId) => (
              <option key={platformId} value={platformId}>
                {platformLabel(platformId)}
              </option>
            ))}
          </select>
          <select
            className="h-10 rounded-md border border-input bg-background px-3 text-sm"
            value={sourceTypeFilter}
            onChange={(e) => setSourceTypeFilter(e.target.value)}
          >
            <option value="all">All source types</option>
            <option value="git">GitHub</option>
            <option value="zip">ZIP</option>
          </select>
        </div>

        <div className="grid gap-2 sm:grid-cols-4">
          <Stat label="Sources" value={projects.length} />
          <Stat label="GitHub" value={projects.filter((p) => p.source_type === "git").length} />
          <Stat label="Docs" value={projects.reduce((sum, p) => sum + (p.doc_count ?? 0), 0)} />
          <Stat label="Jobs" value={projects.reduce((sum, p) => sum + (p.job_count ?? 0), 0)} />
        </div>

        {projects.length === 0 ? (
          <Card>
            <CardBody>
              <p className="text-sm text-muted-foreground">No sources are connected yet.</p>
            </CardBody>
          </Card>
        ) : filteredProjects.length === 0 ? (
          <Card>
            <CardBody>
              <p className="text-sm text-muted-foreground">No sources match this filter.</p>
            </CardBody>
          </Card>
        ) : (
          <div className="space-y-3">
            {filteredProjects.map((source) => (
              <SourceRow
                key={source.id}
                platformId={source.id}
                label={source.display_name || source.id}
                source={{ ...source, doc_count: source.doc_count ?? 0 }}
                platformLabel={platformLabel(source.platform)}
                row={rows[source.id]}
                mode={modes[source.id] ?? "zip"}
                github={githubForms[source.id]}
                onModeChange={(mode) => setModes((prev) => ({ ...prev, [source.id]: mode }))}
                onGithubChange={(patch) => patchGithub(source.id, patch)}
                onGithubConnect={() => undefined}
                onProjectNameChange={(projectName) => patchRow(source.id, { projectName })}
                onFilePicked={(file) =>
                  patchRow(source.id, { file, error: null, stage: "pending" })
                }
                onClear={() =>
                  patchRow(source.id, { file: null, error: null, stage: "pending" })
                }
                onUpload={(file) => runUpload(source.platform, file)}
                onScanExisting={async () => {
                  await runExistingScan(source.id, source.id);
                }}
                onGenerateExisting={async () => {
                  await runExistingGenerate(source.id, source.id);
                }}
                onPushToRag={async () => {
                  await runExistingPush(source.id, source.id);
                }}
                onResetStatus={async () => {
                  await resetProjectStatus(source.id);
                  qc.invalidateQueries({ queryKey: ["source-projects"] });
                }}
                onUpdateToken={async (token) => {
                  await updateGithubToken(source.id, token);
                  patchGithub(source.id, { token: "" });
                  qc.invalidateQueries({ queryKey: ["source-projects"] });
                }}
                onRegisterWebhook={async () => {
                  await registerGithubWebhook(source.id);
                  qc.invalidateQueries({ queryKey: ["source-projects"] });
                }}
                onDeleteWebhook={async () => {
                  await deleteGithubWebhook(source.id);
                  qc.invalidateQueries({ queryKey: ["source-projects"] });
                }}
                onCheckWebhook={async () => {
                  await checkGithubWebhookStatus(source.id);
                  qc.invalidateQueries({ queryKey: ["source-projects"] });
                }}
                onDeleteProject={async () => {
                  await deleteSourceProject(source.id);
                  qc.invalidateQueries({ queryKey: ["source-projects"] });
                  qc.invalidateQueries({ queryKey: ["docs-projects"] });
                }}
                onToggleLogs={() =>
                  patchRow(source.id, {
                    logsOpen: !(rows[source.id]?.logsOpen ?? false),
                  })
                }
              />
            ))}
          </div>
        )}
      </section>
    </div>
  );
}

// ─── Single platform row ───────────────────────────────────────────

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded-md border border-border bg-muted/20 px-3 py-2">
      <p className="text-xs text-muted-foreground">{label}</p>
      <p className="text-lg font-semibold">{value}</p>
    </div>
  );
}

function Meta({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md border border-border bg-background px-3 py-2">
      <p className="text-[11px] uppercase tracking-wide text-muted-foreground">{label}</p>
      <p className="mt-1 truncate text-foreground">{value}</p>
    </div>
  );
}

interface SourceRowProps {
  platformId: string;
  label: string;
  platformLabel?: string;
  source?: SourceProject;
  row: RowState | undefined;
  mode: SourceMode;
  github: GithubState | undefined;
  onModeChange: (mode: SourceMode) => void;
  onGithubChange: (patch: Partial<GithubState>) => void;
  onGithubConnect: () => void;
  onProjectNameChange: (projectName: string) => void;
  onFilePicked: (file: File) => void;
  onClear: () => void;
  onUpload: (file: File) => void;
  onScanExisting: () => void;
  onGenerateExisting: () => void;
  onPushToRag: () => Promise<unknown>;
  onResetStatus: () => Promise<unknown>;
  onUpdateToken: (token: string) => Promise<unknown>;
  onRegisterWebhook: () => Promise<unknown>;
  onDeleteWebhook: () => Promise<unknown>;
  onCheckWebhook: () => Promise<unknown>;
  onDeleteProject: () => Promise<unknown>;
  onToggleLogs: () => void;
}

function SourceRow({
  platformId,
  label,
  platformLabel,
  source,
  row,
  mode,
  github,
  onModeChange,
  onGithubChange,
  onGithubConnect,
  onProjectNameChange,
  onFilePicked,
  onClear,
  onUpload,
  onScanExisting,
  onGenerateExisting,
  onPushToRag,
  onResetStatus,
  onUpdateToken,
  onRegisterWebhook,
  onDeleteWebhook,
  onCheckWebhook,
  onDeleteProject,
  onToggleLogs,
}: SourceRowProps) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [actionBusy, setActionBusy] = useState<string | null>(null);
  const [tokenDraft, setTokenDraft] = useState("");

  const stage: RowStage = row?.stage ?? "pending";
  const active = stage !== "pending" && stage !== "complete" && stage !== "failed";
  const isConnected = Boolean(source) || stage === "complete";
  const sourceBusy = source?.status === "scanning" || source?.status === "generating";

  async function runAction(name: string, action: () => Promise<unknown>) {
    try {
      setActionBusy(name);
      setActionError(null);
      await action();
    } catch (err) {
      setActionError(err instanceof Error ? err.message : String(err));
    } finally {
      setActionBusy(null);
    }
  }

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between gap-3">
          <div className="flex items-center gap-3 min-w-0">
            <StatusBadge stage={stage} uploaded={Boolean(source)} />
            <div className="min-w-0">
              <p className="font-semibold truncate">{label}</p>
              {source ? (
                <p className="text-xs text-muted-foreground truncate">
                  {source.id} &middot; {platformLabel ?? source.platform} &middot;{" "}
                  {source.source_type === "git" ? "GitHub" : "ZIP"} &middot; {source.doc_count ?? 0} docs
                </p>
              ) : (
                <p className="text-xs text-muted-foreground">
                  {stage === "pending" ? "No source uploaded yet" : STAGE_LABELS[stage]}
                </p>
              )}
            </div>
          </div>
          {source && (
            <div className="flex shrink-0 flex-wrap justify-end gap-1">
              <Badge variant={source.has_github_token ? "success" : "muted"}>
                token {source.has_github_token ? "stored" : "missing"}
              </Badge>
              {source.source_type === "git" && (
                <Badge
                  variant={
                    source.webhook_status === "active"
                      ? "success"
                      : source.webhook_status === "not_registered"
                        ? "warning"
                        : "muted"
                  }
                >
                  webhook {source.webhook_status ?? "unknown"}
                </Badge>
              )}
              <Badge variant={source.status === "error" ? "destructive" : "muted"}>
                {source.status}
              </Badge>
            </div>
          )}
        </div>
      </CardHeader>

      {isConnected && source ? (
        <CardBody className="space-y-3">
          {source.repo_url && (
            <p className="truncate text-xs text-muted-foreground">{source.repo_url}</p>
          )}
          <div className="grid gap-2 text-xs text-muted-foreground sm:grid-cols-3">
            <Meta label="Last scan" value={formatDate(source.last_scan_at)} />
            <Meta label="Last generate" value={formatDate(source.last_generate_at)} />
            <Meta label="Last RAG push" value={formatDate(source.last_rag_push_at)} />
          </div>
          {source.source_type === "git" && (
            <div className="rounded-md border border-border bg-muted/30 p-3 text-xs">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div>
                  <p className="font-medium text-foreground">GitHub integration</p>
                  <p className="mt-1 text-muted-foreground">
                    {source.webhook_status === "active"
                      ? "Webhook active. Pushes can trigger updates."
                      : source.webhook_status === "not_registered"
                        ? "Webhook not registered yet."
                        : "Webhook status unknown."}
                  </p>
                </div>
                <Badge variant={source.has_github_token ? "success" : "warning"}>
                  token {source.has_github_token ? "stored" : "missing"}
                </Badge>
              </div>
              {source.last_ingested_commit_sha && (
                <p className="mt-2 font-mono text-muted-foreground">
                  commit {source.last_ingested_commit_sha.slice(0, 12)}
                </p>
              )}
              {source.auto_reingest_error && (
                <p className="mt-2 text-destructive">{source.auto_reingest_error}</p>
              )}
            </div>
          )}
          <div className="flex flex-wrap gap-2">
            <Button
              size="sm"
              variant="secondary"
              isLoading={actionBusy === "scan"}
              onClick={() => runAction("scan", async () => onScanExisting())}
            >
              <RotateCw className="h-3.5 w-3.5" /> Scan & generate
            </Button>
            <Button
              size="sm"
              variant="secondary"
              isLoading={actionBusy === "generate"}
              onClick={() => runAction("generate", async () => onGenerateExisting())}
            >
              Generate docs
            </Button>
            <Button
              size="sm"
              variant="secondary"
              isLoading={actionBusy === "push"}
              onClick={() => runAction("push", onPushToRag)}
            >
              Push to RAG
            </Button>
            {sourceBusy && (
              <Button
                size="sm"
                variant="ghost"
                isLoading={actionBusy === "reset"}
                onClick={() => runAction("reset", onResetStatus)}
              >
                Reset status
              </Button>
            )}
            {source.source_type === "git" && (
              <>
                <Button
                  size="sm"
                  variant="secondary"
                  isLoading={actionBusy === "check"}
                  onClick={() => runAction("check", onCheckWebhook)}
                >
                  Check webhook
                </Button>
                <Button
                  size="sm"
                  variant="secondary"
                  isLoading={actionBusy === "register"}
                  onClick={() => runAction("register", onRegisterWebhook)}
                >
                  Register webhook
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  isLoading={actionBusy === "delete"}
                  onClick={() => runAction("delete", onDeleteWebhook)}
                >
                  Delete webhook
                </Button>
              </>
            )}
            <Button
              size="sm"
              variant="ghost"
              isLoading={actionBusy === "deleteProject"}
              onClick={() => {
                if (
                  window.confirm(
                    `Delete source '${source.id}'? This removes its scan data and generated docs.`,
                  )
                ) {
                  runAction("deleteProject", onDeleteProject);
                }
              }}
            >
              <Trash2 className="h-3.5 w-3.5" /> Delete source
            </Button>
          </div>
          {source.source_type === "git" && (
            <div className="flex gap-2">
              <Input
                type="password"
                value={tokenDraft}
                placeholder="Update GitHub token"
                onChange={(e) => setTokenDraft(e.target.value)}
              />
              <Button
                size="sm"
                variant="secondary"
                disabled={!tokenDraft.trim()}
                isLoading={actionBusy === "token"}
                onClick={() =>
                  runAction("token", async () => {
                    await onUpdateToken(tokenDraft.trim());
                    setTokenDraft("");
                  })
                }
              >
                Save token
              </Button>
            </div>
          )}
          {(row?.error || actionError) && (
            <p className="text-xs text-destructive">{row?.error ?? actionError}</p>
          )}
        </CardBody>
      ) : (
        <CardBody className="space-y-3">
          <div
            role="tablist"
            aria-label={`${label} source type`}
            className="inline-flex w-full rounded-lg border border-border bg-muted/40 p-1"
          >
            <button
              role="tab"
              type="button"
              aria-selected={mode === "zip"}
              onClick={() => onModeChange("zip")}
              className={cn(
                "flex-1 inline-flex items-center justify-center gap-2 rounded-md h-8 px-3 text-sm font-medium transition-all",
                mode === "zip"
                  ? "bg-background text-foreground shadow-elevation-xs"
                  : "text-muted-foreground hover:text-foreground",
              )}
            >
              <FileArchive className="h-4 w-4" /> ZIP
            </button>
            <button
              role="tab"
              type="button"
              aria-selected={mode === "github"}
              onClick={() => onModeChange("github")}
              className={cn(
                "flex-1 inline-flex items-center justify-center gap-2 rounded-md h-8 px-3 text-sm font-medium transition-all",
                mode === "github"
                  ? "bg-background text-foreground shadow-elevation-xs"
                  : "text-muted-foreground hover:text-foreground",
              )}
            >
              <Github className="h-4 w-4" /> GitHub
            </button>
          </div>

          {stage === "pending" || stage === "failed" ? (
            mode === "github" ? (
              <>
                <div className="grid gap-2 sm:grid-cols-2">
                  <Input
                    type="text"
                    placeholder="Project name / ID"
                    value={github?.projectName ?? ""}
                    onChange={(e) => onGithubChange({ projectName: e.target.value })}
                  />
                  <Input
                    type="url"
                    placeholder="https://github.com/org/repo"
                    value={github?.repoUrl ?? ""}
                    onChange={(e) => onGithubChange({ repoUrl: e.target.value, error: null })}
                    hasError={Boolean(github?.error)}
                  />
                </div>
                <Input
                  type="text"
                  placeholder="Display name"
                  value={github?.displayName ?? ""}
                  onChange={(e) => onGithubChange({ displayName: e.target.value })}
                />
                <Input
                  type="password"
                  placeholder="GitHub personal access token"
                  value={github?.token ?? ""}
                  onChange={(e) => onGithubChange({ token: e.target.value, error: null })}
                  hasError={Boolean(github?.error)}
                />
                {(github?.error || row?.error) && (
                  <p className="text-xs text-destructive">{github?.error ?? row?.error}</p>
                )}
                <div className="flex justify-end">
                  <Button
                    size="sm"
                    isLoading={github?.busy}
                    disabled={!github?.repoUrl.trim() || !github?.token.trim()}
                    onClick={onGithubConnect}
                  >
                    <Github className="h-3.5 w-3.5" /> Connect & scan
                  </Button>
                </div>
              </>
            ) : (
            <>
              <Input
                type="text"
                placeholder="Project name / ID"
                value={row?.projectName ?? ""}
                onChange={(e) => onProjectNameChange(e.target.value)}
              />
              <label
                htmlFor={`src-${platformId}`}
                onDragOver={(e) => {
                  e.preventDefault();
                  setDragging(true);
                }}
                onDragLeave={() => setDragging(false)}
                onDrop={(e) => {
                  e.preventDefault();
                  setDragging(false);
                  const f = e.dataTransfer.files?.[0];
                  if (f && f.name.toLowerCase().endsWith(".zip")) onFilePicked(f);
                }}
                className={cn(
                  "group flex items-center gap-3 rounded-lg border-2 border-dashed p-4 cursor-pointer transition-colors",
                  dragging
                    ? "border-primary bg-primary/5"
                    : row?.file
                      ? "border-primary/50 bg-primary/[0.03]"
                      : "border-border hover:border-primary/40 hover:bg-muted/30",
                )}
              >
                <input
                  ref={inputRef}
                  id={`src-${platformId}`}
                  type="file"
                  accept=".zip"
                  className="sr-only"
                  onChange={(e) => {
                    const f = e.target.files?.[0];
                    if (f) onFilePicked(f);
                  }}
                />
                <span
                  className={cn(
                    "flex h-10 w-10 items-center justify-center rounded-full shrink-0",
                    row?.file
                      ? "bg-primary/10 text-primary"
                      : "bg-muted text-muted-foreground group-hover:bg-primary/10 group-hover:text-primary",
                  )}
                >
                  {row?.file ? (
                    <FileArchive className="h-5 w-5" />
                  ) : (
                    <UploadCloud className="h-5 w-5" />
                  )}
                </span>
                <div className="min-w-0 flex-1">
                  {row?.file ? (
                    <p className="truncate text-sm">
                      {row.file.name}{" "}
                      <span className="text-muted-foreground">
                        &middot; {formatSize(row.file.size)}
                      </span>
                    </p>
                  ) : (
                    <p className="text-sm text-muted-foreground">
                      Click or drop a .zip for {label}
                    </p>
                  )}
                </div>
                {row?.file && (
                  <button
                    type="button"
                    onClick={(e) => {
                      e.preventDefault();
                      onClear();
                      if (inputRef.current) inputRef.current.value = "";
                    }}
                    className="shrink-0 rounded-full p-1 text-muted-foreground hover:bg-muted hover:text-foreground"
                    aria-label={`Remove ${row.file.name}`}
                  >
                    <X className="h-4 w-4" />
                  </button>
                )}
              </label>

              {row?.error && (
                <p className="text-xs text-destructive">{row.error}</p>
              )}

              <div className="flex justify-end">
                <Button
                  size="sm"
                  disabled={!row?.file}
                  onClick={() => row?.file && onUpload(row.file)}
                >
                  {stage === "failed" ? (
                    <>
                      <RotateCw className="h-3.5 w-3.5 mr-1" /> Retry upload
                    </>
                  ) : (
                    "Upload & scan"
                  )}
                </Button>
              </div>
            </>
            )
          ) : (
            <>
              <div className="flex items-center gap-2 text-sm">
                <Loader2 className="h-4 w-4 animate-spin text-primary" />
                <span className="font-medium">{STAGE_LABELS[stage]}</span>
                {stage === "uploading" && (
                  <span className="text-muted-foreground">
                    &mdash; {row?.progress ?? 0}%
                  </span>
                )}
              </div>
              {stage === "uploading" && (
                <div className="h-1.5 w-full rounded-full bg-muted">
                  <div
                    className="h-full rounded-full bg-primary transition-all"
                    style={{ width: `${row?.progress ?? 0}%` }}
                  />
                </div>
              )}
            </>
          )}

          {(active || (row?.logs.length ?? 0) > 0) && (
            <>
              <button
                type="button"
                onClick={onToggleLogs}
                className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
              >
                {row?.logsOpen ? (
                  <ChevronDown className="h-3.5 w-3.5" />
                ) : (
                  <ChevronRight className="h-3.5 w-3.5" />
                )}
                {row?.logs.length ?? 0} log line
                {(row?.logs.length ?? 0) === 1 ? "" : "s"}
              </button>
              {row?.logsOpen && (
                <pre className="max-h-48 overflow-auto rounded bg-muted/40 p-2 text-[11px] leading-snug text-muted-foreground">
                  {(row?.logs ?? []).join("\n") || "Waiting for log output…"}
                </pre>
              )}
            </>
          )}
        </CardBody>
      )}
    </Card>
  );
}

function StatusBadge({
  stage,
  uploaded,
}: {
  stage: RowStage;
  uploaded: boolean;
}) {
  if (uploaded || stage === "complete") {
    return (
      <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-emerald-500/10 text-emerald-600">
        <Check className="h-4 w-4" strokeWidth={3} aria-hidden />
      </span>
    );
  }
  if (stage === "failed") {
    return (
      <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-destructive/10 text-destructive">
        <AlertCircle className="h-4 w-4" aria-hidden />
      </span>
    );
  }
  if (stage !== "pending") {
    return (
      <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-primary/10 text-primary">
        <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
      </span>
    );
  }
  return (
    <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-muted text-muted-foreground">
      <Database className="h-4 w-4" aria-hidden />
    </span>
  );
}
