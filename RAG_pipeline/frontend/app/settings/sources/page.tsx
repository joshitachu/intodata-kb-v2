"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
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
  RefreshCw,
  Search,
  RotateCw,
  Zap,
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
  fetchSourceLogHistory,
  generateDocsWhenScanned,
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
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { AzureDevOpsConnectPanel } from "./AzureDevOpsConnectPanel";
import { GithubAppConnectPanel } from "./GithubAppConnectPanel";
import { ProjectConnectorBindings } from "./ProjectConnectorBindings";
import { SimpleConnectorPanel } from "./SimpleConnectorPanel";
import { ProjectLiveSource, useProjectLiveSources } from "./liveSources";

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

const STAGE_PROGRESS: Record<RowStage, number> = {
  pending: 0,
  uploading: 0,
  scanning: 25,
  generating: 55,
  pushing: 90,
  complete: 100,
  failed: 100,
};

const ACTIVE_PIPELINES_STORAGE_KEY = "intodata.sourcePipelines.v1";

interface PersistedPipeline {
  projectId: string;
  platformId: string;
  stage: RowStage;
  updatedAt: number;
}

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

function unitLabel(platform: string, count: number): string {
  const id = platform.toLowerCase();
  if (id === "workato") return count === 1 ? "recipe" : "recipes";
  if (id === "lobster") return count === 1 ? "workflow" : "workflows";
  if (id === "talend") return count === 1 ? "Talend design" : "Talend designs";
  return count === 1 ? "unit" : "units";
}

function stageFromEvent(ev: LogEvent): RowStage | null {
  const text = `${ev.type ?? ""} ${ev.data ?? ""} ${ev.message ?? ""}`.toLowerCase();
  if (ev.type === "error" || ev.level === "error") return "failed";
  if (
    ev.type === "scan_complete" ||
    text.includes("scan complete") ||
    text.includes("scan completed")
  )
    return "generating";
  if (
    ev.level === "done" ||
    ev.type === "done"
  )
    return "complete";
  if (
    ev.type === "generate_complete" ||
    ev.type === "push_complete" ||
    text.includes("generation complete") ||
    text.includes("generate complete") ||
    text.includes("push complete") ||
    text.includes("pushed to rag") ||
    text.includes("pushing") ||
    text.includes("rag push")
  )
    return "pushing";
  return null;
}

function progressFromEvent(ev: LogEvent, stage: RowStage): number {
  const text = `${ev.type ?? ""} ${ev.data ?? ""} ${ev.message ?? ""}`.toLowerCase();
  if (ev.level === "done") return 100;
  if (stage === "scanning") return Math.max(STAGE_PROGRESS.scanning, 30);
  if (text.includes("pre-processing")) return 45;
  if (text.includes("pre-processed")) return 50;
  if (text.includes("resolved doc generation routing")) return 58;
  if (text.includes("phase 2") || text.includes("analysis")) return 68;
  if (text.includes("phase 3") || text.includes("document")) return 78;
  if (text.includes("assessment") || text.includes("quality score")) return 88;
  if (text.includes("rag") || text.includes("push")) return 92;
  return STAGE_PROGRESS[stage];
}

function formatLogLine(ev: LogEvent): string {
  const level = ev.level ?? "info";
  const message = ev.message ?? ev.data ?? "";
  const type = ev.type ? `${ev.type}: ` : "";
  return `[${level}] ${type}${message}`;
}

function readPersistedPipelines(): Record<string, PersistedPipeline> {
  if (typeof window === "undefined") return {};
  try {
    const raw = window.localStorage.getItem(ACTIVE_PIPELINES_STORAGE_KEY);
    if (!raw) return {};
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return {};
    return Object.fromEntries(
      Object.entries(parsed).filter(([, item]) => {
        const value = item as Partial<PersistedPipeline>;
        return (
          typeof value.projectId === "string" &&
          typeof value.platformId === "string" &&
          typeof value.stage === "string" &&
          typeof value.updatedAt === "number"
        );
      }),
    ) as Record<string, PersistedPipeline>;
  } catch {
    return {};
  }
}

function writePersistedPipelines(pipelines: Record<string, PersistedPipeline>) {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(ACTIVE_PIPELINES_STORAGE_KEY, JSON.stringify(pipelines));
}

function upsertPersistedPipeline(pipeline: PersistedPipeline) {
  const pipelines = readPersistedPipelines();
  pipelines[pipeline.projectId] = pipeline;
  writePersistedPipelines(pipelines);
}

function removePersistedPipeline(projectId: string) {
  const pipelines = readPersistedPipelines();
  if (!(projectId in pipelines)) return;
  delete pipelines[projectId];
  writePersistedPipelines(pipelines);
}

