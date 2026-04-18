"use client";

import { useState } from "react";
import { Building2, Loader2 } from "lucide-react";
import { useSaveWorkspace } from "@/hooks/useOnboarding";

interface Props {
  initial: string | null | undefined;
  suggested?: string;
  onBack: () => void;
  onNext: () => void;
}

export function WorkspaceStep({ initial, suggested, onBack, onNext }: Props) {
  const [name, setName] = useState(initial ?? suggested ?? "");
  const [error, setError] = useState<string | null>(null);
  const save = useSaveWorkspace();

  async function handleNext() {
    const trimmed = name.trim();
    if (!trimmed) {
      setError("Please enter a name for your workspace.");
      return;
    }
    setError(null);
    try {
      await save.mutateAsync(trimmed);
      onNext();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not save workspace name.");
    }
  }

  return (
    <div className="max-w-2xl mx-auto px-6 py-10">
      <div className="flex items-center gap-3 mb-6">
        <div className="w-10 h-10 rounded-lg bg-primary/10 flex items-center justify-center">
          <Building2 className="h-5 w-5 text-primary" />
        </div>
        <div>
          <h2 className="text-2xl font-semibold">Name your workspace</h2>
          <p className="text-sm text-muted-foreground">
            This is the company or team this environment belongs to — we&rsquo;ll use
            it throughout the app.
          </p>
        </div>
      </div>

      <label
        htmlFor="workspace-name"
        className="block text-sm font-medium mb-1.5"
      >
        Workspace name
      </label>
      <input
        id="workspace-name"
        type="text"
        value={name}
        onChange={(e) => setName(e.target.value)}
        maxLength={200}
        placeholder="Acme Corp"
        autoFocus
        className="w-full px-3 py-2.5 border border-border rounded-md text-sm focus:outline-none focus:ring-2 focus:ring-primary/20 focus:border-primary"
      />
      <p className="mt-1.5 text-xs text-muted-foreground">
        You can change this later in settings.
      </p>

      {error && <p className="text-sm text-red-600 mt-3">{error}</p>}

      <div className="flex justify-between items-center mt-10">
        <button
          onClick={onBack}
          className="px-4 py-2 text-sm text-muted-foreground hover:text-foreground transition-colors"
        >
          Back
        </button>
        <button
          onClick={handleNext}
          disabled={save.isPending}
          className="px-6 py-2 bg-primary text-primary-foreground rounded-md text-sm font-medium hover:bg-primary/90 transition-colors disabled:opacity-50 flex items-center gap-2"
        >
          {save.isPending && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
          Continue
        </button>
      </div>
    </div>
  );
}
