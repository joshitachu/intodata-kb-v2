"use client";

import { useState } from "react";
import { Save } from "lucide-react";
import { Button } from "@/components/ui/button";

interface PackEditorProps {
  onSave: (manifest: unknown, existingPackId?: string) => Promise<void>;
}

const EXAMPLE = JSON.stringify({
  schema_version: 1, id: "custom.project-notes", name: "Project Notes", version: "1.0.0", pack_type: "domain",
  includes: ["nexus.documentation"], documentation_provider_ref: null, vocabulary: [], entities: [], workflows: [], evidence_ranking: [], artifact_templates: [], connector_requirements: [],
  default_scopes: { project_required: true, permissions: [], domains: ["documentation"] },
  policy_hints: { approval_required_for: [], evidence_required: true, read_only: true },
  output_rules: { required_sections: ["summary"], include_confidence: true, include_artifact_links: true },
}, null, 2);

export function PackEditor({ onSave }: PackEditorProps) {
  const [value, setValue] = useState(EXAMPLE);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  async function save() {
    let manifest: { id?: string };
    try {
      manifest = JSON.parse(value) as { id?: string };
    } catch {
      setError("Enter valid Pack JSON.");
      return;
    }
    setSaving(true);
    setError(null);
    try {
      await onSave(manifest, typeof manifest.id === "string" ? manifest.id : undefined);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Pack could not be saved.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <section className="border border-border p-4" aria-label="Custom Pack editor">
      <label className="block text-sm font-semibold" htmlFor="pack-manifest">Custom Pack JSON</label>
      <textarea id="pack-manifest" className="mt-2 min-h-64 w-full resize-y border border-input bg-background p-3 font-mono text-xs" value={value} onChange={(event) => setValue(event.target.value)} spellCheck={false} />
      {error && <p role="alert" className="mt-2 text-sm text-destructive">{error}</p>}
      <div className="mt-3 flex justify-end"><Button onClick={save} isLoading={saving}><Save className="h-3.5 w-3.5" aria-hidden />Save Pack</Button></div>
    </section>
  );
}
