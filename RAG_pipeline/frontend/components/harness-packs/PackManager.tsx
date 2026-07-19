"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { AlertCircle, AlertTriangle, Save } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useChatContextSafe } from "@/contexts/ChatContext";
import { useUserProjects } from "@/hooks/useUserProjects";
import {
  createPrivateStylePack,
  createProjectStylePack,
  fetchCertifiedPackLibrary,
  fetchDocumentationPackSelection,
  fetchDocumentationPacks,
  fetchGovernancePolicy,
  fetchGovernedPackActivation,
  fetchPrivateStylePacks,
  fetchProjectStylePacks,
  saveDocumentationPackSelection,
  saveGovernedPackActivation,
  savePrivateStylePackVersion,
  saveProjectStylePackVersion,
} from "@/lib/harnessPacks";
import type { DocumentationPack, DocumentationPackSelection, GovernedPackActivation, GovernedPackSelection, GovernancePack, GovernancePolicy, StylePack, StylePackRequest } from "@/types";
import { DocumentationPackSelector } from "./DocumentationPackSelector";
import { PackLibrary } from "./PackLibrary";
import { StackInspector } from "./StackInspector";
import { StylePackEditor } from "./StylePackEditor";

interface PackManagerProps {
  initialProjectId?: string;
  initialPackName?: string;
  adminMode?: boolean;
}

const emptySelection: GovernedPackSelection = { foundation_pack_ids: [], functional_pack_ids: [], style_pack_ids: [] };

