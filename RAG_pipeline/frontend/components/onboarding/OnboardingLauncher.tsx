"use client";

import { useEffect, useState } from "react";
import { FileArchive, GitBranch } from "lucide-react";
import { Button } from "@/components/ui/button";
import { listPacks, type Pack } from "@/lib/packs";

interface OnboardingLauncherProps {
  onStart: (message: string) => void;
}

const PLATFORMS = [
  { value: "generic", label: "Generic" },
  { value: "talend", label: "Talend" },
  { value: "workato", label: "Workato" },
  { value: "lobster", label: "Lobster" },
];

export function OnboardingLauncher({ onStart }: OnboardingLauncherProps) {
  const [projectName, setProjectName] = useState("");
  const [sourceType, setSourceType] = useState<"git" | "zip">("git");
  const [repoUrl, setRepoUrl] = useState("");
  const [platform, setPlatform] = useState("generic");
  const [packs, setPacks] = useState<Pack[]>([]);
  const [packId, setPackId] = useState("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    void listPacks()
      .then(setPacks)
      .catch(() => setError("Packs could not be loaded."));
  }, []);

  function start() {
    const normalizedProject = projectName.trim();
    if (!/^[A-Za-z0-9][A-Za-z0-9_-]{0,99}$/.test(normalizedProject)) {
      setError("Use letters, numbers, dashes, or underscores for the project name.");
      return;
    }
    if (sourceType === "git" && !/^https?:\/\//i.test(repoUrl.trim())) {
      setError("Enter the Git repository URL.");
      return;
    }
    const pack = packs.find((item) => String(item.id) === packId);
    const source = sourceType === "git"
      ? `Git repository ${repoUrl.trim()}`
      : "a ZIP upload";
    const packInstruction = pack
      ? ` Use documentation pack "${pack.name}".`
      : " Use the workspace default documentation Pack.";
    onStart(
      `Onboard a new project called ${normalizedProject} from ${source}. `
      + `The source type is ${sourceType} and the platform is ${platform}.`
      + packInstruction,
    );
  }

  return (
    <section className="space-y-5" aria-label="Onboard project">
      <div>
        <h2 className="text-sm font-semibold">Onboard project</h2>
      </div>

      <label className="block text-sm font-medium">
        Project name
        <input
          value={projectName}
          onChange={(event) => setProjectName(event.target.value)}
          placeholder="customer_orders"
          className="mt-2 h-10 w-full border border-input bg-background px-3 text-sm"
        />
      </label>

      <div className="space-y-2">
        <span className="text-sm font-medium">Source</span>
        <div className="inline-flex border border-border p-0.5" aria-label="Project source">
          <Button size="sm" variant={sourceType === "git" ? "primary" : "ghost"} onClick={() => setSourceType("git")}>
            <GitBranch className="h-4 w-4" aria-hidden /> Git
          </Button>
          <Button size="sm" variant={sourceType === "zip" ? "primary" : "ghost"} onClick={() => setSourceType("zip")}>
            <FileArchive className="h-4 w-4" aria-hidden /> ZIP
          </Button>
        </div>
      </div>

      {sourceType === "git" && (
        <label className="block text-sm font-medium">
          Repository URL
          <input
            value={repoUrl}
            onChange={(event) => setRepoUrl(event.target.value)}
            placeholder="https://github.com/company/project"
            className="mt-2 h-10 w-full border border-input bg-background px-3 text-sm"
          />
        </label>
      )}

      <div className="grid gap-4 sm:grid-cols-2">
        <label className="text-sm font-medium">
          Platform
          <select value={platform} onChange={(event) => setPlatform(event.target.value)} className="mt-2 h-10 w-full border border-input bg-background px-3 text-sm">
            {PLATFORMS.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
          </select>
        </label>
        <label className="text-sm font-medium">
          Documentation Pack
          <select value={packId} onChange={(event) => setPackId(event.target.value)} className="mt-2 h-10 w-full border border-input bg-background px-3 text-sm">
            <option value="">Workspace default</option>
            {packs.map((pack) => <option key={pack.id} value={pack.id}>{pack.name}</option>)}
          </select>
        </label>
      </div>

      {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
      <div className="flex justify-end"><Button onClick={start}>Start onboarding</Button></div>
    </section>
  );
}
