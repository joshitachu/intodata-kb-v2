"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import {
  AlertCircle,
  Check,
  ChevronDown,
  ChevronRight,
  Loader2,
  RotateCw,
  Server,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { useSetupStatusPolling } from "@/hooks/useOnboarding";
import { useOnboardingConfig } from "@/hooks/useOnboarding";
import { useOnboardingContext } from "@/contexts/OnboardingContext";
import { Button } from "@/components/ui/button";
import { Card, CardBody } from "@/components/ui/card";
import {
  generateDocsWhenScanned,
  scanProject,
  subscribeToLogs,
  uploadZip,
  type LogEvent,
} from "@/lib/mcpClient";

type Phase = "pending" | "processing" | "completed" | "failed" | "skipped";

type ZipStageId =
  | "queued"
  | "uploading"
  | "scanning"
  | "generating"
  | "background"
  | "pushing"
  | "complete"
  | "failed";

interface ZipState {
  platformId: string;
  file: File;
  projectId: string;
  stage: ZipStageId;
  progress: number;
  error: string | null;
  logs: string[];
}

interface Props {
  initialStatus: Phase;
  workspaceName: string | null | undefined;
  selectedPlatformIds: string[];
  selectedToolIds: string[];
  onDone: () => void;
}

// Cosmetic-only stages for the "no ZIPs to upload" branch (source skipped or GitHub URL)
const SKIP_STAGES = [
  { id: "workspace", label: "Creating your workspace", duration: 800 },
  { id: "knowledge_base", label: "Configuring knowledge base", duration: 1400 },
  { id: "integrations", label: "Setting up integrations", duration: 1400 },
  { id: "environment", label: "Preparing your environment", duration: 1200 },
] as const;

const ZIP_STAGE_LABELS: Record<ZipStageId, string> = {
  queued: "Queued",
  uploading: "Uploading",
  scanning: "Scanning",
  generating: "Generating docs",
  background: "Generating in background",
  pushing: "Pushing to knowledge base",
  complete: "Complete",
  failed: "Failed",
};

const ZIP_STAGE_PROGRESS: Record<ZipStageId, number> = {
  queued: 0,
  uploading: 0,
  scanning: 35,
  generating: 75,
  background: 100,
  pushing: 90,
  complete: 100,
  failed: 100,
};

function slugify(input: string): string {
  return input
    .toLowerCase()
    .replace(/[^a-z0-9_-]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 80) || "project";
}

/**
 * Classify an MCP log event into the next stage for a given ZIP.
 * Returns `null` when the event doesn't change the stage.
 */
function stageFromEvent(ev: LogEvent): ZipStageId | null {
  const text = `${ev.type ?? ""} ${ev.data ?? ""} ${ev.message ?? ""}`.toLowerCase();
  if (ev.type === "error" || ev.level === "error") return "failed";
  if (ev.type === "scan_complete" || text.includes("scan complete") || text.includes("scan completed")) return "generating";
  if (ev.level === "done" || ev.type === "done") return "complete";
  if (ev.type === "generate_complete" || text.includes("generate complete") || text.includes("generation complete")) return "pushing";
  if (ev.type === "push_complete" || text.includes("push complete") || text.includes("pushed to rag")) return "complete";
  return null;
}

function progressFromEvent(ev: LogEvent, stage: ZipStageId): number {
  const text = `${ev.type ?? ""} ${ev.data ?? ""} ${ev.message ?? ""}`.toLowerCase();
  if (ev.level === "done") return 100;
  if (text.includes("pre-processing")) return 45;
  if (text.includes("pre-processed")) return 55;
  if (text.includes("resolved doc generation routing")) return 65;
  if (text.includes("rag") || text.includes("push")) return 90;
  return ZIP_STAGE_PROGRESS[stage];
}

function formatLogLine(ev: LogEvent): string {
  const level = ev.level ?? "info";
  const message = ev.message ?? ev.data ?? "";
  const type = ev.type ? `${ev.type}: ` : "";
  return `[${level}] ${type}${message}`;
}

