"use client";

import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode, type Ref } from "react";
import { Check, ChevronRight, Eye, FilePlus2, FileText, FolderTree, LoaderCircle, Paperclip, Pencil, Plus, Save, Send, Trash2, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { MarkdownContent } from "@/components/MarkdownContent";
import { useChatContextSafe } from "@/contexts/ChatContext";
import { PACK_CREATE_EVENT, takePackCreateCommand } from "@/lib/packWorkspace";

type Attachment = { id: number; filename: string; content_type: string; size_bytes: number; sha256: string; purpose: string; extracted_text_preview?: string | null; created_at: string };
type Analysis = { id: string; label: string; purpose: string };
type Evidence = { id: string; label: string; required: boolean; weight: number };
type OutputSection = { id: string; heading: string; order: number };
type OutputDocument = { id: string; title: string; sections: OutputSection[] };
type Artifact = { purpose?: string; analyses?: Analysis[]; evidence_requirements?: Evidence[]; output_documents?: OutputDocument[]; audience?: { label?: string; roles?: string[]; decision_needs?: string[]; domain_familiarity?: string; desired_outcome?: string } };
type Draft = { id: number; conversation_id: number | null; revision: number; status: string; name: string; artifact: Artifact; attachments?: Attachment[]; validation: { valid?: boolean; errors?: string[] } };
type PrivateVersion = { id: number; name: string; version: string; artifact: Artifact; attachments?: Attachment[] };
type AssistResponse = { draft: Draft; message: string };
type PendingReference = { id: string; file: File; purpose: string };
type ReferenceProgress = { current: number; total: number; filename: string } | null;
type SectionKey = "overview" | "analysis" | "evidence" | "outputs" | "audience" | "references";
type ViewMode = "edit" | "preview" | "changes";

const referenceAccept = ".pdf,.docx,.pptx,.xlsx,.txt,.md,.csv,.json,.yaml,.yml";
const referenceExtensions = new Set(referenceAccept.split(",").map((extension) => extension.slice(1)));
const maxReferenceBytes = 10 * 1024 * 1024;
const maxReferenceCount = 20;
// Mirrors PackAttachmentPurposeRequest.purpose's max_length in harness_packs.py.
const maxPurposeChars = 500;

const sections: Array<{ key: SectionKey; label: string }> = [
  { key: "overview", label: "Overview" },
  { key: "analysis", label: "Analysis focus" },
  { key: "evidence", label: "Evidence" },
  { key: "outputs", label: "Output documents" },
  { key: "audience", label: "Audience and style" },
  { key: "references", label: "References" },
];

const emptyArtifact: Artifact = { purpose: "", analyses: [], evidence_requirements: [], output_documents: [], audience: { label: "", roles: [], decision_needs: [], domain_familiarity: "", desired_outcome: "" } };

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api/harness/packs/builder/${path}`, { credentials: "include", ...init });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = typeof body.error === "string"
      ? body.error
      : typeof body.detail === "string"
        ? body.detail
        : Array.isArray(body.detail)
          ? body.detail.map((item: { msg?: string }) => item.msg).filter(Boolean).join(" ")
          : "Pack request failed";
    throw new Error(detail || "Pack request failed");
  }
  return body as T;
}

function normalizedArtifact(value: Artifact | undefined): Artifact {
  return { ...emptyArtifact, ...value, analyses: value?.analyses ?? [], evidence_requirements: value?.evidence_requirements ?? [], output_documents: value?.output_documents ?? [], audience: { ...emptyArtifact.audience, ...value?.audience } };
}

function sectionValue(key: SectionKey, draft: Draft | null, artifact: Artifact, attachments: Attachment[]) {
  if (key === "overview") return { name: draft?.name, purpose: artifact.purpose };
  if (key === "analysis") return artifact.analyses;
  if (key === "evidence") return artifact.evidence_requirements;
  if (key === "outputs") return artifact.output_documents;
  if (key === "audience") return artifact.audience;
  return attachments;
}

function markdown(artifact: Artifact): string {
  const output = artifact.output_documents ?? [];
  return [
    `# ${artifact.purpose || "Untitled Pack"}`,
    "## Analysis focus",
    ...(artifact.analyses ?? []).map((item) => `- **${item.label}**: ${item.purpose}`),
    "## Evidence",
    ...(artifact.evidence_requirements ?? []).map((item) => `- ${item.label}${item.required ? " (required)" : ""}`),
    "## Output documents",
    ...output.flatMap((document) => [`### ${document.title}`, ...document.sections.map((section) => `- ${section.heading}`)]),
    "## Audience",
    artifact.audience?.label ?? "",
  ].filter(Boolean).join("\n\n");
}