/** Task-first Pack workspace with governance kept available under Advanced. */
export function PackManager({ initialProjectId, initialPackName, adminMode = false }: PackManagerProps) {
  const projects = useUserProjects();
  const chat = useChatContextSafe();
  const [projectId, setProjectId] = useState(initialProjectId ?? "");
  const [policy, setPolicy] = useState<GovernancePolicy | null>(null);
  const [library, setLibrary] = useState<GovernancePack[]>([]);
  const [privateStyles, setPrivateStyles] = useState<StylePack[]>([]);
  const [projectStyles, setProjectStyles] = useState<StylePack[]>([]);
  const [activation, setActivation] = useState<GovernedPackActivation | null>(null);
  const [selection, setSelection] = useState<GovernedPackSelection>(emptySelection);
  const [documentation, setDocumentation] = useState<DocumentationPack[]>([]);
  const [documentationSelection, setDocumentationSelection] = useState<DocumentationPackSelection | null>(null);
  const [documentationDraftId, setDocumentationDraftId] = useState<number | null>(null);
  const [documentationLoading, setDocumentationLoading] = useState(true);
  const [documentationError, setDocumentationError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    if (initialProjectId) setProjectId(initialProjectId);
  }, [initialProjectId]);
  useEffect(() => {
    if (projectId || !projects.data?.projects.length) return;
    setProjectId(chat?.selectedProject || projects.data.projects[0].id);
  }, [chat?.selectedProject, projectId, projects.data]);

  const load = useCallback(async () => {
    if (!adminMode) {
      setLoading(false);
      return;
    }
    setLoading(true); setError(null);
    try {
      const [nextPolicy, nextLibrary, nextPrivateStyles] = await Promise.all([
        fetchGovernancePolicy(), fetchCertifiedPackLibrary(), fetchPrivateStylePacks(),
      ]);
      setPolicy(nextPolicy); setLibrary(nextLibrary); setPrivateStyles(nextPrivateStyles);
      if (projectId) {
        const [nextProjectStyles, nextActivation] = await Promise.all([
          fetchProjectStylePacks(projectId), fetchGovernedPackActivation(projectId, "documentation.generate"),
        ]);
        setProjectStyles(nextProjectStyles); setActivation(nextActivation);
        const required = nextPolicy.required_certified_pack_ids.find((id) => nextLibrary.some((pack) => pack.phase === "foundation" && pack.id === id)) ?? nextLibrary.find((pack) => pack.phase === "foundation")?.id;
        const nextSelection = nextActivation?.selection ?? { foundation_pack_ids: required ? [required] : [], functional_pack_ids: [], style_pack_ids: [] };
        const requested = initialPackName?.toLowerCase();
        const requestedLibraryPack = nextLibrary.find((pack) => pack.id.toLowerCase() === requested || pack.name.toLowerCase() === requested);
        const requestedStylePack = [...nextPrivateStyles, ...nextProjectStyles].find((style) => style.pack.id.toLowerCase() === requested || style.pack.name.toLowerCase() === requested);
        setSelection({
          ...nextSelection,
          functional_pack_ids: requestedLibraryPack?.phase === "functional" ? [...new Set([...nextSelection.functional_pack_ids, requestedLibraryPack.id])].sort() : nextSelection.functional_pack_ids,
          style_pack_ids: requestedStylePack ? [...new Set([...nextSelection.style_pack_ids, requestedStylePack.pack.id])].sort() : nextSelection.style_pack_ids,
        });
      } else {
        setProjectStyles([]); setActivation(null); setSelection(emptySelection);
      }
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Governed Packs could not be loaded."); }
    finally { setLoading(false); }
  }, [adminMode, initialPackName, projectId]);

  useEffect(() => { void load(); }, [load]);

  const loadDocumentation = useCallback(async () => {
    setDocumentationLoading(true); setDocumentationError(null);
    try {
      if (projectId) {
        const nextSelection = await fetchDocumentationPackSelection(projectId);
        const requested = initialPackName?.toLowerCase();
        const requestedPack = nextSelection.packs.find((pack) => pack.name.toLowerCase() === requested || String(pack.id) === requested);
        setDocumentationSelection(nextSelection);
        setDocumentationDraftId(requestedPack?.id ?? nextSelection.selected_plugin_pack_id);
        setDocumentation(nextSelection.packs);
      } else {
        const catalog = await fetchDocumentationPacks();
        setDocumentationSelection(null);
        setDocumentationDraftId(null);
        setDocumentation(catalog.packs);
      }
    } catch (cause) {
      setDocumentation([]);
      setDocumentationSelection(null);
      setDocumentationDraftId(null);
      setDocumentationError(cause instanceof Error ? cause.message : "Certified documentation Packs are unavailable.");
    } finally {
      setDocumentationLoading(false);
    }
  }, [initialPackName, projectId]);

  useEffect(() => { void loadDocumentation(); }, [loadDocumentation]);

  const accessibleStyles = useMemo(() => [...privateStyles, ...projectStyles], [privateStyles, projectStyles]);

  async function saveActivation() {
    if (!projectId || !policy) return;
    setSaving(true); setError(null); setNotice(null);
    try {
      const foundation = policy.required_certified_pack_ids.find((id) => library.some((pack) => pack.phase === "foundation" && pack.id === id)) ?? library.find((pack) => pack.phase === "foundation")?.id;
      const saved = await saveGovernedPackActivation(projectId, "documentation.generate", { ...selection, foundation_pack_ids: foundation ? [foundation] : [] });
      setActivation(saved); setSelection(saved.selection); setNotice("Governed stack updated.");
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Governed stack could not be saved."); }
    finally { setSaving(false); }
  }
  async function saveDocumentation() {
    if (!projectId) return;
    setDocumentationError(null);
    try {
      const saved = await saveDocumentationPackSelection(projectId, documentationDraftId);
      setDocumentationSelection(saved); setDocumentationDraftId(saved.selected_plugin_pack_id); setNotice("Project documentation Pack updated.");
    }
    catch (cause) { setDocumentationError(cause instanceof Error ? cause.message : "Documentation Pack could not be saved."); }
  }
  async function refreshStyles(action: () => Promise<unknown>): Promise<void> { await action(); await load(); setNotice("Style Pack saved as a new version."); }

  return <div className="mx-auto max-w-4xl space-y-8 py-6">
    <header className="border-b border-border pb-5">
      <h1 className="text-2xl font-semibold">Documentation Packs</h1>
      <p className="mt-2 max-w-3xl text-sm text-muted-foreground">Start with your organization’s documentation foundation, then save personal Packs that add the analysis and documents you need.</p>
    </header>
    <div className="flex flex-wrap items-end gap-3 border-b border-border pb-5">
      <label className="min-w-[15rem] flex-1 text-sm font-medium">Use Packs with
        <select value={projectId} onChange={(event) => { setProjectId(event.target.value); chat?.setSelectedProject(event.target.value); setNotice(null); }} className="mt-1 h-10 w-full border border-input bg-background px-3 text-sm" disabled={projects.isLoading}>
          <option value="">My personal library</option>
          {projects.data?.projects.map((project) => <option key={project.id} value={project.id}>{project.display_name}</option>)}
        </select>
      </label>
    </div>
    {error && <p role="alert" className="flex items-center gap-2 text-sm text-destructive"><AlertCircle className="h-4 w-4" aria-hidden />{error}</p>}
    {notice && <p role="status" className="text-sm text-emerald-700">{notice}</p>}
    <section className="border-t border-border pt-6" aria-labelledby="documentation-library-heading">
      <div><h2 id="documentation-library-heading" className="text-base font-semibold">Organization Packs</h2><p className="mt-1 text-sm text-muted-foreground">These approved Packs provide the shared documentation foundation. Your personal Pack adds to them; it does not replace them.</p></div>
      {documentationLoading && <p className="mt-4 text-sm text-muted-foreground">Loading organization Packs...</p>}
      {!documentationLoading && documentationError && <p role="alert" className="mt-4 flex items-start gap-2 border-y border-amber-300 bg-amber-50 py-3 text-sm text-amber-900"><AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />Organization Pack selection is currently unavailable. {documentationError}</p>}
      {!documentationLoading && !documentationError && projectId && documentationSelection && <div className="mt-4"><DocumentationPackSelector packs={documentation} selectedPluginPackId={documentationDraftId} onChange={setDocumentationDraftId} />{documentationDraftId !== documentationSelection.selected_plugin_pack_id && <p className="mt-3 text-xs text-amber-800">Pending documentation Pack selection. Save to apply it to this project.</p>}<div className="mt-3 flex justify-end"><Button size="sm" onClick={saveDocumentation} disabled={documentationDraftId === documentationSelection.selected_plugin_pack_id}>Use for this project</Button></div></div>}
      {!documentationLoading && !documentationError && (!projectId || !documentationSelection) && <div className="mt-4 divide-y border-y border-border">{documentation.map((pack) => <div key={pack.id} className="py-3"><span className="text-sm font-medium">{pack.name}</span><span className="ml-2 text-xs text-muted-foreground">v{pack.version}</span>{pack.description && <p className="mt-1 text-sm text-muted-foreground">{pack.description}</p>}</div>)}{!documentation.length && <p className="py-3 text-sm text-muted-foreground">No certified documentation Packs are available.</p>}</div>}
    </section>
    {adminMode && <details className="border-t border-border pt-5">
      <summary className="cursor-pointer text-sm font-semibold">Organization and advanced settings</summary>
      <p className="mt-1 text-sm text-muted-foreground">Governance stack, presentation settings, and project activation controls.</p>
      <div className="mt-5 space-y-6">
        {loading ? <p className="text-sm text-muted-foreground">Loading organization settings...</p> : <>
          <PackLibrary packs={library} />
          {projectId && policy && <section className="border-t border-border pt-6"><div className="flex flex-wrap items-start justify-between gap-4"><StackInspector policy={policy} library={library} styles={accessibleStyles} selection={selection} activation={activation} onChange={setSelection} /><Button onClick={saveActivation} isLoading={saving} className="shrink-0"><Save className="h-3.5 w-3.5" aria-hidden />Save stack</Button></div></section>}
          <section className="border-t border-border pt-6"><StylePackEditor projectId={projectId || undefined} privatePacks={privateStyles} projectPacks={projectStyles} onCreatePrivate={(payload) => refreshStyles(() => createPrivateStylePack(payload))} onSavePrivateVersion={(id, payload) => refreshStyles(() => savePrivateStylePackVersion(id, payload))} onCreateProject={(payload) => { if (!projectId) return Promise.reject(new Error("Choose a project for a project-scoped Style Pack.")); return refreshStyles(() => createProjectStylePack(projectId, payload)); }} onSaveProjectVersion={(id, payload) => { if (!projectId) return Promise.reject(new Error("Choose a project for a project-scoped Style Pack.")); return refreshStyles(() => saveProjectStylePackVersion(projectId, id, payload)); }} /></section>
        </>}
      </div>
    </details>}
  </div>;
}
