"use client";

import { useState } from "react";
import { Github, FileArchive, Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";
import { useSaveSource } from "@/hooks/useOnboarding";
import type { SourceType } from "@/types/onboarding";

interface Props {
  initialUrl?: string | null;
  initialFilename?: string | null;
  initialType?: SourceType;
  onBack: () => void;
  onNext: () => void;
  onSkip: () => void;
}

export function SourceUploadStep({
  initialUrl,
  initialFilename,
  initialType,
  onBack,
  onNext,
  onSkip,
}: Props) {
  const [mode, setMode] = useState<"github" | "zip">(
    initialType === "zip" ? "zip" : "github",
  );
  const [githubUrl, setGithubUrl] = useState(initialUrl ?? "");
  const [zipFilename, setZipFilename] = useState(initialFilename ?? "");
  const [error, setError] = useState<string | null>(null);

  const save = useSaveSource();

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
      await save.mutateAsync({
        source_type: "github",
        source_url: githubUrl.trim(),
        source_filename: null,
      });
    } else {
      if (!zipFilename) {
        setError("Please select a ZIP file.");
        return;
      }
      await save.mutateAsync({
        source_type: "zip",
        source_url: null,
        source_filename: zipFilename,
      });
    }
    onNext();
  }

  async function handleSkip() {
    await save.mutateAsync({
      source_type: null,
      source_url: null,
      source_filename: null,
    });
    onSkip();
  }

  function handleFile(file: File | null) {
    if (!file) {
      setZipFilename("");
      return;
    }
    if (!file.name.toLowerCase().endsWith(".zip")) {
      setError("File must be a .zip");
      return;
    }
    setError(null);
    setZipFilename(file.name);
    // NOTE: actual upload pipeline is TODO — we only record the filename for now.
  }

  return (
    <div className="max-w-2xl mx-auto px-6 py-8">
      <h2 className="text-2xl font-semibold mb-2">
        Upload your source codebase
      </h2>
      <p className="text-sm text-muted-foreground mb-6">
        Optional — connect a GitHub repo or upload a ZIP so we can scan and index
        it. You can also skip this and add sources later.
      </p>

      <div className="flex gap-2 mb-4">
        <button
          onClick={() => setMode("github")}
          className={cn(
            "flex-1 flex items-center justify-center gap-2 py-2 rounded-md text-sm font-medium transition-colors",
            mode === "github"
              ? "bg-primary text-primary-foreground"
              : "bg-muted text-muted-foreground hover:bg-muted/70",
          )}
        >
          <Github className="h-4 w-4" />
          GitHub URL
        </button>
        <button
          onClick={() => setMode("zip")}
          className={cn(
            "flex-1 flex items-center justify-center gap-2 py-2 rounded-md text-sm font-medium transition-colors",
            mode === "zip"
              ? "bg-primary text-primary-foreground"
              : "bg-muted text-muted-foreground hover:bg-muted/70",
          )}
        >
          <FileArchive className="h-4 w-4" />
          ZIP Upload
        </button>
      </div>

      {mode === "github" ? (
        <input
          type="url"
          placeholder="https://github.com/org/repo"
          value={githubUrl}
          onChange={(e) => setGithubUrl(e.target.value)}
          className="w-full px-3 py-2 border border-border rounded-md text-sm focus:outline-none focus:ring-2 focus:ring-primary/20 focus:border-primary"
        />
      ) : (
        <div className="border-2 border-dashed border-border rounded-md p-6 text-center">
          <input
            type="file"
            accept=".zip"
            id="zip-upload"
            onChange={(e) => handleFile(e.target.files?.[0] ?? null)}
            className="hidden"
          />
          <label
            htmlFor="zip-upload"
            className="cursor-pointer text-sm text-muted-foreground hover:text-foreground"
          >
            {zipFilename ? (
              <span className="font-medium text-foreground">{zipFilename}</span>
            ) : (
              <>
                <FileArchive className="h-6 w-6 mx-auto mb-2" />
                Click to choose a ZIP file
              </>
            )}
          </label>
        </div>
      )}

      {error && <p className="text-sm text-red-600 mt-3">{error}</p>}

      <div className="flex justify-between items-center mt-8">
        <button
          onClick={onBack}
          className="px-4 py-2 text-sm text-muted-foreground hover:text-foreground transition-colors"
        >
          Back
        </button>
        <div className="flex items-center gap-3">
          <button
            onClick={handleSkip}
            disabled={save.isPending}
            className="px-4 py-2 text-sm text-muted-foreground hover:text-foreground transition-colors disabled:opacity-50"
          >
            Skip for now
          </button>
          <button
            onClick={handleContinue}
            disabled={save.isPending}
            className="px-6 py-2 bg-primary text-primary-foreground rounded-md text-sm font-medium hover:bg-primary/90 transition-colors disabled:opacity-50 flex items-center gap-2"
          >
            {save.isPending && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
            Continue
          </button>
        </div>
      </div>
    </div>
  );
}