export function PackWorkbench({ initialPackId, initialProjectId, onClose }: { initialPackId?: string | null; initialProjectId?: string | null; onClose: () => void }) {
  const chat = useChatContextSafe();
  const [draft, setDraft] = useState<Draft | null>(null);
  const [versions, setVersions] = useState<PrivateVersion[]>([]);
  const [selectedVersion, setSelectedVersion] = useState<PrivateVersion | null>(null);
  const [artifact, setArtifact] = useState<Artifact>(emptyArtifact);
  const [baseline, setBaseline] = useState<Artifact>(emptyArtifact);
  const [attachments, setAttachments] = useState<Attachment[]>([]);
  const [baselineAttachments, setBaselineAttachments] = useState<Attachment[]>([]);
  const [selectedSection, setSelectedSection] = useState<SectionKey>("overview");
  const [viewMode, setViewMode] = useState<ViewMode>("edit");
  const [prompt, setPrompt] = useState("");
  const [attachmentPurpose, setAttachmentPurpose] = useState("");
  const [upload, setUpload] = useState<File | null>(null);
  const [pendingReferences, setPendingReferences] = useState<PendingReference[]>([]);
  const [referenceProgress, setReferenceProgress] = useState<ReferenceProgress>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [assistantMessage, setAssistantMessage] = useState<string | null>(null);
  const autoCreateHandled = useRef(false);
  const promptRef = useRef<HTMLTextAreaElement | null>(null);
  const conversationId = chat?.activeConversationId;

  const loadVersions = useCallback(async () => {
    try {
      const next = await api<PrivateVersion[]>("private");
      setVersions(next);
      const wanted = initialPackId ? next.find((item) => String(item.id) === initialPackId || item.name.toLowerCase() === initialPackId.toLowerCase()) : undefined;
      if (wanted) setSelectedVersion(wanted);
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Saved Packs could not be loaded."); }
  }, [initialPackId]);

  useEffect(() => { void loadVersions(); }, [loadVersions]);
  useEffect(() => {
    if (initialProjectId) chat?.setSelectedProject(initialProjectId);
  }, [chat, initialProjectId]);
  useEffect(() => {
    function startStagedCreate() {
      const command = takePackCreateCommand();
      if (!command && autoCreateHandled.current) return;
      autoCreateHandled.current = true;
      void createDraft(command ?? undefined, initialPackId?.toLowerCase() === "active");
    }
    window.addEventListener(PACK_CREATE_EVENT, startStagedCreate);
    if (
      (initialPackId?.toLowerCase() === "create" || initialPackId?.toLowerCase() === "active") &&
      !autoCreateHandled.current
    ) startStagedCreate();
    return () => window.removeEventListener(PACK_CREATE_EVENT, startStagedCreate);
    // Creation follows the active conversation, including the one returned by
    // ChatContext during the same action.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialPackId]);

  function setDraftState(next: Draft, useAsBaseline = false) {
    setDraft(next);
    const nextArtifact = normalizedArtifact(next.artifact);
    setArtifact(nextArtifact);
    setAttachments(next.attachments ?? []);
    if (useAsBaseline) {
      setBaseline(nextArtifact);
      setBaselineAttachments(next.attachments ?? []);
    }
  }

  function clearPendingReferences() {
    setPendingReferences([]);
    setReferenceProgress(null);
  }

  async function createDraft(agentCommand?: string, linkToActiveConversation = false) {
    setBusy("new"); setError(null);
    try {
      const created = await api<Draft>("drafts", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ conversation_id: linkToActiveConversation ? conversationId : undefined, name: "Untitled Documentation Pack" }) });
      setSelectedVersion(null); setDraftState(created, true); setSelectedSection("overview"); setViewMode("edit");
      clearPendingReferences();
      setAssistantMessage(null);
      if (agentCommand) {
        await assistDraft(created.id, agentCommand);
      } else {
        window.setTimeout(() => promptRef.current?.focus(), 0);
      }
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Draft could not be created."); }
    finally { setBusy(null); }
  }

  async function startNewDraft() {
    await createDraft();
  }

  async function forkVersion(version: PrivateVersion, agentCommand?: string) {
    setBusy("fork"); setError(null);
    try {
      const next = await api<Draft>(`private/${version.id}/drafts`, { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
      setSelectedVersion(null); setDraftState(next, true); setSelectedSection("overview"); setViewMode("edit");
      setAssistantMessage(null);
      if (agentCommand) {
        await uploadPendingReferences(next.id);
        await assistDraft(next.id, agentCommand);
      } else {
        window.setTimeout(() => promptRef.current?.focus(), 0);
      }
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Saved Pack could not be opened for editing."); }
    finally { setBusy(null); }
  }

  async function persistDraft(): Promise<Draft> {
    if (!draft) throw new Error("No Pack draft is open.");
    return api<Draft>(`drafts/${draft.id}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ expected_revision: draft.revision, name: draft.name, patch: artifact }) });
  }

  async function saveDraft() {
    if (!draft) return;
    setBusy("save"); setError(null);
    try {
      const updated = await persistDraft();
      setDraftState(updated);
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Draft changes could not be saved."); }
    finally { setBusy(null); }
  }

  async function saveVersion() {
    if (!draft) return;
    setBusy("version"); setError(null);
    try {
      const persisted = await persistDraft();
      setDraftState(persisted);
      const checked = await api<Draft>(`drafts/${persisted.id}/validate`, { method: "POST" });
      if (!checked.validation.valid) { setDraftState(checked); setError(checked.validation.errors?.join(" ") || "Complete the Pack before saving a version."); return; }
      const saved = await api<PrivateVersion>(`drafts/${checked.id}/save`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ expected_revision: checked.revision }) });
      setVersions((items) => [saved, ...items]); setSelectedVersion(saved); setDraft(null);
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Version could not be saved."); }
    finally { setBusy(null); }
  }

  async function sendPrompt() {
    if (!draft || (!prompt.trim() && pendingReferences.length === 0)) return;
    setBusy(pendingReferences.length ? "references" : "assistant"); setError(null);
    try {
      await uploadPendingReferences(draft.id);
      await assistDraft(draft.id, prompt.trim() || "Create or refine this Pack using the attached context files and their stated purposes.");
    } catch (cause) { setError(cause instanceof Error ? cause.message : "The assistant could not update the Pack."); }
    finally { setBusy(null); }
  }

  async function assistDraft(draftId: number, instruction: string) {
    setBusy("assistant"); setError(null);
    const result = await api<AssistResponse>(`drafts/${draftId}/assist`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ instruction }),
    });
    setDraftState(result.draft);
    setAssistantMessage(result.message);
    setPrompt("");
  }

  async function refineSavedVersion(version: PrivateVersion) {
    const instruction = prompt.trim() || (pendingReferences.length ? "Refine this Pack using the attached context files and their stated purposes." : "");
    if (!instruction) return;
    await forkVersion(
      version,
      instruction,
    );
  }

  function queueReferences(files: File[]) {
    if (!files.length) return;
    setError(null);
    const existingCount = (draft ? attachments.length : selectedVersion?.attachments?.length ?? 0) + pendingReferences.length;
    const remaining = Math.max(0, maxReferenceCount - existingCount);
    if (!remaining) { setError("This Pack already has the maximum of 20 reference files."); return; }
    const accepted: PendingReference[] = [];
    for (const [index, file] of files.entries()) {
      if (accepted.length >= remaining) break;
      const extension = file.name.split(".").pop()?.toLowerCase() ?? "";
      if (!referenceExtensions.has(extension)) { setError(`${file.name} is not a supported reference file.`); continue; }
      if (file.size > maxReferenceBytes) { setError(`${file.name} is larger than the 10 MB file limit.`); continue; }
      accepted.push({ id: `${Date.now()}-${index}-${file.name}`, file, purpose: "" });
    }
    setPendingReferences((items) => [...items, ...accepted]);
    if (files.length > accepted.length && accepted.length === remaining) setError("Only the files that fit within the 20-reference limit were added.");
  }

  function updatePendingReference(id: string, purpose: string) {
    setPendingReferences((items) => items.map((item) => item.id === id ? { ...item, purpose } : item));
  }

  function removePendingReference(id: string) {
    setPendingReferences((items) => items.filter((item) => item.id !== id));
  }

  async function uploadPendingReferences(draftId: number) {
    const queued = pendingReferences;
    if (!queued.length) return;
    if (queued.some((item) => !item.purpose.trim())) {
      throw new Error("Explain how Nexus should use every attached file before sending.");
    }
    try {
      for (const [index, item] of queued.entries()) {
        setBusy("references");
        setReferenceProgress({ current: index + 1, total: queued.length, filename: item.file.name });
        const form = new FormData();
        form.set("file", item.file);
        form.set("purpose", item.purpose.trim());
        const uploaded = await api<Attachment>(`drafts/${draftId}/attachments`, { method: "POST", body: form });
        setAttachments((items) => [...items, uploaded]);
        setPendingReferences((items) => items.filter((entry) => entry.id !== item.id));
      }
    } finally {
      setReferenceProgress(null);
    }
  }

  async function uploadAttachment() {
    if (!draft || !upload || !attachmentPurpose.trim()) { setError("Choose a file and give it a purpose before uploading."); return; }
    setBusy("upload"); setError(null);
    try {
      const form = new FormData(); form.set("file", upload); form.set("purpose", attachmentPurpose.trim());
      const uploaded = await api<Attachment>(`drafts/${draft.id}/attachments`, { method: "POST", body: form });
      setAttachments((items) => [...items, uploaded]); setUpload(null); setAttachmentPurpose("");
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Reference could not be uploaded."); }
    finally { setBusy(null); }
  }

  async function updateAttachment(item: Attachment, purpose: string) {
    if (!draft || !purpose.trim()) return;
    setBusy(`attachment-${item.id}`); setError(null);
    try {
      const updated = await api<Attachment>(`drafts/${draft.id}/attachments/${item.id}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ purpose: purpose.trim() }) });
      setAttachments((items) => items.map((entry) => entry.id === item.id ? updated : entry));
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Reference could not be updated."); }
    finally { setBusy(null); }
  }

  async function deleteAttachment(item: Attachment) {
    if (!draft) return;
    setBusy(`attachment-${item.id}`); setError(null);
    try {
      await api<void>(`drafts/${draft.id}/attachments/${item.id}`, { method: "DELETE" });
      setAttachments((items) => items.filter((entry) => entry.id !== item.id));
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Reference could not be deleted."); }
    finally { setBusy(null); }
  }

  const changed = useMemo(() => Object.fromEntries(sections.map(({ key }) => [key, JSON.stringify(sectionValue(key, draft, artifact, attachments)) !== JSON.stringify(sectionValue(key, draft, baseline, baselineAttachments))])) as Record<SectionKey, boolean>, [artifact, attachments, baseline, baselineAttachments, draft]);
  const editing = Boolean(draft);
  const activeArtifact = editing ? artifact : normalizedArtifact(selectedVersion?.artifact);
  const activeAttachments = editing ? attachments : selectedVersion?.attachments ?? [];
  const assistantWorking = false;
  const activityLabel = busy === "assistant"
    ? "Nexus is updating the Pack"
    : busy === "new"
      ? "Starting a Pack draft"
      : busy === "fork"
        ? "Opening an editable draft"
        : null;

  function mutate(mutator: (current: Artifact) => Artifact) { setArtifact((current) => normalizedArtifact(mutator(current))); }
  function changeDraftName(name: string) { setDraft((current) => current ? { ...current, name } : current); }

  return <aside className="flex h-full min-h-0 w-full flex-col bg-background" aria-label="Pack workbench">
    <header className="flex h-14 shrink-0 items-center justify-between border-b border-border px-3">
      <div className="flex min-w-0 items-center gap-2"><FolderTree className="h-4 w-4 shrink-0 text-primary" aria-hidden /><div className="min-w-0"><h2 className="truncate text-sm font-semibold">Pack workbench</h2><p className="truncate text-xs text-muted-foreground">{activityLabel ?? (draft ? (draft.conversation_id ? "Draft linked to this chat" : "Editable personal draft") : selectedVersion ? `Saved version ${selectedVersion.version}` : "Personal Pack inventory")}</p></div></div>
      <div className="flex items-center gap-1"><Button type="button" size="sm" variant="secondary" onClick={() => void startNewDraft()} disabled={busy !== null || assistantWorking} title="New Pack"><Plus className="h-4 w-4" aria-hidden />New</Button><button type="button" onClick={onClose} className="flex h-8 w-8 items-center justify-center rounded-md text-muted-foreground hover:bg-muted hover:text-foreground" aria-label="Close Pack workbench" title="Close Pack workbench"><X className="h-4 w-4" /></button></div>
    </header>
    {activityLabel && <div role="status" className="flex items-center gap-2 border-b border-border bg-muted/30 px-3 py-2 text-xs text-muted-foreground"><LoaderCircle className="h-3.5 w-3.5 animate-spin text-primary" aria-hidden /><span>{activityLabel}</span></div>}
    {error && <p role="alert" className="border-b border-destructive/30 px-3 py-2 text-xs text-destructive">{error}</p>}
    {assistantMessage && <p role="status" className="border-b border-border px-3 py-2 text-xs leading-5 text-muted-foreground">{assistantMessage}</p>}
    <div className="flex min-h-0 flex-1">
      <nav className="flex w-48 shrink-0 flex-col border-r border-border bg-muted/20" aria-label="Pack inventory">
        <div className="border-b border-border px-2 py-2"><p className="px-2 text-[11px] font-semibold uppercase text-muted-foreground">Saved Packs</p><div className="mt-1 max-h-36 overflow-y-auto">{versions.map((version) => <button key={version.id} type="button" onClick={() => { setSelectedVersion(version); setDraft(null); setSelectedSection("overview"); clearPendingReferences(); }} className={`flex w-full items-center gap-2 px-2 py-1.5 text-left text-xs hover:bg-muted ${selectedVersion?.id === version.id && !draft ? "bg-muted font-medium text-foreground" : "text-muted-foreground"}`}><FileText className="h-3.5 w-3.5 shrink-0" /><span className="min-w-0 flex-1 truncate">{version.name}</span><span>v{version.version}</span></button>)}{!versions.length && <p className="px-2 py-2 text-xs text-muted-foreground">No saved Packs</p>}</div></div>
        <div className="min-h-0 flex-1 overflow-y-auto px-2 py-2"><p className="px-2 text-[11px] font-semibold uppercase text-muted-foreground">{draft ? "Draft sections" : selectedVersion ? "Version sections" : "Select a Pack"}</p>{sections.map(({ key, label }) => <button key={key} type="button" disabled={!editing && !selectedVersion} onClick={() => setSelectedSection(key)} className={`mt-0.5 flex min-h-8 w-full items-center gap-2 px-2 text-left text-xs ${selectedSection === key && (editing || selectedVersion) ? "bg-primary/10 text-foreground" : "text-muted-foreground"}`}><ChevronRight className="h-3.5 w-3.5" /><span className="min-w-0 flex-1 truncate">{label}</span>{editing && changed[key] && <span className="h-1.5 w-1.5 rounded-full bg-amber-500" aria-label="Changed" />}</button>)}</div>
      </nav>
      <section className="flex min-w-0 flex-1 flex-col">
        {!editing ? <SavedPackView version={selectedVersion} section={selectedSection} onFork={(version) => void forkVersion(version)} onRefine={(version) => void refineSavedVersion(version)} prompt={prompt} onPrompt={setPrompt} busy={busy} pendingReferences={pendingReferences} referenceProgress={referenceProgress} onQueueReferences={queueReferences} onPendingPurpose={updatePendingReference} onRemovePending={removePendingReference} /> : <>
          <div className="flex shrink-0 items-center justify-between border-b border-border px-3 py-2"><h3 className="text-sm font-semibold">{sections.find((section) => section.key === selectedSection)?.label}</h3><div className="flex items-center rounded-md border border-border p-0.5"><ModeButton mode="edit" active={viewMode} onChange={setViewMode} icon={Pencil} label="Edit" /><ModeButton mode="preview" active={viewMode} onChange={setViewMode} icon={Eye} label="Preview" /><ModeButton mode="changes" active={viewMode} onChange={setViewMode} icon={Check} label="Changes" /></div></div>
          <div className="min-h-0 flex-1 overflow-y-auto p-3">{viewMode === "edit" && <SectionEditor section={selectedSection} draft={draft!} artifact={activeArtifact} attachments={activeAttachments} attachmentPurpose={attachmentPurpose} upload={upload} busy={busy} onDraftName={changeDraftName} onArtifact={mutate} onAttachmentPurpose={setAttachmentPurpose} onUpload={setUpload} onUploadAttachment={() => void uploadAttachment()} onUpdateAttachment={(item, purpose) => void updateAttachment(item, purpose)} onDeleteAttachment={(item) => void deleteAttachment(item)} />}{viewMode === "preview" && <MarkdownContent content={markdown(activeArtifact)} className="text-sm" />}{viewMode === "changes" && <ChangesView before={sectionValue(selectedSection, draft, baseline, baselineAttachments)} after={sectionValue(selectedSection, draft, artifact, attachments)} />}</div>
          <footer className="shrink-0 border-t border-border p-3">
            <PackAssistantComposer
              textareaRef={promptRef}
              value={prompt}
              onChange={setPrompt}
              onSend={() => void sendPrompt()}
              placeholder={(artifact.analyses?.length ?? 0) === 0 ? "Describe what to analyze, which documents to produce, and who will read them" : "Describe a change to this Pack"}
              ariaLabel="Ask the Pack assistant"
              sendLabel="Send"
              busy={busy}
              assistantWorking={assistantWorking}
              pendingReferences={pendingReferences}
              savedReferenceCount={attachments.length}
              referenceProgress={referenceProgress}
              onQueueReferences={queueReferences}
              onPendingPurpose={updatePendingReference}
              onRemovePending={removePendingReference}
            />
            <div className="mt-2 flex justify-end gap-2"><Button type="button" size="sm" variant="secondary" onClick={() => void saveDraft()} disabled={busy !== null || assistantWorking} isLoading={busy === "save"}><Save className="h-3.5 w-3.5" aria-hidden />Save draft</Button><Button type="button" size="sm" onClick={() => void saveVersion()} disabled={busy !== null || assistantWorking} isLoading={busy === "version"}><FilePlus2 className="h-3.5 w-3.5" aria-hidden />Save version</Button></div>
          </footer>
        </>}
      </section>
    </div>
  </aside>;
}

function ModeButton({ mode, active, onChange, icon: Icon, label }: { mode: ViewMode; active: ViewMode; onChange: (value: ViewMode) => void; icon: typeof Pencil; label: string }) { return <button type="button" onClick={() => onChange(mode)} className={`flex h-7 items-center gap-1 px-2 text-xs ${active === mode ? "bg-muted text-foreground" : "text-muted-foreground"}`} title={label}><Icon className="h-3.5 w-3.5" />{label}</button>; }

function InlineAssistantProgress({ visible, label }: { visible: boolean; label: string }) {
  if (!visible) return null;
  return <div className="mb-3" role="status" aria-live="polite"><div className="flex items-center gap-2 text-xs font-medium text-foreground"><LoaderCircle className="h-3.5 w-3.5 animate-spin text-primary" aria-hidden /><span>{label}</span></div><div className="mt-2 h-1 overflow-hidden rounded-full bg-muted" role="progressbar" aria-label={label}><div className="h-full w-1/2 animate-pulse rounded-full bg-primary" /></div></div>;
}

function PackAssistantComposer({ textareaRef, value, onChange, onSend, placeholder, ariaLabel, sendLabel, busy, assistantWorking = false, pendingReferences, savedReferenceCount, referenceProgress, onQueueReferences, onPendingPurpose, onRemovePending }: {
  textareaRef?: Ref<HTMLTextAreaElement>;
  value: string;
  onChange: (value: string) => void;
  onSend: () => void;
  placeholder: string;
  ariaLabel: string;
  sendLabel: string;
  busy: string | null;
  assistantWorking?: boolean;
  pendingReferences: PendingReference[];
  savedReferenceCount: number;
  referenceProgress: ReferenceProgress;
  onQueueReferences: (files: File[]) => void;
  onPendingPurpose: (id: string, purpose: string) => void;
  onRemovePending: (id: string) => void;
}) {
  const fileInput = useRef<HTMLInputElement | null>(null);
  const [dragging, setDragging] = useState(false);
  const isWorking = busy === "references" || busy === "assistant" || busy === "fork" || assistantWorking;
  const progressLabel = busy === "references" && referenceProgress
    ? `Attaching ${referenceProgress.current} of ${referenceProgress.total}: ${referenceProgress.filename}`
    : busy === "fork"
      ? "Creating an editable draft"
      : "Reviewing your request and updating the Pack";
  const actionLabel = busy === "references" ? "Attaching" : isWorking ? "Updating" : sendLabel;

  function addFiles(files: FileList | File[]) {
    onQueueReferences(Array.from(files));
    if (fileInput.current) fileInput.current.value = "";
  }

  return <div
    aria-label="Pack request composer"
    className={`border transition-colors ${dragging ? "border-primary bg-primary/5" : "border-input bg-background"}`}
    onDragEnter={(event) => { event.preventDefault(); setDragging(true); }}
    onDragOver={(event) => { event.preventDefault(); event.dataTransfer.dropEffect = "copy"; setDragging(true); }}
    onDragLeave={(event) => { if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setDragging(false); }}
    onDrop={(event) => { event.preventDefault(); setDragging(false); addFiles(event.dataTransfer.files); }}
  >
    {isWorking && <div className="px-2 pt-2"><InlineAssistantProgress visible label={progressLabel} /></div>}
    {pendingReferences.length > 0 && <div className="border-b border-border bg-muted/20 px-2 py-2">
      <div className="flex items-center justify-between gap-2"><p className="text-xs font-medium">Context for this request</p><span className="text-[11px] text-muted-foreground">{pendingReferences.length} queued</span></div>
      <p className="mt-0.5 text-[11px] text-muted-foreground">Explain each file&apos;s role so Nexus knows how to use it.</p>
      <div className="mt-2 space-y-2">{pendingReferences.map((item) => <div key={item.id} className="grid grid-cols-[minmax(0,1fr)_auto] gap-x-2 gap-y-1 border-t border-border pt-2 first:border-t-0 first:pt-0">
        <div className="flex min-w-0 items-center gap-1.5"><FileText className="h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-hidden /><span className="truncate text-xs font-medium">{item.file.name}</span><span className="shrink-0 text-[10px] text-muted-foreground">{formatFileSize(item.file.size)}</span></div>
        <button type="button" className="flex h-6 w-6 items-center justify-center text-muted-foreground hover:text-destructive" onClick={() => onRemovePending(item.id)} aria-label={`Remove ${item.file.name}`} title="Remove file" disabled={isWorking}><X className="h-3.5 w-3.5" /></button>
        <input aria-label={`Purpose for queued ${item.file.name}`} className="col-span-2 h-8 w-full border border-input bg-background px-2 text-xs" value={item.purpose} onChange={(event) => onPendingPurpose(item.id, event.target.value)} placeholder="How should Nexus use this file? e.g. output template or scope definition" maxLength={maxPurposeChars} disabled={isWorking} />
      </div>)}</div>
    </div>}
    {dragging && <div className="border-b border-primary/30 px-3 py-2 text-center text-xs font-medium text-primary">Drop files to add them as Pack context</div>}
    <div className="flex items-end gap-2 p-2">
      <input ref={fileInput} aria-label="Context files" type="file" multiple accept={referenceAccept} className="sr-only" onChange={(event) => event.target.files && addFiles(event.target.files)} />
      <button type="button" className="flex h-9 w-9 shrink-0 items-center justify-center text-muted-foreground hover:bg-muted hover:text-foreground disabled:opacity-50" onClick={() => fileInput.current?.click()} disabled={busy !== null || assistantWorking} aria-label="Attach context files" title="Attach context files"><Paperclip className="h-4 w-4" /></button>
      <textarea ref={textareaRef} aria-label={ariaLabel} value={value} onChange={(event) => onChange(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter" && (event.metaKey || event.ctrlKey) && !isWorking) onSend(); }} className="min-h-10 flex-1 resize-y border-0 bg-transparent px-1 py-2 text-sm outline-none" placeholder={placeholder} disabled={isWorking} />
      <Button type="button" size="sm" onClick={onSend} disabled={(!value.trim() && pendingReferences.length === 0) || busy !== null || assistantWorking} isLoading={isWorking} title={sendLabel}>{!isWorking && <Send className="h-4 w-4" aria-hidden />}{actionLabel}</Button>
    </div>
    <div className="flex items-center justify-between border-t border-border px-2 py-1.5 text-[10px] text-muted-foreground"><span>{savedReferenceCount ? `${savedReferenceCount} saved reference${savedReferenceCount === 1 ? "" : "s"} available to Nexus` : "PDF, Office, text, and data files"}</span><span>10 MB per file</span></div>
  </div>;
}

function formatFileSize(bytes: number) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.ceil(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function SavedPackView({ version, section, onFork, onRefine, prompt, onPrompt, busy, pendingReferences, referenceProgress, onQueueReferences, onPendingPurpose, onRemovePending }: { version: PrivateVersion | null; section: SectionKey; onFork: (version: PrivateVersion) => void; onRefine: (version: PrivateVersion) => void; prompt: string; onPrompt: (value: string) => void; busy: string | null; pendingReferences: PendingReference[]; referenceProgress: ReferenceProgress; onQueueReferences: (files: File[]) => void; onPendingPurpose: (id: string, purpose: string) => void; onRemovePending: (id: string) => void }) {
  if (!version) return <div className="flex flex-1 flex-col items-center justify-center p-6 text-center"><FileText className="h-7 w-7 text-muted-foreground" /><h3 className="mt-3 text-sm font-semibold">Select a saved Pack</h3><p className="mt-1 max-w-xs text-xs text-muted-foreground">Create a draft to shape a reusable documentation Pack.</p></div>;
  const artifact = normalizedArtifact(version.artifact);
  return <><div className="flex shrink-0 items-center justify-between border-b border-border px-3 py-2"><div className="min-w-0"><h3 className="truncate text-sm font-semibold">{sections.find((item) => item.key === section)?.label}</h3><p className="truncate text-xs text-muted-foreground">{version.name} · v{version.version}</p></div><Button type="button" size="sm" variant="secondary" onClick={() => onFork(version)} isLoading={busy === "fork"}><Pencil className="h-3.5 w-3.5" aria-hidden />Edit saved version</Button></div><div className="min-h-0 flex-1 overflow-y-auto p-4"><ReadOnlySection section={section} version={version} artifact={artifact} /></div><footer className="shrink-0 border-t border-border p-3"><PackAssistantComposer value={prompt} onChange={onPrompt} onSend={() => onRefine(version)} placeholder="Describe a change, or attach files that should shape this Pack" ariaLabel="Refine saved Pack" sendLabel="Refine" busy={busy} pendingReferences={pendingReferences} savedReferenceCount={version.attachments?.length ?? 0} referenceProgress={referenceProgress} onQueueReferences={onQueueReferences} onPendingPurpose={onPendingPurpose} onRemovePending={onRemovePending} /></footer></>;
}

function ReadOnlySection({ section, version, artifact }: { section: SectionKey; version: PrivateVersion; artifact: Artifact }) {
  if (section === "overview") return <div><h4 className="text-lg font-semibold">{version.name}</h4><p className="mt-3 whitespace-pre-wrap text-sm leading-6 text-muted-foreground">{artifact.purpose || "No purpose has been defined."}</p></div>;
  if (section === "analysis") return <ReadOnlyList empty="No analysis focus has been defined.">{(artifact.analyses ?? []).map((item) => <div key={item.id} className="border-b border-border py-3"><h4 className="text-sm font-semibold">{item.label}</h4><p className="mt-1 whitespace-pre-wrap text-sm leading-6 text-muted-foreground">{item.purpose}</p></div>)}</ReadOnlyList>;
  if (section === "evidence") return <ReadOnlyList empty="No evidence requirements have been defined.">{(artifact.evidence_requirements ?? []).map((item) => <div key={item.id} className="flex items-start justify-between gap-3 border-b border-border py-3"><div><h4 className="text-sm font-semibold">{item.label}</h4><p className="mt-1 text-xs text-muted-foreground">{item.required ? "Required evidence" : "Supporting evidence"}</p></div><span className="text-xs tabular-nums text-muted-foreground">Weight {item.weight}</span></div>)}</ReadOnlyList>;
  if (section === "outputs") return <ReadOnlyList empty="No output documents have been defined.">{(artifact.output_documents ?? []).map((document) => <div key={document.id} className="border-b border-border py-3"><h4 className="text-sm font-semibold">{document.title}</h4><ol className="mt-2 space-y-1 border-l border-border pl-3">{document.sections.map((item) => <li key={item.id} className="text-sm text-muted-foreground">{item.heading}</li>)}</ol></div>)}</ReadOnlyList>;
  if (section === "audience") { const audience = artifact.audience; return <dl className="space-y-4 text-sm"><ReadOnlyField label="Audience" value={audience?.label} /><ReadOnlyField label="Roles" value={audience?.roles?.join(", ")} /><ReadOnlyField label="Decision needs" value={audience?.decision_needs?.join(", ")} /><ReadOnlyField label="Domain familiarity" value={audience?.domain_familiarity} /><ReadOnlyField label="Desired outcome" value={audience?.desired_outcome} /></dl>; }
  return <ReadOnlyList empty="No references are attached to this version.">{(version.attachments ?? []).map((item) => <div key={item.id} className="border-b border-border py-3"><div className="flex items-center gap-2"><FileText className="h-4 w-4 text-muted-foreground" /><h4 className="min-w-0 flex-1 truncate text-sm font-semibold">{item.filename}</h4><a href={`/api/harness/packs/builder/private/${version.id}/attachments/${item.id}/content`} target="_blank" rel="noreferrer" className="text-xs text-primary hover:underline">Preview</a></div><p className="mt-2 text-sm text-muted-foreground">{item.purpose}</p>{item.extracted_text_preview && <p className="mt-2 border-l border-border pl-3 text-xs leading-5 text-muted-foreground">{item.extracted_text_preview}</p>}</div>)}</ReadOnlyList>;
}

function ReadOnlyList({ children, empty }: { children: ReactNode[]; empty: string }) { return children.length ? <div>{children}</div> : <p className="text-sm text-muted-foreground">{empty}</p>; }
function ReadOnlyField({ label, value }: { label: string; value?: string }) { return <div><dt className="text-xs font-medium uppercase text-muted-foreground">{label}</dt><dd className="mt-1 leading-6">{value || "Not specified"}</dd></div>; }

function ChangesView({ before, after }: { before: unknown; after: unknown }) { const changed = JSON.stringify(before) !== JSON.stringify(after); return <div><p className="text-sm font-medium">{changed ? "This section has changes" : "No changes in this section"}</p>{changed && <div className="mt-3 grid gap-3 text-xs"><pre className="overflow-x-auto border border-border bg-muted/30 p-2"><strong>Previous</strong>{"\n"}{JSON.stringify(before, null, 2)}</pre><pre className="overflow-x-auto border border-primary/30 bg-primary/5 p-2"><strong>Current</strong>{"\n"}{JSON.stringify(after, null, 2)}</pre></div>}</div>; }

function SectionEditor(props: { section: SectionKey; draft: Draft; artifact: Artifact; attachments: Attachment[]; attachmentPurpose: string; upload: File | null; busy: string | null; onDraftName: (value: string) => void; onArtifact: (mutator: (current: Artifact) => Artifact) => void; onAttachmentPurpose: (value: string) => void; onUpload: (file: File | null) => void; onUploadAttachment: () => void; onUpdateAttachment: (item: Attachment, purpose: string) => void; onDeleteAttachment: (item: Attachment) => void }) {
  const { section, draft, artifact, attachments, onArtifact } = props;
  const input = "mt-1 h-9 w-full border border-input bg-background px-2 text-sm";
  if (section === "overview") return <div className="space-y-4"><label className="block text-sm font-medium">Pack name<input className={input} value={draft.name} onChange={(event) => props.onDraftName(event.target.value)} /></label><label className="block text-sm font-medium">Purpose<textarea className="mt-1 min-h-24 w-full border border-input bg-background p-2 text-sm" value={artifact.purpose ?? ""} onChange={(event) => onArtifact((current) => ({ ...current, purpose: event.target.value }))} /></label></div>;
  if (section === "analysis") return <ListEditor title="Analysis" items={artifact.analyses ?? []} onChange={(items) => onArtifact((current) => ({ ...current, analyses: items as Analysis[] }))} fields={["label", "purpose"]} />;
  if (section === "evidence") return <ListEditor title="Evidence requirement" items={artifact.evidence_requirements ?? []} onChange={(items) => onArtifact((current) => ({ ...current, evidence_requirements: items as Evidence[] }))} fields={["label", "weight"]} />;
  if (section === "outputs") return <OutputEditor documents={artifact.output_documents ?? []} onChange={(documents) => onArtifact((current) => ({ ...current, output_documents: documents }))} />;
  if (section === "audience") { const audience = artifact.audience ?? {}; return <div className="space-y-4"><label className="block text-sm font-medium">Audience<label className="sr-only" htmlFor="audience-label">Audience</label><input id="audience-label" className={input} value={audience.label ?? ""} onChange={(event) => onArtifact((current) => ({ ...current, audience: { ...current.audience, label: event.target.value } }))} /></label><label className="block text-sm font-medium">Decision needs<textarea className="mt-1 min-h-20 w-full border border-input bg-background p-2 text-sm" value={(audience.decision_needs ?? []).join(", ")} onChange={(event) => onArtifact((current) => ({ ...current, audience: { ...current.audience, decision_needs: event.target.value.split(",").map((item) => item.trim()).filter(Boolean) } }))} /></label></div>; }
  return <ReferencesEditor {...props} attachments={attachments} />;
}

function ListEditor({ title, items, fields, onChange }: { title: string; items: Array<Record<string, unknown>>; fields: string[]; onChange: (items: Array<Record<string, unknown>>) => void }) { return <div className="space-y-3">{items.map((item, index) => <div key={String(item.id ?? index)} className="border-b border-border pb-3">{fields.map((field) => <label key={field} className="mt-2 block text-xs font-medium">{field}<input className="mt-1 h-8 w-full border border-input bg-background px-2 text-sm" value={String(item[field] ?? "")} onChange={(event) => onChange(items.map((entry, itemIndex) => itemIndex === index ? { ...entry, [field]: field === "weight" ? Number(event.target.value) || 0 : event.target.value } : entry))} /></label>)}<button type="button" className="mt-2 text-xs text-destructive" onClick={() => onChange(items.filter((_, itemIndex) => itemIndex !== index))}>Remove</button></div>)}<Button type="button" variant="secondary" size="sm" onClick={() => onChange([...items, { id: `${title.toLowerCase().replace(/\s/g, "-")}-${items.length + 1}`, label: "", purpose: "", required: true, weight: 50 }])}><Plus className="h-3.5 w-3.5" aria-hidden />Add {title}</Button></div>; }

function OutputEditor({ documents, onChange }: { documents: OutputDocument[]; onChange: (items: OutputDocument[]) => void }) { return <div className="space-y-3">{documents.map((document, documentIndex) => <div key={document.id} className="border-b border-border pb-3"><label className="block text-sm font-medium">Document title<input className="mt-1 h-8 w-full border border-input bg-background px-2 text-sm" value={document.title} onChange={(event) => onChange(documents.map((item, index) => index === documentIndex ? { ...item, title: event.target.value } : item))} /></label><div className="mt-3 border-l border-border pl-3">{document.sections.map((section, sectionIndex) => <label key={section.id} className="mt-2 block text-xs font-medium">Section heading<input className="mt-1 h-8 w-full border border-input bg-background px-2 text-sm" value={section.heading} onChange={(event) => onChange(documents.map((item, index) => index !== documentIndex ? item : { ...item, sections: item.sections.map((child, childIndex) => childIndex === sectionIndex ? { ...child, heading: event.target.value } : child) }))} /></label>)}<button type="button" className="mt-3 text-xs text-primary" onClick={() => onChange(documents.map((item, index) => index === documentIndex ? { ...item, sections: [...item.sections, { id: `section-${item.sections.length + 1}`, heading: "", order: item.sections.length + 1 }] } : item))}>Add section</button></div></div>)}<Button type="button" variant="secondary" size="sm" onClick={() => onChange([...documents, { id: `document-${documents.length + 1}`, title: "", sections: [] }])}><Plus className="h-3.5 w-3.5" aria-hidden />Add document</Button></div>; }

function ReferencesEditor({ attachments, attachmentPurpose, upload, busy, onAttachmentPurpose, onUpload, onUploadAttachment, onUpdateAttachment, onDeleteAttachment, draft }: { attachments: Attachment[]; attachmentPurpose: string; upload: File | null; busy: string | null; onAttachmentPurpose: (value: string) => void; onUpload: (file: File | null) => void; onUploadAttachment: () => void; onUpdateAttachment: (item: Attachment, purpose: string) => void; onDeleteAttachment: (item: Attachment) => void; draft: Draft }) { return <div className="space-y-4"><div className="border-b border-border pb-4"><label className="block text-sm font-medium">Reference purpose<input aria-label="Reference purpose" className="mt-1 h-9 w-full border border-input bg-background px-2 text-sm" value={attachmentPurpose} onChange={(event) => onAttachmentPurpose(event.target.value)} placeholder="Why should the assistant use this file?" maxLength={maxPurposeChars} /></label><label className="mt-3 block text-sm font-medium">Attachment<input aria-label="Attachment file" type="file" className="mt-1 block w-full text-sm" onChange={(event) => onUpload(event.target.files?.[0] ?? null)} /></label><Button type="button" size="sm" className="mt-3" onClick={onUploadAttachment} disabled={!upload || !attachmentPurpose.trim() || busy !== null} isLoading={busy === "upload"}><Paperclip className="h-3.5 w-3.5" aria-hidden />Upload reference</Button></div><div>{attachments.map((item) => <ReferenceRow key={item.id} item={item} draftId={draft.id} busy={busy} onUpdate={onUpdateAttachment} onDelete={onDeleteAttachment} />)}{!attachments.length && <p className="text-sm text-muted-foreground">No references attached.</p>}</div></div>; }

function ReferenceRow({ item, draftId, busy, onUpdate, onDelete }: { item: Attachment; draftId: number; busy: string | null; onUpdate: (item: Attachment, purpose: string) => void; onDelete: (item: Attachment) => void }) { const [purpose, setPurpose] = useState(item.purpose); useEffect(() => setPurpose(item.purpose), [item.purpose]); return <div className="border-b border-border py-3"><div className="flex items-center gap-2"><FileText className="h-4 w-4 text-muted-foreground" /><span className="min-w-0 flex-1 truncate text-sm font-medium">{item.filename}</span><a href={`/api/harness/packs/builder/drafts/${draftId}/attachments/${item.id}/content`} target="_blank" rel="noreferrer" className="text-xs text-primary hover:underline">Preview</a><button type="button" onClick={() => onDelete(item)} className="p-1 text-muted-foreground hover:text-destructive" title="Delete reference" aria-label={`Delete ${item.filename}`} disabled={busy === `attachment-${item.id}`}><Trash2 className="h-3.5 w-3.5" /></button></div><input aria-label={`Purpose for ${item.filename}`} className="mt-2 h-8 w-full border border-input bg-background px-2 text-xs" value={purpose} onChange={(event) => setPurpose(event.target.value)} onBlur={() => purpose !== item.purpose && onUpdate(item, purpose)} maxLength={maxPurposeChars} /><p className="mt-1 text-xs text-muted-foreground">{item.content_type} · {Math.ceil(item.size_bytes / 1024)} KB{item.extracted_text_preview ? ` · ${item.extracted_text_preview}` : ""}</p></div>; }
