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
import { addGithubSource, uploadZip } from "@/lib/mcpClient";
import type { GitImportDraft } from "@/contexts/OnboardingContext";

type Phase = "pending" | "processing" | "completed" | "failed" | "skipped";

/**
 * Onboarding imports and stops.
 *
 * The wizard used to upload, scan and then dispatch a documentation run, which
 * spent hours of model time on a choice the user was never shown -- including
 * which pack to write with. Scanning and generating are now explicit actions on
 * the project's row in Settings > Sources.
 */
type ZipStageId = "queued" | "uploading" | "complete" | "failed";

interface ZipState {
  platformId: string;
  file: File;
  projectId: string;
  stage: ZipStageId;
  progress: number;
  error: string | null;
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
  queued: "Waiting",
  uploading: "Uploading",
  complete: "Imported, ready to scan",
  failed: "Failed",
};

const ZIP_STAGE_PROGRESS: Record<ZipStageId, number> = {
  queued: 0,
  uploading: 0,
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

export function ProvisioningStep({
  initialStatus,
  workspaceName,
  selectedPlatformIds,
  selectedToolIds,
  onDone,
}: Props) {
  const { zipFiles, gitImport } = useOnboardingContext();
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

  // A queued Git import is real work. It used to fall through to SkipProcessing,
  // whose stages are cosmetic timers, so the wizard showed "Configuring
  // knowledge base" and reported success without registering anything.
  if (gitImport) {
    return (
      <GitImportProcessing
        workspaceName={workspaceName}
        draft={gitImport}
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

// --- real Git import ------------------------------------------------------

/** Derive a project id and display name from the repository address. */
function projectIdentityFromRepoUrl(repoUrl: string, workspaceSlug: string): {
  projectId: string;
  displayName: string;
} {
  // Tolerates a trailing slash, a `.git` suffix and Azure DevOps `_git/<name>`.
  const trimmed = repoUrl.trim().replace(/\/+$/, "").replace(/\.git$/i, "");
  const name = trimmed.split("/").filter(Boolean).pop() ?? "";
  return {
    projectId: slugify(name || workspaceSlug),
    displayName: name || workspaceSlug,
  };
}

function GitImportProcessing({
  workspaceName,
  draft,
  onDone,
}: {
  workspaceName: string | null | undefined;
  draft: GitImportDraft;
  onDone: () => void;
}) {
  const workspaceSlug = slugify(workspaceName || "workspace");
  const [state, setState] = useState<"importing" | "done" | "failed">("importing");
  const [error, setError] = useState<string | null>(null);
  const [projectId, setProjectId] = useState<string | null>(null);
  const startedRef = useRef(false);

  useEffect(() => {
    // Strict Mode mounts effects twice in development; a second POST would try to
    // register the same project again.
    if (startedRef.current) return;
    startedRef.current = true;

    const identity = projectIdentityFromRepoUrl(draft.repoUrl, workspaceSlug);
    setProjectId(identity.projectId);
    addGithubSource({
      repoUrl: draft.repoUrl,
      projectId: identity.projectId,
      platform: draft.platformId,
      displayName: identity.displayName,
      githubToken: draft.token,
      installationId: draft.installationId,
    })
      .then(() => setState("done"))
      .catch((err: unknown) => {
        setError(err instanceof Error ? err.message : String(err));
        setState("failed");
      });
  }, [draft, workspaceSlug]);

  return (
    <div className="w-full max-w-xl mx-auto px-6">
      <div className="flex flex-col items-center text-center mb-8">
        <div className="relative mb-6">
          {state === "importing" && (
            <span className="absolute inset-0 rounded-full bg-primary/15 blur-2xl scale-110 animate-pulse" aria-hidden />
          )}
          <div
            className={cn(
              "relative flex h-16 w-16 items-center justify-center rounded-2xl shadow-elevation-md",
              state === "failed"
                ? "bg-destructive/10 text-destructive"
                : "bg-primary text-primary-foreground",
            )}
          >
            {state === "failed" ? (
              <AlertCircle className="h-8 w-8" aria-hidden />
            ) : (
              <Server className="h-8 w-8" aria-hidden />
            )}
          </div>
        </div>
        <h2 className="text-2xl font-semibold tracking-tight text-foreground mb-2">
          {state === "importing"
            ? "Importing your repository"
            : state === "done"
              ? "Repository imported"
              : "We could not import that repository"}
        </h2>
        <p className="text-sm text-muted-foreground max-w-md">
          {state === "importing"
            ? "Fetching a copy of the code. This usually takes well under a minute."
            : state === "done"
              ? `Saved as "${projectId}". Nothing has been scanned yet — open Settings > Sources and choose "Scan now" when you are ready.`
              : (error ??
                "Check the address and, for a private repository, the access token. You can also add it later from Settings > Sources.")}
        </p>
      </div>

      <Card className="mb-8">
        <CardBody className="space-y-3">
          <div className="flex items-center gap-3">
            <StageIcon stage={state === "importing" ? "uploading" : state === "done" ? "complete" : "failed"} />
            <div className="min-w-0 flex-1">
              <p className="truncate text-sm font-semibold text-foreground">{draft.repoUrl}</p>
              <p className="text-xs text-muted-foreground">
                {state === "importing"
                  ? "Cloning"
                  : state === "done"
                    ? "Imported, ready to scan"
                    : "Failed"}
              </p>
            </div>
          </div>
        </CardBody>
      </Card>

      {state !== "importing" && (
        <div className="flex justify-center">
          <Button onClick={onDone}>Continue</Button>
        </div>
      )}
    </div>
  );
}

// --- real ZIP import ------------------------------------------------------

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
    })),
  );
  const [currentIdx, setCurrentIdx] = useState(0);

  const activeIdxRef = useRef(currentIdx);
  activeIdxRef.current = currentIdx;
  const doneRef = useRef(false);

  // Upload the current ZIP when it enters the "queued" slot. Sequential on
  // purpose: several concurrent multi-hundred-megabyte uploads from a browser
  // is not kinder to anyone.
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

    let cancelled = false;

    async function run() {
      try {
        updateItem(idx, { stage: "uploading", progress: 0 });
        // `upload-zip` unpacks and registers the project. It does not scan, and
        // nothing here asks it to: the scan is the user's call, from
        // Settings > Sources.
        await uploadZip({
          file: item.file,
          projectId: item.projectId,
          platform: item.platformId,
          onProgress: (pct) => {
            if (!cancelled) updateItem(idx, { progress: pct });
          },
        });
        if (cancelled) return;
        updateItem(idx, { stage: "complete", progress: 100 });
      } catch (err) {
        if (cancelled) return;
        updateItem(idx, {
          stage: "failed",
          progress: ZIP_STAGE_PROGRESS.failed,
          error: err instanceof Error ? err.message : String(err),
        });
      } finally {
        // Advance either way: one bad archive must not strand the rest.
        if (!cancelled) setCurrentIdx((i) => i + 1);
      }
    }

    run();

    return () => {
      cancelled = true;
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

  function retry(idx: number) {
    updateItem(idx, { stage: "queued", progress: 0, error: null });
    if (idx < activeIdxRef.current) setCurrentIdx(idx);
  }

  const allSettled = items.every((i) => i.stage === "complete" || i.stage === "failed");
  const importedCount = items.filter((i) => i.stage === "complete").length;

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
          {allSettled ? "Your sources are imported" : "Importing your sources"}
        </h2>
        <p className="text-sm text-muted-foreground max-w-md">
          {allSettled
            ? "Nothing has been scanned yet. Open Settings > Sources and choose “Scan now” on a source when you are ready — then you pick what documentation to write."
            : "Each archive is uploaded and unpacked here. Scanning happens later, when you ask for it."}
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
                    {item.stage === "uploading" && ` — ${Math.round(item.progress)}%`}
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

              {item.error && <p className="text-xs text-destructive">{item.error}</p>}
            </CardBody>
          </Card>
        ))}
      </div>

      {allSettled && (
        <div className="flex flex-col items-center gap-2">
          {importedCount === 0 && (
            <p className="text-sm text-destructive">
              None of the archives could be imported. You can try again from
              Settings &gt; Sources.
            </p>
          )}
          <Button onClick={onDone}>Continue</Button>
        </div>
      )}
    </div>
  );
}

function StageIcon({ stage }: { stage: ZipStageId }) {
  if (stage === "complete") {
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
