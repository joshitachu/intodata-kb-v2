"use client";

import { useEffect, useState } from "react";
import { AlertCircle } from "lucide-react";
import { Button } from "@/components/ui/button";
import { DocumentationPackSelector } from "./DocumentationPackSelector";
import { DocumentationPackBuilder } from "./DocumentationPackBuilder";
import {
  createDocumentationPack,
  fetchDocumentationPacks,
  fetchDocumentationPackSelection,
  saveDocumentationPackSelection,
} from "@/lib/harnessPacks";
import type { CreateDocumentationPackRequest } from "@/types";
import type { DocumentationPack, DocumentationPackSelection } from "@/types";

interface PackManagerProps {
  projectId?: string;
  initialMode?: "list" | "select" | "create";
  initialPackName?: string;
}

/** Reusable IntoDoc Pack catalog with optional project assignment. */
export function PackManager({ projectId, initialMode = "list", initialPackName }: PackManagerProps) {
  const [selection, setSelection] = useState<DocumentationPackSelection | null>(null);
  const [packs, setPacks] = useState<DocumentationPack[]>([]);
  const [selectedPluginPackId, setSelectedPluginPackId] = useState<number | null>(null);
  const [mode, setMode] = useState<"list" | "select" | "create">(initialMode);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [savingSelection, setSavingSelection] = useState(false);

  async function load() {
    try {
      if (projectId) {
        const nextSelection = await fetchDocumentationPackSelection(projectId);
        setSelection(nextSelection);
        setPacks(nextSelection.packs);
        const requested = initialPackName
          ? nextSelection.packs.find((pack) => pack.name.toLowerCase() === initialPackName.toLowerCase())
          : null;
        setSelectedPluginPackId(requested?.id ?? nextSelection.selected_plugin_pack_id);
      } else {
        const catalog = await fetchDocumentationPacks();
        setSelection(null);
        setPacks(catalog.packs);
        setSelectedPluginPackId(null);
      }
      setError(null);
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Documentation packs could not be loaded."); }
  }
  useEffect(() => { void load(); }, [projectId, initialPackName]);
  useEffect(() => { setMode(initialMode); }, [initialMode]);

  async function saveSelection() {
    if (!projectId) return;
    setSavingSelection(true);
    try {
      const nextSelection = await saveDocumentationPackSelection(projectId, selectedPluginPackId);
      setSelection(nextSelection);
      setPacks(nextSelection.packs);
      setSelectedPluginPackId(nextSelection.selected_plugin_pack_id);
      setNotice("Project documentation Pack updated.");
      setError(null);
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Documentation pack selection could not be saved."); }
    finally { setSavingSelection(false); }
  }

  async function createPack(draft: CreateDocumentationPackRequest) {
    const created = await createDocumentationPack(draft);
    setPacks((current) => [...current.filter((pack) => pack.id !== created.id), created]);
    setMode(projectId ? "select" : "list");
    setNotice(`Reusable Pack “${created.name}” created.`);
    setError(null);
  }

  return <div className="space-y-6">
    <div className="flex items-center justify-between gap-4">
      <h2 className="text-sm font-semibold">Documentation Packs</h2>
      <div className="inline-flex border border-border p-0.5" aria-label="Documentation Pack mode">
        <Button size="sm" variant={mode !== "create" ? "primary" : "ghost"} onClick={() => setMode(projectId ? "select" : "list")}>{projectId ? "Assign" : "Library"}</Button>
        <Button size="sm" variant={mode === "create" ? "primary" : "ghost"} onClick={() => setMode("create")}>Create</Button>
      </div>
    </div>
    {error && <p role="alert" className="flex gap-2 text-sm text-destructive"><AlertCircle className="h-4 w-4" aria-hidden />{error}</p>}
    {notice && <p role="status" className="text-sm text-emerald-700">{notice}</p>}
    {mode === "list" && (
      <div className="divide-y border-y" aria-label="Reusable documentation Packs">
        {packs.map((pack) => (
          <div key={pack.id} className="py-3">
            <p className="text-sm font-medium">{pack.name} <span className="text-xs text-muted-foreground">v{pack.version}</span></p>
            {pack.description && <p className="mt-1 text-sm text-muted-foreground">{pack.description}</p>}
          </div>
        ))}
        {!packs.length && <p className="py-3 text-sm text-muted-foreground">No documentation Packs are available.</p>}
      </div>
    )}
    {selection && mode === "select" && <>
      <DocumentationPackSelector
        packs={packs}
        selectedPluginPackId={selectedPluginPackId}
        onChange={setSelectedPluginPackId}
      />
      <div className="flex justify-end"><Button onClick={saveSelection} isLoading={savingSelection}>Use for this project</Button></div>
    </>}
    {mode === "create" && <DocumentationPackBuilder onCreate={createPack} />}
  </div>;
}