export function ProvisioningStep({
  initialStatus,
  workspaceName,
  selectedPlatformIds,
  selectedToolIds,
  onDone,
}: Props) {
  const { zipFiles } = useOnboardingContext();
  const hasZips = Object.keys(zipFiles).length > 0;

  if (hasZips) {
    return (
      <RealProcessing
        workspaceName={workspaceName}
        zipFiles={zipFiles}
        onDone={onDone}
      />
    );
  }

  return (
    <SkipProcessing
      initialStatus={initialStatus}
      workspaceName={workspaceName}
      selectedPlatformIds={selectedPlatformIds}
      selectedToolIds={selectedToolIds}
      onDone={onDone}
    />
  );
}

// --- real SSE-driven flow --------------------------------------------------

function RealProcessing({
  workspaceName,
  zipFiles,
  onDone,
}: {
  workspaceName: string | null | undefined;
  zipFiles: Record<string, File>;
  onDone: () => void;
}) {
  const workspaceSlug = slugify(workspaceName || "workspace");
  const entries = useMemo(() => Object.entries(zipFiles), [zipFiles]);

  const [items, setItems] = useState<ZipState[]>(() =>
    entries.map(([platformId, file]) => ({
      platformId,
      file,
      projectId: `${workspaceSlug}-${platformId}`,
      stage: "queued" as ZipStageId,
      progress: 0,
      error: null,
      logs: [],
    })),
  );
  const [currentIdx, setCurrentIdx] = useState(0);
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});

  const activeIdxRef = useRef(currentIdx);
  activeIdxRef.current = currentIdx;
  const doneRef = useRef(false);

  // Process the current ZIP when it enters the "queued" slot
  useEffect(() => {
    if (currentIdx >= items.length) {
      if (!doneRef.current) {
        doneRef.current = true;
        const t = setTimeout(onDone, 700);
        return () => clearTimeout(t);
      }
      return;
    }
    const idx = currentIdx;
    const item = items[idx];
    if (!item || item.stage !== "queued") return;

    let unsubscribe: (() => void) | null = null;

    async function run() {
      try {
        // 1. Upload
        updateItem(idx, { stage: "uploading", progress: 0 });
        await uploadZip({
          file: item.file,
          projectId: item.projectId,
          platform: item.platformId,
          onProgress: (pct) => updateItem(idx, { progress: pct }),
        });

        // 2. Subscribe to logs, trigger scan, then trigger generation once scan completes.
        // Onboarding advances after generation is accepted; the long generation/RAG work
        // continues in the MCP server background.
        await new Promise<void>((resolve, reject) => {
          let generateTriggered = false;
          let settled = false;
          unsubscribe = subscribeToLogs(
            item.projectId,
            (ev) => {
              appendLog(idx, ev);
              const next = stageFromEvent(ev);
              if (!next) {
                updateItem(idx, {
                  progress: progressFromEvent(ev, generateTriggered ? "generating" : "scanning"),
                });
                return;
              }
              if (next === "generating" && !generateTriggered) {
                generateTriggered = true;
                updateItem(idx, {
                  stage: "generating",
                  progress: progressFromEvent(ev, "generating"),
                });
                // Same guard as the Sources pipeline: the log marker is a hint,
                // the project row is the authority on scan_result.json existing.
                generateDocsWhenScanned(item.projectId)
                  .then(() => {
                    if (settled) return;
                    settled = true;
                    appendLog(
                      idx,
                      {
                        level: "info",
                        message:
                          "Documentation generation started. You can continue while it runs in the background.",
                        raw: null,
                      },
                    );
                    updateItem(idx, {
                      stage: "background",
                      progress: ZIP_STAGE_PROGRESS.background,
                    });
                    resolve();
                  })
                  .catch((err) => {
                    if (settled) return;
                    settled = true;
                    updateItem(idx, {
                      stage: "failed",
                      progress: ZIP_STAGE_PROGRESS.failed,
                      error: err instanceof Error ? err.message : String(err),
                    });
                    reject(err);
                  });
              } else if (next === "pushing") {
                updateItem(idx, { stage: "pushing", progress: progressFromEvent(ev, "pushing") });
              } else if (next === "complete") {
                if (settled) return;
                settled = true;
                updateItem(idx, { stage: "complete", progress: 100 });
                resolve();
              } else if (next === "failed") {
                if (settled) return;
                settled = true;
                updateItem(idx, {
                  stage: "failed",
                  progress: ZIP_STAGE_PROGRESS.failed,
                  error: ev.message ?? ev.data ?? "Pipeline reported an error",
                });
                reject(new Error(ev.message ?? ev.data ?? "Pipeline error"));
              }
            },
            () => {
              // SSE errors are expected on server restart — we only log, not fail
            },
          );

          // upload-zip only unpacks + registers — scan must be triggered explicitly
          updateItem(idx, { stage: "scanning", progress: ZIP_STAGE_PROGRESS.scanning });
          scanProject(item.projectId).catch((err) => {
            if (settled) return;
            settled = true;
            updateItem(idx, {
              stage: "failed",
              progress: ZIP_STAGE_PROGRESS.failed,
              error: err instanceof Error ? err.message : String(err),
            });
            reject(err);
          });
        });

        setCurrentIdx((i) => i + 1);
      } catch (err) {
        updateItem(idx, {
          stage: "failed",
          error: err instanceof Error ? err.message : String(err),
        });
      } finally {
        if (unsubscribe) unsubscribe();
      }
    }

    run();

    return () => {
      if (unsubscribe) unsubscribe();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentIdx]);

  function updateItem(idx: number, patch: Partial<ZipState>) {
    setItems((prev) => {
      const next = [...prev];
      if (next[idx]) next[idx] = { ...next[idx], ...patch };
      return next;
    });
  }

  function appendLog(idx: number, ev: LogEvent) {
    setItems((prev) => {
      const next = [...prev];
      const cur = next[idx];
      if (!cur) return prev;
      const line = formatLogLine(ev);
      next[idx] = { ...cur, logs: [...cur.logs.slice(-199), line] };
      return next;
    });
  }

  function retry(idx: number) {
    updateItem(idx, { stage: "queued", progress: 0, error: null, logs: [] });
    if (idx < activeIdxRef.current) setCurrentIdx(idx);
  }

  const allReadyToContinue = items.every(
    (i) => i.stage === "background" || i.stage === "complete" || i.stage === "failed",
  );

  return (
    <div className="w-full max-w-2xl mx-auto px-6">
      <div className="flex flex-col items-center text-center mb-8">
        <div className="relative mb-6">
          <span className="absolute inset-0 rounded-full bg-primary/15 blur-2xl scale-110 animate-pulse" aria-hidden />
          <div className="relative flex h-16 w-16 items-center justify-center rounded-2xl shadow-elevation-md bg-primary text-primary-foreground">
            <Server className="h-8 w-8" aria-hidden />
          </div>
        </div>
        <h2 className="text-2xl font-semibold tracking-tight text-foreground mb-2">
          Processing your sources
        </h2>
        <p className="text-sm text-muted-foreground max-w-md">
          Each ZIP uploads and scans here. Documentation generation starts in
          the background so you can continue without waiting for the full run.
        </p>
      </div>

      <div className="space-y-3 mb-6">
        {items.map((item, idx) => (
          <Card key={item.platformId}>
            <CardBody className="space-y-3">
              <div className="flex items-center gap-3">
                <StageIcon stage={item.stage} />
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-semibold text-foreground truncate">
                    {item.platformId} &middot; {item.file.name}
                  </p>
                  <p className="text-xs text-muted-foreground">
                    {ZIP_STAGE_LABELS[item.stage]}
                    {item.stage === "uploading" && ` — ${item.progress}%`}
                  </p>
                </div>
                {item.stage === "failed" && (
                  <Button size="sm" variant="secondary" onClick={() => retry(idx)}>
                    <RotateCw className="h-3.5 w-3.5 mr-1" /> Retry
                  </Button>
                )}
              </div>

              {item.stage !== "queued" && (
                <div className="h-1.5 w-full rounded-full bg-muted">
                  <div
                    className={cn(
                      "h-full rounded-full transition-all",
                      item.stage === "failed" ? "bg-destructive" : "bg-primary",
                    )}
                    style={{ width: `${Math.max(0, Math.min(100, item.progress))}%` }}
                  />
                </div>
              )}

              {item.stage === "background" && (
                <p className="text-xs text-muted-foreground">
                  Generation is running in the background. You can check progress later in Settings &gt; Sources.
                </p>
              )}

              {item.error && (
                <p className="text-xs text-destructive">{item.error}</p>
              )}

              <button
                type="button"
                onClick={() =>
                  setExpanded((e) => ({ ...e, [item.platformId]: !e[item.platformId] }))
                }
                className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
              >
                {expanded[item.platformId] ? (
                  <ChevronDown className="h-3.5 w-3.5" />
                ) : (
                  <ChevronRight className="h-3.5 w-3.5" />
                )}
                {item.logs.length} log line{item.logs.length === 1 ? "" : "s"}
              </button>

              {expanded[item.platformId] && (
                <pre className="max-h-48 overflow-auto rounded bg-muted/40 p-2 text-[11px] leading-snug text-muted-foreground">
                  {item.logs.join("\n") || "Waiting for log output…"}
                </pre>
              )}
            </CardBody>
          </Card>
        ))}
      </div>

      {allReadyToContinue && (
        <div className="flex justify-center">
          <Button onClick={onDone}>Continue</Button>
        </div>
      )}
    </div>
  );
}

