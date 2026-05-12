"use client";

import { useMemo, useState } from "react";
import { FolderGit2, Github, FileArchive, UploadCloud, X } from "lucide-react";
import { cn } from "@/lib/utils";
import { useSaveSource } from "@/hooks/useOnboarding";
import { useOnboardingConfig } from "@/hooks/useOnboarding";
import { useOnboardingContext } from "@/contexts/OnboardingContext";
import type { SourceType } from "@/types/onboarding";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Card, CardBody, CardFooter, CardHeader } from "@/components/ui/card";
import { trackWizardEvent } from "@/lib/telemetry";

interface SaveMutation {
  mutateAsync: (arg: {
    source_type: SourceType;
    source_url?: string | null;
    source_filename?: string | null;
  }) => Promise<unknown>;
  isPending: boolean;
}

interface Props {
  initialUrl?: string | null;
  initialFilename?: string | null;
  initialType?: SourceType;
  onBack: () => void;
  onNext: () => void;
  onSkip: () => void;
  selectedPlatformIds: string[];
  overrideSave?: SaveMutation;
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

export function SourceUploadStep({
  initialUrl,
  initialType,
  onBack,
  onNext,
  onSkip,
  selectedPlatformIds,
  overrideSave,
}: Props) {
  const { zipFiles, setZipFile } = useOnboardingContext();
  const { data: config } = useOnboardingConfig();
  const [mode, setMode] = useState<"github" | "zip">(
    initialType === "zip" ? "zip" : selectedPlatformIds.length > 0 ? "zip" : "github",
  );
  const [githubUrl, setGithubUrl] = useState(initialUrl ?? "");
  const [error, setError] = useState<string | null>(null);
  const [draggingId, setDraggingId] = useState<string | null>(null);

  const defaultSave = useSaveSource();
  const save = overrideSave ?? defaultSave;

  // Resolve platform labels (fall back to raw id if config not loaded yet)
  const platformLabel = useMemo(() => {
    const byId = new Map(
      (config?.platforms ?? []).map((p) => [p.id, p.label] as const),
    );
    return (id: string) => byId.get(id) ?? id;
  }, [config]);

  const zipTargets = selectedPlatformIds.length > 0 ? selectedPlatformIds : ["generic"];

  async function handleContinue() {
    setError(null);
    if (mode === "github") {
      if (!githubUrl.trim()) {
        setError("Please enter a GitHub repository URL.");
        return;
      }
      if (!/^https?:\/\/.+/i.test(githubUrl.trim())) {
        setError("URL must start with http:// or https://");
        return;
      }
      trackWizardEvent("wizard_source_method", 4, { method: "github" });
      await save.mutateAsync({
        source_type: "github",
        source_url: githubUrl.trim(),
        source_filename: null,
      });
    } else {
      trackWizardEvent("wizard_source_method", 4, {
        method: "zip",
        count: Object.keys(zipFiles).length,
        declared: zipTargets.length,
      });
      // Record a summary of filenames in the DB so the status row reflects
      // the user's selection. Actual ZIP bytes ride in memory (zipFiles
      // context) to the ProcessingStep.
      const summary = Object.entries(zipFiles)
        .map(([pid, f]) => `${pid}:${f.name}`)
        .join("; ");
      await save.mutateAsync({
        source_type: "zip",
        source_url: null,
        source_filename: summary,
      });
    }
    onNext();
  }

  async function handleSkip() {
    trackWizardEvent("wizard_source_method", 4, { method: "skip" });
    await save.mutateAsync({
      source_type: null,
      source_url: null,
      source_filename: null,
    });
    onSkip();
  }

  function handleFile(platformId: string, file: File | null) {
    if (!file) return;
    if (!file.name.toLowerCase().endsWith(".zip")) {
      setError("Only .zip files are accepted");
      return;
    }
    setError(null);
    setZipFile(platformId, file);
  }

  return (
    <div className="w-full max-w-2xl mx-auto px-6">
      <Card>
        <CardHeader>
          <div className="flex items-center gap-3">
            <span className="flex h-10 w-10 items-center justify-center rounded-lg bg-primary/10 text-primary">
              <FolderGit2 className="h-5 w-5" aria-hidden />
            </span>
            <div>
              <h2 className="text-xl font-semibold text-foreground">
                Connect your source codebase
              </h2>
              <p className="mt-0.5 text-sm text-muted-foreground">
                Upload what you have ready now &mdash; you can add the rest
                later from <span className="font-medium">Settings &rarr; Sources</span>.
                Each ZIP is scanned and indexed sequentially.
              </p>
            </div>
          </div>
        </CardHeader>

        <CardBody>
          {/* Segmented tab */}
          <div
            role="tablist"
            aria-label="Source type"
            className="inline-flex w-full rounded-lg border border-border bg-muted/40 p-1"
          >
            <button
              role="tab"
              type="button"
              aria-selected={mode === "github"}
              onClick={() => setMode("github")}
              className={cn(
                "flex-1 inline-flex items-center justify-center gap-2 rounded-md h-8 px-3 text-sm font-medium transition-all",
                "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/30",
                mode === "github"
                  ? "bg-background text-foreground shadow-elevation-xs"
                  : "text-muted-foreground hover:text-foreground",
              )}
            >
              <Github className="h-4 w-4" />
              GitHub URL
            </button>
            <button
              role="tab"
              type="button"
              aria-selected={mode === "zip"}
              onClick={() => setMode("zip")}
              className={cn(
                "flex-1 inline-flex items-center justify-center gap-2 rounded-md h-8 px-3 text-sm font-medium transition-all",
                "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/30",
                mode === "zip"
                  ? "bg-background text-foreground shadow-elevation-xs"
                  : "text-muted-foreground hover:text-foreground",
              )}
            >
              <FileArchive className="h-4 w-4" />
              ZIP upload
            </button>
          </div>

          <div className="mt-5 space-y-3">
            {mode === "github" ? (
              <Input
                type="url"
                placeholder="https://github.com/org/repo"
                value={githubUrl}
                onChange={(e) => setGithubUrl(e.target.value)}
                hasError={Boolean(error)}
              />
            ) : (
              zipTargets.map((platformId) => {
                const file = zipFiles[platformId];
                const label = platformLabel(platformId);
                const inputId = `zip-upload-${platformId}`;
                return (
                  <label
                    key={platformId}
                    htmlFor={inputId}
                    onDragOver={(e) => {
                      e.preventDefault();
                      setDraggingId(platformId);
                    }}
                    onDragLeave={() => setDraggingId(null)}
                    onDrop={(e) => {
                      e.preventDefault();
                      setDraggingId(null);
                      handleFile(platformId, e.dataTransfer.files?.[0] ?? null);
                    }}
                    className={cn(
                      "group flex items-center gap-3 rounded-lg border-2 border-dashed p-4 cursor-pointer transition-colors",
                      draggingId === platformId
                        ? "border-primary bg-primary/5"
                        : file
                          ? "border-primary/50 bg-primary/[0.03]"
                          : "border-border hover:border-primary/40 hover:bg-muted/30",
                    )}
                  >
                    <input
                      type="file"
                      accept=".zip"
                      id={inputId}
                      onChange={(e) =>
                        handleFile(platformId, e.target.files?.[0] ?? null)
                      }
                      className="sr-only"
                    />
                    <span
                      className={cn(
                        "flex h-10 w-10 items-center justify-center rounded-full shrink-0 transition-colors",
                        file
                          ? "bg-primary/10 text-primary"
                          : "bg-muted text-muted-foreground group-hover:bg-primary/10 group-hover:text-primary",
                      )}
                    >
                      {file ? (
                        <FileArchive className="h-5 w-5" />
                      ) : (
                        <UploadCloud className="h-5 w-5" />
                      )}
                    </span>
                    <div className="min-w-0 flex-1">
                      <p className="text-sm font-semibold text-foreground">
                        {label}
                      </p>
                      {file ? (
                        <p className="truncate text-xs text-muted-foreground">
                          {file.name} · {formatSize(file.size)}
                        </p>
                      ) : (
                        <p className="text-xs text-muted-foreground">
                          Click or drop a .zip for {label}
                        </p>
                      )}
                    </div>
                    {file && (
                      <button
                        type="button"
                        onClick={(e) => {
                          e.preventDefault();
                          setZipFile(platformId, null);
                        }}
                        className="shrink-0 rounded-full p-1 text-muted-foreground hover:bg-muted hover:text-foreground"
                        aria-label={`Remove ${file.name}`}
                      >
                        <X className="h-4 w-4" />
                      </button>
                    )}
                  </label>
                );
              })
            )}
          </div>

          {error && (
            <p className="mt-3 text-sm text-destructive">{error}</p>
          )}
        </CardBody>

        <CardFooter>
          <Button variant="ghost" onClick={onBack}>
            Back
          </Button>
          <div className="flex items-center gap-2">
            <Button
              variant="ghost"
              onClick={handleSkip}
              disabled={save.isPending}
            >
              Skip for now
            </Button>
            <Button
              onClick={handleContinue}
              isLoading={save.isPending}
            >
              Continue
            </Button>
          </div>
        </CardFooter>
      </Card>
    </div>
  );
}
