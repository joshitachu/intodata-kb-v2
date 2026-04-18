"use client";

import { useEffect, useMemo, useState } from "react";
import {
  AlertCircle,
  Check,
  CheckCircle2,
  Loader2,
  Server,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { useSetupStatusPolling } from "@/hooks/useOnboarding";
import { useOnboardingConfig } from "@/hooks/useOnboarding";

type Phase = "pending" | "processing" | "completed" | "failed" | "skipped";

interface Props {
  initialStatus: Phase;
  workspaceName: string | null | undefined;
  selectedPlatformIds: string[];
  selectedToolIds: string[];
  onDone: () => void;
}

const STAGES = [
  { id: "workspace", label: "Creating your workspace", duration: 800 },
  { id: "knowledge_base", label: "Configuring knowledge base", duration: 1400 },
  { id: "integrations", label: "Setting up integrations", duration: 1400 },
  { id: "environment", label: "Preparing your environment", duration: 1200 },
] as const;

export function ProvisioningStep({
  initialStatus,
  workspaceName,
  selectedPlatformIds,
  selectedToolIds,
  onDone,
}: Props) {
  const { data: config } = useOnboardingConfig();

  // "Real" backend setup — only polled when a source is being ingested.
  // Cosmetic animation runs in parallel; the step completes once both are
  // done (the animation guarantees a minimum perceived effort).
  const pollingEnabled =
    initialStatus === "processing" || initialStatus === "pending";
  const { data: poll } = useSetupStatusPolling(pollingEnabled);
  const backendStatus: Phase =
    (poll?.setup_status as Phase | undefined) ?? initialStatus;

  const [stageIdx, setStageIdx] = useState(0);
  const [animationDone, setAnimationDone] = useState(false);

  // Drive the staged animation
  useEffect(() => {
    if (stageIdx >= STAGES.length) {
      setAnimationDone(true);
      return;
    }
    const t = setTimeout(
      () => setStageIdx((i) => i + 1),
      STAGES[stageIdx].duration,
    );
    return () => clearTimeout(t);
  }, [stageIdx]);

  // Resolve the final phase once both animation and backend are done
  const phase: Phase = useMemo(() => {
    if (backendStatus === "failed") return "failed";
    if (!animationDone) return "processing";
    if (backendStatus === "completed" || backendStatus === "skipped") {
      return backendStatus;
    }
    return "processing"; // still waiting on backend after animation
  }, [animationDone, backendStatus]);

  // Reveal "Continue" after a short pause once everything is ready
  const [ready, setReady] = useState(false);
  useEffect(() => {
    if (phase === "completed" || phase === "skipped") {
      const t = setTimeout(() => setReady(true), 600);
      return () => clearTimeout(t);
    }
  }, [phase]);

  // Map selected IDs → labels for the summary card
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

  return (
    <div className="flex flex-col items-center w-full max-w-xl mx-auto px-6 py-10">
      <div className="w-14 h-14 rounded-full bg-primary/10 flex items-center justify-center mb-6">
        {phase === "failed" ? (
          <AlertCircle className="h-7 w-7 text-red-500" />
        ) : phase === "completed" || phase === "skipped" ? (
          <CheckCircle2 className="h-7 w-7 text-green-500" />
        ) : (
          <Server className="h-7 w-7 text-primary" />
        )}
      </div>

      <h2 className="text-2xl font-semibold mb-1 text-center">
        {phase === "failed"
          ? "Setup failed"
          : phase === "completed" || phase === "skipped"
            ? `${workspaceName || "Your workspace"} is ready`
            : "Provisioning your workspace"}
      </h2>
      <p className="text-sm text-muted-foreground mb-8 text-center max-w-md">
        {phase === "failed"
          ? (poll?.setup_error ??
            "Something went wrong while ingesting your source. You can still continue and add sources later.")
          : phase === "completed" || phase === "skipped"
            ? "Your environment is live. We'll walk you through it in a moment."
            : "Hang tight — we're spinning up your environment. This usually takes under a minute."}
      </p>

      {/* Staged progress list (Azure-style) */}
      <div className="w-full border border-border rounded-lg bg-card divide-y divide-border mb-6">
        {STAGES.map((stage, i) => {
          const state =
            i < stageIdx ? "done" : i === stageIdx && !animationDone ? "active" : "pending";
          return (
            <div
              key={stage.id}
              className="flex items-center gap-3 px-4 py-3 text-sm"
            >
              <div className="shrink-0 w-5 h-5 flex items-center justify-center">
                {state === "done" ? (
                  <Check className="h-4 w-4 text-green-500" />
                ) : state === "active" ? (
                  <Loader2 className="h-4 w-4 text-primary animate-spin" />
                ) : (
                  <div className="h-2 w-2 rounded-full bg-muted" />
                )}
              </div>
              <span
                className={cn(
                  state === "done" && "text-foreground",
                  state === "active" && "text-foreground font-medium",
                  state === "pending" && "text-muted-foreground",
                )}
              >
                {stage.label}
                {state === "active" && "…"}
              </span>
            </div>
          );
        })}
        {/* Real ingest status (only if uploading a source) */}
        {pollingEnabled && (
          <div className="flex items-center gap-3 px-4 py-3 text-sm">
            <div className="shrink-0 w-5 h-5 flex items-center justify-center">
              {backendStatus === "completed" ? (
                <Check className="h-4 w-4 text-green-500" />
              ) : backendStatus === "failed" ? (
                <AlertCircle className="h-4 w-4 text-red-500" />
              ) : (
                <Loader2 className="h-4 w-4 text-primary animate-spin" />
              )}
            </div>
            <span
              className={cn(
                backendStatus === "completed" && "text-foreground",
                backendStatus === "failed" && "text-red-600",
                backendStatus !== "completed" &&
                  backendStatus !== "failed" &&
                  "text-foreground font-medium",
              )}
            >
              Ingesting your source
              {backendStatus !== "completed" && backendStatus !== "failed" && "…"}
            </span>
          </div>
        )}
      </div>

      {/* Summary card — visible throughout so users see what's being set up */}
      <div className="w-full border border-border rounded-lg bg-muted/30 p-4 mb-8">
        <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground mb-3">
          Your configuration
        </h3>
        <dl className="space-y-2 text-sm">
          <div className="flex gap-3">
            <dt className="text-muted-foreground w-24 shrink-0">Workspace</dt>
            <dd className="font-medium">{workspaceName || "—"}</dd>
          </div>
          <div className="flex gap-3">
            <dt className="text-muted-foreground w-24 shrink-0">Platforms</dt>
            <dd className="font-medium">
              {platformLabels.length > 0 ? platformLabels.join(", ") : "None selected"}
            </dd>
          </div>
          <div className="flex gap-3">
            <dt className="text-muted-foreground w-24 shrink-0">Tools</dt>
            <dd className="font-medium">
              {toolLabels.length > 0 ? toolLabels.join(", ") : "None selected"}
            </dd>
          </div>
        </dl>
      </div>

      {(ready || phase === "failed") && (
        <button
          onClick={onDone}
          className="px-6 py-2.5 bg-primary text-primary-foreground rounded-md text-sm font-medium hover:bg-primary/90 transition-colors"
        >
          {phase === "failed" ? "Continue anyway" : "Take the tour"}
        </button>
      )}
    </div>
  );
}
