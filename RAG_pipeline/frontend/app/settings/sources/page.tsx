"use client";

import { useMemo, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
  AlertCircle,
  Check,
  ChevronDown,
  ChevronRight,
  Database,
  FileArchive,
  Loader2,
  RotateCw,
  UploadCloud,
  X,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { useOnboardingStatus, useOnboardingConfig } from "@/hooks/useOnboarding";
import { useDocsProjects } from "@/hooks/useDocsProjects";
import {
  generateDocs,
  scanProject,
  subscribeToLogs,
  uploadZip,
  type LogEvent,
} from "@/lib/mcpClient";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";

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
  stage: RowStage;
  progress: number;
  error: string | null;
  logs: string[];
  logsOpen: boolean;
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

export default function SourcesSettingsPage() {
  const { data: status, isLoading: statusLoading } = useOnboardingStatus();
  const { data: config } = useOnboardingConfig();
  const { data: docsProjects, isLoading: projectsLoading } = useDocsProjects();
  const qc = useQueryClient();

  const platformLabel = useMemo(() => {
    const byId = new Map(
      (config?.platforms ?? []).map((p) => [p.id, p.label] as const),
    );
    return (id: string) => byId.get(id) ?? id;
  }, [config]);

  const workspaceSlug = slugify(status?.workspace_name || "workspace");
  const selected = status?.selected_platforms ?? [];

  // Map platform id (normalized lower-case) → uploaded project (if any)
  const uploadedByPlatform = useMemo(() => {
    const map = new Map<string, { displayName: string; docCount: number }>();
    for (const p of docsProjects?.projects ?? []) {
      map.set(p.platform.toLowerCase(), {
        displayName: p.display_name,
        docCount: p.doc_count,
      });
    }
    return map;
  }, [docsProjects]);

  // Per-platform interactive state for the pending rows
  const [rows, setRows] = useState<Record<string, RowState>>({});

  function patchRow(platformId: string, patch: Partial<RowState>) {
    setRows((prev) => ({
      ...prev,
      [platformId]: {
        file: prev[platformId]?.file ?? null,
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

  async function runUpload(platformId: string, file: File) {
    const projectId = `${workspaceSlug}-${platformId}`;
    const sub: { stop?: () => void } = {};

    try {
      patchRow(platformId, { stage: "uploading", progress: 0, error: null });
      await uploadZip({
        file,
        projectId,
        platform: platformId,
        onProgress: (pct) => patchRow(platformId, { progress: pct }),
      });

      patchRow(platformId, { stage: "scanning", progress: 100 });
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
            // SSE errors are expected on server restart; don't fail the upload
          },
        );
      });

      qc.invalidateQueries({ queryKey: ["docs-projects"] });
    } catch (err) {
      patchRow(platformId, {
        stage: "failed",
        error: err instanceof Error ? err.message : String(err),
      });
    } finally {
      sub.stop?.();
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
          Upload a ZIP for each platform you declared during onboarding. You
          can add sources at any time &mdash; scans run sequentially in the
          background.
        </p>
      </div>

      {selected.length === 0 ? (
        <Card>
          <CardBody>
            <p className="text-sm text-muted-foreground">
              You haven&apos;t declared any platforms yet. Run through the
              onboarding wizard first, or add a platform from the admin area.
            </p>
          </CardBody>
        </Card>
      ) : (
        <div className="space-y-3">
          {selected.map((platformId) => {
            const uploaded = uploadedByPlatform.get(platformId.toLowerCase());
            return (
              <SourceRow
                key={platformId}
                platformId={platformId}
                label={platformLabel(platformId)}
                uploaded={uploaded}
                row={rows[platformId]}
                onFilePicked={(file) =>
                  patchRow(platformId, { file, error: null, stage: "pending" })
                }
                onClear={() =>
                  patchRow(platformId, { file: null, error: null, stage: "pending" })
                }
                onUpload={(file) => runUpload(platformId, file)}
                onToggleLogs={() =>
                  patchRow(platformId, {
                    logsOpen: !(rows[platformId]?.logsOpen ?? false),
                  })
                }
              />
            );
          })}
        </div>
      )}
    </div>
  );
}

// ─── Single platform row ───────────────────────────────────────────

interface SourceRowProps {
  platformId: string;
  label: string;
  uploaded?: { displayName: string; docCount: number };
  row: RowState | undefined;
  onFilePicked: (file: File) => void;
  onClear: () => void;
  onUpload: (file: File) => void;
  onToggleLogs: () => void;
}

function SourceRow({
  platformId,
  label,
  uploaded,
  row,
  onFilePicked,
  onClear,
  onUpload,
  onToggleLogs,
}: SourceRowProps) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);

  const stage: RowStage = row?.stage ?? "pending";
  const active = stage !== "pending" && stage !== "complete" && stage !== "failed";
  const isComplete = Boolean(uploaded) || stage === "complete";

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between gap-3">
          <div className="flex items-center gap-3 min-w-0">
            <StatusBadge stage={stage} uploaded={Boolean(uploaded)} />
            <div className="min-w-0">
              <p className="font-semibold truncate">{label}</p>
              {uploaded ? (
                <p className="text-xs text-muted-foreground truncate">
                  {uploaded.displayName} &middot; {uploaded.docCount} docs
                </p>
              ) : (
                <p className="text-xs text-muted-foreground">
                  {stage === "pending" ? "No source uploaded yet" : STAGE_LABELS[stage]}
                </p>
              )}
            </div>
          </div>
        </div>
      </CardHeader>

      {!isComplete && (
        <CardBody className="space-y-3">
          {stage === "pending" || stage === "failed" ? (
            <>
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
