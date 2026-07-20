"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Check,
  ChevronDown,
  Eye,
  FileText,
  MessageSquare,
  Plus,
  Save,
  Send,
  Share2,
  ShieldCheck,
  Sparkles,
  Trash2,
  Upload,
  X,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { useChatContextSafe } from "@/contexts/ChatContext";

type Validation = { valid?: boolean; errors?: string[]; unknown_capabilities?: string[]; audience_render?: Record<string, string> };
type Draft = { id: number; conversation_id: number; revision: number; status: string; name: string; artifact: Artifact; validation: Validation };
type PrivateVersion = { id: number; pack_id: string; name: string; version: string; content_hash: string; artifact: Artifact; receipt: Record<string, unknown> };
type Analysis = { id: string; label: string; purpose: string };
type EvidenceRequirement = { id: string; label: string; required: boolean; weight: number };
type OutputSection = { id: string; heading: string; order: number };
type OutputDocument = { id: string; title: string; sections: OutputSection[] };
type ScoringDimension = { id: string; label: string; weight: number };
type CapabilityRequirement = { capability_id: string; required: boolean; purpose: string };
type Audience = { label: string; roles: string[]; decision_needs: string[]; domain_familiarity: string; desired_outcome: string; render: RenderProfile };
type RenderProfile = { seniority?: string; domain_familiarity?: string; decision_horizon?: string; detail_level?: string };
type Artifact = {
  schema_version?: number;
  purpose?: string;
  analyses?: Analysis[];
  evidence_requirements?: EvidenceRequirement[];
  output_documents?: OutputDocument[];
  scoring_dimensions?: ScoringDimension[];
  capability_requirements?: CapabilityRequirement[];
  compatibility?: { supported_project_kinds?: string[]; required_domains?: string[] };
  audience?: Audience;
};
type ProjectResult = { project_id?: string; compatible?: boolean; missing_required?: string[]; missing_optional?: string[]; markdown?: string; receipt?: Record<string, unknown>; shared?: boolean; activation_snapshot_hash?: string; status?: string; detail?: Record<string, unknown> };

