"use client";

import { useEffect, useState } from "react";
import { AlertCircle } from "lucide-react";
import { Button } from "@/components/ui/button";
import { DocumentationPackSelector } from "./DocumentationPackSelector";
import { fetchDocumentationPackSelection, saveDocumentationPackSelection } from "@/lib/harnessPacks";
import type { DocumentationPackSelection } from "@/types";

interface PackManagerProps { projectId: string; }

/** Project-level selection for MCP document-generation swap packs. */
export function PackManager({ projectId }: PackManagerProps) {
  const [selection, setSelection] = useState<DocumentationPackSelection | null>(null);
  const [selectedPluginPackId, setSelectedPluginPackId] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [savingSelection, setSavingSelection] = useState(false);

  async function load() {
    try {
      const nextSelection = await fetchDocumentationPackSelection(projectId);
      setSelection(nextSelection);
      setSelectedPluginPackId(nextSelection.selected_plugin_pack_id);
      setError(null);
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Documentation packs could not be loaded."); }
  }
  useEffect(() => { void load(); }, [projectId]);

  async function saveSelection() {
    setSavingSelection(true);
    try {
      const nextSelection = await saveDocumentationPackSelection(projectId, selectedPluginPackId);
      setSelection(nextSelection);
      setSelectedPluginPackId(nextSelection.selected_plugin_pack_id);
      setError(null);
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Documentation pack selection could not be saved."); }
    finally { setSavingSelection(false); }
  }

  return <div className="space-y-6">
    {error && <p role="alert" className="flex gap-2 text-sm text-destructive"><AlertCircle className="h-4 w-4" aria-hidden />{error}</p>}
    {selection && <>
      <DocumentationPackSelector
        packs={selection.packs}
        selectedPluginPackId={selectedPluginPackId}
        onChange={setSelectedPluginPackId}
      />
      <div className="flex justify-end"><Button onClick={saveSelection} isLoading={savingSelection}>Save documentation pack</Button></div>
    </>}
  </div>;
}