function StageIcon({ stage }: { stage: ZipStageId }) {
  if (stage === "complete" || stage === "background") {
    return (
      <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-primary text-primary-foreground">
        <Check className="h-3.5 w-3.5" strokeWidth={3} aria-hidden />
      </span>
    );
  }
  if (stage === "failed") {
    return (
      <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-destructive/10 text-destructive">
        <AlertCircle className="h-4 w-4" aria-hidden />
      </span>
    );
  }
  if (stage === "queued") {
    return (
      <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full border border-border">
        <span className="h-1.5 w-1.5 rounded-full bg-muted-foreground" />
      </span>
    );
  }
  return (
    <span className="flex h-6 w-6 shrink-0 items-center justify-center">
      <Loader2 className="h-4 w-4 animate-spin text-primary" aria-hidden />
    </span>
  );
}

// --- fallback cosmetic flow when no ZIPs are queued -----------------------

function SkipProcessing({
  initialStatus,
  workspaceName,
  selectedPlatformIds,
  selectedToolIds,
  onDone,
}: {
  initialStatus: Phase;
  workspaceName: string | null | undefined;
  selectedPlatformIds: string[];
  selectedToolIds: string[];
  onDone: () => void;
}) {
  const { data: config } = useOnboardingConfig();
  const pollingEnabled =
    initialStatus === "processing" || initialStatus === "pending";
  const { data: poll } = useSetupStatusPolling(pollingEnabled);
  const backendStatus: Phase =
    (poll?.setup_status as Phase | undefined) ?? initialStatus;

  const [stageIdx, setStageIdx] = useState(0);
  const [animationDone, setAnimationDone] = useState(false);

  useEffect(() => {
    if (stageIdx >= SKIP_STAGES.length) {
      setAnimationDone(true);
      return;
    }
    const t = setTimeout(
      () => setStageIdx((i) => i + 1),
      SKIP_STAGES[stageIdx].duration,
    );
    return () => clearTimeout(t);
  }, [stageIdx]);

  const phase: Phase = useMemo(() => {
    if (backendStatus === "failed") return "failed";
    if (!animationDone) return "processing";
    if (backendStatus === "completed" || backendStatus === "skipped") {
      return backendStatus;
    }
    return "processing";
  }, [animationDone, backendStatus]);

  const [advanceReady, setAdvanceReady] = useState(false);
  useEffect(() => {
    if (phase === "completed" || phase === "skipped") {
      const t = setTimeout(() => setAdvanceReady(true), 500);
      return () => clearTimeout(t);
    }
  }, [phase]);

  useEffect(() => {
    if (advanceReady) onDone();
  }, [advanceReady, onDone]);

  const platformLabels = useMemo(() => {
    if (!config) return selectedPlatformIds;
    const byId = Object.fromEntries(config.platforms.map((p) => [p.id, p.label]));
    return selectedPlatformIds.map((id) => byId[id] ?? id);
  }, [config, selectedPlatformIds]);

  const toolLabels = useMemo(() => {
    if (!config) return selectedToolIds;
    const byId: Record<string, string> = {};
    for (const cat of config.tool_categories) {
      for (const t of cat.tools) byId[t.id] = t.label;
    }
    return selectedToolIds.map((id) => byId[id] ?? id);
  }, [config, selectedToolIds]);

  const isFailed = phase === "failed";

  return (
    <div className="w-full max-w-xl mx-auto px-6">
      <div className="flex flex-col items-center text-center mb-8">
        <div className="relative mb-6">
          {!isFailed && (
            <span className="absolute inset-0 rounded-full bg-primary/15 blur-2xl scale-110 animate-pulse" aria-hidden />
          )}
          <div
            className={cn(
              "relative flex h-16 w-16 items-center justify-center rounded-2xl shadow-elevation-md",
              isFailed ? "bg-destructive/10 text-destructive" : "bg-primary text-primary-foreground",
            )}
          >
            {isFailed ? <AlertCircle className="h-8 w-8" aria-hidden /> : <Server className="h-8 w-8" aria-hidden />}
          </div>
        </div>
        <h2 className="text-2xl font-semibold tracking-tight text-foreground mb-2">
          {isFailed ? "Setup couldn't finish" : "Provisioning your workspace"}
        </h2>
        <p className="text-sm text-muted-foreground max-w-md">
          {isFailed
            ? (poll?.setup_error ?? "Something went wrong during setup. You can continue and add sources later.")
            : "Hang tight — we're spinning up your environment. This usually takes under a minute."}
        </p>
      </div>

      <Card className="mb-4 overflow-hidden">
        <ul className="divide-y divide-border/60">
          {SKIP_STAGES.map((stage, i) => {
            const state = i < stageIdx ? "done" : i === stageIdx && !animationDone ? "active" : "pending";
            return (
              <li key={stage.id} className="flex items-center gap-3 px-5 py-3.5">
                <SkipStageIcon state={state} />
                <span
                  className={cn(
                    "text-sm",
                    state === "done" && "text-foreground",
                    state === "active" && "text-foreground font-medium",
                    state === "pending" && "text-muted-foreground",
                  )}
                >
                  {stage.label}
                  {state === "active" && "…"}
                </span>
              </li>
            );
          })}
        </ul>
      </Card>

      <Card className="mb-8">
        <CardBody className="space-y-3">
          <h3 className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
            Your configuration
          </h3>
          <dl className="divide-y divide-border/60 -mx-2">
            <SummaryRow label="Workspace" value={workspaceName || "—"} />
            <SummaryRow
              label="Platforms"
              value={platformLabels.length > 0 ? platformLabels.join(", ") : "None selected"}
              muted={platformLabels.length === 0}
            />
            <SummaryRow
              label="Tools"
              value={toolLabels.length > 0 ? toolLabels.join(", ") : "None selected"}
              muted={toolLabels.length === 0}
            />
          </dl>
        </CardBody>
      </Card>

      {isFailed && (
        <div className="flex justify-center">
          <Button onClick={onDone}>Continue anyway</Button>
        </div>
      )}
    </div>
  );
}