function stageFromProject(source: SourceProject): RowStage | null {
  if (source.status === "scanning") return "scanning";
  if (source.status === "generating") return "generating";
  if (source.status === "error") return "failed";
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
    refetchInterval: (query) =>
      query.state.data?.projects.some((project) =>
        project.status === "scanning" || project.status === "generating"
      )
        ? 5000
        : false,
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
  const [manualAddOpen, setManualAddOpen] = useState(false);
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
  const activePipelineRefs = useRef<Set<string>>(new Set());

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
      const line = formatLogLine(ev);
      return {
        ...prev,
        [platformId]: { ...cur, logs: [...cur.logs.slice(-199), line] },
      };
    });
  }

  function appendLogHistory(platformId: string, lines: string[]) {
    if (lines.length === 0) return;
    setRows((prev) => {
      const cur = prev[platformId];
      if (!cur || cur.logs.length > 0) return prev;
      return {
        ...prev,
        [platformId]: { ...cur, logs: lines.slice(-200), logsOpen: true },
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

  const monitorProjectPipeline = useCallback(async (
    platformId: string,
    projectId: string,
    options: { startScan?: boolean; startGenerate?: boolean; initialStage?: RowStage } = {},
  ) => {
    if (activePipelineRefs.current.has(projectId)) return;
    activePipelineRefs.current.add(projectId);
    upsertPersistedPipeline({
      projectId,
      platformId,
      stage: options.initialStage ?? (options.startGenerate ? "generating" : "scanning"),
      updatedAt: Date.now(),
    });

    const sub: { stop?: () => void } = {};
    try {
      fetchSourceLogHistory(projectId)
        .then((history) => appendLogHistory(platformId, history.lines))
        .catch(() => {
          // Log history is best-effort; live SSE/project status remains authoritative.
        });

      await new Promise<void>((resolve, reject) => {
        let generateTriggered = options.initialStage === "generating" || options.startGenerate === true;
        let settled = false;
        sub.stop = subscribeToLogs(
          projectId,
          (ev) => {
            pushLog(platformId, ev);
            const next = stageFromEvent(ev);
            if (!next) {
              const currentStage = generateTriggered ? "generating" : "scanning";
              patchRow(platformId, { progress: progressFromEvent(ev, currentStage) });
              return;
            }
            if (next === "generating" && !generateTriggered) {
              generateTriggered = true;
              upsertPersistedPipeline({
                projectId,
                platformId,
                stage: "generating",
                updatedAt: Date.now(),
              });
              patchRow(platformId, {
                stage: "generating",
                progress: progressFromEvent(ev, "generating"),
              });
              // The log marker only says "the scanner thinks it is done". It can
              // arrive from an earlier run or before scan_result.json lands, so the
              // dispatch itself waits on the project row before generating.
              generateDocsWhenScanned(projectId).catch((err) => {
                if (settled) return;
                settled = true;
                removePersistedPipeline(projectId);
                patchRow(platformId, {
                  stage: "failed",
                  progress: STAGE_PROGRESS.failed,
                  error: err instanceof Error ? err.message : String(err),
                });
                reject(err);
              });
            } else if (next === "pushing") {
              upsertPersistedPipeline({
                projectId,
                platformId,
                stage: "pushing",
                updatedAt: Date.now(),
              });
              patchRow(platformId, {
                stage: "pushing",
                progress: progressFromEvent(ev, "pushing"),
              });
            } else if (next === "complete") {
              if (settled) return;
              settled = true;
              removePersistedPipeline(projectId);
              patchRow(platformId, { stage: "complete", progress: STAGE_PROGRESS.complete });
              resolve();
            } else if (next === "failed") {
              if (settled) return;
              settled = true;
              removePersistedPipeline(projectId);
              patchRow(platformId, {
                stage: "failed",
                progress: STAGE_PROGRESS.failed,
                error: ev.message ?? ev.data ?? "Pipeline reported an error",
              });
              reject(new Error(ev.message ?? ev.data ?? "Pipeline error"));
            } else {
              patchRow(platformId, { progress: progressFromEvent(ev, next) });
            }
          },
          () => {
            // SSE errors are expected on server restart; don't fail the pipeline.
          },
        );

        patchRow(platformId, {
          stage: options.initialStage ?? (options.startGenerate ? "generating" : "scanning"),
          progress: STAGE_PROGRESS[options.initialStage ?? (options.startGenerate ? "generating" : "scanning")],
          error: null,
          logs: [],
          logsOpen: true,
        });
        const starter = options.startGenerate
          ? generateDocsWhenScanned(projectId)
          : options.startScan === false
            ? Promise.resolve()
            : scanProject(projectId);
        starter.catch((err) => {
          if (settled) return;
          settled = true;
          removePersistedPipeline(projectId);
          patchRow(platformId, {
            stage: "failed",
            progress: STAGE_PROGRESS.failed,
            error: err instanceof Error ? err.message : String(err),
          });
          reject(err);
        });
      });

      qc.invalidateQueries({ queryKey: ["source-projects"] });
      qc.invalidateQueries({ queryKey: ["docs-projects"] });
    } finally {
      sub.stop?.();
      activePipelineRefs.current.delete(projectId);
    }
  }, [qc]);

  async function runProjectPipeline(platformId: string, projectId: string) {
    await monitorProjectPipeline(platformId, projectId, { startScan: true, initialStage: "scanning" });
  }

  async function runUpload(platformId: string, file: File) {
    const projectName = rows[platformId]?.projectName.trim();
    const projectId = slugify(projectName || `${workspaceSlug}-${platformId}`);
    try {
      patchRow(platformId, {
        stage: "uploading",
        progress: 0,
        error: null,
        logs: [],
        logsOpen: true,
      });
      await uploadZip({
        file,
        projectId,
        platform: platformId,
        onProgress: (pct) => patchRow(platformId, { progress: Math.round(pct * 0.2) }),
      });
      await runProjectPipeline(platformId, projectId);
    } catch (err) {
      patchRow(platformId, {
        stage: "failed",
        progress: STAGE_PROGRESS.failed,
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
      patchRow(platformId, {
        stage: "uploading",
        progress: 10,
        error: null,
        logs: [],
        logsOpen: true,
      });
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
      patchRow(platformId, { stage: "failed", progress: STAGE_PROGRESS.failed, error: message });
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
        progress: STAGE_PROGRESS.failed,
        error: err instanceof Error ? err.message : String(err),
      });
    }
  }

  async function runExistingGenerate(platformId: string, projectId: string) {
    const sub: { stop?: () => void } = {};
    try {
      upsertPersistedPipeline({
        projectId,
        platformId,
        stage: "generating",
        updatedAt: Date.now(),
      });
      await new Promise<void>((resolve, reject) => {
        let settled = false;
        sub.stop = subscribeToLogs(
          projectId,
          (ev) => {
            pushLog(platformId, ev);
            const next = stageFromEvent(ev);
            if (!next) {
              patchRow(platformId, {
                stage: "generating",
                progress: Math.max(55, progressFromEvent(ev, "generating")),
              });
            } else if (next === "failed") {
              if (settled) return;
              settled = true;
              removePersistedPipeline(projectId);
              patchRow(platformId, {
                stage: "failed",
                progress: STAGE_PROGRESS.failed,
                error: ev.message ?? ev.data ?? "Generation failed",
              });
              reject(new Error(ev.message ?? ev.data ?? "Generation failed"));
            } else if (next === "pushing") {
              patchRow(platformId, {
                stage: "pushing",
                progress: progressFromEvent(ev, "pushing"),
              });
            } else if (next === "complete") {
              if (settled) return;
              settled = true;
              removePersistedPipeline(projectId);
              patchRow(platformId, { stage: "complete", progress: STAGE_PROGRESS.complete });
              resolve();
            } else if (next === "generating") {
              patchRow(platformId, {
                stage: "generating",
                progress: Math.max(55, progressFromEvent(ev, "generating")),
              });
            }
          },
          () => {
            // Keep waiting for explicit completion/failure events.
          },
        );

        patchRow(platformId, {
          stage: "generating",
          progress: STAGE_PROGRESS.generating,
          error: null,
          logs: [],
          logsOpen: true,
        });
        generateDocsWhenScanned(projectId).catch((err) => {
          if (settled) return;
          settled = true;
          removePersistedPipeline(projectId);
          patchRow(platformId, {
            stage: "failed",
            progress: STAGE_PROGRESS.failed,
            error: err instanceof Error ? err.message : String(err),
          });
          reject(err);
        });
      });
      qc.invalidateQueries({ queryKey: ["source-projects"] });
      qc.invalidateQueries({ queryKey: ["docs-projects"] });
    } catch (err) {
      patchRow(platformId, {
        stage: "failed",
        progress: STAGE_PROGRESS.failed,
        error: err instanceof Error ? err.message : String(err),
      });
    } finally {
      sub.stop?.();
    }
  }

  async function runExistingPush(platformId: string, projectId: string) {
    try {
      upsertPersistedPipeline({
        projectId,
        platformId,
        stage: "pushing",
        updatedAt: Date.now(),
      });
      patchRow(platformId, {
        stage: "pushing",
        progress: STAGE_PROGRESS.pushing,
        error: null,
        logs: ["[info] Pushing generated documents to the knowledge base..."],
        logsOpen: true,
      });
      await pushProjectToRag(projectId);
      removePersistedPipeline(projectId);
      patchRow(platformId, {
        stage: "complete",
        progress: STAGE_PROGRESS.complete,
        logs: ["[info] Pushing generated documents to the knowledge base...", "[done] Push complete"],
      });
      qc.invalidateQueries({ queryKey: ["source-projects"] });
      qc.invalidateQueries({ queryKey: ["docs-projects"] });
    } catch (err) {
      removePersistedPipeline(projectId);
      patchRow(platformId, {
        stage: "failed",
        progress: STAGE_PROGRESS.failed,
        error: err instanceof Error ? err.message : String(err),
      });
    }
  }

  useEffect(() => {
    if (!sourceProjects) return;

    const persisted = readPersistedPipelines();
    const nextPersisted: Record<string, PersistedPipeline> = {};
    const now = Date.now();

    for (const source of projects) {
      const saved = persisted[source.id];
      const liveStage = stageFromProject(source);
      const lastScanAt = source.last_scan_at ? new Date(source.last_scan_at).getTime() : 0;
      const lastGenerateAt = source.last_generate_at
        ? new Date(source.last_generate_at).getTime()
        : 0;
      const scanNeedsGeneration =
        source.status === "ready" &&
        saved &&
        (saved.stage === "scanning" || saved.stage === "generating") &&
        lastScanAt > 0 &&
        lastScanAt >= lastGenerateAt;

      if (source.status === "error") {
        if (saved) removePersistedPipeline(source.id);
        patchRow(source.id, {
          stage: "failed",
          progress: STAGE_PROGRESS.failed,
          error: source.error_message ?? "Source workflow failed",
          logsOpen: true,
        });
        fetchSourceLogHistory(source.id)
          .then((history) => appendLogHistory(source.id, history.lines))
          .catch(() => {});
        continue;
      }

      if (scanNeedsGeneration) {
        nextPersisted[source.id] = {
          projectId: source.id,
          platformId: source.id,
          stage: "generating",
          updatedAt: now,
        };
        patchRow(source.id, {
          stage: "generating",
          progress: STAGE_PROGRESS.generating,
          error: null,
          logsOpen: true,
        });
        void monitorProjectPipeline(source.id, source.id, {
          startGenerate: true,
          initialStage: "generating",
        });
        continue;
      }

      const recoveredStage = liveStage ?? saved?.stage ?? null;
      if (!recoveredStage) continue;

      if (source.status === "ready" && saved) {
        removePersistedPipeline(source.id);
        patchRow(source.id, {
          stage: "complete",
          progress: STAGE_PROGRESS.complete,
          error: null,
          logsOpen: true,
        });
        fetchSourceLogHistory(source.id)
          .then((history) => appendLogHistory(source.id, history.lines))
          .catch(() => {});
        continue;
      }

      if (saved || recoveredStage === "generating") {
        nextPersisted[source.id] = {
          projectId: source.id,
          platformId: source.id,
          stage: recoveredStage,
          updatedAt: now,
        };
      }
      patchRow(source.id, {
        stage: recoveredStage,
        progress: Math.max(STAGE_PROGRESS[recoveredStage], recoveredStage === "generating" ? 55 : 0),
        error: null,
        logsOpen: true,
      });
      if (saved || recoveredStage === "generating") {
        void monitorProjectPipeline(source.id, source.id, {
          startScan: false,
          initialStage: recoveredStage,
        });
      } else {
        fetchSourceLogHistory(source.id)
          .then((history) => appendLogHistory(source.id, history.lines))
          .catch(() => {});
      }
    }

    const validIds = new Set(projects.map((project) => project.id));
    for (const [projectId, pipeline] of Object.entries(persisted)) {
      if (validIds.has(projectId)) continue;
      if (now - pipeline.updatedAt < 24 * 60 * 60 * 1000) {
        nextPersisted[projectId] = pipeline;
      }
    }
    writePersistedPipelines(nextPersisted);
  }, [monitorProjectPipeline, projects, sourceProjects]);

  if (statusLoading || projectsLoading) {
    return (
      <div className="mx-auto w-full max-w-3xl p-6">
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" /> Loading sources…
        </div>
      </div>
    );
  }

  const attentionCount = projects.filter((project) => project.status === "error").length;
  const totalDocs = projects.reduce((sum, project) => sum + (project.doc_count ?? 0), 0);
  const totalUnits = projects.reduce((sum, project) => sum + (project.job_count ?? 0), 0);
  const { byProject: liveSourcesByProject } = useProjectLiveSources();
  const projectsWithLiveData = projects.filter((project) => (liveSourcesByProject.get(project.id)?.length ?? 0) > 0).length;

  return (
    <div className="mx-auto w-full max-w-3xl space-y-10 px-6 py-12">
      <header className="flex flex-col gap-4 sm:flex-row sm:items-baseline sm:justify-between">
        <div>
          <h1 className="text-3xl font-semibold tracking-tight">Sources</h1>
          <p className="mt-2 max-w-xl text-sm leading-6 text-muted-foreground">
            Source code, generated documentation, and live evidence for every project you can manage.
          </p>
        </div>
        <div className="flex shrink-0 items-baseline gap-5">
          <span className="text-sm text-muted-foreground">
            {attentionCount > 0 ? `${attentionCount} need attention` : `${projects.length} sources`}
          </span>
        </div>
      </header>

      <section aria-labelledby="add-source-heading">
        <h2 id="add-source-heading" className="sr-only">Add source</h2>
        <GithubAppConnectPanel
          platforms={platformOptions.map((id) => ({ id, label: platformLabel(id) }))}
          defaultPlatform={activeAddPlatform}
          existingProjects={projects}
          onConnected={async (project) => {
            qc.invalidateQueries({ queryKey: ["source-projects"] });
            await runProjectPipeline(project.id, project.id);
          }}
        />

        <AzureDevOpsConnectPanel
          platforms={platformOptions.map((id) => ({ id, label: platformLabel(id) }))}
          defaultPlatform={activeAddPlatform}
          existingProjects={projects}
          onConnected={async (project) => {
            qc.invalidateQueries({ queryKey: ["source-projects"] });
            await runProjectPipeline(project.id, project.id);
          }}
        />

        <div className="border-b border-border">
          <div className="flex flex-col gap-4 px-1 py-5 sm:flex-row sm:items-center sm:justify-between">
            <div className="flex min-w-0 items-center gap-3">
              <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-muted text-muted-foreground">
                <FileArchive className="h-5 w-5" />
              </span>
              <div>
                <h3 className="text-base font-semibold">ZIP or manual Git URL</h3>
                <p className="mt-1 text-sm text-muted-foreground">
                  Upload an archive, or paste a GitHub / Azure DevOps repository URL with
                  a personal access token.
                </p>
              </div>
            </div>
            <button
              type="button"
              onClick={() => setManualAddOpen((current) => !current)}
              className="shrink-0 border-b-2 border-[#fe9543] text-sm font-semibold text-foreground transition-colors hover:text-[#d96f23]"
              aria-expanded={manualAddOpen}
            >
              {manualAddOpen ? "Close form" : "Add manually"}
            </button>
          </div>

          {manualAddOpen && (
            <div className="space-y-4 border-t border-border px-1 py-5">
              <label className="flex items-center gap-2 text-sm text-muted-foreground">
                Platform
                <select
                  className="h-9 rounded-md border border-input bg-background px-3 text-sm text-foreground"
                  value={activeAddPlatform}
                  onChange={(e) => setAddPlatform(e.target.value)}
                >
                  {platformOptions.map((platformId) => (
                    <option key={platformId} value={platformId}>
                      {platformLabel(platformId)}
                    </option>
                  ))}
                </select>
              </label>

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
            </div>
          )}
        </div>
      </section>

      <SimpleConnectorPanel />

      <section className="space-y-5" aria-labelledby="existing-sources-heading">
        <div className="flex flex-col gap-2 sm:flex-row sm:items-end sm:justify-between">
          <div>
            <h2 id="existing-sources-heading" className="text-base font-semibold">Connected sources</h2>
            <p className="mt-1 text-sm text-muted-foreground">
              Select a project to manage its pipeline, GitHub settings, and logs.
            </p>
            {projectsWithLiveData > 0 && (
              <p className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
                <span>
                  {projectsWithLiveData} of {projects.length} read external data:
                </span>
                <span className="inline-flex items-center gap-1">
                  <Zap className="h-3 w-3" aria-hidden /> live evidence, queried during OpenCode runs
                </span>
                <span className="inline-flex items-center gap-1">
                  <RefreshCw className="h-3 w-3" aria-hidden /> runtime history, mirrored on a schedule
                </span>
                <span>Amber means connected but not currently read.</span>
              </p>
            )}
          </div>
          <p className="text-xs text-muted-foreground">
            {totalUnits} units · {totalDocs} docs · {projects.filter((p) => p.source_type === "git").length} GitHub
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
          <div className="border-y border-border">
            <div className="hidden grid-cols-[minmax(0,1fr)_auto_auto] gap-5 border-b border-border px-1 py-2 text-[11px] font-semibold uppercase tracking-[0.12em] text-muted-foreground sm:grid">
              <span>Project source</span>
              <span className="min-w-28 text-right">Evidence</span>
              <span className="min-w-28 text-right">Updated</span>
            </div>
            {filteredProjects.map((source) => (
              <SourceRow
                key={source.id}
                platformId={source.id}
                label={source.display_name || source.id}
                source={{ ...source, doc_count: source.doc_count ?? 0 }}
                liveSources={liveSourcesByProject.get(source.id)}
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

function Meta({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <p className="text-[11px] uppercase tracking-wide text-muted-foreground">{label}</p>
      <p className="mt-1 truncate text-sm font-medium text-foreground">{value}</p>
    </div>
  );
}

/** The external sources a documentation run for one project will read.
 *
 * Rendered on the collapsed row because that is where an operator asks the
 * question -- the per-project connector editor further down answers it only
 * after expanding, and it never covered scheduled runtime mirrors at all.
 * An unhealthy source still gets a chip: "bound but not being read" is the
 * state most worth seeing, and hiding it would read as "not configured".
 */
function LiveSourceChips({ sources }: { sources: ProjectLiveSource[] }) {
  if (sources.length === 0) return null;
  return (
    <span className="mt-1 flex flex-wrap items-center gap-1">
      {sources.map((entry) => {
        const Icon = entry.kind === "runtime_sync" ? RefreshCw : Zap;
        return (
          <span
            key={entry.key}
            title={`${entry.label} - ${entry.detail}`}
            className={cn(
              "inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-medium",
              entry.healthy
                ? "border-emerald-200 bg-emerald-50 text-emerald-700"
                : "border-amber-200 bg-amber-50 text-amber-700",
            )}
          >
            <Icon className="h-3 w-3 shrink-0" aria-hidden />
            <span className="truncate">{entry.label}</span>
          </span>
        );
      })}
    </span>
  );
}

interface SourceRowProps {
  platformId: string;
  label: string;
  platformLabel?: string;
  source?: SourceProject;
  liveSources?: ProjectLiveSource[];
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
  liveSources,
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
  const [confirmDeleteOpen, setConfirmDeleteOpen] = useState(false);
  const [expanded, setExpanded] = useState(!source);

  const stage: RowStage = row?.stage ?? "pending";
  const active = stage !== "pending" && stage !== "complete" && stage !== "failed";
  const isConnected = Boolean(source) || stage === "complete";
  const sourceBusy = source?.status === "scanning" || source?.status === "generating";
  // Azure DevOps projects resolve their credential from an organization connection,
  // so the per-project GitHub token and webhook controls do not apply to them.
  const isAzureDevOpsSource = source?.connection_kind === "azure_devops";
  const sourceStatus = source?.status === "error"
    ? "Needs attention"
    : sourceBusy
      ? STAGE_LABELS[stage === "pending" ? "scanning" : stage]
      : source?.last_rag_push_at
        ? `${source.doc_count ?? 0} docs · in RAG`
        : source?.doc_count
          ? `${source.doc_count} docs`
          : "Scanned, no docs";

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
    <Card
      className={cn(
        source
          ? "rounded-none border-0 border-b border-border bg-transparent shadow-none last:border-b-0"
          : "rounded-none border-x-0 shadow-none",
      )}
    >
      <CardHeader className={cn(source ? "border-0 p-0" : "px-1")}>
        {source ? (
          <button
            type="button"
            onClick={() => setExpanded((current) => !current)}
            className="grid w-full grid-cols-[minmax(0,1fr)_auto] items-center gap-4 px-1 py-4 text-left transition-colors hover:bg-muted/30 sm:grid-cols-[minmax(0,1fr)_auto_auto]"
            aria-expanded={expanded}
          >
            <span className="flex min-w-0 items-center gap-3">
              <span
                className={cn(
                  "h-1.5 w-1.5 shrink-0 rounded-full",
                  source.status === "error"
                    ? "bg-destructive"
                    : sourceBusy
                      ? "bg-amber-500"
                      : "bg-emerald-500",
                )}
              />
              {source.source_type === "git" ? (
                <Github className="h-4 w-4 shrink-0 text-muted-foreground" />
              ) : (
                <FileArchive className="h-4 w-4 shrink-0 text-muted-foreground" />
              )}
              <span className="min-w-0">
                <span className="block truncate text-sm font-semibold">{label}</span>
                <span className="mt-0.5 block truncate text-xs text-muted-foreground">
                  {source.id} · {platformLabel ?? source.platform} · {source.source_type === "git" ? "GitHub" : "ZIP"}
                </span>
                <LiveSourceChips sources={liveSources ?? []} />
              </span>
            </span>
            <span className={cn(
              "hidden min-w-28 text-right text-sm sm:block",
              source.status === "error" ? "text-destructive" : "text-muted-foreground",
            )}>
              {sourceStatus}
            </span>
            <span className="flex min-w-28 items-center justify-end gap-3 text-sm text-muted-foreground">
              {formatDate(source.last_generate_at ?? source.last_scan_at)}
              <ChevronDown className={cn("h-4 w-4 transition-transform", expanded && "rotate-180")} />
            </span>
          </button>
        ) : (
        <div className="flex items-center justify-between gap-3">
          <div className="flex items-center gap-3 min-w-0">
            <StatusBadge stage={stage} uploaded={Boolean(source)} />
            <div className="min-w-0">
              <p className="font-semibold truncate">{label}</p>
              <p className="text-xs text-muted-foreground">
                {stage === "pending" ? "No source uploaded yet" : STAGE_LABELS[stage]}
              </p>
            </div>
          </div>
        </div>
        )}
      </CardHeader>

      {isConnected && source ? (
        expanded ? <CardBody className="space-y-5 border-t border-border bg-muted/[0.12] px-1 py-5">
          {source.repo_url && (
            <p className="truncate text-xs text-muted-foreground">{source.repo_url}</p>
          )}
          <div className="grid gap-2 text-xs text-muted-foreground sm:grid-cols-3">
            <Meta
              label={unitLabel(source.platform, source.job_count ?? 0)}
              value={String(source.job_count ?? 0)}
            />
            {source.platform === "talend" && source.artifact_counts && (
              <Meta
                label="Jobs / Routes"
                value={`${source.artifact_counts.job_count ?? 0} / ${source.artifact_counts.route_count ?? 0}`}
              />
            )}
            <Meta label="Docs" value={String(source.doc_count ?? 0)} />
          </div>
          <div className="grid gap-2 text-xs text-muted-foreground sm:grid-cols-3">
            <Meta label="Last scan" value={formatDate(source.last_scan_at)} />
            <Meta label="Last generate" value={formatDate(source.last_generate_at)} />
            <Meta label="Last RAG push" value={formatDate(source.last_rag_push_at)} />
          </div>
          {source.source_type === "git" && (
            <div className="rounded-md border border-border bg-muted/30 p-3 text-xs">
              {(() => {
                const summary = source.git_update_summary ?? null;
                const action = typeof summary?.action === "string" ? summary.action : null;
                const strategy = typeof summary?.strategy === "string" ? summary.strategy : null;
                const confidence = typeof summary?.confidence === "number" ? summary.confidence : null;
                const affectedDocs = Array.isArray(summary?.affected_docs)
                  ? summary.affected_docs.filter((item): item is string => typeof item === "string")
                  : [];
                const touchedDocs = Array.isArray(summary?.touched_docs)
                  ? summary.touched_docs.filter((item): item is string => typeof item === "string")
                  : [];
                const displayDocs = touchedDocs.length ? touchedDocs : affectedDocs;
                const fallbackUsed = summary?.fallback_used === true;
                const repairAttempts =
                  typeof summary?.repair_attempts === "number" ? summary.repair_attempts : 0;
                const toolCalls = Array.isArray(summary?.tool_calls)
                  ? summary.tool_calls.length
                  : typeof summary?.tool_call_count === "number"
                    ? summary.tool_call_count
                    : 0;
                const manualReason =
                  typeof summary?.manual_review_reason === "string" ? summary.manual_review_reason : null;
                return (
                  <>
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div>
                  <p className="font-medium text-foreground">
                    {isAzureDevOpsSource ? "Azure DevOps integration" : "GitHub integration"}
                  </p>
                  <p className="mt-1 text-muted-foreground">
                    {isAzureDevOpsSource
                      ? // Azure DevOps service hooks are not wired up; the git poller
                        // picks up new commits instead.
                        "Updates are picked up by periodic polling of the repository."
                      : source.webhook_status === "active"
                      ? "Webhook active. Pushes can trigger updates."
                      : source.webhook_status === "not_registered"
                        ? "Webhook not registered yet."
                        : "Webhook status unknown."}
                  </p>
                </div>
                <Badge variant={source.has_github_token ? "success" : "warning"}>
                  {isAzureDevOpsSource
                    ? `organization token ${source.has_github_token ? "stored" : "missing"}`
                    : `token ${source.has_github_token ? "stored" : "missing"}`}
                </Badge>
              </div>
              {action && (
                <p className="mt-2 text-muted-foreground">
                  Last update: <span className="font-mono text-foreground">{action}</span>
                  {strategy ? <> · <span className="font-mono text-foreground">{strategy}</span></> : null}
                  {confidence !== null ? <> · {Math.round(confidence * 100)}%</> : null}
                  {fallbackUsed ? <> · fallback</> : null}
                  {toolCalls ? <> · {toolCalls} tools</> : null}
                  {repairAttempts ? <> · {repairAttempts} repairs</> : null}
                </p>
              )}
              {displayDocs.length > 0 && (
                <p className="mt-1 truncate font-mono text-muted-foreground" title={displayDocs.join(", ")}>
                  {displayDocs.join(", ")}
                </p>
              )}
              {manualReason && (
                <p className="mt-1 truncate text-amber-600" title={manualReason}>
                  {manualReason}
                </p>
              )}
              {source.last_ingested_commit_sha && (
                <p className="mt-2 font-mono text-muted-foreground">
                  commit {source.last_ingested_commit_sha.slice(0, 12)}
                </p>
              )}
              {source.auto_reingest_error && (
                <p className="mt-2 text-destructive">{source.auto_reingest_error}</p>
              )}
                  </>
                );
              })()}
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
            {source.source_type === "git" && !isAzureDevOpsSource && (
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
              onClick={() => setConfirmDeleteOpen(true)}
            >
              <Trash2 className="h-3.5 w-3.5" /> Delete source
            </Button>
            <ConfirmDialog
              open={confirmDeleteOpen}
              title="Delete source"
              message={`Delete source '${source.id}'? This removes its scan data and generated docs.`}
              confirmLabel="Delete"
              confirmVariant="danger"
              onConfirm={() => {
                setConfirmDeleteOpen(false);
                runAction("deleteProject", onDeleteProject);
              }}
              onCancel={() => setConfirmDeleteOpen(false)}
            />
          </div>
          {source.source_type === "git" && isAzureDevOpsSource && (
            <p className="text-xs text-muted-foreground">
              Credentials come from the Azure DevOps organization connection above.
              Rotate the token there and every project from that organization picks it up.
            </p>
          )}
          {source.source_type === "git" && !isAzureDevOpsSource && (
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
          <ProjectConnectorBindings projectId={source.id} />
          {(row?.error || actionError) && (
            <p className="text-xs text-destructive">{row?.error ?? actionError}</p>
          )}
          {(active || (row?.logs.length ?? 0) > 0 || stage === "failed") && (
            <OperationProgress
              stage={stage}
              progress={row?.progress ?? STAGE_PROGRESS[stage]}
              logs={row?.logs ?? []}
              logsOpen={row?.logsOpen ?? false}
              onToggleLogs={onToggleLogs}
            />
          )}
        </CardBody> : null
      ) : (
        <CardBody className="space-y-3 px-1">
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
              <Github className="h-4 w-4" /> Git URL
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
                    placeholder="https://github.com/org/repo or https://dev.azure.com/org/project/_git/repo"
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
                  placeholder="Personal access token (GitHub or Azure DevOps)"
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
            <OperationProgress
              stage={stage}
              progress={row?.progress ?? STAGE_PROGRESS[stage]}
              logs={row?.logs ?? []}
              logsOpen={row?.logsOpen ?? false}
              onToggleLogs={onToggleLogs}
            />
          )}
        </CardBody>
      )}
    </Card>
  );
}

function OperationProgress({
  stage,
  progress,
  logs,
  logsOpen,
  onToggleLogs,
}: {
  stage: RowStage;
  progress: number;
  logs: string[];
  logsOpen: boolean;
  onToggleLogs: () => void;
}) {
  const active = stage !== "pending" && stage !== "complete" && stage !== "failed";
  const pct = Math.max(0, Math.min(100, Math.round(progress)));
  const latestLog = logs.at(-1);

  return (
    <div className="rounded-md border border-border bg-muted/20 p-3">
      <div className="flex items-center justify-between gap-3">
        <div className="flex min-w-0 items-center gap-2 text-sm">
          {active ? (
            <Loader2 className="h-4 w-4 shrink-0 animate-spin text-primary" />
          ) : stage === "failed" ? (
            <AlertCircle className="h-4 w-4 shrink-0 text-destructive" />
          ) : (
            <Check className="h-4 w-4 shrink-0 text-emerald-600" />
          )}
          <span className="font-medium">{STAGE_LABELS[stage]}</span>
        </div>
        <span className="shrink-0 text-xs tabular-nums text-muted-foreground">{pct}%</span>
      </div>

      <div
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={pct}
        className="mt-2 h-1.5 w-full rounded-full bg-background"
      >
        <div
          className={cn(
            "h-full rounded-full transition-all",
            stage === "failed" ? "bg-destructive" : "bg-primary",
          )}
          style={{ width: `${pct}%` }}
        />
      </div>

      <div className="mt-2 flex items-center justify-between gap-2">
        <p className="min-w-0 truncate text-xs text-muted-foreground">
          {latestLog ?? "Waiting for log output..."}
        </p>
        <button
          type="button"
          onClick={onToggleLogs}
          className="inline-flex shrink-0 items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
        >
          {logsOpen ? (
            <ChevronDown className="h-3.5 w-3.5" />
          ) : (
            <ChevronRight className="h-3.5 w-3.5" />
          )}
          {logs.length} log line{logs.length === 1 ? "" : "s"}
        </button>
      </div>

      {logsOpen && (
        <pre className="mt-2 max-h-48 overflow-auto rounded bg-background p-2 text-[11px] leading-snug text-muted-foreground">
          {logs.join("\n") || "Waiting for log output..."}
        </pre>
      )}
    </div>
  );
}

function StatusBadge({
  stage,
  uploaded,
}: {
  stage: RowStage;
  uploaded: boolean;
}) {
  if (stage === "failed") {
    return (
      <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-destructive/10 text-destructive">
        <AlertCircle className="h-4 w-4" aria-hidden />
      </span>
    );
  }
  if (stage !== "pending" && stage !== "complete") {
    return (
      <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-primary/10 text-primary">
        <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
      </span>
    );
  }
  if (uploaded || stage === "complete") {
    return (
      <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-emerald-500/10 text-emerald-600">
        <Check className="h-4 w-4" strokeWidth={3} aria-hidden />
      </span>
    );
  }
  return (
    <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-muted text-muted-foreground">
      <Database className="h-4 w-4" aria-hidden />
    </span>
  );
}