const emptyAudience: Audience = { label: "", roles: [], decision_needs: [], domain_familiarity: "", desired_outcome: "", render: {} };
const emptyArtifact: Artifact = { schema_version: 3, analyses: [], evidence_requirements: [], output_documents: [], scoring_dimensions: [], capability_requirements: [], compatibility: { supported_project_kinds: [], required_domains: [] }, audience: emptyAudience };

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api/harness/packs/builder/${path}`, {
    credentials: "include",
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = typeof body.detail === "string" ? body.detail : typeof body.error === "string" ? body.error : "Pack builder request failed";
    throw new Error(detail);
  }
  return body as T;
}

function idFor(prefix: string, count: number) {
  return `${prefix}-${count + 1}`;
}

function splitWords(value: string) {
  return value.split(",").map((item) => item.trim()).filter(Boolean);
}

function words(value: string[] | undefined) {
  return (value ?? []).join(", ");
}

export function AnalysisPackBuilder({ openOnLoad = false, selectedProjectId }: { openOnLoad?: boolean; selectedProjectId?: string }) {
  const chat = useChatContextSafe();
  const [draft, setDraft] = useState<Draft | null>(null);
  const [artifact, setArtifact] = useState<Artifact>(emptyArtifact);
  const [packName, setPackName] = useState("Untitled Documentation Pack");
  const [chatPrompt, setChatPrompt] = useState("");
  const [versions, setVersions] = useState<PrivateVersion[]>([]);
  const [selectedVersion, setSelectedVersion] = useState<PrivateVersion | null>(null);
  const [projectId, setProjectId] = useState("");
  const [workflowKey, setWorkflowKey] = useState("documentation.generate");
  const [projectResult, setProjectResult] = useState<ProjectResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [dirty, setDirty] = useState(false);
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const starting = useRef(false);
  const autoStarted = useRef(false);
  const waitingForConversation = useRef(false);
  const conversationId = chat?.activeConversationId;

  const loadVersions = useCallback(async () => {
    try {
      setVersions(await api<PrivateVersion[]>("private"));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Saved private versions could not be loaded.");
    }
  }, []);

  useEffect(() => { void loadVersions(); }, [loadVersions]);
  useEffect(() => { setProjectId(selectedProjectId ?? ""); }, [selectedProjectId]);

  const start = useCallback(async (prompt?: string) => {
    if (starting.current) return;
    setError(null);
    if (!conversationId) {
      if (!chat) { setError("Open the chat workspace before starting a Documentation Pack draft."); return; }
      waitingForConversation.current = true;
      await chat.createConversation();
      return;
    }
    starting.current = true;
    setBusy("start");
    try {
      const created = await api<Draft>("drafts", { method: "POST", body: JSON.stringify({ conversation_id: conversationId, name: packName.trim() || "Untitled Documentation Pack" }) });
      setDraft(created);
      setArtifact({ ...emptyArtifact, ...created.artifact, audience: { ...emptyAudience, ...(created.artifact.audience ?? {}) } });
      setDirty(false);
      if (prompt?.trim() && chat?.handleSendMessage) {
        await chat.handleSendMessage({ query: prompt.trim().startsWith("/pack") ? prompt.trim() : `/pack create ${prompt.trim()}` });
        const refreshed = await api<Draft>(`drafts/${created.id}`);
        setDraft(refreshed);
        setArtifact({ ...emptyArtifact, ...refreshed.artifact, audience: { ...emptyAudience, ...(refreshed.artifact.audience ?? {}) } });
        setChatPrompt("");
        setNotice("Your Documentation Pack is ready to review.");
      } else {
        setNotice("Draft opened. Tell the assistant what you need.");
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not start the Documentation Pack draft.");
    } finally {
      starting.current = false;
      setBusy(null);
    }
  }, [chat, conversationId, packName]);

  useEffect(() => {
    const shouldStart = conversationId && !draft && (waitingForConversation.current || (openOnLoad && !autoStarted.current));
    if (shouldStart) {
      waitingForConversation.current = false;
      if (openOnLoad) autoStarted.current = true;
      void start();
    }
  }, [conversationId, draft, openOnLoad, start]);

  const activeVersion = selectedVersion;
  const workingArtifact = draft ? artifact : activeVersion?.artifact ?? null;
  const validation = draft?.validation;
  const canSave = Boolean(draft && !dirty && validation?.valid);
  const completion = useMemo(() => {
    const value = workingArtifact;
    return [
      Boolean(value?.purpose?.trim()), Boolean(value?.analyses?.length), Boolean(value?.evidence_requirements?.length),
      Boolean(value?.output_documents?.length), Boolean(value?.audience?.label?.trim()),
    ].filter(Boolean).length;
  }, [workingArtifact]);

  function changeArtifact(next: Artifact) {
    setArtifact({ ...next, schema_version: 3 });
    setDirty(true);
    setNotice(null);
  }

  function updateArtifact<K extends keyof Artifact>(key: K, value: Artifact[K]) {
    changeArtifact({ ...artifact, [key]: value });
  }

  async function persist() {
    if (!draft || !dirty) return true;
    setBusy("update"); setError(null);
    try {
      const updated = await api<Draft>(`drafts/${draft.id}`, { method: "PATCH", body: JSON.stringify({ expected_revision: draft.revision, patch: artifact }) });
      setDraft(updated); setArtifact({ ...emptyArtifact, ...updated.artifact, audience: { ...emptyAudience, ...(updated.artifact.audience ?? {}) } }); setDirty(false);
      return true;
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Draft changes could not be updated.");
      return false;
    } finally { setBusy(null); }
  }

  async function validate() {
    if (!draft) return;
    if (!(await persist())) return;
    setBusy("validate"); setError(null);
    try {
      const checked = await api<Draft>(`drafts/${draft.id}/validate`, { method: "POST" });
      setDraft(checked); setArtifact({ ...emptyArtifact, ...checked.artifact, audience: { ...emptyAudience, ...(checked.artifact.audience ?? {}) } });
      setNotice(checked.validation.valid ? "This Pack is ready to save privately." : "Finish the highlighted requirements before saving.");
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Pack validation failed."); }
    finally { setBusy(null); }
  }

  async function save() {
    if (!draft) return;
    if (!(await persist())) return;
    setBusy("save"); setError(null);
    try {
      const saved = await api<PrivateVersion>(`drafts/${draft.id}/save`, { method: "POST", body: JSON.stringify({ expected_revision: draft.revision }) });
      setSelectedVersion(saved); setDraft(null); setArtifact(emptyArtifact); setDirty(false); setNotice(`Saved private version ${saved.version}.`); await loadVersions();
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Pack could not be saved."); }
    finally { setBusy(null); }
  }

  async function abandon() {
    if (!draft) return;
    setBusy("abandon"); setError(null);
    try { await api<Draft>(`drafts/${draft.id}/abandon`, { method: "POST", body: JSON.stringify({ expected_revision: draft.revision }) }); setDraft(null); setArtifact(emptyArtifact); setDirty(false); setNotice("Draft abandoned. Your saved private versions are unchanged."); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "Draft could not be abandoned."); }
    finally { setBusy(null); }
  }

  async function sendToChat() {
    const prompt = chatPrompt.trim();
    if (!prompt || !chat?.handleSendMessage) return;
    if (!draft) { setError("Start the draft first so the assistant can update this Pack."); return; }
    setBusy("chat"); setError(null);
    try {
      await chat.handleSendMessage({ query: prompt.startsWith("/pack") ? prompt : `/pack create ${prompt}` });
      const refreshed = await api<Draft>(`drafts/${draft.id}`);
      setDraft(refreshed); setArtifact({ ...emptyArtifact, ...refreshed.artifact, audience: { ...emptyAudience, ...(refreshed.artifact.audience ?? {}) } }); setDirty(false); setChatPrompt("");
    } catch (cause) { setError(cause instanceof Error ? cause.message : "The assistant could not update this draft."); }
    finally { setBusy(null); }
  }

  async function projectAction(action: "compatibility" | "preview" | "share" | "activate") {
    if (!activeVersion || !projectId.trim()) { setError("Enter a project ID before using this project action."); return; }
    setBusy(action); setError(null); setProjectResult(null);
    try {
      const path = `private/${activeVersion.id}/${action === "activate" ? "activate" : action}`;
      const body = action === "activate" ? { project_id: projectId.trim(), workflow_key: workflowKey.trim() || "documentation.generate" } : { project_id: projectId.trim() };
      setProjectResult(await api<ProjectResult>(path, { method: "POST", body: JSON.stringify(body) }));
    } catch (cause) { setError(cause instanceof Error ? cause.message : `Project ${action} failed.`); }
    finally { setBusy(null); }
  }

  async function useWithSelectedProject(version: PrivateVersion) {
    if (!selectedProjectId) return;
    setBusy(`use-${version.id}`); setError(null); setProjectResult(null);
    try {
      const result = await api<ProjectResult>(`private/${version.id}/activate`, { method: "POST", body: JSON.stringify({ project_id: selectedProjectId, workflow_key: "documentation.generate" }) });
      setSelectedVersion(version); setProjectResult(result); setNotice("Pack is ready to use with the selected project.");
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Pack could not be used with the selected project."); }
    finally { setBusy(null); }
  }

  async function proposePromotion() {
    if (!activeVersion) return;
    setBusy("promotion"); setError(null);
    try { setProjectResult(await api<ProjectResult>(`private/${activeVersion.id}/promotion-proposals`, { method: "POST" })); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "Promotion proposal could not be created."); }
    finally { setBusy(null); }
  }

  const audience = artifact.audience ?? emptyAudience;
  const inputClass = "mt-1 h-9 w-full border border-input bg-background px-2 text-sm";
  const textareaClass = "mt-1 min-h-20 w-full border border-input bg-background p-2 text-sm";

  return <section className="border-y border-border py-6" aria-labelledby="analysis-pack-builder-heading">
    <div className="flex flex-wrap items-start justify-between gap-4">
      <div>
        <h2 id="analysis-pack-builder-heading" className="text-lg font-semibold">Create a Documentation Pack</h2>
        <p className="mt-1 max-w-2xl text-sm text-muted-foreground">Describe the documentation you need. Nexus turns it into a Pack you can review, save, and use again.</p>
      </div>
      {!draft && <Button onClick={() => void start()} isLoading={busy === "start"}><Sparkles className="h-4 w-4" aria-hidden />New Pack</Button>}
    </div>

    {error && <p role="alert" className="mt-4 border-l-2 border-destructive px-3 py-2 text-sm text-destructive">{error}</p>}
    {notice && <p role="status" className="mt-4 border-l-2 border-primary px-3 py-2 text-sm text-muted-foreground">{notice}</p>}

    {!draft && <div className="mt-6 space-y-5">
      <div className="border border-primary/30 bg-primary/5 p-4" aria-labelledby="new-pack-prompt-heading">
        <div className="flex items-start gap-3"><MessageSquare className="mt-0.5 h-5 w-5 shrink-0 text-primary" aria-hidden /><div className="min-w-0 flex-1"><h3 id="new-pack-prompt-heading" className="text-sm font-semibold">What should this Pack help document?</h3><p className="mt-1 text-sm text-muted-foreground">Include the goal, the people who will use it, and the documents you expect.</p><div className="mt-3 flex flex-col gap-2 sm:flex-row"><textarea value={chatPrompt} onChange={(event) => setChatPrompt(event.target.value)} className="min-h-12 flex-1 resize-y border border-input bg-background p-2 text-sm" placeholder="Example: Create a migration Pack for engineering leaders with an executive brief and implementation guide." maxLength={4000} aria-label="Describe your Documentation Pack" /><Button onClick={() => void start(chatPrompt)} disabled={!chatPrompt.trim() || busy !== null} isLoading={busy === "start"}><Sparkles className="h-4 w-4" aria-hidden />Create with assistant</Button></div></div></div>
      </div>
      <details className="border-t border-border pt-4"><summary className="cursor-pointer text-sm font-medium">Name this Pack</summary><label className="mt-3 block max-w-xl text-sm font-medium">Pack name
        <input value={packName} onChange={(event) => setPackName(event.target.value)} className={inputClass} maxLength={160} />
        <span className="mt-1 block text-xs font-normal text-muted-foreground">Used when you save your personal Pack.</span>
      </label></details>
      <SavedVersions versions={versions} onSelect={setSelectedVersion} selectedProjectId={selectedProjectId} busy={busy} onUse={useWithSelectedProject} />
    </div>}

    {draft && <>
      <div className="mt-6 border-y border-border py-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div><p className="text-sm font-medium">{draft.name}</p><p className="text-xs text-muted-foreground">{completion}/5 essentials ready{dirty ? " · changes not yet saved" : ""}</p></div>
          <div className="flex flex-wrap gap-2"><Button variant="secondary" size="sm" onClick={() => void persist()} disabled={!dirty || busy !== null} isLoading={busy === "update"}><Save className="h-3.5 w-3.5" aria-hidden />Update</Button><Button variant="secondary" size="sm" onClick={() => void validate()} disabled={busy !== null} isLoading={busy === "validate"}><ShieldCheck className="h-3.5 w-3.5" aria-hidden />Check ready</Button></div>
        </div>
      </div>

      <div className="mt-5 border border-primary/30 bg-primary/5 p-4" aria-labelledby="pack-chat-heading">
        <div className="flex items-start gap-3"><MessageSquare className="mt-0.5 h-5 w-5 shrink-0 text-primary" aria-hidden /><div className="min-w-0 flex-1"><h3 id="pack-chat-heading" className="text-sm font-semibold">Refine with the assistant</h3><p className="mt-1 text-sm text-muted-foreground">Ask for a change in plain language, then review the updated Pack below.</p><div className="mt-3 flex flex-col gap-2 sm:flex-row"><textarea value={chatPrompt} onChange={(event) => setChatPrompt(event.target.value)} className="min-h-12 flex-1 resize-y border border-input bg-background p-2 text-sm" placeholder="Example: Add a risk analysis for supplier concentration and include a mitigation plan." maxLength={4000} aria-label="Message the Pack assistant" /><Button onClick={() => void sendToChat()} disabled={!chatPrompt.trim() || busy !== null} isLoading={busy === "chat"}><Send className="h-4 w-4" aria-hidden />Ask assistant</Button></div></div></div>
      </div>

      <PackSummary artifact={artifact} />

      <details className="mt-6 border-t border-border pt-5">
        <summary className="cursor-pointer text-sm font-semibold">Review and refine this Pack</summary>
        <p className="mt-1 text-sm text-muted-foreground">Edit the generated content when you need more control.</p>
      <div className="mt-5 space-y-6">
        <EditorSection title="Purpose and audience" description="State the decision this Pack supports, then describe the people and outcome in your own words.">
          <label className="block text-sm font-medium">Purpose<textarea value={artifact.purpose ?? ""} onChange={(event) => updateArtifact("purpose", event.target.value)} className={textareaClass} maxLength={1000} placeholder="What decision should this Pack support?" /></label>
          <div className="mt-4 grid gap-4 sm:grid-cols-2"><label className="text-sm font-medium">Audience description<input value={audience.label} onChange={(event) => updateArtifact("audience", { ...audience, label: event.target.value })} className={inputClass} maxLength={160} placeholder="e.g. Regional procurement directors" /></label><label className="text-sm font-medium">Desired outcome<input value={audience.desired_outcome} onChange={(event) => updateArtifact("audience", { ...audience, desired_outcome: event.target.value })} className={inputClass} maxLength={300} placeholder="What should they be able to decide or do?" /></label><label className="text-sm font-medium">Roles<input value={words(audience.roles)} onChange={(event) => updateArtifact("audience", { ...audience, roles: splitWords(event.target.value) })} className={inputClass} placeholder="Comma-separated roles" /></label><label className="text-sm font-medium">Decision needs<input value={words(audience.decision_needs)} onChange={(event) => updateArtifact("audience", { ...audience, decision_needs: splitWords(event.target.value) })} className={inputClass} placeholder="Comma-separated needs" /></label></div>
          <label className="mt-4 block text-sm font-medium">Domain familiarity in their words<input value={audience.domain_familiarity} onChange={(event) => updateArtifact("audience", { ...audience, domain_familiarity: event.target.value })} className={inputClass} maxLength={160} placeholder="e.g. Familiar with regulated cold-chain operations" /></label>
          <fieldset className="mt-4 border-t border-border pt-4"><legend className="text-sm font-medium">Rendering dimensions</legend><p className="mt-1 text-xs text-muted-foreground">These four selectors are the only normalized audience values sent to rendering. Your description above stays intact for review.</p><div className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-4"><SelectField label="Seniority" value={audience.render.seniority ?? ""} options={[["", "Not set"], ["executive", "Executive"], ["director", "Director"], ["manager", "Manager"], ["practitioner", "Practitioner"]]} onChange={(value) => updateArtifact("audience", { ...audience, render: { ...audience.render, seniority: value || undefined } })} /><SelectField label="Domain familiarity" value={audience.render.domain_familiarity ?? ""} options={[["", "Not set"], ["introductory", "Introductory"], ["working", "Working"], ["expert", "Expert"]]} onChange={(value) => updateArtifact("audience", { ...audience, render: { ...audience.render, domain_familiarity: value || undefined } })} /><SelectField label="Decision horizon" value={audience.render.decision_horizon ?? ""} options={[["", "Not set"], ["immediate", "Immediate"], ["near_term", "Near term"], ["strategic", "Strategic"]]} onChange={(value) => updateArtifact("audience", { ...audience, render: { ...audience.render, decision_horizon: value || undefined } })} /><SelectField label="Detail level" value={audience.render.detail_level ?? ""} options={[["", "Not set"], ["brief", "Brief"], ["standard", "Standard"], ["detailed", "Detailed"]]} onChange={(value) => updateArtifact("audience", { ...audience, render: { ...audience.render, detail_level: value || undefined } })} /></div></fieldset>
        </EditorSection>

        <EditorSection title="Analyses" description="List the distinct questions or lenses the assistant should apply.">
          <EditableRows label="analysis" rows={artifact.analyses ?? []} empty="No analyses yet." onAdd={() => updateArtifact("analyses", [...(artifact.analyses ?? []), { id: idFor("analysis", artifact.analyses?.length ?? 0), label: "", purpose: "" }])} onRemove={(index) => updateArtifact("analyses", (artifact.analyses ?? []).filter((_, rowIndex) => rowIndex !== index))} renderRow={(row, index) => <div className="grid gap-2 sm:grid-cols-[minmax(8rem,0.7fr)_minmax(10rem,1fr)_minmax(14rem,2fr)]"><TextInput label="ID" value={row.id} onChange={(value) => updateRow("analyses", index, { ...row, id: value })} /><TextInput label="Label" value={row.label} onChange={(value) => updateRow("analyses", index, { ...row, label: value })} /><TextInput label="What this analysis answers" value={row.purpose} onChange={(value) => updateRow("analyses", index, { ...row, purpose: value })} /></div>} />
        </EditorSection>

        <details className="border-t border-border pt-5"><summary className="cursor-pointer text-sm font-semibold">Advanced Pack settings</summary><p className="mt-1 text-sm text-muted-foreground">Evidence rules, scoring, compatibility, capability requirements, and stable internal identifiers.</p><div className="mt-5 space-y-6"><EditorSection title="Evidence requirements" description="Say what evidence is needed and how important each source or evidence set is. Platform checks still require grounded findings and uncertainty disclosure.">
          <EditableRows label="evidence requirement" rows={artifact.evidence_requirements ?? []} empty="No evidence requirements yet." onAdd={() => updateArtifact("evidence_requirements", [...(artifact.evidence_requirements ?? []), { id: idFor("evidence", artifact.evidence_requirements?.length ?? 0), label: "", required: true, weight: 1 }])} onRemove={(index) => updateArtifact("evidence_requirements", (artifact.evidence_requirements ?? []).filter((_, rowIndex) => rowIndex !== index))} renderRow={(row, index) => <div className="grid gap-2 sm:grid-cols-[minmax(7rem,0.7fr)_minmax(12rem,2fr)_7rem_auto] sm:items-end"><TextInput label="ID" value={row.id} onChange={(value) => updateRow("evidence_requirements", index, { ...row, id: value })} /><TextInput label="Evidence needed" value={row.label} onChange={(value) => updateRow("evidence_requirements", index, { ...row, label: value })} /><NumberInput label="Weight" value={row.weight} min={1} max={100} onChange={(value) => updateRow("evidence_requirements", index, { ...row, weight: value })} /><Checkbox label="Required" checked={row.required} onChange={(checked) => updateRow("evidence_requirements", index, { ...row, required: checked })} /></div>} />
        </EditorSection>

        <EditorSection title="Output documents and sections" description="Define the documents to produce and the sections inside each one. Order is kept explicit for predictable output.">
          <div className="space-y-5">{(artifact.output_documents ?? []).map((document, documentIndex) => <div key={`${document.id}-${documentIndex}`} className="border-y border-border py-4"><div className="grid gap-2 sm:grid-cols-[minmax(7rem,0.7fr)_minmax(14rem,2fr)_auto] sm:items-end"><TextInput label="Document ID" value={document.id} onChange={(value) => updateDocument(documentIndex, { ...document, id: value })} /><TextInput label="Document title" value={document.title} onChange={(value) => updateDocument(documentIndex, { ...document, title: value })} /><IconButton label="Remove document" onClick={() => updateArtifact("output_documents", (artifact.output_documents ?? []).filter((_, index) => index !== documentIndex))} icon={<Trash2 className="h-4 w-4" />} /></div><div className="mt-4 pl-0 sm:pl-4"><p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Sections</p><div className="mt-2 divide-y border-y border-border">{document.sections.map((section, sectionIndex) => <div key={`${section.id}-${sectionIndex}`} className="grid gap-2 py-3 sm:grid-cols-[minmax(7rem,0.7fr)_minmax(12rem,2fr)_6rem_auto] sm:items-end"><TextInput label="Section ID" value={section.id} onChange={(value) => updateSection(documentIndex, sectionIndex, { ...section, id: value })} /><TextInput label="Heading" value={section.heading} onChange={(value) => updateSection(documentIndex, sectionIndex, { ...section, heading: value })} /><NumberInput label="Order" value={section.order} min={1} max={100} onChange={(value) => updateSection(documentIndex, sectionIndex, { ...section, order: value })} /><IconButton label="Remove section" onClick={() => updateDocument(documentIndex, { ...document, sections: document.sections.filter((_, index) => index !== sectionIndex) })} icon={<Trash2 className="h-4 w-4" />} /></div>)}</div><Button variant="secondary" size="sm" className="mt-3" onClick={() => updateDocument(documentIndex, { ...document, sections: [...document.sections, { id: idFor("section", document.sections.length), heading: "", order: document.sections.length + 1 }] })}><Plus className="h-3.5 w-3.5" aria-hidden />Add section</Button></div></div>)}<Button variant="secondary" size="sm" onClick={() => updateArtifact("output_documents", [...(artifact.output_documents ?? []), { id: idFor("document", artifact.output_documents?.length ?? 0), title: "", sections: [{ id: "section-1", heading: "", order: 1 }] }])}><Plus className="h-3.5 w-3.5" aria-hidden />Add document</Button></div>
        </EditorSection>

        <EditorSection title="Scoring" description="Optional dimensions make findings comparable. Weights are declarative and are not a replacement for required evidence.">
          <EditableRows label="scoring dimension" rows={artifact.scoring_dimensions ?? []} empty="No scoring dimensions. Add one if this Pack needs a scorecard." onAdd={() => updateArtifact("scoring_dimensions", [...(artifact.scoring_dimensions ?? []), { id: idFor("score", artifact.scoring_dimensions?.length ?? 0), label: "", weight: 1 }])} onRemove={(index) => updateArtifact("scoring_dimensions", (artifact.scoring_dimensions ?? []).filter((_, rowIndex) => rowIndex !== index))} renderRow={(row, index) => <div className="grid gap-2 sm:grid-cols-[minmax(7rem,0.7fr)_minmax(14rem,2fr)_7rem_auto] sm:items-end"><TextInput label="ID" value={row.id} onChange={(value) => updateRow("scoring_dimensions", index, { ...row, id: value })} /><TextInput label="Dimension" value={row.label} onChange={(value) => updateRow("scoring_dimensions", index, { ...row, label: value })} /><NumberInput label="Weight" value={row.weight} min={1} max={100} onChange={(value) => updateRow("scoring_dimensions", index, { ...row, weight: value })} /><IconButton label="Remove scoring dimension" onClick={() => updateArtifact("scoring_dimensions", (artifact.scoring_dimensions ?? []).filter((_, rowIndex) => rowIndex !== index))} icon={<Trash2 className="h-4 w-4" />} /></div>} />
        </EditorSection>

        <EditorSection title="Optional capability requirements" description="Declare platform capabilities this Pack can use. Required capabilities can block project compatibility; optional ones are reported when unavailable.">
          <EditableRows label="capability requirement" rows={artifact.capability_requirements ?? []} empty="No capability requirements." onAdd={() => updateArtifact("capability_requirements", [...(artifact.capability_requirements ?? []), { capability_id: "", required: false, purpose: "" }])} onRemove={(index) => updateArtifact("capability_requirements", (artifact.capability_requirements ?? []).filter((_, rowIndex) => rowIndex !== index))} renderRow={(row, index) => <div className="grid gap-2 sm:grid-cols-[minmax(12rem,1fr)_minmax(12rem,1fr)_auto_auto] sm:items-end"><TextInput label="Capability ID" value={row.capability_id} onChange={(value) => updateRow("capability_requirements", index, { ...row, capability_id: value })} /><TextInput label="Why it is needed" value={row.purpose} onChange={(value) => updateRow("capability_requirements", index, { ...row, purpose: value })} /><Checkbox label="Required" checked={row.required} onChange={(checked) => updateRow("capability_requirements", index, { ...row, required: checked })} /><IconButton label="Remove capability" onClick={() => updateArtifact("capability_requirements", (artifact.capability_requirements ?? []).filter((_, rowIndex) => rowIndex !== index))} icon={<Trash2 className="h-4 w-4" />} /></div>} />
        </EditorSection>

        <EditorSection title="Compatibility" description="Optional project descriptors help you check where this Pack fits. Enter your own values; they do not grant access or change project permissions.">
          <div className="grid gap-4 sm:grid-cols-2"><TextInput label="Supported project kinds" value={words(artifact.compatibility?.supported_project_kinds)} onChange={(value) => updateArtifact("compatibility", { ...artifact.compatibility, supported_project_kinds: splitWords(value) })} /><TextInput label="Required domains" value={words(artifact.compatibility?.required_domains)} onChange={(value) => updateArtifact("compatibility", { ...artifact.compatibility, required_domains: splitWords(value) })} /></div>
        </EditorSection>
        </div></details>
      </div>
      </details>

      <div className="mt-6 border-t border-border pt-4"><div className="mt-4 flex flex-wrap justify-between gap-2"><Button variant="secondary" onClick={() => void abandon()} disabled={busy !== null} isLoading={busy === "abandon"}><X className="h-4 w-4" aria-hidden />Discard draft</Button><Button onClick={() => void save()} disabled={!canSave || busy !== null} isLoading={busy === "save"}><Save className="h-4 w-4" aria-hidden />Save Pack</Button></div>{validation?.errors?.length ? <div className="mt-3 border-l-2 border-amber-500 px-3 text-sm text-amber-700" role="status"><p className="font-medium">What still needs attention</p><ul className="mt-1 list-disc pl-4">{validation.errors.slice(0, 5).map((item, index) => <li key={`${item}-${index}`}>{item}</li>)}</ul></div> : null}</div>
    </>}

    {!draft && <div className="mt-6"><details open={advancedOpen} onToggle={(event) => setAdvancedOpen((event.currentTarget as HTMLDetailsElement).open)} className="border-t border-border pt-4"><summary className="flex cursor-pointer list-none items-center gap-2 text-sm font-medium"><ChevronDown className="h-4 w-4" aria-hidden />Advanced project actions</summary><div className="mt-4">{activeVersion ? <VersionWorkspace version={activeVersion} versions={versions} onSelect={setSelectedVersion} projectId={projectId} setProjectId={setProjectId} workflowKey={workflowKey} setWorkflowKey={setWorkflowKey} busy={busy} result={projectResult} onProjectAction={projectAction} onPromotion={proposePromotion} /> : <p className="text-sm text-muted-foreground">Save a personal Pack before checking a project, previewing output, sharing, or requesting activation.</p>}</div></details></div>}
  </section>;

  function updateRow<K extends keyof Artifact>(key: K, index: number, value: unknown) {
    const rows = [...((artifact[key] as unknown[] | undefined) ?? [])];
    rows[index] = value;
    updateArtifact(key, rows as Artifact[K]);
  }

  function updateDocument(index: number, value: OutputDocument) {
    const rows = [...(artifact.output_documents ?? [])]; rows[index] = value; updateArtifact("output_documents", rows);
  }

  function updateSection(documentIndex: number, sectionIndex: number, value: OutputSection) {
    const document = artifact.output_documents?.[documentIndex]; if (!document) return;
    updateDocument(documentIndex, { ...document, sections: document.sections.map((section, index) => index === sectionIndex ? value : section) });
  }
}

function EditorSection({ title, description, children }: { title: string; description: string; children: React.ReactNode }) {
  return <section className="border-t border-border pt-5" aria-labelledby={`builder-${title.toLowerCase().replaceAll(" ", "-")}`}><h3 id={`builder-${title.toLowerCase().replaceAll(" ", "-")}`} className="text-base font-semibold">{title}</h3><p className="mt-1 text-sm text-muted-foreground">{description}</p><div className="mt-4">{children}</div></section>;
}

function PackSummary({ artifact }: { artifact: Artifact }) {
  const audience = artifact.audience ?? emptyAudience;
  const documents = artifact.output_documents?.map((document) => document.title).filter(Boolean) ?? [];
  const analyses = artifact.analyses?.map((analysis) => analysis.label).filter(Boolean) ?? [];
  return <section className="mt-6 border-y border-border py-4" aria-labelledby="pack-summary-heading"><div className="flex items-center gap-2"><Check className="h-4 w-4 text-emerald-700" aria-hidden /><h3 id="pack-summary-heading" className="text-sm font-semibold">Your Pack at a glance</h3></div><dl className="mt-3 grid gap-4 text-sm sm:grid-cols-2"><SummaryItem label="Purpose" value={artifact.purpose} fallback="Tell the assistant what decision or outcome this Pack should support." /><SummaryItem label="Analyses" value={analyses.join(", ")} fallback="The assistant will add the analysis lenses." /><SummaryItem label="Documents" value={documents.join(", ")} fallback="The assistant will propose the documents to produce." /><SummaryItem label="Audience" value={audience.label} fallback="Describe who will use the documentation." /></dl></section>;
}

function SummaryItem({ label, value, fallback }: { label: string; value?: string; fallback: string }) {
  return <div><dt className="text-xs font-medium uppercase tracking-wide text-muted-foreground">{label}</dt><dd className="mt-1 text-foreground/80">{value || fallback}</dd></div>;
}

function EditableRows<T>({ label, rows, empty, onAdd, onRemove, renderRow }: { label: string; rows: T[]; empty: string; onAdd: () => void; onRemove: (index: number) => void; renderRow: (row: T, index: number) => React.ReactNode }) {
  return <div><div className="divide-y border-y border-border">{rows.length ? rows.map((row, index) => <div key={index} className="py-3"><div className="flex items-start gap-3"><div className="min-w-0 flex-1">{renderRow(row, index)}</div><IconButton label={`Remove ${label} ${index + 1}`} onClick={() => onRemove(index)} icon={<Trash2 className="h-4 w-4" />} /></div></div>) : <p className="py-3 text-sm text-muted-foreground">{empty}</p>}</div><Button variant="secondary" size="sm" className="mt-3" onClick={onAdd}><Plus className="h-3.5 w-3.5" aria-hidden />Add {label}</Button></div>;
}

function TextInput({ label, value, onChange }: { label: string; value: string; onChange: (value: string) => void }) {
  const input = <label className="block text-sm font-medium">{label}<input value={value} onChange={(event) => onChange(event.target.value)} className="mt-1 h-9 w-full min-w-0 border border-input bg-background px-2 text-sm" /></label>;
  return label === "ID" || label.endsWith(" ID") ? <details className="text-sm"><summary className="cursor-pointer text-muted-foreground">Internal ID</summary><div className="mt-2">{input}</div></details> : input;
}

function NumberInput({ label, value, min, max, onChange }: { label: string; value: number; min: number; max: number; onChange: (value: number) => void }) {
  const input = <label className="block text-sm font-medium">{label}<input type="number" value={value} min={min} max={max} onChange={(event) => onChange(Number(event.target.value))} className="mt-1 h-9 w-full min-w-0 border border-input bg-background px-2 text-sm" /></label>;
  return label === "Order" ? <details className="text-sm"><summary className="cursor-pointer text-muted-foreground">Document order</summary><div className="mt-2">{input}</div></details> : input;
}

function Checkbox({ label, checked, onChange }: { label: string; checked: boolean; onChange: (checked: boolean) => void }) {
  return <label className="flex min-h-9 items-center gap-2 text-sm font-medium"><input type="checkbox" checked={checked} onChange={(event) => onChange(event.target.checked)} className="h-4 w-4 accent-primary" />{label}</label>;
}

function SelectField({ label, value, options, onChange }: { label: string; value: string; options: readonly (readonly [string, string])[]; onChange: (value: string) => void }) {
  return <label className="block text-sm font-medium">{label}<select value={value} onChange={(event) => onChange(event.target.value)} className="mt-1 h-9 w-full border border-input bg-background px-2 text-sm">{options.map(([optionValue, text]) => <option key={optionValue} value={optionValue}>{text}</option>)}</select></label>;
}

function IconButton({ label, icon, onClick }: { label: string; icon: React.ReactNode; onClick: () => void }) {
  return <button type="button" aria-label={label} title={label} onClick={onClick} className="mt-5 inline-flex h-9 w-9 shrink-0 items-center justify-center border border-border text-muted-foreground hover:bg-muted hover:text-foreground">{icon}</button>;
}

function SavedVersions({ versions, onSelect, selectedProjectId, busy, onUse }: { versions: PrivateVersion[]; onSelect: (version: PrivateVersion) => void; selectedProjectId?: string; busy: string | null; onUse: (version: PrivateVersion) => void }) {
  return <section aria-labelledby="saved-analysis-pack-versions"><div className="flex items-center gap-2"><FileText className="h-4 w-4" aria-hidden /><h3 id="saved-analysis-pack-versions" className="text-sm font-semibold">Your saved Packs</h3></div>{versions.length ? <div className="mt-3 divide-y border-y border-border">{versions.map((version) => <div key={version.id} className="flex flex-wrap items-center justify-between gap-2 py-3"><button type="button" onClick={() => onSelect(version)} className="min-w-0 text-left hover:text-primary"><span className="font-medium">{version.name}</span><span className="ml-2 text-xs text-muted-foreground">v{version.version}</span><span className="ml-2 text-xs text-muted-foreground">View <Eye className="inline h-3.5 w-3.5" aria-hidden /></span></button>{selectedProjectId && <Button size="sm" onClick={() => onUse(version)} disabled={busy !== null} isLoading={busy === `use-${version.id}`}><Upload className="h-3.5 w-3.5" aria-hidden />Use with project</Button>}</div>)}</div> : <p className="mt-3 text-sm text-muted-foreground">No saved Packs yet. Describe what you need to create one.</p>}</section>;
}

function VersionWorkspace({ version, versions, onSelect, projectId, setProjectId, workflowKey, setWorkflowKey, busy, result, onProjectAction, onPromotion }: { version: PrivateVersion; versions: PrivateVersion[]; onSelect: (version: PrivateVersion) => void; projectId: string; setProjectId: (value: string) => void; workflowKey: string; setWorkflowKey: (value: string) => void; busy: string | null; result: ProjectResult | null; onProjectAction: (action: "compatibility" | "preview" | "share" | "activate") => void; onPromotion: () => void }) {
  return <div className="space-y-5"><div className="flex flex-wrap items-center justify-between gap-3"><div><p className="text-sm font-semibold">{version.name} <span className="font-normal text-muted-foreground">v{version.version}</span></p><p className="mt-1 text-xs text-muted-foreground">Private to you · content hash {version.content_hash.slice(0, 12)}…</p></div><label className="text-sm font-medium">Inspect version<select value={version.id} onChange={(event) => { const next = versions.find((item) => item.id === Number(event.target.value)); if (next) onSelect(next); }} className="ml-2 h-9 border border-input bg-background px-2 text-sm">{versions.map((item) => <option key={item.id} value={item.id}>{item.name} v{item.version}</option>)}</select></label></div><VersionContents artifact={version.artifact} /><div className="border-y border-border py-4"><label className="block text-sm font-medium">Project ID<input value={projectId} onChange={(event) => setProjectId(event.target.value)} className="mt-1 h-9 w-full border border-input bg-background px-2 text-sm" placeholder="Enter a project ID for checks or project actions" /></label><label className="mt-3 block text-sm font-medium">Workflow key for activation<input value={workflowKey} onChange={(event) => setWorkflowKey(event.target.value)} className="mt-1 h-9 w-full border border-input bg-background px-2 text-sm" placeholder="documentation.generate" /></label><div className="mt-4 flex flex-wrap gap-2"><Button variant="secondary" size="sm" onClick={() => onProjectAction("compatibility")} disabled={!projectId.trim() || busy !== null} isLoading={busy === "compatibility"}><Check className="h-3.5 w-3.5" aria-hidden />Check compatibility</Button><Button variant="secondary" size="sm" onClick={() => onProjectAction("preview")} disabled={!projectId.trim() || busy !== null} isLoading={busy === "preview"}><Eye className="h-3.5 w-3.5" aria-hidden />Preview output</Button><Button variant="secondary" size="sm" onClick={() => onProjectAction("share")} disabled={!projectId.trim() || busy !== null} isLoading={busy === "share"}><Share2 className="h-3.5 w-3.5" aria-hidden />Share with project</Button><Button size="sm" onClick={() => onProjectAction("activate")} disabled={!projectId.trim() || busy !== null} isLoading={busy === "activate"}><Upload className="h-3.5 w-3.5" aria-hidden />Activate in project</Button></div></div><div className="border-y border-border py-4"><div className="flex flex-wrap items-center justify-between gap-3"><div><p className="text-sm font-medium">Promotion proposal</p><p className="mt-1 text-sm text-muted-foreground">A proposal can be recorded for review, but it cannot install or certify a Pack.</p></div><Button variant="secondary" size="sm" onClick={onPromotion} disabled={busy !== null} isLoading={busy === "promotion"}><Send className="h-3.5 w-3.5" aria-hidden />Propose promotion</Button></div></div>{result && <ProjectResultView result={result} />}</div>;
}

function VersionContents({ artifact }: { artifact: Artifact }) {
  const audience = artifact.audience ?? emptyAudience;
  return <details open className="border-y border-border py-3"><summary className="flex cursor-pointer list-none items-center gap-2 text-sm font-medium"><FileText className="h-4 w-4" aria-hidden />Version contents <span className="font-normal text-muted-foreground">Read-only snapshot</span></summary><div className="mt-4 space-y-4 text-sm"><div><p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Purpose</p><p className="mt-1 whitespace-pre-wrap">{artifact.purpose || "Not set"}</p></div><div className="grid gap-4 sm:grid-cols-2"><ReadOnlyList label="Analyses" values={(artifact.analyses ?? []).map((item) => `${item.label}: ${item.purpose}`)} /><ReadOnlyList label="Evidence requirements" values={(artifact.evidence_requirements ?? []).map((item) => `${item.label} (${item.required ? "required" : "optional"}, weight ${item.weight})`)} /><ReadOnlyList label="Output documents" values={(artifact.output_documents ?? []).map((item) => `${item.title}: ${item.sections.map((section) => section.heading).join(", ")}`)} /><ReadOnlyList label="Scoring" values={(artifact.scoring_dimensions ?? []).map((item) => `${item.label} (weight ${item.weight})`)} /><ReadOnlyList label="Capabilities" values={(artifact.capability_requirements ?? []).map((item) => `${item.capability_id} (${item.required ? "required" : "optional"})`)} /><ReadOnlyList label="Compatibility" values={[...(artifact.compatibility?.supported_project_kinds ?? []).map((item) => `Project kind: ${item}`), ...(artifact.compatibility?.required_domains ?? []).map((item) => `Domain: ${item}`)]} /></div><div><p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Audience</p><p className="mt-1">{audience.label || "Not set"}</p><p className="mt-1 text-muted-foreground">{[...audience.roles, ...audience.decision_needs].join(" · ") || "No roles or decision needs"}</p><p className="mt-1 text-xs text-muted-foreground">Render: {Object.entries(audience.render).map(([key, value]) => `${key.replaceAll("_", " ")} ${value}`).join(" · ") || "no normalized dimensions"}</p></div></div></details>;
}

function ReadOnlyList({ label, values }: { label: string; values: string[] }) {
  return <div><p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">{label}</p>{values.length ? <ul className="mt-1 list-disc space-y-1 pl-4">{values.map((value, index) => <li key={`${value}-${index}`}>{value}</li>)}</ul> : <p className="mt-1 text-muted-foreground">Not set</p>}</div>;
}

function ProjectResultView({ result }: { result: ProjectResult }) {
  const blocked = result.status?.startsWith("blocked") || Boolean(result.detail && !result.compatible && result.status);
  return <div className={`border-l-2 px-3 py-2 text-sm ${blocked ? "border-amber-500 text-amber-800" : "border-primary"}`} role="status"><p className="font-medium">{blocked ? "Blocked: platform setup is still required" : result.shared ? "Shared with project" : result.activation_snapshot_hash ? "Activated for this project" : result.markdown ? "Preview output" : result.compatible === false ? "Not compatible with this project" : "Project check complete"}</p>{result.detail && <p className="mt-1 text-muted-foreground">{typeof result.detail.reason === "string" ? result.detail.reason : "This action has no effect until the required platform review or setup is available."}</p>}{result.missing_required?.length ? <p className="mt-1">Missing required capabilities: {result.missing_required.join(", ")}</p> : null}{result.missing_optional?.length ? <p className="mt-1 text-muted-foreground">Optional capabilities unavailable: {result.missing_optional.join(", ")}</p> : null}{result.markdown && <pre className="mt-3 max-h-96 overflow-auto whitespace-pre-wrap border-t border-border pt-3 text-xs text-foreground">{result.markdown}</pre>}</div>;
}