function SkipStageIcon({ state }: { state: "done" | "active" | "pending" | "failed" }) {
  if (state === "done") {
    return (
      <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-primary text-primary-foreground">
        <Check className="h-3 w-3" strokeWidth={3} aria-hidden />
      </span>
    );
  }
  if (state === "active") {
    return (
      <span className="flex h-5 w-5 shrink-0 items-center justify-center">
        <Loader2 className="h-4 w-4 animate-spin text-primary" aria-hidden />
      </span>
    );
  }
  if (state === "failed") {
    return (
      <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-destructive/10 text-destructive">
        <AlertCircle className="h-3.5 w-3.5" aria-hidden />
      </span>
    );
  }
  return (
    <span className="flex h-5 w-5 shrink-0 items-center justify-center">
      <span className="h-1.5 w-1.5 rounded-full bg-border" aria-hidden />
    </span>
  );
}

function SummaryRow({
  label,
  value,
  muted = false,
}: {
  label: string;
  value: string;
  muted?: boolean;
}) {
  return (
    <div className="flex gap-4 px-2 py-2">
      <dt className="w-24 shrink-0 text-sm text-muted-foreground">{label}</dt>
      <dd className={cn("text-sm font-medium", muted ? "text-muted-foreground" : "text-foreground")}>
        {value}
      </dd>
    </div>
  );
}
